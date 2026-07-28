"""Dashboard de proyectos: alta/edición, auto-escaneo (con detección de rutas
desaparecidas), filtros, resumen agregado, sincronización y checklist de tareas."""
import os

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..config import settings
from ..database import get_db
from ..flash import redirect_flash
from ..models import Project, TaskItem
from ..services import local_scanner, readme
from ..services.sync import normalize_remote_repo, sync_project
from ..templating import templates

REPO_BASE_URLS = {
    "github": "https://github.com/",
    "gitlab": "https://gitlab.com/",
    "bitbucket": "https://bitbucket.org/",
}


def _repo_url(project: Project) -> str | None:
    base = REPO_BASE_URLS.get(project.remote_provider or "")
    return base + project.remote_repo if base and project.remote_repo else None

router = APIRouter(tags=["proyectos"], dependencies=[Depends(verify_auth)])

FILTERS = {
    "todos": ("Todos", lambda p: True),
    "cambios": ("Con cambios sin commitear", lambda p: p.has_uncommitted_changes),
    "prs": ("Con PRs abiertos", lambda p: (p.open_prs or 0) > 0),
    "errores": ("Con error de sync", lambda p: bool(p.sync_error)),
    "ruta-perdida": ("Ruta local perdida", lambda p: p.local_path_missing),
}


def _is_stale(p: Project, stale_days: int) -> bool:
    """Proyecto 'parado': no archivado y con el último commit hace más de stale_days."""
    d = p.days_since_commit()
    return not p.is_archived and d is not None and d > stale_days


def _clean_tags(raw: str) -> str:
    """Normaliza los tags separados por coma: sin vacíos ni duplicados, conservando el orden."""
    seen: list[str] = []
    for t in (raw or "").split(","):
        t = t.strip()
        if t and t not in seen:
            seen.append(t)
    return ", ".join(seen)


def _summary(projects: list[Project], stale_days: int) -> dict:
    return {
        "total": len(projects),
        "prs": sum(p.open_prs or 0 for p in projects),
        "issues": sum(p.open_issues or 0 for p in projects),
        "todos": sum(p.todo_count or 0 for p in projects),
        "cambios": sum(1 for p in projects if p.has_uncommitted_changes),
        "errores": sum(1 for p in projects if p.sync_error),
        "rutas_perdidas": sum(1 for p in projects if p.local_path_missing),
        "parados": sum(1 for p in projects if _is_stale(p, stale_days)),
    }


@router.get("/")
def list_projects(request: Request, filtro: str = "todos", tag: str | None = None,
                  db: Session = Depends(get_db)):
    all_projects = db.query(Project).order_by(Project.name).all()
    filtro = filtro if filtro in FILTERS else "todos"
    stale_days = settings.stale_project_days

    projects = [p for p in all_projects if FILTERS[filtro][1](p)]
    if tag:
        projects = [p for p in projects if tag in p.tag_list()]

    # Cada proyecto cae en exactamente un grupo: favoritos (no archivados) arriba,
    # luego activos / parados, y los archivados al final.
    favoritos = [p for p in projects if p.is_favorite and not p.is_archived]
    activos = [p for p in projects if not p.is_favorite and not p.is_archived and not _is_stale(p, stale_days)]
    parados = [p for p in projects if not p.is_favorite and not p.is_archived and _is_stale(p, stale_days)]
    archivados = [p for p in projects if p.is_archived]
    groups = [
        ("Favoritos", "star", favoritos),
        ("Activos", "circle-check", activos),
        ("Parados", "moon", parados),
        ("Archivados", "archive", archivados),
    ]
    groups = [g for g in groups if g[2]]  # ocultar grupos vacíos

    all_tags = sorted({t for p in all_projects for t in p.tag_list()})

    return templates.TemplateResponse(request, "dashboard.html", {
        "groups": groups,
        "shown": len(projects),
        "summary": _summary(all_projects, stale_days),
        "filtro": filtro,
        "filtros": [(k, v[0]) for k, v in FILTERS.items()],
        "all_tags": all_tags,
        "tag": tag,
        "stale_days": stale_days,
        "stale_pr_days": settings.stale_pr_days,
        "scan_path": settings.local_repos_base_path,
    })


@router.get("/proyecto/{project_id}")
def project_detail(project_id: int, request: Request, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        return redirect_flash("/", "El proyecto ya no existe", "error")

    local_ok = bool(project.local_path) and not project.local_path_missing
    commits = local_scanner.get_recent_commits(project.local_path) if local_ok else []
    todos = local_scanner.scan_todos(project.local_path)["items"] if local_ok else []
    readme_raw = local_scanner.read_readme(project.local_path) if local_ok else None

    return templates.TemplateResponse(request, "detail.html", {
        "p": project,
        "commits": commits,
        "todos": todos,
        "readme_html": readme.render(readme_raw) if readme_raw else None,
        "repo_url": _repo_url(project),
        "stale_days": settings.stale_project_days,
        "stale_pr_days": settings.stale_pr_days,
    })


@router.post("/nuevo")
def create_project(
    name: str = Form(...),
    local_path: str = Form(""),
    remote_provider: str = Form(""),
    remote_repo: str = Form(""),
    tags: str = Form(""),
    description: str = Form(""),
    homepage_url: str = Form(""),
    db: Session = Depends(get_db),
):
    project = Project(
        name=name.strip(),
        local_path=local_path.strip() or None,
        remote_provider=remote_provider or None,
        remote_repo=normalize_remote_repo(remote_repo),
        tags=_clean_tags(tags),
        description=description.strip() or None,
        homepage_url=homepage_url.strip() or None,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    sync_project(project)
    db.commit()
    return redirect_flash("/", 'Proyecto "%s" añadido y sincronizado' % project.name)


@router.post("/{project_id}/editar")
def edit_project(
    request: Request,
    project_id: int,
    name: str = Form(...),
    local_path: str = Form(""),
    remote_provider: str = Form(""),
    remote_repo: str = Form(""),
    tags: str = Form(""),
    description: str = Form(""),
    homepage_url: str = Form(""),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project:
        return redirect_flash("/", "El proyecto ya no existe", "error")
    project.name = name.strip()
    project.local_path = local_path.strip() or None
    project.remote_provider = remote_provider or None
    project.remote_repo = normalize_remote_repo(remote_repo)
    project.tags = _clean_tags(tags)
    project.description = description.strip() or None
    project.homepage_url = homepage_url.strip() or None
    sync_project(project)
    db.commit()
    referer = request.headers.get("referer") or "/"
    return redirect_flash(referer, 'Proyecto "%s" actualizado y resincronizado' % project.name)


@router.post("/{project_id}/favorito")
def toggle_favorite(request: Request, project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project:
        project.is_favorite = not project.is_favorite
        db.commit()
    referer = request.headers.get("referer") or "/"
    return redirect_flash(referer, "Favoritos actualizados", "info")


@router.post("/{project_id}/archivar")
def toggle_archive(request: Request, project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        return redirect_flash("/", "El proyecto ya no existe", "error")
    project.is_archived = not project.is_archived
    db.commit()
    estado = "archivado" if project.is_archived else "desarchivado"
    referer = request.headers.get("referer") or "/"
    return redirect_flash(referer, '"%s" %s' % (project.name, estado), "info")


@router.post("/escanear")
def scan_local_repos(db: Session = Depends(get_db)):
    """Descubre repos git nuevos y marca los proyectos cuya ruta local ya no exista."""
    existing_paths = {p.local_path for p in db.query(Project).filter(Project.local_path.isnot(None)).all()}
    discovered = local_scanner.discover_repos(settings.local_repos_base_path)
    nuevos = 0
    for repo in discovered:
        if repo["local_path"] in existing_paths:
            continue
        project = Project(name=repo["name"], local_path=repo["local_path"])
        db.add(project)
        db.flush()
        sync_project(project)
        nuevos += 1

    # Detección de rutas desaparecidas en proyectos ya registrados
    perdidos = 0
    for project in db.query(Project).filter(Project.local_path.isnot(None)).all():
        missing = not os.path.isdir(project.local_path)
        if missing and not project.local_path_missing:
            project.local_path_missing = True
            project.local_error = "La ruta local ya no existe"
            project.has_uncommitted_changes = False
            perdidos += 1
        elif not missing and project.local_path_missing:
            sync_project(project)  # la ruta ha vuelto (disco montado de nuevo, etc.)
    db.commit()

    msg = "Escaneo completado: %d proyectos nuevos" % nuevos
    if perdidos:
        msg += ", %d con ruta desaparecida" % perdidos
    return redirect_flash("/", msg, "success" if nuevos or not perdidos else "info")


@router.post("/sincronizar-todo")
def sync_all(db: Session = Depends(get_db)):
    projects = db.query(Project).all()
    errores = 0
    for project in projects:
        sync_project(project)
        if project.sync_error:
            errores += 1
    db.commit()
    msg = "%d proyectos sincronizados" % len(projects)
    if errores:
        msg += " (%d con errores)" % errores
    return redirect_flash("/", msg, "success" if not errores else "info")


@router.post("/{project_id}/sincronizar")
def sync_one(request: Request, project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        return redirect_flash("/", "El proyecto ya no existe", "error")
    sync_project(project)
    db.commit()
    referer = request.headers.get("referer") or "/"
    if project.sync_error:
        return redirect_flash(referer, '"%s": %s' % (project.name, project.sync_error), "error")
    return redirect_flash(referer, '"%s" sincronizado' % project.name)


@router.post("/{project_id}/notas")
def update_notes(request: Request, project_id: int, notes: str = Form(""), db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project:
        project.notes = notes
        db.commit()
    referer = request.headers.get("referer") or "/"
    return redirect_flash(referer, "Notas guardadas")


@router.post("/{project_id}/eliminar")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if project:
        db.delete(project)
        db.commit()
    return redirect_flash("/", "Proyecto eliminado", "info")


# ---- Tareas: endpoints HTMX que devuelven el fragmento actualizado ----

def _tasks_fragment(request: Request, db: Session, project_id: int):
    project = db.get(Project, project_id)
    return templates.TemplateResponse(request, "_tasks.html", {"p": project})


@router.post("/{project_id}/tareas")
def add_task(request: Request, project_id: int, text: str = Form(...), db: Session = Depends(get_db)):
    if text.strip():
        max_order = db.query(TaskItem).filter(TaskItem.project_id == project_id).count()
        db.add(TaskItem(project_id=project_id, text=text.strip(), order=max_order))
        db.commit()
    return _tasks_fragment(request, db, project_id)


@router.post("/tareas/{task_id}/toggle")
def toggle_task(request: Request, task_id: int, db: Session = Depends(get_db)):
    task = db.get(TaskItem, task_id)
    project_id = task.project_id if task else 0
    if task:
        task.done = not task.done
        db.commit()
    return _tasks_fragment(request, db, project_id)


@router.post("/tareas/{task_id}/eliminar")
def delete_task(request: Request, task_id: int, db: Session = Depends(get_db)):
    task = db.get(TaskItem, task_id)
    project_id = task.project_id if task else 0
    if task:
        db.delete(task)
        db.commit()
    return _tasks_fragment(request, db, project_id)
