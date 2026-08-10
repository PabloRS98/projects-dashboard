"""Dashboard de proyectos: alta/edición, descubrimiento automático, búsqueda,
filtros combinables, orden, resumen agregado, panel de estado, sincronización y
checklist de tareas."""
from fastapi import APIRouter, BackgroundTasks, Depends, Form, Query, Request, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..config import settings
from ..database import SessionLocal, get_db
from ..flash import redirect_flash
from ..models import Project, TaskItem
from ..security import safe_external_url
from ..services import github_client, history, local_scanner, readme, scheduler, telegram
from ..services.sync import normalize_remote_repo, sync_project
from ..templating import templates

router = APIRouter(tags=["proyectos"], dependencies=[Depends(verify_auth)])

CI_BAD = {"failure", "failed", "error", "cancelled", "timed_out"}

# Filtros combinables: se aplican en AND. Cada uno es (etiqueta, icono, predicado).
FILTERS = {
    "cambios": ("Sin commitear", "git-branch", lambda p, s: p.has_uncommitted_changes),
    "prs": ("Con PRs abiertos", "git-pull-request", lambda p, s: (p.open_prs or 0) > 0),
    "pr-estancado": (
        "PR estancado", "hourglass",
        lambda p, s: (p.oldest_open_pr_days or 0) > s.stale_pr_days,
    ),
    "ci-rojo": ("CI en rojo", "triangle-alert", lambda p, s: p.ci_status in CI_BAD),
    "errores": ("Con error de sync", "plug-zap", lambda p, s: bool(p.sync_error)),
    "ruta-perdida": ("Ruta local perdida", "folder-x", lambda p, s: p.local_path_missing),
    "parados": ("Parados", "moon", lambda p, s: _is_stale(p, s.stale_project_days)),
    "favoritos": ("Favoritos", "star", lambda p, s: p.is_favorite),
}

def _staleness(p: Project) -> int:
    """Días desde el último commit; los proyectos sin fecha van al final.

    Sin el valor centinela, `None` no es comparable con `int` y ordenar reventaba
    en cuanto había un proyecto recién dado de alta.
    """
    dias = p.days_since_commit()
    return dias if dias is not None else 10**6


# Orden: (etiqueta, clave). El sentido lo fija cada criterio, no un parámetro
# aparte, porque para cada uno solo hay uno que el usuario quiera ver primero.
SORTS = {
    "commit": ("Actividad reciente", _staleness),
    "nombre": ("Nombre", lambda p: p.name.lower()),
    "estrellas": ("Estrellas", lambda p: -(p.stars or 0)),
    "todos": ("TODOs", lambda p: -(p.todo_count or 0)),
    "prs": ("PRs abiertos", lambda p: -(p.open_prs or 0)),
}
DEFAULT_SORT = "commit"


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


def _summary(projects: list[Project], stale_days: int, stale_pr_days: int) -> dict:
    return {
        "total": len(projects),
        "prs": sum(p.open_prs or 0 for p in projects),
        "issues": sum(p.open_issues or 0 for p in projects),
        "todos": sum(p.todo_count or 0 for p in projects),
        "cambios": sum(1 for p in projects if p.has_uncommitted_changes),
        "errores": sum(1 for p in projects if p.sync_error),
        "rutas_perdidas": sum(1 for p in projects if p.local_path_missing),
        "parados": sum(1 for p in projects if _is_stale(p, stale_days)),
        "ci_rojo": sum(1 for p in projects if p.ci_status in CI_BAD),
        "prs_estancados": sum(1 for p in projects if (p.oldest_open_pr_days or 0) > stale_pr_days),
        "estrellas": sum(p.stars or 0 for p in projects),
    }


def _matches_search(p: Project, needle: str) -> bool:
    """Busca en lo que el usuario recuerda de un proyecto: nombre, descripción,
    tags y el 'owner/repo' del remoto."""
    if not needle:
        return True
    haystack = " ".join(
        filter(None, [p.name, p.description, p.tags, p.remote_repo, p.local_path])
    ).lower()
    return all(word in haystack for word in needle.lower().split())


def _select(db: Session, q: str, filtros: list[str], tag: str | None, orden: str):
    """Aplica búsqueda, filtros combinables, tag y orden. Devuelve (todos, visibles)."""
    all_projects = db.query(Project).all()
    activos = [f for f in filtros if f in FILTERS]

    visible = [p for p in all_projects if _matches_search(p, q)]
    for key in activos:
        predicate = FILTERS[key][2]
        visible = [p for p in visible if predicate(p, settings)]
    if tag:
        visible = [p for p in visible if tag in p.tag_list()]

    visible.sort(key=SORTS[orden][1])
    return all_projects, visible


def _grouped(projects: list[Project], stale_days: int):
    """Favoritos arriba, luego activos, parados y archivados. Cada proyecto en un solo grupo."""
    favoritos = [p for p in projects if p.is_favorite and not p.is_archived]
    activos = [p for p in projects
               if not p.is_favorite and not p.is_archived and not _is_stale(p, stale_days)]
    parados = [p for p in projects
               if not p.is_favorite and not p.is_archived and _is_stale(p, stale_days)]
    archivados = [p for p in projects if p.is_archived]
    groups = [
        ("Favoritos", "star", favoritos),
        ("Activos", "circle-check", activos),
        ("Parados", "moon", parados),
        ("Archivados", "archive", archivados),
    ]
    return [g for g in groups if g[2]]  # ocultar grupos vacíos


def _view_context(db: Session, q: str, filtros: list[str], tag: str | None,
                  orden: str, vista: str) -> dict:
    orden = orden if orden in SORTS else DEFAULT_SORT
    vista = vista if vista in ("tarjetas", "tabla") else "tarjetas"
    filtros = [f for f in filtros if f in FILTERS]

    all_projects, visible = _select(db, q, filtros, tag, orden)
    stale_days = settings.stale_project_days
    return {
        "groups": _grouped(visible, stale_days),
        "visible": visible,
        "shown": len(visible),
        # Dos resúmenes: el global alimenta los KPI (que siempre hablan de todo el
        # panel) y el de lo visible acompaña a la lista, que es lo que el usuario
        # está mirando tras filtrar.
        "summary": _summary(all_projects, stale_days, settings.stale_pr_days),
        "visible_summary": _summary(visible, stale_days, settings.stale_pr_days),
        "q": q,
        "filtros": filtros,
        "filtros_disponibles": [(k, v[0], v[1]) for k, v in FILTERS.items()],
        "orden": orden,
        "ordenes": [(k, v[0]) for k, v in SORTS.items()],
        "vista": vista,
        "all_tags": sorted({t for p in all_projects for t in p.tag_list()}),
        "tag": tag,
        "stale_days": stale_days,
        "stale_pr_days": settings.stale_pr_days,
        "scan_path": settings.local_repos_base_path,
    }


@router.get("/")
def list_projects(
    request: Request,
    q: str = "",
    filtro: list[str] = Query(default=[]),
    tag: str | None = None,
    orden: str = DEFAULT_SORT,
    vista: str = "tarjetas",
    db: Session = Depends(get_db),
):
    context = _view_context(db, q.strip(), filtro, tag, orden, vista)
    return templates.TemplateResponse(request, "dashboard.html", context)


@router.get("/lista")
def project_list_fragment(
    request: Request,
    q: str = "",
    filtro: list[str] = Query(default=[]),
    tag: str | None = None,
    orden: str = DEFAULT_SORT,
    vista: str = "tarjetas",
    db: Session = Depends(get_db),
):
    """Solo la lista de proyectos. Lo pide HTMX al teclear en el buscador o al
    cambiar orden/filtros, para no repintar la página entera en cada pulsación."""
    context = _view_context(db, q.strip(), filtro, tag, orden, vista)
    return templates.TemplateResponse(request, "_project_list.html", context)


@router.get("/tv")
def tv_mode(request: Request, db: Session = Depends(get_db)):
    """Modo pantalla: solo el resumen y lo que necesita atención, con autorrefresco.

    Pensado para dejarlo puesto en un monitor: sin formularios ni acciones, para
    que no haga falta interactuar y no se pueda tocar nada sin querer.
    """
    projects = db.query(Project).filter(Project.is_archived.is_(False)).all()
    stale_days = settings.stale_project_days
    atencion = [
        p for p in projects
        if p.ci_status in CI_BAD
        or p.sync_error
        or _is_stale(p, stale_days)
        or (p.oldest_open_pr_days or 0) > settings.stale_pr_days
    ]
    atencion.sort(key=lambda p: (p.ci_status not in CI_BAD, -(p.days_since_commit() or 0)))
    return templates.TemplateResponse(request, "tv.html", {
        "summary": _summary(projects, stale_days, settings.stale_pr_days),
        "atencion": atencion,
        "stale_days": stale_days,
        "stale_pr_days": settings.stale_pr_days,
    })


# Métricas del histórico que se pintan como tendencia, en este orden.
# `stars` se deja fuera a propósito: mide atención de terceros, no trabajo
# propio, y en un panel personal no dice nada accionable.
TENDENCIAS = (
    ("commits_7d", "Commits (7 días)", "git-commit-horizontal"),
    ("open_prs", "PRs abiertos", "git-pull-request"),
    ("open_issues", "Issues abiertas", "circle-dot"),
    ("todo_count", "TODOs en el código", "list-todo"),
)
TENDENCIA_DIAS = 90


def _tendencias(db: Session, project_id: int | None = None, days: int = TENDENCIA_DIAS) -> list[dict]:
    """Series del histórico listas para pintar, globales o de un proyecto.

    Devuelve siempre las cuatro métricas aunque estén vacías: así la plantilla
    puede decir "todavía no hay histórico" en vez de esconder la sección, que es
    lo que haría pensar que la funcionalidad no existe.
    """
    salida = []
    for campo, etiqueta, icono in TENDENCIAS:
        puntos = (
            history.series(db, campo, days)
            if project_id is None
            else history.project_series(db, project_id, campo, days)
        )
        valores = [valor for _, valor in puntos]
        salida.append({
            "campo": campo,
            "label": etiqueta,
            "icon": icono,
            # Días que abarca el dato, no la ventana pedida: con dos semanas de
            # histórico, "−195 en 90 días" es falso y además invita a leer la
            # pendiente como si fuera trimestral.
            "dias": (puntos[-1][0] - puntos[0][0]).days if len(puntos) > 1 else 0,
            "puntos": puntos,
            "primero": valores[0] if valores else None,
            "actual": valores[-1] if valores else None,
            "delta": (valores[-1] - valores[0]) if len(valores) > 1 else None,
        })
    return salida


@router.get("/estado")
def system_status(request: Request, db: Session = Depends(get_db)):
    """Salud del propio panel: qué jobs han corrido, cuota de la API y configuración.

    Sin esta vista, un token caducado o un job caído solo se notaban porque los
    datos dejaban de moverse, sin ninguna pista de por qué.
    """
    jobs = []
    running = getattr(request.app.state, "scheduler", None)
    for job_id, label in scheduler.JOB_LABELS.items():
        status = scheduler.JOB_STATUS.get(job_id, {})
        job = running.get_job(job_id) if running else None
        jobs.append({
            "id": job_id,
            "label": label,
            "last_at": status.get("at"),
            "ok": status.get("ok"),
            "detail": status.get("detail"),
            "next_at": getattr(job, "next_run_time", None) if job else None,
        })

    errores = [
        p for p in db.query(Project).all() if p.sync_error
    ]
    return templates.TemplateResponse(request, "estado.html", {
        "jobs": jobs,
        "errores": errores,
        "tendencias": _tendencias(db),
        "rate_limit": github_client.rate_limit,
        "telegram_ok": telegram.is_configured(),
        "github_token": bool(settings.github_token),
        "auto_import": settings.auto_import_github,
        "scan_path": settings.local_repos_base_path,
        "scan_depth": settings.projects_scan_depth,
        "discovery_minutes": settings.discovery_minutes,
        "local_sync_minutes": settings.local_sync_minutes,
        "remote_sync_minutes": settings.remote_sync_minutes,
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
        "tendencias": _tendencias(db, project.id),
        "readme_html": readme.render(readme_raw, project.repo_url, project.branch) if readme_raw else None,
        "stale_days": settings.stale_project_days,
        "stale_pr_days": settings.stale_pr_days,
    })


def _sync_in_background(project_id: int) -> None:
    """Sincroniza fuera del ciclo petición-respuesta.

    `sync_project` puede tardar decenas de segundos (cuatro llamadas HTTP con 10s
    de timeout cada una). Hacerlo dentro del POST dejaba el navegador colgado y,
    con varios proyectos, convertía "Sincronizar todo" en minutos de pantalla en
    blanco. Aquí se responde ya y la página siguiente muestra el estado nuevo.
    """
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project:
            sync_project(project)
            db.commit()
    finally:
        db.close()


@router.post("/nuevo")
def create_project(
    background: BackgroundTasks,
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
        homepage_url=safe_external_url(homepage_url),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    background.add_task(_sync_in_background, project.id)
    return redirect_flash("/", 'Proyecto "%s" añadido; sincronizando…' % project.name)


@router.post("/{project_id}/editar")
def edit_project(
    request: Request,
    background: BackgroundTasks,
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
    project.homepage_url = safe_external_url(homepage_url)
    db.commit()
    background.add_task(_sync_in_background, project.id)
    referer = request.headers.get("referer") or "/"
    return redirect_flash(referer, 'Proyecto "%s" actualizado; resincronizando…' % project.name)


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
def scan_local_repos(background: BackgroundTasks):
    """Descubrimiento a demanda: el mismo que corre solo cada `discovery_minutes`.

    En segundo plano por el mismo motivo que `_sync_in_background`, y con más
    razón: es la operación más cara de todas. Recorre el disco hasta
    `PROJECTS_SCAN_DEPTH` niveles invocando `git` por cada repo, cuenta los TODOs
    de los nuevos y pide hasta cinco páginas a la API de GitHub. En la primera
    ejecución sobre una cuenta con treinta repos eso son minutos — justo cuando
    más se usa el botón.

    Se encola `scheduler.run_discovery` y no `discovery.run_discovery` a
    propósito: la versión del scheduler abre su propia sesión y deja el resultado
    en `JOB_STATUS`, que `/estado` ya pinta. No hay que construir nada nuevo para
    enterarse de cómo fue.
    """
    background.add_task(scheduler.run_discovery)
    return redirect_flash(
        "/", "Descubrimiento en marcha… El resultado aparece en Estado.", "info"
    )


@router.post("/sincronizar-todo")
def sync_all(background: BackgroundTasks, db: Session = Depends(get_db)):
    ids = [p.id for p in db.query(Project).all()]
    for project_id in ids:
        background.add_task(_sync_in_background, project_id)
    return redirect_flash(
        "/", "Sincronizando %d proyectos en segundo plano…" % len(ids), "info"
    )


@router.post("/{project_id}/sincronizar")
def sync_one(
    request: Request,
    background: BackgroundTasks,
    project_id: int,
    db: Session = Depends(get_db),
):
    """Sincroniza un proyecto, en segundo plano como "Sincronizar todo".

    Antes era síncrono, y la razón era buena: devolvía el error concreto en el
    flash. Pero dejaba el navegador colgado hasta 40 s (cuatro peticiones HTTP
    con 10 s de timeout), y era el botón que más se pulsa — así que sincronizar
    *todos* los proyectos respondía al instante y sincronizar *uno* no, que es
    justo al revés de lo esperable.

    El flash no hacía falta para enterarse: `sync_local` y `sync_remote` guardan
    `local_error` y `remote_error` en el propio proyecto, y la tarjeta los pinta
    (`_card.html`). El error se ve igual en la siguiente carga.
    """
    project = db.get(Project, project_id)
    if not project:
        return redirect_flash("/", "El proyecto ya no existe", "error")
    nombre = project.name
    background.add_task(_sync_in_background, project_id)
    referer = request.headers.get("referer") or "/"
    return redirect_flash(referer, 'Sincronizando "%s"…' % nombre, "info")


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
    """Fragmento con la checklist, o 404 si el proyecto ya no existe.

    Devolver 404 en lugar de renderizar con `p=None` es lo que quiere HTMX: por
    defecto no intercambia contenido en respuestas de error, así que la lista
    que ya está en pantalla se queda como está en vez de vaciarse.
    """
    project = db.get(Project, project_id)
    if not project:
        return Response(status_code=404)
    return templates.TemplateResponse(request, "_tasks.html", {"p": project})


@router.post("/{project_id}/tareas")
def add_task(request: Request, project_id: int, text: str = Form(...), db: Session = Depends(get_db)):
    # Comprobar ANTES de crear: `_tasks_fragment` también lo comprueba, pero corre
    # al final, y para entonces la tarea ya estaba creada y commiteada colgando de
    # un proyecto inexistente. Basta con dos pestañas abiertas: se borra el
    # proyecto en una y se añade una tarea en la otra.
    if db.get(Project, project_id) is None:
        return Response(status_code=404)
    if text.strip():
        # max(order)+1, no count(): con count(), borrar una tarea intermedia hace
        # que la siguiente nazca con un `order` que ya existe.
        current_max = db.query(func.max(TaskItem.order)).filter(
            TaskItem.project_id == project_id
        ).scalar()
        next_order = (current_max + 1) if current_max is not None else 0
        db.add(TaskItem(project_id=project_id, text=text.strip(), order=next_order))
        db.commit()
    return _tasks_fragment(request, db, project_id)


@router.post("/tareas/{task_id}/toggle")
def toggle_task(request: Request, task_id: int, db: Session = Depends(get_db)):
    task = db.get(TaskItem, task_id)
    if task is None:
        return Response(status_code=404)
    project_id = task.project_id
    task.done = not task.done
    db.commit()
    return _tasks_fragment(request, db, project_id)


@router.post("/tareas/{task_id}/eliminar")
def delete_task(request: Request, task_id: int, db: Session = Depends(get_db)):
    task = db.get(TaskItem, task_id)
    if task is None:
        return Response(status_code=404)
    project_id = task.project_id
    db.delete(task)
    db.commit()
    return _tasks_fragment(request, db, project_id)
