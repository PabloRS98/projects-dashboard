"""Entorno de Alembic.

La conexión llega siempre desde la app (`config.attributes["connection"]`), que
la construye a partir de `DB_PATH`. Nunca se lee una URL del `alembic.ini`: así
las migraciones no pueden apuntar a una base distinta de la que usa la
aplicación, que es el fallo clásico de tener la URL en dos sitios.

`render_as_batch=True` es obligatorio en SQLite: no soporta la mayoría de
`ALTER TABLE`, así que Alembic recrea la tabla, copia los datos y la renombra.
Es justo lo que hacía falta para añadir el `ON DELETE CASCADE` de [PD-A4], que
`ensure_columns` no podía hacer.
"""
from alembic import context

from app.database import Base
from app.models import Project, ProjectSnapshot, TaskItem  # noqa: F401  registra las tablas

target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = context.config.attributes.get("connection")
    if connectable is None:
        raise RuntimeError(
            "Falta la conexión: estas migraciones se ejecutan desde app.database.init_db(), "
            "no con el comando `alembic` a secas."
        )
    context.configure(
        connection=connectable,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
