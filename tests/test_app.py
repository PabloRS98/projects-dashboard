"""Comportamiento funcional: vistas, agrupación, tareas y utilidades."""
from datetime import timedelta

import pytest

from app.models import Project, TaskItem, utcnow
from app.routers.projects import _clean_tags, _is_stale, _summary
from app.templating import timeago

from .conftest import SAME_ORIGIN

# --------------------------------------------------------------------------
# Vistas
# --------------------------------------------------------------------------

def test_dashboard_renderiza(client, project):
    respuesta = client.get("/")
    assert respuesta.status_code == 200
    assert "demo" in respuesta.text


def test_ficha_de_proyecto_renderiza(client, project):
    respuesta = client.get(f"/proyecto/{project.id}")
    assert respuesta.status_code == 200
    assert "demo" in respuesta.text


def test_ficha_inexistente_redirige(client):
    respuesta = client.get("/proyecto/999999")
    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/"


def test_salud(client):
    assert client.get("/salud").json() == {"status": "ok"}


def test_enlace_al_repo_aparece_en_el_dashboard(client, db):
    """Con StrictUndefined esto además protege de referencias inexistentes."""
    db.add(Project(name="conremoto", remote_provider="github", remote_repo="o/r"))
    db.commit()
    assert "https://github.com/o/r" in client.get("/").text


def test_repo_url_del_modelo():
    assert Project(name="x", remote_provider="github", remote_repo="o/r").repo_url == \
        "https://github.com/o/r"
    assert Project(name="x").repo_url is None
    assert Project(name="x", remote_provider="otro", remote_repo="o/r").repo_url is None


# --------------------------------------------------------------------------
# Tareas
# --------------------------------------------------------------------------

def test_orden_de_tareas_no_colisiona_tras_borrar(client, db, project):
    """Con count() en vez de max()+1, la nueva tarea reutilizaba un `order`."""
    for texto in ("una", "dos", "tres"):
        client.post(f"/{project.id}/tareas", data={"text": texto}, headers=SAME_ORIGIN)

    intermedia = db.query(TaskItem).filter(TaskItem.text == "dos").one()
    client.post(f"/tareas/{intermedia.id}/eliminar", headers=SAME_ORIGIN)
    client.post(f"/{project.id}/tareas", data={"text": "cuatro"}, headers=SAME_ORIGIN)

    ordenes = [t.order for t in db.query(TaskItem).filter(TaskItem.project_id == project.id)]
    assert len(ordenes) == len(set(ordenes)), f"órdenes duplicados: {ordenes}"


def test_marcar_tarea(client, db, project):
    client.post(f"/{project.id}/tareas", data={"text": "una"}, headers=SAME_ORIGIN)
    tarea = db.query(TaskItem).one()
    client.post(f"/tareas/{tarea.id}/toggle", headers=SAME_ORIGIN)
    db.expire_all()
    assert db.get(TaskItem, tarea.id).done is True


def test_tarea_inexistente_devuelve_404(client):
    assert client.post("/tareas/999999/toggle", headers=SAME_ORIGIN).status_code == 404


# --------------------------------------------------------------------------
# Agrupación y resumen
# --------------------------------------------------------------------------

def test_un_proyecto_cae_en_un_unico_grupo(client, db):
    """Favoritos, activos, parados y archivados deben ser disjuntos."""
    viejo = utcnow() - timedelta(days=400)
    db.add_all([
        Project(name="favorito", is_favorite=True),
        Project(name="activo", last_commit_date=utcnow()),
        Project(name="parado", last_commit_date=viejo),
        Project(name="archivado", is_archived=True, last_commit_date=viejo),
    ])
    db.commit()
    texto = client.get("/").text
    for nombre in ("favorito", "activo", "parado", "archivado"):
        assert texto.count(f">{nombre}</a>") == 1, f"{nombre} aparece en más de un grupo"


def test_is_stale():
    assert _is_stale(Project(name="x", last_commit_date=utcnow() - timedelta(days=40)), 30)
    assert not _is_stale(Project(name="x", last_commit_date=utcnow()), 30)
    assert not _is_stale(Project(name="x"), 30)  # sin fecha no es "parado"
    archivado = Project(name="x", is_archived=True, last_commit_date=utcnow() - timedelta(days=40))
    assert not _is_stale(archivado, 30)


def test_summary_agrega():
    proyectos = [
        Project(name="a", open_prs=2, open_issues=1, has_uncommitted_changes=True),
        Project(name="b", open_prs=3, local_error="roto"),
    ]
    resumen = _summary(proyectos, 30)
    assert resumen["total"] == 2
    assert resumen["prs"] == 5
    assert resumen["issues"] == 1
    assert resumen["cambios"] == 1
    assert resumen["errores"] == 1


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------

@pytest.mark.parametrize("entrada,esperado", [
    ("a, b, a,  , c", "a, b, c"),      # sin duplicados ni vacíos, orden intacto
    ("", ""),
    ("  ", ""),
    ("uno", "uno"),
])
def test_clean_tags(entrada, esperado):
    assert _clean_tags(entrada) == esperado


def test_tag_list():
    assert Project(name="x", tags="a, b ,c").tag_list() == ["a", "b", "c"]
    assert Project(name="x", tags="").tag_list() == []


def test_timeago():
    ahora = utcnow()
    assert timeago(None) == "-"
    assert timeago(ahora) == "ahora mismo"
    assert timeago(ahora - timedelta(minutes=30)) == "hace 30 min"
    assert timeago(ahora - timedelta(hours=5)) == "hace 5 h"
    assert timeago(ahora - timedelta(days=3)) == "hace 3 días"


def test_days_since_commit():
    assert Project(name="x", last_commit_date=utcnow() - timedelta(days=5)).days_since_commit() == 5
    assert Project(name="x").days_since_commit() is None


def test_sync_error_combina_local_y_remoto():
    assert Project(name="x", local_error="L", remote_error="R").sync_error == "L | R"
    assert Project(name="x", local_error="L").sync_error == "L"
    assert Project(name="x").sync_error is None
