"""Configuracion centralizada via variables de entorno (.env)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Dashboard de Proyectos"

    # Autenticacion HTTP Basic opcional (recomendado activar si se expone via VPN)
    enable_auth: bool = False
    auth_username: str = "admin"
    auth_password: str = "changeme"

    # Base de datos SQLite
    db_path: str = "/data/projects.db"

    # Nº de backups diarios de la BD que se conservan en /data/backups
    backup_keep: int = 14

    # Profundidad máxima para escaneo de repositorios locales (1 a 3 recomendado)
    projects_scan_depth: int = 1

    # Carpeta raiz (dentro del contenedor, montada desde el host) donde viven tus repos locales
    local_repos_base_path: str = "/repos"

    # Tokens opcionales para leer repos remotos (dejar vacio = solo limites publicos de la API)
    github_token: str = ""
    gitlab_token: str = ""
    bitbucket_token: str = ""  # formato: usuario:app_password

    # Frecuencia de sincronizacion automatica en segundo plano.
    # v3: se parte en dos ciclos (git local barato y frecuente, API remota espaciada
    # para no chocar con los limites de GitHub).
    sync_interval_minutes: int = 30  # legado (fallback); v3 usa los dos de abajo
    local_sync_minutes: int = 15
    remote_sync_minutes: int = 60

    # Umbrales del rediseno v3
    stale_project_days: int = 30  # sin commits => "parado"
    stale_pr_days: int = 7        # PR abierto mas tiempo => "estancado"

    # Zona horaria (para el job de resumen diario)
    timezone: str = "UTC"

    # Avisos por Telegram (opcional): crea un bot con @BotFather. Vacio = sin avisos.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


settings = Settings()
