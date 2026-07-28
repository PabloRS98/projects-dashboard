"""Sincronización periódica en segundo plano, partida en dos ciclos:
git local frecuente y barato, API remota espaciada (rate limits de GitHub).
Ambos corren también poco después de arrancar (si no, el panel sale con datos viejos)."""
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from ..config import settings
from ..database import SessionLocal
from ..models import Project
from . import alerts
from .sync import sync_local, sync_remote

logger = logging.getLogger(__name__)


def sync_all_local() -> None:
    db = SessionLocal()
    try:
        projects = db.query(Project).all()
        for project in projects:
            sync_local(project)
        db.commit()
        logger.info("Sync local: %d proyectos", len(projects))
    finally:
        db.close()


def sync_all_remote() -> None:
    db = SessionLocal()
    try:
        projects = db.query(Project).filter(
            Project.remote_provider.isnot(None), Project.remote_repo.isnot(None)
        ).all()
        for project in projects:
            sync_remote(project)
        db.commit()
        logger.info("Sync remoto: %d proyectos", len(projects))
    finally:
        db.close()


def run_alerts() -> None:
    db = SessionLocal()
    try:
        alerts.check_alerts(db)
    except Exception:
        logger.exception("Fallo en el chequeo de avisos")
    finally:
        db.close()


def run_daily_summary() -> None:
    db = SessionLocal()
    try:
        alerts.daily_summary(db)
    except Exception:
        logger.exception("Fallo en el resumen diario")
    finally:
        db.close()


def backup_database(dest_path: str | None = None) -> str:
    """Copia consistente de la BD (API de backup de SQLite, segura aunque haya
    escrituras). Sin `dest_path` va a /data/backups/projects-AAAAMMDD.db y rota
    las copias antiguas (se conservan `backup_keep`). Devuelve la ruta creada."""
    if dest_path is None:
        backups_dir = os.path.join(os.path.dirname(settings.db_path), "backups")
        os.makedirs(backups_dir, exist_ok=True)
        dest_path = os.path.join(backups_dir, "projects-%s.db" % date.today().strftime("%Y%m%d"))

    src = sqlite3.connect(settings.db_path)
    dst = sqlite3.connect(dest_path)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()

    # Rotación (solo en el directorio estándar de backups)
    backups_dir = os.path.dirname(dest_path)
    if os.path.basename(backups_dir) == "backups":
        existing = sorted(
            f for f in os.listdir(backups_dir)
            if f.startswith("projects-") and f.endswith(".db")
        )
        for old in existing[:-settings.backup_keep]:
            os.remove(os.path.join(backups_dir, old))
    logger.info("Backup de la BD guardado en %s", dest_path)
    return dest_path


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    # Con tz: el reloj del contenedor va en UTC y un datetime naive se interpreta
    # en la zona del scheduler (quedaría "en el pasado").
    now = datetime.now(scheduler.timezone)
    scheduler.add_job(
        sync_all_local, "interval", minutes=settings.local_sync_minutes,
        next_run_time=now + timedelta(seconds=5), id="sync_local",
    )
    scheduler.add_job(
        sync_all_remote, "interval", minutes=settings.remote_sync_minutes,
        next_run_time=now + timedelta(seconds=15), id="sync_remote",
    )
    # Avisos: tras el sync remoto para tener datos frescos; luego cada remote_sync_minutes.
    scheduler.add_job(
        run_alerts, "interval", minutes=settings.remote_sync_minutes,
        next_run_time=now + timedelta(seconds=30), id="alerts",
    )
    # Resumen diario a las 9:00 (zona horaria configurada).
    scheduler.add_job(run_daily_summary, "cron", hour=9, minute=0, id="daily_summary")
    scheduler.add_job(backup_database, "cron", hour=4, minute=45, id="daily_backup")
    scheduler.start()
    return scheduler
