"""Trabajo en segundo plano. Todo lo que el panel necesita para estar al día sin
que nadie pulse un botón:

- descubrimiento (repos nuevos en disco y en la cuenta de GitHub), cada 6 h,
- sync local (git), barato y frecuente,
- sync remoto (API), espaciado y en paralelo acotado por los límites del forge,
- avisos de Telegram, snapshot diario del histórico y backup de la base.

Cada ejecución deja rastro en `JOB_STATUS` para que el panel `/estado` pueda decir
cuándo corrió cada cosa y con qué resultado: antes, si la sincronización de fondo
fallaba, la única señal era que los datos no se movían.
"""
import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from ..config import settings
from ..database import SessionLocal
from ..models import Project, utcnow
from . import alerts, discovery, github_client, history
from .sync import sync_local, sync_remote

logger = logging.getLogger(__name__)

# Peticiones remotas simultáneas. Bajo a propósito: el cuello de botella no es la
# CPU sino la cuota del forge, y con 5 hilos un panel de 50 repos baja de varios
# minutos a unos segundos sin acercarse a los límites de abuso de GitHub.
REMOTE_WORKERS = 5

# Estado de la última ejecución de cada job, para el panel /estado.
JOB_STATUS: dict[str, dict] = {}


def _record(job_id: str, ok: bool, detail: str) -> None:
    JOB_STATUS[job_id] = {"at": utcnow(), "ok": ok, "detail": detail}


def _job(job_id: str):
    """Envuelve un job: registra resultado y no deja que una excepción mate el scheduler."""

    def decorator(func):
        def wrapper(*args, **kwargs):
            db = SessionLocal()
            try:
                detail = func(db) or ""
                _record(job_id, True, detail)
            except Exception as exc:  # noqa: BLE001  el scheduler debe sobrevivir a cualquier job
                logger.exception("Fallo en el job %s", job_id)
                _record(job_id, False, str(exc)[:200])
            finally:
                db.close()

        wrapper.__name__ = func.__name__
        return wrapper

    return decorator


@_job("discovery")
def run_discovery(db) -> str:
    result = discovery.run_discovery(db)
    if result.get("ya_en_marcha"):
        return "ya había uno en marcha, no se lanzó otro"
    parts = ["%d locales nuevos" % result["nuevos"]]
    if result["enlazados"]:
        parts.append("%d enlazados a su remoto" % result["enlazados"])
    if result["remotos_nuevos"]:
        parts.append("%d remotos nuevos" % result["remotos_nuevos"])
    if result["perdidos"]:
        parts.append("%d con ruta perdida" % result["perdidos"])
    if result["remote_error"]:
        parts.append("GitHub: %s" % result["remote_error"])
    return ", ".join(parts)


@_job("sync_local")
def sync_all_local(db) -> str:
    projects = db.query(Project).all()
    for project in projects:
        sync_local(project)
    db.commit()
    return "%d proyectos" % len(projects)


def _sync_one_remote(project_id: int) -> bool:
    """Sincroniza un proyecto remoto en su propia sesión.

    Cada hilo necesita la suya: una sesión de SQLAlchemy no es segura para usar
    desde varios hilos a la vez.
    """
    db = SessionLocal()
    try:
        project = db.get(Project, project_id)
        if project is None:
            return False
        sync_remote(project)
        db.commit()
        return not project.remote_error
    except Exception:
        logger.exception("Fallo sincronizando el proyecto %s", project_id)
        return False
    finally:
        db.close()


@_job("sync_remote")
def sync_all_remote(db) -> str:
    ids = [
        p.id
        for p in db.query(Project)
        .filter(Project.remote_provider.isnot(None), Project.remote_repo.isnot(None))
        .all()
    ]
    if not ids:
        return "sin proyectos remotos"

    with ThreadPoolExecutor(max_workers=REMOTE_WORKERS) as pool:
        results = list(pool.map(_sync_one_remote, ids))

    fallos = sum(1 for ok in results if not ok)
    detail = "%d proyectos" % len(ids)
    if fallos:
        detail += " (%d con error)" % fallos
    if github_client.rate_limit["remaining"] is not None:
        detail += " · cuota GitHub %s" % github_client.rate_limit["remaining"]
    return detail


@_job("alerts")
def run_alerts(db) -> str:
    sent = alerts.check_alerts(db)
    return "%d avisos enviados" % sent


@_job("daily_summary")
def run_daily_summary(db) -> str:
    return "enviado" if alerts.daily_summary(db) else "sin enviar (Telegram no configurado)"


@_job("snapshot")
def run_snapshot(db) -> str:
    actualizados = history.refresh_remote_activity(db)
    filas = history.take_snapshot(db)
    detail = "%d proyectos" % filas
    if actualizados:
        detail += ", %d con actividad remota refrescada" % actualizados
    return detail


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
        # existing[:-0] es existing[:0] (lista vacía), no "todos": con
        # BACKUP_KEEP=0 no se borraba ningún backup, justo lo contrario de
        # la intención. Con 0 se conserva al menos el que se acaba de crear.
        a_borrar = existing[:-settings.backup_keep] if settings.backup_keep > 0 else existing[:-1]
        for old in a_borrar:
            os.remove(os.path.join(backups_dir, old))
    logger.info("Backup de la BD guardado en %s", dest_path)
    return dest_path


def run_backup() -> None:
    try:
        path = backup_database()
        _record("backup", True, os.path.basename(path))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Fallo en el backup de la base de datos")
        _record("backup", False, str(exc)[:200])


# Etiquetas legibles para el panel de estado.
JOB_LABELS = {
    "discovery": "Descubrimiento de proyectos",
    "sync_local": "Sincronización local (git)",
    "sync_remote": "Sincronización remota (API)",
    "alerts": "Avisos de Telegram",
    "daily_summary": "Resumen diario",
    "snapshot": "Histórico diario",
    "backup": "Backup de la base de datos",
}


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    # Con tz: el reloj del contenedor va en UTC y un datetime naive se interpreta
    # en la zona del scheduler (quedaría "en el pasado").
    now = datetime.now(scheduler.timezone)

    # El descubrimiento va primero: si hay repos nuevos, los dos ciclos de sync
    # que arrancan detrás ya los encuentran dados de alta.
    scheduler.add_job(
        run_discovery, "interval", minutes=settings.discovery_minutes,
        next_run_time=now + timedelta(seconds=5), id="discovery", max_instances=1,
    )
    scheduler.add_job(
        sync_all_local, "interval", minutes=settings.local_sync_minutes,
        next_run_time=now + timedelta(seconds=20), id="sync_local", max_instances=1,
    )
    scheduler.add_job(
        sync_all_remote, "interval", minutes=settings.remote_sync_minutes,
        next_run_time=now + timedelta(seconds=35), id="sync_remote", max_instances=1,
    )
    # Avisos: tras el sync remoto para tener datos frescos; luego cada remote_sync_minutes.
    scheduler.add_job(
        run_alerts, "interval", minutes=settings.remote_sync_minutes,
        next_run_time=now + timedelta(seconds=60), id="alerts", max_instances=1,
    )
    # Resumen diario a las 9:00 (zona horaria configurada).
    scheduler.add_job(run_daily_summary, "cron", hour=9, minute=0, id="daily_summary")
    # Histórico antes del backup, para que la copia del día ya lo incluya.
    scheduler.add_job(run_snapshot, "cron", hour=4, minute=30, id="snapshot")
    scheduler.add_job(run_backup, "cron", hour=4, minute=45, id="backup")
    scheduler.start()
    return scheduler
