"""Histórico diario de métricas: lo que convierte el panel en algo que muestra
tendencias en vez de solo el estado de este instante.

Un job diario escribe una fila por proyecto (`ProjectSnapshot`). Con eso se puede
responder "¿esto va a más o a menos?", que es justo lo que el estado actual no
dice: 3 PRs abiertos significa algo muy distinto si ayer había 1 o si había 10.

Además rellena la actividad semanal de los proyectos solo-remoto, que no tienen
clon local del que sacarla con `git log`.
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..models import Project, ProjectSnapshot
from . import github_client

logger = logging.getLogger(__name__)

# Un año de histórico: suficiente para ver tendencia anual y sigue siendo
# despreciable en disco (unas 400 filas por proyecto).
KEEP_DAYS = 400

# Peticiones simultáneas al pedir la actividad. El mismo número que
# `scheduler.REMOTE_WORKERS` y por el mismo motivo: el cuello de botella es la
# cuota del forge, no la CPU.
ACTIVIDAD_WORKERS = 5

# Segundos antes de reintentar cuando GitHub responde 202 (aún calculando).
ESPERA_REINTENTO = 4.0


def _commits_last_7d(project: Project) -> int | None:
    """Commits de los últimos 7 días.

    El último cubo de `commit_weeks` es exactamente la ventana 0-6 días, así que
    ya está calculado y no hace falta volver a invocar a git.
    """
    weeks = project.week_counts()
    return weeks[-1] if weeks else None


def take_snapshot(db: Session, day: date | None = None) -> int:
    """Escribe (o actualiza) la foto de hoy de todos los proyectos. Devuelve cuántas filas tocó."""
    day = day or date.today()
    existing = {
        row.project_id: row
        for row in db.query(ProjectSnapshot).filter(ProjectSnapshot.day == day).all()
    }

    touched = 0
    for project in db.query(Project).all():
        snapshot = existing.get(project.id) or ProjectSnapshot(project_id=project.id, day=day)
        snapshot.commits_7d = _commits_last_7d(project)
        snapshot.open_prs = project.open_prs
        snapshot.open_issues = project.open_issues
        snapshot.stars = project.stars
        snapshot.todo_count = project.todo_count
        snapshot.ci_status = project.ci_status
        snapshot.days_since_commit = project.days_since_commit()
        if snapshot.id is None:
            db.add(snapshot)
        touched += 1

    # Poda del histórico viejo en la misma pasada.
    cutoff = day - timedelta(days=KEEP_DAYS)
    db.execute(delete(ProjectSnapshot).where(ProjectSnapshot.day < cutoff))
    db.commit()
    logger.info("Snapshot diario: %d proyectos", touched)
    return touched


def _actividad_con_reintento(owner_repo: str) -> list[int] | None:
    """Pide la actividad y reintenta una vez si GitHub aún la está calculando.

    `/stats/participation` devuelve 202 con cuerpo vacío mientras calcula, y
    `get_commit_weeks` devuelve None en ese caso para no guardar una serie de
    ceros. Pero la "siguiente pasada" es el job de mañana: para un repo recién
    importado eso significaba días sin actividad en la tarjeta. GitHub suele
    tener las estadísticas listas unos segundos después de la primera solicitud,
    que es justo lo que las dispara.

    Un solo reintento: si sigue calculando, se deja para mañana de verdad.
    """
    weeks = github_client.get_commit_weeks(owner_repo)
    if weeks is not None:
        return weeks
    time.sleep(ESPERA_REINTENTO)
    return github_client.get_commit_weeks(owner_repo)


def refresh_remote_activity(db: Session) -> int:
    """Rellena `commit_weeks` de los proyectos solo-remoto desde la API del forge.

    Solo GitHub y solo una vez al día: el endpoint de estadísticas es caro y se
    calcula en diferido, así que no tiene sentido pedirlo en cada ciclo de sync.
    Los proyectos con clon local ya lo tienen de `git log`, que es gratis.

    En paralelo con el mismo patrón que `scheduler.sync_all_remote`: eran una
    petición por proyecto en serie con 10 s de timeout, así que con 40 repos
    importados —lo que `AUTO_IMPORT_GITHUB=true` hace probable— podían ser
    siete minutos dentro del job de las 4:30.

    Solo los hilos hacen red; la escritura en la base se hace después, desde el
    hilo que ya tiene la sesión, para no repartir una `Session` de SQLAlchemy
    entre varios hilos.
    """
    projects = (
        db.query(Project)
        .filter(
            Project.local_path.is_(None),
            Project.remote_provider == "github",
            Project.remote_repo.isnot(None),
        )
        .all()
    )
    if not projects:
        return 0

    actualizados = 0
    with ThreadPoolExecutor(max_workers=ACTIVIDAD_WORKERS) as pool:
        pendientes = {
            pool.submit(_actividad_con_reintento, p.remote_repo): p for p in projects
        }
        for futuro in as_completed(pendientes):
            project = pendientes[futuro]
            try:
                weeks = futuro.result()
            except Exception:  # noqa: BLE001  un repo que falla no tumba el job
                logger.warning("Fallo al pedir la actividad de %s", project.remote_repo)
                continue
            if weeks is None:
                continue
            project.commit_weeks = json.dumps(weeks)
            # Commit por proyecto: con uno solo al final, un fallo a mitad tiraba
            # las peticiones ya hechas y había que repetirlas mañana.
            db.commit()
            actualizados += 1

    if actualizados:
        logger.info("Actividad remota actualizada: %d proyectos", actualizados)
    return actualizados


# Campos que se pueden pedir a las series. Lista blanca porque acaban en un
# getattr sobre el modelo; hoy no vienen de la URL, pero es el tipo de parámetro
# que acaba llegando de fuera en cuanto la vista crece.
CAMPOS_SERIE = {"commits_7d", "open_prs", "open_issues", "stars", "todo_count"}


def _validar_campo(field: str) -> None:
    if field not in CAMPOS_SERIE:
        raise ValueError("Campo de histórico no permitido: %s" % field)


def series(db: Session, field: str, days: int = 90) -> list[tuple[date, int]]:
    """Suma diaria de `field` en todos los proyectos, para la gráfica global."""
    _validar_campo(field)

    since = date.today() - timedelta(days=days)
    rows = (
        db.query(ProjectSnapshot)
        .filter(ProjectSnapshot.day >= since)
        .order_by(ProjectSnapshot.day)
        .all()
    )
    totals: dict[date, int] = {}
    for row in rows:
        value = getattr(row, field)
        if value is not None:
            totals[row.day] = totals.get(row.day, 0) + value
    return sorted(totals.items())


def project_series(db: Session, project_id: int, field: str, days: int = 90) -> list[tuple[date, int]]:
    """Serie diaria de `field` para un solo proyecto, para su ficha.

    Es la misma pregunta que `series` pero acotada: en el panel interesa el
    agregado ("¿el trabajo pendiente sube o baja?") y en la ficha interesa el
    proyecto ("¿este en concreto se está atascando?").
    """
    _validar_campo(field)

    since = date.today() - timedelta(days=days)
    rows = (
        db.query(ProjectSnapshot)
        .filter(ProjectSnapshot.project_id == project_id, ProjectSnapshot.day >= since)
        .order_by(ProjectSnapshot.day)
        .all()
    )
    return [(row.day, getattr(row, field)) for row in rows if getattr(row, field) is not None]
