"""Modelos de datos: proyectos (locales y/o remotos) y checklist de tareas propias."""
from datetime import datetime, timezone

from sqlalchemy import String, Integer, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    """UTC naive (compatible con las filas ya guardadas); evita datetime.utcnow(), deprecado en 3.12."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))

    local_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    remote_provider: Mapped[str | None] = mapped_column(String(20), nullable=True)  # github|gitlab|bitbucket
    remote_repo: Mapped[str | None] = mapped_column(String(255), nullable=True)  # "owner/repo"

    notes: Mapped[str] = mapped_column(Text, default="")

    # Estado cacheado, actualizado por sincronización manual o periódica
    branch: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_commit_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_commit_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    has_uncommitted_changes: Mapped[bool] = mapped_column(Boolean, default=False)

    open_issues: Mapped[int | None] = mapped_column(Integer, nullable=True)
    open_prs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ci_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    todo_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Añadido en v2 (migrado con ensure_columns): la ruta local configurada ya no existe
    local_path_missing: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- v3 (rediseño a medida): organización + escaparate + dedup de avisos ---
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)   # ★ fijado arriba
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)   # oculto del grupo activo
    tags: Mapped[str] = mapped_column(String(255), default="")          # categorías propias, separadas por coma
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)   # autofill GitHub, editable
    homepage_url: Mapped[str | None] = mapped_column(String(500), nullable=True)  # web/deploy, autofill GitHub, editable
    # Edad (días) del PR abierto más viejo, para detectar PR estancado
    oldest_open_pr_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Flags para no repetir avisos de Telegram (se resetean cuando la condición desaparece)
    ci_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    stale_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    pr_stale_notified: Mapped[bool] = mapped_column(Boolean, default=False)

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Errores separados por origen: los dos ciclos (local ~15min / remoto ~60min) corren
    # por separado y cada uno gestiona el suyo sin pisar el del otro.
    local_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    remote_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    tasks: Mapped[list["TaskItem"]] = relationship(
        order_by="TaskItem.order", cascade="all, delete-orphan"
    )

    def days_since_commit(self) -> int | None:
        """Días desde el último commit conocido (local o remoto). None si no hay fecha."""
        if not self.last_commit_date:
            return None
        return (utcnow() - self.last_commit_date).days

    def tag_list(self) -> list[str]:
        return [t.strip() for t in (self.tags or "").split(",") if t.strip()]

    @property
    def sync_error(self) -> str | None:
        """Error combinado (local + remoto) para mostrar/filtrar."""
        parts = [e for e in (self.local_error, self.remote_error) if e]
        return " | ".join(parts) if parts else None


class TaskItem(Base):
    __tablename__ = "task_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    text: Mapped[str] = mapped_column(String(500))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
