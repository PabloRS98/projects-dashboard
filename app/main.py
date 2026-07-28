"""Punto de entrada: Dashboard de Proyectos Personales."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import init_db
from .routers import projects
from .security import CSRFMiddleware
from .services.scheduler import start_scheduler

logging.basicConfig(level=logging.INFO)

# Absoluta por el mismo motivo que las plantillas: no depender del cwd.
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    app.state.scheduler = start_scheduler()
    yield
    app.state.scheduler.shutdown(wait=False)


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# Antes que el router: bloquea las peticiones cross-site que cambian estado.
app.add_middleware(CSRFMiddleware, trusted_hosts=settings.trusted_origin_hosts())

app.include_router(projects.router)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/salud")
def health():
    return {"status": "ok"}
