"""Motor y sesión de SQLAlchemy sobre SQLite, con migración ligera de columnas."""
import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

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


def ensure_columns(table: str, columns: dict[str, str]) -> list[str]:
    """Migración mínima sin Alembic: añade a `table` las columnas de `columns`
    ({nombre: DDL}) que aún no existan, con ALTER TABLE ADD COLUMN.
    Solo para columnas nullable/con default: no rompe bases de datos existentes."""
    added: list[str] = []
    with engine.begin() as conn:
        existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                added.append(name)
    return added


def init_db():
    from . import models  # noqa: F401  asegura que los modelos queden registrados

    Base.metadata.create_all(bind=engine)
    # Columnas añadidas después de la v1 (bases de datos ya desplegadas)
    ensure_columns("projects", {
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
    })
