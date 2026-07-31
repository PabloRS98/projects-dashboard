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
from datetime import date, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..models import Project, ProjectSnapshot
from . import github_client

logger = logging.getLogger(__name__)

# Un año de histórico: suficiente para ver tendencia anual y sigue siendo
# despreciable en disco (unas 400 filas por proyecto).
KEEP_DAYS = 400


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


def refresh_remote_activity(db: Session) -> int:
    """Rellena `commit_weeks` de los proyectos solo-remoto desde la API del forge.

    Solo GitHub y solo una vez al día: el endpoint de estadísticas es caro y se
    calcula en diferido, así que no tiene sentido pedirlo en cada ciclo de sync.
    Los proyectos con clon local ya lo tienen de `git log`, que es gratis.
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
    actualizados = 0
    for project in projects:
        weeks = github_client.get_commit_weeks(project.remote_repo)
        if weeks is None:
            continue
        project.commit_weeks = json.dumps(weeks)
        actualizados += 1
    db.commit()
    if actualizados:
        logger.info("Actividad remota actualizada: %d proyectos", actualizados)
    return actualizados


def series(db: Session, field: str, days: int = 90) -> list[tuple[date, int]]:
    """Suma diaria de `field` en todos los proyectos, para la gráfica global.

    `field` se valida contra una lista blanca porque acaba en un getattr sobre el
    modelo; no viene de la URL hoy, pero es el tipo de parámetro que acaba
    llegando de fuera en cuanto la vista crece.
    """
    allowed = {"commits_7d", "open_prs", "open_issues", "stars", "todo_count"}
    if field not in allowed:
        raise ValueError("Campo de histórico no permitido: %s" % field)

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
