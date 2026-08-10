"""Sincronizar un proyecto concreto, fuera de la petición. Ver [PD-M13].

Sincronizar *todos* los proyectos respondía al instante y sincronizar *uno*
dejaba el navegador colgado hasta 40 s (4 peticiones HTTP × 10 s de timeout).
Justo al revés de lo esperable, y el botón de un proyecto concreto es el que más
se pulsa.
"""
from fastapi import BackgroundTasks

from app.models import Project
from app.routers import projects as router_projects
from app.routers.projects import sync_one

from .conftest import SAME_ORIGIN

# El parche va sobre `app.routers.projects.sync_project` y no sobre
# `app.services.sync.sync_project`: el router lo importa por nombre, así que son
# dos enlaces distintos y parchear el módulo de origen no intercepta nada.


class _PeticionFalsa:
    """Lo único que `sync_one` necesita de la petición es la cabecera Referer."""

    headers = {"referer": "/"}


def test_sincronizar_uno_no_hace_el_trabajo_dentro_de_la_peticion(db, project, monkeypatch):
    """Igual que en el descubrimiento, se llama al handler directamente: el
    TestClient ejecuta las tareas de fondo dentro de la propia llamada."""
    llamadas = []
    monkeypatch.setattr(router_projects, "sync_project", lambda p: llamadas.append(p.name))

    tareas = BackgroundTasks()
    respuesta = sync_one(_PeticionFalsa(), tareas, project.id, db)

    assert respuesta.status_code == 303
    assert llamadas == []
    assert len(tareas.tasks) == 1


def test_sincronizar_un_proyecto_inexistente_no_encola_nada(db):
    tareas = BackgroundTasks()
    respuesta = sync_one(_PeticionFalsa(), tareas, 9999, db)

    assert respuesta.status_code == 303
    assert tareas.tasks == []


def test_el_error_de_sync_sigue_siendo_visible_en_la_tarjeta(db, client, project):
    """La justificación de que fuera síncrono era el flash con el error concreto.
    No hace falta: `sync_local` y `sync_remote` ya persisten el error en el
    proyecto y la tarjeta lo pinta."""
    project.remote_error = "GITHUB_TOKEN inválido o caducado (401)"
    db.commit()

    assert "GITHUB_TOKEN inválido o caducado (401)" in client.get("/").text


def test_el_endpoint_sigue_respondiendo_por_http(client, project):
    respuesta = client.post("/%d/sincronizar" % project.id, headers=SAME_ORIGIN)
    assert respuesta.status_code == 303


def test_la_sincronizacion_de_fondo_guarda_lo_que_encuentra(db, project, monkeypatch):
    """La tarea encolada corre en su propia sesión y commitea: si no, el trabajo
    se perdería al cerrarse la sesión de la petición."""
    def _marca(p: Project) -> None:
        p.branch = "main"
        p.remote_error = "no hay token"

    monkeypatch.setattr(router_projects, "sync_project", _marca)
    router_projects._sync_in_background(project.id)

    db.expire_all()
    recargado = db.get(Project, project.id)
    assert recargado.branch == "main"
    assert recargado.remote_error == "no hay token"
