"""Punto de entrada: Dashboard de Proyectos Personales."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .database import init_db
from .routers import projects
from .services.scheduler import start_scheduler

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    app.state.scheduler = start_scheduler()
    yield
    app.state.scheduler.shutdown(wait=False)


app = FastAPI(title="Dashboard de Proyectos", lifespan=lifespan)

app.include_router(projects.router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/salud")
def health():
    return {"status": "ok"}
