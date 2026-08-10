# Dashboard de Proyectos

Panel personal para vigilar todos tus proyectos en un sitio. Se rellena solo: busca
los repositorios git de una carpeta, deduce a qué repo remoto pertenece cada uno
leyendo su `origin`, y a partir de ahí trae estado local (rama, último commit,
cambios sin commitear, TODOs) y remoto (estrellas, issues, PRs, estado de CI,
descripción y web). Encima puedes poner notas, tareas y avisos por Telegram.

Mono-usuario y pensado para correr en tu propia máquina o en tu red local.

## Puesta en marcha

### Con Docker (recomendado)

```bash
cp .env.example .env       # ajusta lo que necesites
mkdir -p data
LOCAL_REPOS_HOST_PATH=~/repos docker compose up -d --build
```

Disponible en <http://localhost:8003> (cambia `HOST_PORT` en el `.env` para otro puerto).

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

## Cómo se rellena solo

No hace falta dar de alta nada a mano. Al arrancar, y luego cada
`DISCOVERY_MINUTES`, la aplicación:

1. **Busca repos git** bajo `LOCAL_REPOS_BASE_PATH`, hasta `PROJECTS_SCAN_DEPTH`
   niveles de profundidad.
2. **Deduce el remoto** de cada uno leyendo `git remote get-url origin`, y de ahí
   saca proveedor y `owner/repo`. Es lo que hace que un repo recién descubierto
   traiga ya sus estrellas, PRs y estado de CI.
3. **Da de alta los repos de tu cuenta de GitHub** que no estén clonados aquí
   (necesita `GITHUB_TOKEN`; se desactiva con `AUTO_IMPORT_GITHUB=false`).
4. **Marca los que han perdido su ruta local** en vez de dejar datos viejos.

Lo que escribes tú siempre manda: si corriges el `owner/repo`, la descripción o
la web de un proyecto, el descubrimiento no los pisa.

> Si tus repos están agrupados en subcarpetas (`Aplicaciones Servidor/mi-app`),
> `PROJECTS_SCAN_DEPTH=1` **no los encuentra**. Sube el valor a 2 o 3.

## Configuración

Todo se configura por variables de entorno; `.env.example` las lista todas con
sus valores por defecto. Las que más importan:

| Variable | Por defecto | Para qué sirve |
|---|---|---|
| `LOCAL_REPOS_BASE_PATH` | `/repos` | Carpeta raíz donde se buscan repos git |
| `PROJECTS_SCAN_DEPTH` | `3` | Niveles de profundidad del escaneo automático |
| `DB_PATH` | `/data/projects.db` | Fichero SQLite |
| `HOST_PORT` | `8003` | Puerto publicado por docker-compose |
| `ENABLE_AUTH` | `false` | Activa la autenticación HTTP Basic |
| `GITHUB_TOKEN` | vacío | Sube los límites de la API, da acceso a repos privados y habilita el alta automática |
| `AUTO_IMPORT_GITHUB` | `true` | Da de alta solos los repos de tu cuenta |
| `DISCOVERY_MINUTES` | `360` | Cada cuánto se buscan proyectos nuevos |
| `LOCAL_SYNC_MINUTES` | `15` | Cada cuánto se relee el estado git local |
| `REMOTE_SYNC_MINUTES` | `60` | Cada cuánto se consulta la API remota |
| `STALE_PROJECT_DAYS` | `30` | Días sin commits para marcar un proyecto como "parado" |
| `STALE_PR_DAYS` | `7` | Días con un PR abierto para considerarlo estancado |
| `TELEGRAM_BOT_TOKEN` | vacío | Activa los avisos (junto con `TELEGRAM_CHAT_ID`) |

## Las vistas

- **`/`** — el panel. Buscador incremental, orden configurable, filtros que se
  combinan y dos presentaciones (tarjetas o tabla densa). Los KPI de arriba son
  enlaces a la vista ya filtrada.
- **`/proyecto/{id}`** — ficha: README (plegado), últimos commits, TODOs con
  `fichero:línea`, actividad por semanas, notas y tareas.
- **`/tv`** — modo pantalla: solo el resumen y lo que necesita atención, sin
  acciones y recargándose sola cada minuto. Para dejarlo en un monitor.
- **`/estado`** — salud del propio panel: cuándo corrió cada job y con qué
  resultado, cuota restante de la API de GitHub, si Telegram está configurado y
  la configuración activa. Es el primer sitio donde mirar si los datos no se
  mueven.

## Diagnóstico de errores

Los fallos contra la API se traducen a la causa concreta, porque el arreglo es
distinto en cada caso: token caducado (401), sin permiso sobre el repo (403),
cuota agotada (con la hora a la que se repone), repo inexistente o privado (404),
forge caído (5xx), timeout y fallo de red.

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
petición que cambia estado, cabeceras de seguridad HTTP (CSP, `nosniff`,
`Referrer-Policy`), redirecciones limitadas a rutas internas, saneado del README
por lista blanca (`nh3`) y validación de esquemas en las URLs que introduce el
usuario.

### Los secretos del `.env`

El `.env` está en `.gitignore`, así que no llega al repositorio. Eso evita
publicarlo, pero no lo respalda: si se pierde este disco, se pierde la
configuración y hay que regenerar el token de GitHub y el del bot de Telegram.

Las tres aplicaciones de esta carpeta comparten un `../.sops.yaml` con una clave
`age` para versionar el `.env` cifrado:

```bash
sops --encrypt .env > .env.enc     # se versiona
sops --decrypt .env.enc > .env     # al reconstruir el despliegue
```

**Lo importante no es el cifrado, es la clave.** Un `.env.enc` sin la clave
privada para descifrarlo no sirve de nada, y la clave no está en el repositorio
por definición: va en `%APPDATA%\sops\age\keys.txt` (o donde apunte
`SOPS_AGE_KEY_FILE`) y hay que respaldarla aparte — gestor de contraseñas o
copia fuera de esta máquina.

> **Estado actual:** este proyecto todavía no tiene `.env.enc`. En la máquina de
> desarrollo no están instalados `sops` ni `age`, y la clave privada de
> `../.sops.yaml` no aparece en ninguna de las rutas estándar. Hasta que la clave
> esté localizada y respaldada, cifrar aquí no aportaría nada. Ver [PD-A9].

Si accedes por un nombre de host distinto al que ve el proxy inverso, añádelo a
`TRUSTED_ORIGINS` (separados por comas) o la comprobación CSRF rechazará los
formularios.

## Desarrollo

```bash
pip install -r requirements-dev.txt
pytest -q          # 346 pruebas
ruff check .
```

Las pruebas del escáner y del descubrimiento crean repositorios git reales en
carpetas temporales, así que necesitas `git` con `user.name` y `user.email`
configurados.

## Cómo está organizado

```
app/
  main.py          punto de entrada, middleware y arranque del scheduler
  config.py        configuración por entorno (pydantic-settings)
  database.py      motor SQLite + migración ligera de columnas
  models.py        Project, TaskItem y ProjectSnapshot (histórico)
  security.py      CSRF, redirecciones seguras, validación de URLs
  auth.py          HTTP Basic opcional
  routers/         vistas HTTP
  services/
    local_scanner.py  git local: estado, commits, TODOs, remoto, actividad
    discovery.py      alta automática (disco + cuenta de GitHub)
    sync.py           sincronización local y remota
    forge_errors.py   traducción de fallos HTTP a un diagnóstico accionable
    history.py        snapshots diarios y series para las gráficas
    scheduler.py      jobs de fondo y registro de su último resultado
    alerts.py         avisos de Telegram con deduplicación
  templates/       Jinja2 + HTMX
```

La sincronización va en ciclos separados a propósito: el descubrimiento recorre
el disco y va muy espaciado, el local (git) es barato y corre a menudo, y el
remoto se espacia para no chocar con los límites de la API. El remoto además va
en paralelo acotado (5 hilos) y se corta solo si la cuota se agota, para que un
panel con muchos repos no tarde minutos. Cada ciclo guarda su propio error para
que uno no borre el diagnóstico del otro.

Las mutaciones que tocan la red (alta, edición, "sincronizar todo") responden
inmediatamente y hacen el trabajo en segundo plano: antes el navegador se
quedaba colgado hasta 40 segundos por proyecto.

El esquema se migra con `ensure_columns` (ALTER TABLE ADD COLUMN sobre columnas
nullable o con valor por defecto), sin Alembic: suficiente para lo que hace esta
aplicación, pero conviene saberlo antes de intentar un cambio de esquema que
requiera reescribir datos.
