"""Cabeceras de seguridad HTTP. Ver [PD-A7].

La app renderiza HTML generado desde el Markdown de repositorios de terceros
(`detail.html`: `{{ readme_html|safe }}`). El saneado con `nh3` por lista blanca
es sólido, pero era la única capa. La CSP es la segunda, y es exactamente el
escenario para el que existe.
"""
import pytest

RUTAS = ["/", "/estado", "/tv", "/salud"]


@pytest.mark.parametrize("ruta", RUTAS)
def test_las_paginas_llevan_cabeceras_de_seguridad(client, ruta):
    cabeceras = client.get(ruta).headers
    assert cabeceras["X-Content-Type-Options"] == "nosniff"
    assert cabeceras["Referrer-Policy"] == "same-origin"
    assert "Content-Security-Policy" in cabeceras


def test_la_csp_permite_las_imagenes_de_los_readme(client):
    """Los README de terceros traen badges de shields.io, raw.githubusercontent
    y dominios arbitrarios. Si alguien endurece `img-src` a 'self', se rompen
    todos sin que nadie relacione una cosa con la otra."""
    csp = client.get("/").headers["Content-Security-Policy"]
    directivas = dict(
        (trozo.strip().split(" ", 1) + [""])[:2] for trozo in csp.split(";") if trozo.strip()
    )
    assert "https:" in directivas["img-src"]
    assert "data:" in directivas["img-src"]


def test_la_csp_no_permite_scripts_de_terceros(client):
    """`script-src` sí va acotado: los bloques en línea de dashboard.html y
    tv.html obligan a 'unsafe-inline', pero no a abrir dominios externos."""
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "script-src 'self' 'unsafe-inline'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp


def test_la_csp_no_se_aplica_dos_veces(client):
    """`setdefault`: si algún día un endpoint pone la suya, gana la del endpoint."""
    assert len(client.get("/").headers.get_list("Content-Security-Policy")) == 1


def test_las_cabeceras_llegan_tambien_en_los_fragmentos_htmx(client):
    """`/lista` se pide por HTMX y se inyecta en el DOM: si la CSP no viaja con
    ella, la mitad de las respuestas de la app quedan sin cubrir."""
    assert "Content-Security-Policy" in client.get("/lista").headers


def test_las_cabeceras_llegan_en_una_ruta_inexistente(client):
    respuesta = client.get("/no-existe")
    assert respuesta.status_code == 404
    assert respuesta.headers["X-Content-Type-Options"] == "nosniff"


def test_las_cabeceras_llegan_en_las_redirecciones(client):
    """Un proyecto inexistente no da 404: redirige con un flash. Es lo que hace
    la app, y esa respuesta también tiene que llevar las cabeceras."""
    respuesta = client.get("/proyecto/9999")
    assert respuesta.status_code == 303
    assert "Content-Security-Policy" in respuesta.headers
