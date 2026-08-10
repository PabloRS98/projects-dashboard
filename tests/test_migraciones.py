"""Migraciones de esquema con Alembic. Ver [PD-M15].

El caso difícil no es una base vacía: es una base que ya existe, creada con
`create_all()` y parcheada con `ensure_columns`, y que por tanto **no tiene tabla
`alembic_version`**. `upgrade` la trataría como vacía e intentaría crear tablas
que ya están, así que se detecta, se le completan las columnas que le falten y se
marca en la revisión inicial.
"""
import sqlite3

import pytest
from sqlalchemy import create_engine, inspect

from app import database


@pytest.fixture
def base_temporal(tmp_path, monkeypatch):
    """Apunta el motor a una base nueva y devuelve su ruta."""
    ruta = tmp_path / "migraciones.db"
    motor = create_engine("sqlite:///%s" % ruta, connect_args={"check_same_thread": False})
    monkeypatch.setattr(database, "engine", motor)
    yield ruta
    motor.dispose()


def _tablas(ruta) -> set[str]:
    con = sqlite3.connect(ruta)
    try:
        return {f[0] for f in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def _revision(ruta) -> str | None:
    con = sqlite3.connect(ruta)
    try:
        return con.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    except sqlite3.OperationalError:
        return None
    finally:
        con.close()


# --------------------------------------------------------------------------
# Base nueva
# --------------------------------------------------------------------------

def test_una_base_vacia_se_crea_entera(base_temporal):
    database.init_db()

    tablas = _tablas(base_temporal)
    assert {"projects", "task_items", "project_snapshots", "alembic_version"} <= tablas


def test_una_base_nueva_queda_en_la_ultima_revision(base_temporal):
    database.init_db()
    actual, head = database.revision_pendiente()
    assert actual == head
    assert actual is not None


def test_init_db_es_idempotente(base_temporal):
    database.init_db()
    database.init_db()
    assert _revision(base_temporal) is not None


# --------------------------------------------------------------------------
# Base anterior a Alembic
# --------------------------------------------------------------------------

def _base_pre_alembic(ruta, con_columnas_v4: bool = False) -> None:
    """Imita una base creada con `create_all()` antes de que existiera Alembic:
    sin `alembic_version` y sin las columnas añadidas después."""
    con = sqlite3.connect(ruta)
    try:
        con.execute("""
            CREATE TABLE projects (
                id INTEGER NOT NULL PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                local_path VARCHAR(500),
                remote_provider VARCHAR(20),
                remote_repo VARCHAR(255),
                notes TEXT NOT NULL DEFAULT '',
                branch VARCHAR(100),
                last_commit_sha VARCHAR(64),
                last_commit_message VARCHAR(500),
                last_commit_date DATETIME,
                has_uncommitted_changes BOOLEAN NOT NULL DEFAULT 0,
                open_issues INTEGER, open_prs INTEGER, stars INTEGER,
                ci_status VARCHAR(20), todo_count INTEGER,
                last_synced_at DATETIME,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("""
            CREATE TABLE task_items (
                id INTEGER NOT NULL PRIMARY KEY,
                project_id INTEGER NOT NULL,
                text VARCHAR(500) NOT NULL,
                done BOOLEAN NOT NULL DEFAULT 0,
                "order" INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(project_id) REFERENCES projects (id)
            )
        """)
        con.execute("INSERT INTO projects (id, name) VALUES (1, 'de antes')")
        con.execute('INSERT INTO task_items (project_id, text, "order") VALUES (1, "pendiente", 0)')
        con.commit()
    finally:
        con.close()


def test_una_base_pre_alembic_se_migra_sola(base_temporal):
    """El caso que rompió `finance-tracker` de verdad: la app estuvo semanas
    devolviendo 500 por una columna que faltaba."""
    _base_pre_alembic(base_temporal)

    database.init_db()

    assert _revision(base_temporal) is not None
    columnas = {c["name"] for c in inspect(database.engine).get_columns("projects")}
    assert "commit_weeks" in columnas          # añadida en v4
    assert "is_favorite" in columnas           # añadida en v3


def test_los_datos_de_una_base_pre_alembic_sobreviven(base_temporal):
    _base_pre_alembic(base_temporal)

    database.init_db()

    con = sqlite3.connect(base_temporal)
    try:
        assert con.execute("SELECT name FROM projects").fetchone()[0] == "de antes"
        assert con.execute("SELECT text FROM task_items").fetchone()[0] == "pendiente"
    finally:
        con.close()


def test_la_migracion_anade_el_cascade_a_las_tareas(base_temporal):
    """Lo que `ensure_columns` no sabía hacer: SQLite exige recrear la tabla."""
    _base_pre_alembic(base_temporal)

    database.init_db()

    claves = inspect(database.engine).get_foreign_keys("task_items")
    assert claves
    assert claves[0]["options"].get("ondelete") == "CASCADE"


def test_la_migracion_barre_las_tareas_huerfanas(base_temporal):
    """Con la clave foránea nueva, copiarlas habría fallado."""
    _base_pre_alembic(base_temporal)
    con = sqlite3.connect(base_temporal)
    con.execute('INSERT INTO task_items (project_id, text, "order") VALUES (999, "huérfana", 1)')
    con.commit()
    con.close()

    database.init_db()

    con = sqlite3.connect(base_temporal)
    try:
        textos = [f[0] for f in con.execute("SELECT text FROM task_items")]
    finally:
        con.close()
    assert textos == ["pendiente"]
