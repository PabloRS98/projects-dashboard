"""Modelos de datos: proyectos (locales y/o remotos), checklist de tareas propias
e histórico diario para las gráficas de tendencia."""
import json
from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

REPO_BASE_URLS = {
    "github": "https://github.com/",
    "gitlab": "https://gitlab.com/",
    "bitbucket": "https://bitbucket.org/",
}

# Vocabulario de CI, en un solo sitio. Estaba duplicado en el router y en
# alerts.py con el mismo contenido, y una tercera versión escrita a mano en
# detail.html a la que le faltaban `cancelled` y `timed_out`: el mismo pipeline
# cancelado salía gris en la ficha y rojo en la tarjeta.
CI_BAD = {"failure", "failed", "error", "cancelled", "timed_out"}
CI_GOOD = {"success", "passed", "completed"}


def utcnow() -> datetime:
    """UTC naive (compatible con las filas ya guardadas); evita datetime.utcnow(), deprecado en 3.12."""
    return datetime.now(UTC).replace(tzinfo=None)


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
    # SHA del commit en el que se contaron los TODOs por última vez. Permite
    # saltarse el recorrido del árbol cuando el repo no ha cambiado.
    todo_scanned_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Añadido en v2 (migrado con ensure_columns): la ruta local configurada ya no existe
    local_path_missing: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- v3 (rediseño a medida): organización + escaparate + dedup de avisos ---
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)   # ★ fijado arriba
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)   # oculto del grupo activo
    # Categorías propias del usuario, separadas por coma
    tags: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)   # autofill GitHub, editable
    # Web/deploy: autorrellenado desde GitHub, editable por el usuario
    homepage_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Edad (días) del PR abierto más viejo, para detectar PR estancado
    oldest_open_pr_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Flags para no repetir avisos de Telegram (se resetean cuando la condición desaparece)
    ci_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    stale_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    pr_stale_notified: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- v4: ingesta automática e histórico ---
    # Commits por semana (JSON, lista de enteros, de la más antigua a la actual).
    # Se guarda serializado en lugar de en una tabla aparte porque es un dato
    # derivado y desechable: se recalcula entero en cada sync.
    commit_weeks: Mapped[str | None] = mapped_column(String(255), nullable=True)

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Errores separados por origen: los dos ciclos (local ~15min / remoto ~60min) corren
    # por separado y cada uno gestiona el suyo sin pisar el del otro.
    local_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    remote_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    tasks: Mapped[list["TaskItem"]] = relationship(
        order_by="TaskItem.order", cascade="all, delete-orphan"
    )
    # Con cascade en la relación (y no solo el ON DELETE del esquema): SQLite no
    # aplica claves foráneas salvo que se active el PRAGMA, así que borrar un
    # proyecto dejaría su histórico huérfano en la tabla.
    snapshots: Mapped[list["ProjectSnapshot"]] = relationship(
        order_by="ProjectSnapshot.day", cascade="all, delete-orphan"
    )

    def days_since_commit(self) -> int | None:
        """Días desde el último commit conocido (local o remoto). None si no hay fecha."""
        if not self.last_commit_date:
            return None
        return (utcnow() - self.last_commit_date).days

    def tag_list(self) -> list[str]:
        return [t.strip() for t in (self.tags or "").split(",") if t.strip()]

    def week_counts(self) -> list[int]:
        """Commits por semana ya deserializados. [] si no hay dato o está corrupto."""
        if not self.commit_weeks:
            return []
        try:
            data = json.loads(self.commit_weeks)
        except (ValueError, TypeError):
            return []
        return [int(n) for n in data] if isinstance(data, list) else []

    @property
    def repo_url(self) -> str | None:
        """URL pública del repo remoto, o None si no hay remoto configurado.

        Vive en el modelo (y no en el router) porque las tarjetas del dashboard
        la necesitan una por proyecto dentro del bucle: como variable de contexto
        solo existía en la ficha de detalle y en el dashboard quedaba indefinida,
        de modo que el enlace no llegaba a pintarse nunca.
        """
        base = REPO_BASE_URLS.get(self.remote_provider or "")
        return base + self.remote_repo if base and self.remote_repo else None

    @property
    def sync_error(self) -> str | None:
        """Error combinado (local + remoto) para mostrar/filtrar."""
        parts = [e for e in (self.local_error, self.remote_error) if e]
        return " | ".join(parts) if parts else None


class ProjectSnapshot(Base):
    """Foto diaria de las métricas de un proyecto.

    El resto del modelo solo guarda el estado actual, así que no había forma de
    responder "¿esto va a más o a menos?". Una fila por proyecto y día basta para
    las tendencias y ocupa nada: 5 proyectos × 365 días son 1.825 filas al año.

    La restricción única (proyecto, día) hace que reejecutar el job del día no
    duplique: se actualiza la fila existente.
    """

    __tablename__ = "project_snapshots"
    __table_args__ = (UniqueConstraint("project_id", "day", name="uq_snapshot_project_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)

    commits_7d: Mapped[int | None] = mapped_column(Integer, nullable=True)
    open_prs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    open_issues: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    todo_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ci_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    days_since_commit: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TaskItem(Base):
    __tablename__ = "task_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ondelete=CASCADE igual que en ProjectSnapshot: hoy quien borra de verdad es
    # el `cascade` de la relación (SQLite no aplica las claves foráneas sin el
    # PRAGMA), pero deja el esquema correcto para cuando se activen o se migre a
    # otro motor. Las bases ya creadas conservan la FK sin ON DELETE hasta que
    # haya migraciones que sepan recrear la tabla ([PD-M15]); las huérfanas que
    # dejó [PD-A4] las barre `limpiar_tareas_huerfanas` en cada arranque.
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(String(500))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    # `order` es palabra reservada de SQL. SQLAlchemy la entrecomilla siempre, así
    # que la app funciona; lo que falla es cualquier consulta escrita a mano
    # (`SELECT ... ORDER BY order`) o un dump revisado con sqlite3. Renombrarla
    # exige recrear la tabla, así que se queda hasta que haya migraciones que
    # sepan hacerlo ([PD-M15]). Mientras tanto, entrecomíllala tú también.
    order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
