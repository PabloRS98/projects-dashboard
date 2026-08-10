"""Control de cuota compartido por los tres forges. Ver [PD-M7].

`github_client` tenía el mecanismo completo y los otros dos no tenían nada:
`forge_errors` sabía diagnosticar un 429, pero nadie cortaba el ciclo, así que se
seguían gastando peticiones fallidas y cada proyecto acababa con el mismo error.
"""
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.models import Project
from app.services import bitbucket_client, cuota, gitlab_client, sync


@pytest.fixture(autouse=True)
def cuota_limpia():
    cuota.reiniciar()
    yield
    cuota.reiniciar()


def _respuesta(status: int, headers: dict | None = None) -> httpx.Response:
    peticion = httpx.Request("GET", "https://gitlab.com/api/v4/projects/1")
    return httpx.Response(status, headers=headers or {}, request=peticion)


# --------------------------------------------------------------------------
# El mecanismo
# --------------------------------------------------------------------------

def test_una_respuesta_normal_no_marca_nada():
    cuota.recordar("gitlab", _respuesta(200, {"RateLimit-Remaining": "1500"}))
    assert cuota.agotada("gitlab") is False
    assert cuota.estado("gitlab")["remaining"] == 1500


def test_la_cuota_a_cero_corta():
    futuro = int((datetime.now(UTC) + timedelta(hours=1)).timestamp())
    cuota.recordar("gitlab", _respuesta(200, {
        "RateLimit-Remaining": "0", "RateLimit-Reset": str(futuro),
    }))
    assert cuota.agotada("gitlab") is True


def test_pasado_el_reset_deja_de_cortar():
    pasado = int((datetime.now(UTC) - timedelta(minutes=1)).timestamp())
    cuota.recordar("gitlab", _respuesta(200, {
        "RateLimit-Remaining": "0", "RateLimit-Reset": str(pasado),
    }))
    assert cuota.agotada("gitlab") is False


def test_un_429_sin_cabeceras_tambien_corta():
    """Bitbucket devuelve 429 sin RateLimit-*: sin esto no habría forma de saber
    que hay que parar."""
    error = httpx.HTTPStatusError("boom", request=_respuesta(429).request,
                                  response=_respuesta(429))
    cuota.anotar_error("bitbucket", error)
    assert cuota.agotada("bitbucket") is True


def test_cada_proveedor_lleva_su_cuenta():
    """GitHub agotado no puede parar los proyectos de GitLab."""
    error = httpx.HTTPStatusError("boom", request=_respuesta(429).request,
                                  response=_respuesta(429))
    cuota.anotar_error("github", error)
    assert cuota.agotada("github") is True
    assert cuota.agotada("gitlab") is False


def test_un_403_que_no_es_de_cuota_no_corta():
    respuesta = _respuesta(403)
    error = httpx.HTTPStatusError("boom", request=respuesta.request, response=respuesta)
    cuota.anotar_error("gitlab", error)
    assert cuota.agotada("gitlab") is False


# --------------------------------------------------------------------------
# Los clientes lo usan
# --------------------------------------------------------------------------

def _instalar_contador(monkeypatch, status: int, headers: dict | None = None):
    """Cliente HTTP simulado que cuenta las peticiones que salen."""
    peticiones = []

    def handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(str(request.url))
        return httpx.Response(status, headers=headers or {}, json={})

    transporte = httpx.MockTransport(handler)
    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda *a, **k: original(*a, **{**k, "transport": transporte})
    )
    return peticiones


def test_un_429_de_gitlab_aborta_el_ciclo(monkeypatch):
    """Con el primer proyecto devolviendo 429, los siguientes de GitLab no
    generan ni una petición."""
    peticiones = _instalar_contador(monkeypatch, 429)

    primero = gitlab_client.get_repo_info("grupo/uno")
    assert "Cuota" in primero["error"]
    hechas = len(peticiones)

    segundo = gitlab_client.get_repo_info("grupo/dos")
    assert "Cuota" in segundo["error"]
    assert len(peticiones) == hechas          # ni una más


def test_un_429_de_bitbucket_aborta_el_ciclo(monkeypatch):
    peticiones = _instalar_contador(monkeypatch, 429)

    bitbucket_client.get_repo_info("pablo/uno")
    hechas = len(peticiones)

    info = bitbucket_client.get_repo_info("pablo/dos")
    assert "Cuota" in info["error"]
    assert len(peticiones) == hechas


def test_la_cuota_de_un_forge_no_para_a_los_demas(monkeypatch):
    peticiones = _instalar_contador(monkeypatch, 429)
    gitlab_client.get_repo_info("grupo/uno")
    hechas = len(peticiones)

    bitbucket_client.get_repo_info("pablo/uno")
    assert len(peticiones) > hechas           # Bitbucket sí lo intenta


def test_sync_remote_propaga_el_mensaje_de_cuota(monkeypatch):
    _instalar_contador(monkeypatch, 429)
    p = Project(name="x", remote_provider="gitlab", remote_repo="grupo/x")

    sync.sync_remote(p)

    assert "Cuota" in p.remote_error


def test_el_panel_de_estado_ensena_la_cuota_de_los_tres(client):
    texto = client.get("/estado").text
    assert "Cuota" in texto
