"""ON DELETE CASCADE en task_items.project_id, y limpieza de huérfanas.

Es la migración que motivó traer Alembic: SQLite no permite añadir un ON DELETE
con ALTER TABLE, hay que recrear la tabla y copiar los datos. `ensure_columns`
solo sabía hacer ADD COLUMN, así que [PD-A4] se quedó a medias — el modelo
declaraba la cascada pero las bases ya creadas conservaban la clave foránea sin
ella.

Se recrea a mano en vez de con `batch_alter_table` y `drop_constraint`: las
claves foráneas que creó `create_all` no tienen nombre, y sin nombre no se
pueden soltar. Renombrar, crear la tabla nueva y copiar es más largo de escribir
pero no depende de una convención de nombres que esta base nunca tuvo.

Revision ID: 0002_cascade_en_tareas
Revises: 0001_esquema_inicial
Create Date: 2026-08-10
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_cascade_en_tareas"
down_revision = "0001_esquema_inicial"
branch_labels = None
depends_on = None

# `order` es palabra reservada de SQL: va entrecomillada en el INSERT. Ver [PD-B1].
COLUMNAS = '(id, project_id, text, done, "order", created_at)'


def _crear_tabla(ondelete: str | None) -> None:
    op.create_table(
        "task_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete=ondelete),
        sa.PrimaryKeyConstraint("id"),
    )


def _migrar(ondelete: str | None) -> None:
    # Las huérfanas primero: con la clave foránea nueva, copiarlas fallaría.
    op.execute("DELETE FROM task_items WHERE project_id NOT IN (SELECT id FROM projects)")
    op.rename_table("task_items", "task_items_anterior")
    _crear_tabla(ondelete)
    op.execute(
        "INSERT INTO task_items %s SELECT %s FROM task_items_anterior"
        % (COLUMNAS, COLUMNAS.strip("()"))
    )
    op.drop_table("task_items_anterior")


def upgrade() -> None:
    _migrar("CASCADE")


def downgrade() -> None:
    _migrar(None)
