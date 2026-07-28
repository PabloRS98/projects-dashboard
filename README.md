# Dashboard de Proyectos

Panel personal para vigilar todos tus proyectos en un sitio: estado de los
repositorios git locales (rama, último commit, cambios sin commitear, TODOs) y
de los remotos en GitHub / GitLab / Bitbucket (estrellas, issues, PRs abiertos,
estado de CI), con notas, checklist de tareas y avisos opcionales por Telegram.

Mono-usuario y pensado para correr en tu propia máquina o en tu red local.

## Puesta en marcha

### Con Docker (recomendado)

```bash
cp .env.example .env       # ajusta lo que necesites
mkdir -p data
LOCAL_REPOS_HOST_PATH=~/repos docker compose up -d --build
```

Disponible en <http://localhost:8000>.

`docker-compose.yml` monta dos volúmenes: `./data` para la base de datos y los
backups, y tu carpeta de repositorios en `/repos` **en solo lectura** — la app
nunca escribe en tus repos.

### En local, sin Docker

Hace falta Python 3.12+ y `git` en el PATH.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Ajusta DB_PATH y LOCAL_REPOS_BASE_PATH: los valores por defecto (/data, /repos)
# son rutas del contenedor.
uvicorn app.main:app --reload
```

## Configuración

Todo se configura por variables de entorno; `.env.example` las lista todas con
sus valores por defecto. Las que más importan:

| Variable | Por defecto | Para qué sirve |
|---|---|---|
| `LOCAL_REPOS_BASE_PATH` | `/repos` | Carpeta raíz donde se buscan repos git |
| `PROJECTS_SCAN_DEPTH` | `1` | Profundidad del escaneo automático |
| `DB_PATH` | `/data/projects.db` | Fichero SQLite |
| `ENABLE_AUTH` | `false` | Activa la autenticación HTTP Basic |
| `GITHUB_TOKEN` | vacío | Sube los límites de la API y da acceso a repos privados |
| `LOCAL_SYNC_MINUTES` | `15` | Cada cuánto se relee el estado git local |
| `REMOTE_SYNC_MINUTES` | `60` | Cada cuánto se consulta la API remota |
| `STALE_PROJECT_DAYS` | `30` | Días sin commits para marcar un proyecto como "parado" |
| `TELEGRAM_BOT_TOKEN` | vacío | Activa los avisos (junto con `TELEGRAM_CHAT_ID`) |

## Seguridad

La aplicación no tiene modelo multiusuario: quien llega a ella puede verlo y
cambiarlo todo. Antes de sacarla de `localhost`:

- **Activa `ENABLE_AUTH=true` y pon una contraseña propia.** Con la
  autenticación activada la app se niega a arrancar si `AUTH_PASSWORD` sigue
  siendo `changeme` o tiene menos de 8 caracteres.
- **Ponla detrás de TLS.** HTTP Basic manda las credenciales en claro en cada
  petición: sin HTTPS por delante (proxy inverso o VPN) viajan legibles.
- El `docker-compose.yml` publica el puerto solo en `127.0.0.1` a propósito.
  Si lo cambias a `0.0.0.0`, asegúrate de tener lo anterior resuelto.

Protecciones ya incorporadas: comprobación de origen contra CSRF en toda
petición que cambia estado, redirecciones limitadas a rutas internas, saneado
del README por lista blanca (`nh3`) y validación de esquemas en las URLs que
introduce el usuario.

Si accedes por un nombre de host distinto al que ve el proxy inverso, añádelo a
`TRUSTED_ORIGINS` (separados por comas) o la comprobación CSRF rechazará los
formularios.

## Desarrollo

```bash
pip install -r requirements-dev.txt
pytest -q          # 72 pruebas
ruff check .
```

Las pruebas del escáner crean repositorios git reales en carpetas temporales,
así que necesitas `git` con `user.name` y `user.email` configurados.

## Cómo está organizado

```
app/
  main.py          punto de entrada, middleware y arranque del scheduler
  config.py        configuración por entorno (pydantic-settings)
  database.py      motor SQLite + migración ligera de columnas
  models.py        Project y TaskItem
  security.py      CSRF, redirecciones seguras, validación de URLs
  auth.py          HTTP Basic opcional
  routers/         vistas HTTP
  services/        escaneo local, clientes de API, sync, avisos, scheduler
  templates/       Jinja2 + HTMX
```

La sincronización va en dos ciclos separados a propósito: el local (git) es
barato y corre a menudo, mientras que el remoto se espacia para no chocar con
los límites de la API de GitHub. Cada uno guarda su propio error para que un
ciclo no borre el diagnóstico del otro.

El esquema se migra con `ensure_columns` (ALTER TABLE ADD COLUMN sobre columnas
nullable o con valor por defecto), sin Alembic: suficiente para lo que hace esta
aplicación, pero conviene saberlo antes de intentar un cambio de esquema que
requiera reescribir datos.
