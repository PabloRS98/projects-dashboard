"""Clientes de forge: diagnóstico de errores y lectura de la cuota.

El valor de estas pruebas está en el mensaje: la versión anterior devolvía la
misma frase para cualquier fallo, y eso hacía imposible saber si había que
renovar el token, corregir el nombre del repo o esperar a que se repusiera la
cuota. Aquí se fija que cada causa produzca un mensaje distinto.
"""
import httpx
import pytest

from app.services import forge_errors, github_client


def _status_error(status: int, headers: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.github.com/repos/a/b")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


# --------------------------------------------------------------------------
# Traducción de errores
# --------------------------------------------------------------------------

def test_401_señala_el_token():
    msg = forge_errors.describe(_status_error(401), "GitHub", "GITHUB_TOKEN", True)
    assert "GITHUB_TOKEN" in msg and "401" in msg


def test_404_sin_token_sugiere_que_puede_ser_privado():
    msg = forge_errors.describe(_status_error(404), "GitHub", "GITHUB_TOKEN", has_token=False)
    assert "privado" in msg


def test_404_con_token_apunta_al_nombre_o_al_alcance():
    msg = forge_errors.describe(_status_error(404), "GitHub", "GITHUB_TOKEN", has_token=True)
    assert "owner/repo" in msg
    assert "privado" not in msg


def test_cuota_agotada_incluye_la_hora_de_reposicion():
    # 2026-01-01 12:00:00 UTC
    error = _status_error(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1767268800"})
    msg = forge_errors.describe(error, "GitHub", "GITHUB_TOKEN", True)
    assert "Cuota" in msg and "12:00" in msg


def test_403_sin_cuota_agotada_es_falta_de_permiso():
    msg = forge_errors.describe(_status_error(403), "GitHub", "GITHUB_TOKEN", True)
    assert "permiso" in msg
    assert "Cuota" not in msg


def test_429_se_trata_como_cuota():
    assert "Cuota" in forge_errors.describe(_status_error(429), "GitLab", "GITLAB_TOKEN", True)


def test_error_de_servidor_se_distingue_del_de_cliente():
    assert "caído" in forge_errors.describe(_status_error(503), "GitHub", "GITHUB_TOKEN", True)


def test_timeout_y_red_no_culpan_al_token():
    timeout = forge_errors.describe(httpx.ReadTimeout("lento"), "GitHub", "GITHUB_TOKEN", True)
    red = forge_errors.describe(httpx.ConnectError("sin ruta"), "GitHub", "GITHUB_TOKEN", True)
    assert "tiempo" in timeout
    assert "red" in red
    assert "GITHUB_TOKEN" not in timeout + red


# --------------------------------------------------------------------------
# Cuota
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def cuota_limpia():
    """La cuota es estado de módulo: se restablece entre pruebas."""
    original = dict(github_client.rate_limit)
    github_client.rate_limit.update(
        {"remaining": None, "limit": None, "reset": None, "checked_at": None}
    )
    yield
    github_client.rate_limit.update(original)


def test_recuerda_la_cuota_de_la_respuesta():
    response = httpx.Response(200, headers={
        "X-RateLimit-Remaining": "4987", "X-RateLimit-Limit": "5000",
        "X-RateLimit-Reset": "1767268800",
    })
    github_client._remember_rate_limit(response)
    assert github_client.rate_limit["remaining"] == 4987
    assert github_client.rate_limit["limit"] == 5000
    assert github_client.rate_limit["reset"] is not None


def test_cuota_agotada_corta_antes_de_llamar(monkeypatch):
    """Con la cuota a cero no se gasta una petición fallida por proyecto."""
    from datetime import UTC, datetime, timedelta

    github_client.rate_limit["remaining"] = 0
    github_client.rate_limit["reset"] = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    def _no_llamar(*args, **kwargs):
        raise AssertionError("no debería salir a la red con la cuota agotada")

    monkeypatch.setattr(httpx, "Client", _no_llamar)
    info = github_client.get_repo_info("pablo/repo")
    assert "Cuota" in info["error"]


def test_cuota_repuesta_deja_de_bloquear():
    from datetime import UTC, datetime, timedelta

    github_client.rate_limit["remaining"] = 0
    github_client.rate_limit["reset"] = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    assert github_client.quota_exhausted() is False


# --------------------------------------------------------------------------
# get_repo_info con la API simulada
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def sin_cliente_compartido():
    """Descarta el cliente compartido de github_client antes y después.

    Desde [PD-M8] el cliente se crea una vez y se guarda en el módulo, así que
    sin esto un cliente real creado por otra prueba sobreviviría al parche de
    `httpx.Client` y las peticiones simuladas saldrían de verdad a la red.
    """
    github_client.cerrar_cliente()
    yield
    github_client.cerrar_cliente()


def _mock_github(monkeypatch, rutas: dict, status_por_ruta: dict | None = None):
    """Sustituye httpx.Client por uno que responde desde un diccionario de rutas."""
    status_por_ruta = status_por_ruta or {}
    # El cliente compartido puede estar ya creado con el transporte real.
    github_client.cerrar_cliente()

    def handler(request: httpx.Request) -> httpx.Response:
        for ruta, cuerpo in rutas.items():
            if request.url.path.endswith(ruta):
                return httpx.Response(status_por_ruta.get(ruta, 200), json=cuerpo)
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    original = httpx.Client

    def _client(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _client)


def test_get_repo_info_rellena_escaparate_y_metricas(monkeypatch):
    _mock_github(monkeypatch, {
        "/repos/pablo/repo": {
            "stargazers_count": 12, "default_branch": "main",
            "description": "Mi proyecto", "homepage": "https://ejemplo.tld",
            "open_issues_count": 5,
        },
        "/commits": [{"sha": "abc", "commit": {"message": "arreglo\n\ndetalle",
                                               "committer": {"date": "2026-01-02T10:00:00Z"}}}],
        "/pulls": [{"created_at": "2026-01-01T00:00:00Z"}],
        "/actions/runs": {"workflow_runs": [{"conclusion": "success"}]},
    })
    info = github_client.get_repo_info("pablo/repo")

    assert info["stars"] == 12
    assert info["description"] == "Mi proyecto"
    assert info["homepage"] == "https://ejemplo.tld"
    assert info["last_commit_message"] == "arreglo"   # solo la primera línea
    assert info["open_prs"] == 1
    assert info["open_issues"] == 4                   # 5 totales - 1 PR
    assert info["ci_status"] == "success"
    assert "error" not in info


def test_get_repo_info_traduce_el_401(monkeypatch):
    _mock_github(monkeypatch, {"/repos/pablo/repo": {"message": "Bad credentials"}},
                 {"/repos/pablo/repo": 401})
    info = github_client.get_repo_info("pablo/repo")
    assert "GITHUB_TOKEN" in info["error"]
