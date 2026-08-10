"""Avisos de Telegram sobre el estado de los proyectos:
- CI en rojo, PR estancado (> stale_pr_days) y proyecto parado (> stale_project_days).
- Resumen diario del estado general.

Cada aviso lleva un flag de dedup en el proyecto (ci_notified / pr_stale_notified /
stale_notified) que se pone al avisar y se resetea cuando la condición desaparece,
para no repetir el mismo aviso en cada ciclo.
"""
import logging

from sqlalchemy.orm import Session

from ..config import settings
from ..models import CI_BAD, CI_GOOD, Project
from . import telegram

logger = logging.getLogger(__name__)


def _is_stale(project: Project) -> bool:
    d = project.days_since_commit()
    return not project.is_archived and d is not None and d > settings.stale_project_days


def check_alerts(db: Session) -> int:
    """Revisa condiciones y envía los avisos pendientes. Devuelve cuántos envió.
    Sin Telegram configurado no hace nada (ni toca los flags), para que al activarlo
    después se envíen los avisos del estado actual."""
    if not telegram.is_configured():
        return 0

    sent = 0
    for p in db.query(Project).all():
        # Los mensajes van con parse_mode HTML, así que todo dato que se
        # interpole se escapa: un nombre con "&" (válido en GitHub y en una
        # carpeta local) hacía que Telegram respondiera 400 y el aviso se
        # perdiera. Y el flag solo se marca si el envío funcionó: marcarlo igual
        # dejaba el aviso silenciado hasta que la condición se rearmara, o sea
        # que ese proyecto no avisaba nunca.
        nombre = telegram.esc(p.name)

        # CI en rojo
        if p.ci_status in CI_BAD:
            if not p.ci_notified and telegram.send_message(
                "🔴 <b>%s</b> — CI en rojo (%s)" % (nombre, telegram.esc(p.ci_status))
            ):
                sent += 1
                p.ci_notified = True
        elif p.ci_status in CI_GOOD:
            p.ci_notified = False

        # PR estancado
        oldest = p.oldest_open_pr_days
        if oldest is not None and oldest > settings.stale_pr_days:
            if not p.pr_stale_notified and telegram.send_message(
                "⏳ <b>%s</b> — PR abierto desde hace %d días" % (nombre, oldest)
            ):
                sent += 1
                p.pr_stale_notified = True
        else:
            p.pr_stale_notified = False

        # Proyecto parado
        if _is_stale(p):
            if not p.stale_notified and telegram.send_message(
                "🌙 <b>%s</b> — parado: %d días sin commits" % (nombre, p.days_since_commit())
            ):
                sent += 1
                p.stale_notified = True
        else:
            p.stale_notified = False

    db.commit()
    if sent:
        logger.info("Avisos de proyectos enviados: %d", sent)
    return sent


def daily_summary(db: Session) -> bool:
    """Un mensaje/día con el estado general. Devuelve True si se envió."""
    if not telegram.is_configured():
        return False

    projects = db.query(Project).all()
    if not projects:
        return False

    parados = [p for p in projects if _is_stale(p)]
    prs = sum(p.open_prs or 0 for p in projects)
    cambios = sum(1 for p in projects if p.has_uncommitted_changes)
    errores = sum(1 for p in projects if p.sync_error)

    lines = [
        "📊 <b>Resumen de proyectos</b>",
        "%d proyectos · %d PRs abiertos · %d con cambios sin commitear" % (len(projects), prs, cambios),
    ]
    if parados:
        nombres = ", ".join(telegram.esc(p.name) for p in parados[:10])
        lines.append("🌙 Parados (%d): %s" % (len(parados), nombres))
    if errores:
        lines.append("⚠️ %d con error de sincronización" % errores)
    return telegram.send_message("\n".join(lines))
