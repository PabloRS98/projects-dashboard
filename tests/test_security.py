"""Regresiones de las vulnerabilidades corregidas en la auditoría.

Cada prueba de este fichero falla contra el código anterior a la auditoría.
"""
from html.parser import HTMLParser

import pytest

from app.security import safe_external_url, safe_redirect_path
from app.services.readme import render
from app.services.sync import normalize_remote_repo

from .conftest import SAME_ORIGIN

# --------------------------------------------------------------------------
# Saneado del README (XSS almacenado)
# --------------------------------------------------------------------------

class _Audit(HTMLParser):
    """Recolecta lo que sería ejecutable en el HTML resultante."""

    def __init__(self):
        super().__init__()
        self.hallazgos: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "iframe", "object", "embed", "svg"}:
            self.hallazgos.append(f"etiqueta <{tag}>")
        for name, value in attrs:
            if name.lower().startswith("on"):
                self.hallazgos.append(f"manejador {name}")
            if name.lower() in {"href", "src"} and value:
                limpio = value.strip().lower().replace("\t", "").replace("\n", "")
                if limpio.startswith(("javascript:", "data:")):
                    self.hallazgos.append(f"url {name}={value[:40]}")


def _ejecutable(markdown_source: str) -> list[str]:
    auditor = _Audit()
    auditor.feed(render(markdown_source))
    return auditor.hallazgos


@pytest.mark.parametrize("vector", [
    # El filtro por regex quitaba la coincidencia interior y reconstruía el
    # esquema al recomponer los extremos.
    "[x](javajavascript:script:alert(1))",
    # El patrón exigía \s antes del manejador, así que "/" lo esquivaba.
    "<img src=x/onerror=alert(1)>",
    "<svg/onload=alert(1)>",
    # El patrón de etiquetas exigía cierre; sin él, pasaba entera.
    '<script src="//evil.tld/x.js">',
    "<img src=x\nonerror=alert(1)>",
    "<scr<script></script>ipt>alert(1)</script>",
    "[a](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    '<a href="jAvAsCrIpT:alert(1)">x</a>',
    '<iframe src="//evil.tld"></iframe>',
    "[a](java\tscript:alert(1))",
])
def test_readme_neutraliza_vectores_xss(vector):
    assert _ejecutable(vector) == []


def test_readme_conserva_el_formato_legitimo():
    html = render("# Título\n\n**negrita** y [enlace](https://ejemplo.tld)\n\n- uno\n- dos")
    assert "<h1>" in html
    assert "<strong>" in html
    assert 'href="https://ejemplo.tld"' in html
    assert "<li>" in html


# --------------------------------------------------------------------------
# Open redirect
# --------------------------------------------------------------------------

@pytest.mark.parametrize("hostil", [
    "https://evil.tld/phishing",
    "//evil.tld/phishing",
    "http://evil.tld",
    "javascript:alert(1)",
])
def test_safe_redirect_path_descarta_destinos_externos(hostil):
    assert not safe_redirect_path(hostil).startswith(("http", "//", "javascript"))


def test_safe_redirect_path_conserva_la_ruta_interna():
    # Se mantiene la ruta+query para volver a la página de origen, sin el host.
    assert safe_redirect_path("http://miservidor/proyecto/7?filtro=prs") == "/proyecto/7?filtro=prs"
    assert safe_redirect_path("/proyecto/7") == "/proyecto/7"
    assert safe_redirect_path(None) == "/"


def test_referer_hostil_no_produce_redireccion_externa(client, project):
    respuesta = client.post(
        f"/{project.id}/favorito",
        headers={**SAME_ORIGIN, "Referer": "https://evil.tld/phishing"},
    )
    assert respuesta.status_code == 303
    assert "evil.tld" not in respuesta.headers["location"]


# --------------------------------------------------------------------------
# CSRF
# --------------------------------------------------------------------------

def test_post_cross_site_es_rechazado(client, project):
    respuesta = client.post(
        f"/{project.id}/eliminar", headers={"Origin": "https://evil.tld"}
    )
    assert respuesta.status_code == 403


def test_post_sin_origen_es_rechazado(client, project):
    assert client.post(f"/{project.id}/eliminar").status_code == 403


def test_post_del_mismo_origen_se_acepta(client, project):
    respuesta = client.post(f"/{project.id}/favorito", headers=SAME_ORIGIN)
    assert respuesta.status_code == 303


def test_las_peticiones_get_no_necesitan_origen(client):
    assert client.get("/").status_code == 200


# --------------------------------------------------------------------------
# Esquemas de URL en campos de usuario
# --------------------------------------------------------------------------

@pytest.mark.parametrize("hostil", [
    "javascript:alert(document.cookie)",
    "data:text/html,<script>alert(1)</script>",
    "  javascript:alert(1)",
    "vbscript:msgbox(1)",
    "/ruta/relativa",
])
def test_safe_external_url_rechaza_esquemas_peligrosos(hostil):
    assert safe_external_url(hostil) is None


def test_safe_external_url_acepta_http_y_https():
    assert safe_external_url("https://ejemplo.tld/a") == "https://ejemplo.tld/a"
    assert safe_external_url("http://ejemplo.tld") == "http://ejemplo.tld"


def test_homepage_javascript_no_se_guarda(client, db):
    from app.models import Project

    client.post(
        "/nuevo",
        data={"name": "x", "homepage_url": "javascript:alert(1)"},
        headers=SAME_ORIGIN,
    )
    creado = db.query(Project).filter(Project.name == "x").one()
    assert creado.homepage_url is None


# --------------------------------------------------------------------------
# Recorrido de rutas en el identificador del repo remoto
# --------------------------------------------------------------------------

@pytest.mark.parametrize("hostil", [
    "owner/repo/../../../user",
    "../../user",
    "owner/../../orgs/otra",
    "owner",          # sin barra no es owner/repo
    "owner//repo",
])
def test_normalize_remote_repo_rechaza_rutas_invalidas(hostil):
    assert normalize_remote_repo(hostil) is None


@pytest.mark.parametrize("entrada,esperado", [
    ("owner/repo", "owner/repo"),
    ("https://github.com/owner/repo.git", "owner/repo"),
    ("git@github.com:owner/repo.git", "owner/repo"),
    ("grupo/subgrupo/repo", "grupo/subgrupo/repo"),   # subgrupos de GitLab
    ("owner/repo.js", "owner/repo.js"),
])
def test_normalize_remote_repo_acepta_formas_validas(entrada, esperado):
    assert normalize_remote_repo(entrada) == esperado


# --------------------------------------------------------------------------
# Autenticación
# --------------------------------------------------------------------------

def test_credenciales_no_ascii_no_rompen_la_comparacion(monkeypatch):
    """Antes lanzaba TypeError y devolvía 500 en vez de 401."""
    from fastapi import HTTPException
    from fastapi.security import HTTPBasicCredentials

    from app import auth

    monkeypatch.setattr(auth.settings, "enable_auth", True)
    monkeypatch.setattr(auth.settings, "auth_username", "admin")
    monkeypatch.setattr(auth.settings, "auth_password", "contraseña-larga")

    with pytest.raises(HTTPException) as excinfo:
        auth.verify_auth(HTTPBasicCredentials(username="admin", password="incorrecta"))
    assert excinfo.value.status_code == 401

    assert auth.verify_auth(
        HTTPBasicCredentials(username="admin", password="contraseña-larga")
    ) is True


def test_no_arranca_con_la_contrasena_de_fabrica(monkeypatch):
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.setenv("ENABLE_AUTH", "true")
    monkeypatch.setenv("AUTH_PASSWORD", "changeme")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_no_arranca_con_contrasena_corta(monkeypatch):
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.setenv("ENABLE_AUTH", "true")
    monkeypatch.setenv("AUTH_PASSWORD", "corta")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
