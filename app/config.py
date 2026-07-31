"""Configuración centralizada vía variables de entorno (.env)."""
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_PASSWORD = "changeme"
MIN_PASSWORD_LENGTH = 8

# Absoluta, por el mismo motivo que las plantillas y los estáticos: con una ruta
# relativa el .env solo se lee si el proceso arranca desde la raíz del repo, y si
# no, la app se levanta en silencio con toda la configuración por defecto.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    app_name: str = "Dashboard de Proyectos"

    # Autenticación HTTP Basic opcional (recomendado activar si se expone vía VPN)
    enable_auth: bool = False
    auth_username: str = "admin"
    auth_password: str = DEFAULT_PASSWORD

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

    # Frecuencia de sincronización automática en segundo plano: dos ciclos
    # separados (git local barato y frecuente, API remota espaciada para no
    # chocar con los límites de GitHub).
    local_sync_minutes: int = 15
    remote_sync_minutes: int = 60
    # Cada cuánto se buscan proyectos nuevos (en disco y en la cuenta de GitHub).
    # Espaciado: los repos no aparecen cada minuto y el escaneo recorre el disco.
    discovery_minutes: int = 360

    # Alta automática de los repos de la cuenta de GitHub que no estén clonados
    # aquí. Requiere GITHUB_TOKEN. Desactívalo si solo quieres ver lo que tienes
    # en local.
    auto_import_github: bool = True

    # Umbrales del rediseno v3
    stale_project_days: int = 30  # sin commits => "parado"
    stale_pr_days: int = 7        # PR abierto mas tiempo => "estancado"

    # Zona horaria (para el job de resumen diario)
    timezone: str = "UTC"

    # Avisos por Telegram (opcional): crea un bot con @BotFather. Vacio = sin avisos.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Hosts extra admitidos como origen de las peticiones (comprobación CSRF).
    # Solo hace falta si accedes por un nombre distinto al del proxy inverso.
    trusted_origins: str = ""

    @model_validator(mode="after")
    def _reject_insecure_password(self) -> "Settings":
        """Con la autenticación activada, no arrancar con la contraseña de fábrica.

        Un fallo al arrancar es ruidoso y se corrige en un minuto; una app
        expuesta con admin/changeme puede pasar meses sin que nadie lo note.
        """
        if not self.enable_auth:
            return self
        if self.auth_password == DEFAULT_PASSWORD:
            raise ValueError(
                "ENABLE_AUTH está activado pero AUTH_PASSWORD sigue siendo el valor "
                "de fábrica. Cámbialo en el .env antes de exponer la aplicación."
            )
        if len(self.auth_password) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                "AUTH_PASSWORD debe tener al menos %d caracteres." % MIN_PASSWORD_LENGTH
            )
        return self

    def trusted_origin_hosts(self) -> set[str]:
        return {h.strip() for h in self.trusted_origins.split(",") if h.strip()}


settings = Settings()
