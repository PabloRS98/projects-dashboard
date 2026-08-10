"""Esquema inicial: el estado al que llegó la app con create_all + ensure_columns.

Esta revisión describe la base tal y como quedaba antes de Alembic. Una base ya
desplegada NO la ejecuta: `init_db` detecta que las tablas existen y la marca
como aplicada (`stamp`) tras completarle las columnas que le falten. Las bases
nuevas sí la ejecutan entera.

Revision ID: 0001_esquema_inicial
Revises:
Create Date: 2026-08-10
"""
import sqlalchemy as sa
from alembic import op

revision = "0001_esquema_inicial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("local_path", sa.String(length=500), nullable=True),
        sa.Column("remote_provider", sa.String(length=20), nullable=True),
        sa.Column("remote_repo", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("branch", sa.String(length=100), nullable=True),
        sa.Column("last_commit_sha", sa.String(length=64), nullable=True),
        sa.Column("last_commit_message", sa.String(length=500), nullable=True),
        sa.Column("last_commit_date", sa.DateTime(), nullable=True),
        sa.Column("has_uncommitted_changes", sa.Boolean(), nullable=False),
        sa.Column("open_issues", sa.Integer(), nullable=True),
        sa.Column("open_prs", sa.Integer(), nullable=True),
        sa.Column("stars", sa.Integer(), nullable=True),
        sa.Column("ci_status", sa.String(length=20), nullable=True),
        sa.Column("todo_count", sa.Integer(), nullable=True),
        sa.Column("todo_scanned_sha", sa.String(length=64), nullable=True),
        sa.Column("local_path_missing", sa.Boolean(), nullable=False),
        sa.Column("is_favorite", sa.Boolean(), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False),
        sa.Column("tags", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("homepage_url", sa.String(length=500), nullable=True),
        sa.Column("oldest_open_pr_days", sa.Integer(), nullable=True),
        sa.Column("ci_notified", sa.Boolean(), nullable=False),
        sa.Column("stale_notified", sa.Boolean(), nullable=False),
        sa.Column("pr_stale_notified", sa.Boolean(), nullable=False),
        sa.Column("commit_weeks", sa.String(length=255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("local_error", sa.String(length=500), nullable=True),
        sa.Column("remote_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "task_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        # Sin ON DELETE todavía: así era el esquema anterior. Lo añade la 0002,
        # que es justo lo que `ensure_columns` no sabía hacer.
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "project_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("commits_7d", sa.Integer(), nullable=True),
        sa.Column("open_prs", sa.Integer(), nullable=True),
        sa.Column("open_issues", sa.Integer(), nullable=True),
        sa.Column("stars", sa.Integer(), nullable=True),
        sa.Column("todo_count", sa.Integer(), nullable=True),
        sa.Column("ci_status", sa.String(length=20), nullable=True),
        sa.Column("days_since_commit", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "day", name="uq_snapshot_project_day"),
    )
    op.create_index(
        op.f("ix_project_snapshots_project_id"), "project_snapshots", ["project_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_project_snapshots_project_id"), table_name="project_snapshots")
    op.drop_table("project_snapshots")
    op.drop_table("task_items")
    op.drop_table("projects")
