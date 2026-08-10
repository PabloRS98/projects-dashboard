"""Fixtures comunes: base de datos temporal y cliente de pruebas.

DB_PATH se fija ANTES de importar la app: `app.database` crea el engine al
importarse, así que en ese momento la configuración ya tiene que apuntar al
fichero temporal y no a /data/projects.db.
"""
import os
import tempfile

import pytest

_TMP_DIR = tempfile.mkdtemp(prefix="projects-dashboard-tests-")
os.environ["DB_PATH"] = os.path.join(_TMP_DIR, "test.db")
os.environ["ENABLE_AUTH"] = "false"

from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Project  # noqa: E402

# Origen que el middleware CSRF acepta: los navegadores mandan Origin en cada
# POST, así que las pruebas hacen lo mismo.
SAME_ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    # `alembic_version` no está en el metadata, así que `drop_all` la deja: sin
    # borrarla, Alembic creería que el esquema está al día con las tablas ya
    # borradas y no volvería a crearlas.
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    init_db()
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client():
    # Sin follow_redirects para poder inspeccionar la cabecera Location.
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture
def project(db):
    p = Project(name="demo")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p
