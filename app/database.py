"""Motor y sesión de SQLAlchemy sobre SQLite, con migración ligera de columnas."""
import logging
import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

logger = logging.getLogger(__name__)

os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_columns(table: str, columns: dict[str, str], conn=None) -> list[str]:
    """Añade a `table` las columnas de `columns` ({nombre: DDL}) que falten.

    Ya no es el mecanismo de migración —de eso se encarga Alembic desde
    [PD-M15]—; queda solo para reconciliar una base anterior a Alembic antes de
    marcarla. Solo sirve para columnas nullable o con default: es todo lo que
    `ALTER TABLE ADD COLUMN` permite en SQLite, y por eso hizo falta Alembic.
    """
    def _aplicar(c) -> list[str]:
        added: list[str] = []
        existing = {row[1] for row in c.exec_driver_sql(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                c.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                added.append(name)
        return added

    if conn is not None:
        return _aplicar(conn)
    with engine.begin() as nueva:
        return _aplicar(nueva)


def limpiar_tareas_huerfanas() -> int:
    """Borra las tareas cuyo proyecto ya no existe y devuelve cuántas eran.

    Limpieza de lo que dejó el fallo de [PD-A4]: `add_task` creaba y commiteaba
    la tarea antes de que nadie comprobara que el proyecto existía, así que
    bastaban dos pestañas abiertas (borrar el proyecto en una, añadir una tarea
    en la otra) para dejar filas colgando de un `project_id` inexistente.

    No se limpian solas: la clave foránea de `task_items` no lleva ON DELETE, y
    SQLite tampoco aplicaría las claves foráneas sin activar el PRAGMA. Y como
    SQLite reasigna los ids sin AUTOINCREMENT, un proyecto nuevo puede recibir el
    id de uno borrado y aparecer con las tareas del anterior: por eso se barren
    en el arranque y no solo se evita el caso nuevo.
    """
    with engine.begin() as conn:
        resultado = conn.exec_driver_sql(
            "DELETE FROM task_items WHERE project_id NOT IN (SELECT id FROM projects)"
        )
        return resultado.rowcount


# Revisión que describe el esquema tal y como quedaba ANTES de Alembic. Una base
# ya desplegada se marca con esta y sigue desde ahí, en vez de intentar crear
# tablas que ya existen.
REVISION_INICIAL = "0001_esquema_inicial"

# Columnas que se fueron añadiendo al modelo antes de que existiera Alembic.
#
# Solo se usan para reconciliar una base anterior: esas bases se crearon con
# `create_all()`, que no altera tablas ya existentes, así que a cada una le falta
# todo lo que se añadiera después de su creación. Hay que completarlas ANTES de
# marcarlas en la revisión inicial, o la marca mentiría.
COLUMNAS_PRE_ALEMBIC = {
    "local_path_missing": "BOOLEAN NOT NULL DEFAULT 0",
    # v3 (rediseño a medida)
    "is_favorite": "BOOLEAN NOT NULL DEFAULT 0",
    "is_archived": "BOOLEAN NOT NULL DEFAULT 0",
    "tags": "VARCHAR(255) NOT NULL DEFAULT ''",
    "description": "VARCHAR(500)",
    "homepage_url": "VARCHAR(500)",
    "oldest_open_pr_days": "INTEGER",
    "ci_notified": "BOOLEAN NOT NULL DEFAULT 0",
    "stale_notified": "BOOLEAN NOT NULL DEFAULT 0",
    "pr_stale_notified": "BOOLEAN NOT NULL DEFAULT 0",
    "local_error": "VARCHAR(500)",
    "remote_error": "VARCHAR(500)",
    # v4: cachea el SHA del último conteo de TODOs
    "todo_scanned_sha": "VARCHAR(64)",
    # v4: commits por semana (JSON) para el sparkline de actividad
    "commit_weeks": "VARCHAR(255)",
}


def _config_alembic(bind=None):
    """Configuración de Alembic con la conexión de la app ya puesta.

    La URL no se lee del `alembic.ini` a propósito: la app la construye desde
    `DB_PATH`, y tenerla en dos sitios es la forma clásica de acabar migrando una
    base distinta de la que se usa.
    """
    from alembic.config import Config

    ruta_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    config = Config(str(ruta_ini))
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parent.parent / "migraciones"),
    )
    config.attributes["connection"] = bind
    return config


def revision_pendiente() -> tuple[str | None, str | None]:
    """(revisión de la base, revisión objetivo). Iguales = esquema al día.

    Solo lee `alembic_version`: no abre transacción de escritura ni toca el DDL,
    así que es seguro llamarlo desde una vista.
    """
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    with engine.connect() as conn:
        actual = MigrationContext.configure(conn).get_current_revision()
        head = ScriptDirectory.from_config(_config_alembic(conn)).get_current_head()
    return actual, head


def init_db():
    """Deja el esquema al día aplicando las migraciones pendientes.

    Alembic sustituye a `ensure_columns` como mecanismo general: aquel solo sabía
    hacer ADD COLUMN, no podía crear índices, cambiar tipos, añadir un ON DELETE
    ni llevar registro de versión.

    Una base anterior a Alembic no tiene tabla `alembic_version`, así que
    `upgrade` la trataría como vacía e intentaría crear tablas que ya existen. Se
    detecta y se marca en la revisión inicial, completándole antes las columnas
    que le falten para que la marca no mienta. Automático a propósito: pedir un
    `alembic stamp` a mano dejaría la app rota hasta que alguien lo recordara.
    """
    from alembic import command
    from sqlalchemy import inspect

    from . import models  # noqa: F401  asegura que los modelos queden registrados

    with engine.begin() as conn:
        tablas = set(inspect(conn).get_table_names())
        if "projects" in tablas and "alembic_version" not in tablas:
            logger.info("Base anterior a Alembic: se reconcilia y se marca en %s", REVISION_INICIAL)
            ensure_columns("projects", COLUMNAS_PRE_ALEMBIC, conn)
            command.stamp(_config_alembic(conn), REVISION_INICIAL)
        command.upgrade(_config_alembic(conn), "head")

    huerfanas = limpiar_tareas_huerfanas()
    if huerfanas:
        logger.warning("Borradas %d tareas huérfanas (proyecto inexistente)", huerfanas)


