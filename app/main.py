"""Punto de entrada: Dashboard de Proyectos Personales."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import SessionLocal, init_db
from .routers import projects
from .security import CSRFMiddleware, avisar_rutas_fuera_de_la_base
from .services import github_client
from .services.scheduler import start_scheduler

logging.basicConfig(level=logging.INFO)

# Absoluta por el mismo motivo que las plantillas: no depender del cwd.
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Avisa (no borra) de las rutas locales guardadas que quedarían fuera de la
    # base tras [PD-M10]. Vaciarlas en silencio sería peor que el problema.
    db = SessionLocal()
    try:
        avisar_rutas_fuera_de_la_base(db)
    finally:
        db.close()
    app.state.scheduler = start_scheduler()
    yield
    app.state.scheduler.shutdown(wait=False)
    # El cliente de GitHub es compartido y vive en el módulo: se cierra aquí
    # para no dejar el pool de conexiones abierto al apagar.
    github_client.cerrar_cliente()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# Antes que el router: bloquea las peticiones cross-site que cambian estado.
app.add_middleware(CSRFMiddleware, trusted_hosts=settings.trusted_origin_hosts())


# Política de seguridad de contenido. Ver el modelo de amenaza de security.py: la
# app renderiza HTML generado desde el Markdown de repositorios de terceros
# (detail.html: {{ readme_html|safe }}). El saneado por lista blanca con nh3 es
# sólido y está argumentado en readme.py, pero era la única capa; esta es la
# segunda, y es exactamente el escenario para el que la CSP existe.
CSP = (
    "default-src 'self'; "
    # img-src abierto a https: los README de terceros traen badges de
    # shields.io, raw.githubusercontent.com y otros dominios arbitrarios.
    # Acotarlo a 'self' los rompe todos, y nadie relacionaría una cosa con la
    # otra. El coste asumido es que un README malicioso puede usar una imagen
    # como baliza de seguimiento; a cambio, no puede ejecutar nada.
    "img-src 'self' data: https:; "
    # 'unsafe-inline' hace falta: dashboard.html y tv.html llevan bloques
    # <script> en línea, y los estilos de las barras del sparkline se calculan
    # en la propia plantilla. Aun así no se abre ningún dominio externo, que es
    # lo que corta la exfiltración.
    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


@app.middleware("http")
async def cabeceras_de_seguridad(request: Request, call_next):
    response = await call_next(request)
    # setdefault y no asignación directa: si algún día un endpoint necesita su
    # propia política, la suya tiene que ganar.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", CSP)
    return response


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(projects.router)


@app.get("/salud")
def health():
    return {"status": "ok"}
