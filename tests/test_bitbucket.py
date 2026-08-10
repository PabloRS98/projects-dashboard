"""Bitbucket: estado de CI y tabla de capacidades por proveedor. Ver [PD-M20].

El módulo declaraba en su docstring que no soportaba CI. No es un bug del
cliente, es una limitación — pero la interfaz no la comunicaba: el filtro "CI en
rojo" nunca marcaba un proyecto de Bitbucket, el KPI subestimaba, `/tv` omitía
esos fallos en silencio y los avisos de Telegram tampoco llegaban. Un usuario con
proyectos en Bitbucket veía "0 CI en rojo" y confiaba.
"""
import httpx
import pytest

from app.models import Project
from app.services import bitbucket_client, capacidades

from .conftest import SAME_ORIGIN


def _mock(monkeypatch, rutas: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        for sufijo, cuerpo in rutas.items():
            if request.url.path.rstrip("/").endswith(sufijo):
                return httpx.Response(200, json=cuerpo)
        return httpx.Response(200, json={})

    transporte = httpx.MockTransport(handler)
    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda *a, **k: original(*a, **{**k, "transport": transporte})
    )


# --------------------------------------------------------------------------
# Estado del pipeline
# --------------------------------------------------------------------------

@pytest.mark.parametrize("resultado,esperado", [
    ("SUCCESSFUL", "success"),
    ("FAILED", "failure"),
    ("ERROR", "error"),
    ("STOPPED", "cancelled"),
])
def test_bitbucket_devuelve_el_estado_del_pipeline(monkeypatch, resultado, esperado):
    _mock(monkeypatch, {"/pipelines": {"values": [
        {"state": {"name": "COMPLETED", "result": {"name": resultado}}}
    ]}})
    assert bitbucket_client.get_repo_info("pablo/repo")["ci_status"] == esperado


def test_un_pipeline_en_marcha_se_marca_como_en_ejecucion(monkeypatch):
    _mock(monkeypatch, {"/pipelines": {"values": [{"state": {"name": "IN_PROGRESS"}}]}})
    assert bitbucket_client.get_repo_info("pablo/repo")["ci_status"] == "running"


def test_sin_pipelines_no_se_inventa_un_estado(monkeypatch):
    """Clave ausente = "no lo sé", y la política de sync conserva lo anterior."""
    _mock(monkeypatch, {"/pipelines": {"values": []}})
    assert "ci_status" not in bitbucket_client.get_repo_info("pablo/repo")


def test_un_estado_desconocido_no_rompe(monkeypatch):
    _mock(monkeypatch, {"/pipelines": {"values": [
        {"state": {"name": "COMPLETED", "result": {"name": "ALGO_NUEVO"}}}
    ]}})
    info = bitbucket_client.get_repo_info("pablo/repo")
    assert info.get("ci_status") == "algo_nuevo"


def test_un_proyecto_de_bitbucket_con_ci_rojo_aparece_en_el_filtro(client, db):
    db.add(Project(name="bb", remote_provider="bitbucket", remote_repo="pablo/bb",
                   ci_status="failure"))
    db.commit()
    assert "bb" in client.get("/?filtro=ci-rojo").text


# --------------------------------------------------------------------------
# Tabla de capacidades
# --------------------------------------------------------------------------

def test_cada_proveedor_declara_lo_que_da():
    assert capacidades.soporta("github", "stars") is True
    assert capacidades.soporta("bitbucket", "stars") is False
    assert capacidades.soporta("bitbucket", "ci_status") is True      # ya sí
    assert capacidades.soporta("gitlab", "commit_weeks") is False


def test_un_proveedor_desconocido_no_soporta_nada():
    assert capacidades.soporta("inventado", "stars") is False
    assert capacidades.soporta(None, "stars") is False


def test_estado_publica_la_tabla_de_capacidades(client):
    """Para que "0 estrellas" y "no aplica" dejen de pintarse igual."""
    texto = client.get("/estado").text
    assert "Qué da cada proveedor" in texto
    assert "Bitbucket" in texto


def test_la_ficha_distingue_no_disponible_de_cero(client, db):
    """Un proyecto de Bitbucket sin estrellas no tiene 0 estrellas: no tiene
    estrellas en absoluto."""
    p = Project(name="bb", remote_provider="bitbucket", remote_repo="pablo/bb")
    db.add(p)
    db.commit()
    texto = client.get("/proyecto/%d" % p.id).text
    assert "no lo da Bitbucket" in texto


def test_un_proyecto_sin_remoto_no_habla_de_proveedores(client, project):
    assert "no lo da" not in client.get("/proyecto/%d" % project.id).text


def test_editar_un_proyecto_de_bitbucket_sigue_funcionando(client, db):
    p = Project(name="bb", remote_provider="bitbucket", remote_repo="pablo/bb")
    db.add(p)
    db.commit()
    respuesta = client.post(
        "/%d/editar" % p.id,
        data={"name": "bb", "local_path": "", "remote_provider": "bitbucket",
              "remote_repo": "pablo/bb", "description": "", "homepage_url": "", "tags": ""},
        headers=SAME_ORIGIN,
    )
    assert respuesta.status_code == 303
