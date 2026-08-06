"""Filtros de Jinja.

El de `tojson` es el que importa: fija por test una propiedad de seguridad que
no se ve al leer el código, para que nadie vuelva a "simplificarlo" a un
`json.dumps` a secas. Ver [PD-A1].
"""
from datetime import date, datetime

import pytest
from markupsafe import Markup

from app.templating import templates

tojson = templates.env.filters["tojson"]


def test_tojson_escapa_el_cierre_de_script():
    """La razón de ser del filtro nativo de Jinja.

    Con un json.dumps crudo, `{{ algo | tojson }}` dentro de un <script> deja
    cerrar la etiqueta desde cualquier dato guardado: el nombre del proyecto, la
    descripción que se autorrellena desde la API de GitHub o los tags.
    """
    assert "</script>" not in tojson("</script><script>alert(1)</script>")


@pytest.mark.parametrize("hostil", ["<", ">", "&", "'"])
def test_tojson_escapa_los_cuatro_caracteres_peligrosos(hostil):
    assert hostil not in tojson(hostil)


def test_tojson_escapa_dentro_de_estructuras_anidadas():
    """No basta con la cadena suelta: los datos van en listas y diccionarios."""
    salida = tojson({"proyectos": [{"name": "</script>"}]})
    assert "</script>" not in salida


def test_tojson_serializa_fechas():
    """El motivo por el que el filtro se sobrescribió en su día: el nativo no
    sabe serializar date/datetime y las series del histórico van llenas."""
    assert "2026-08-07" in tojson({"dia": date(2026, 8, 7)})
    assert tojson(datetime(2026, 8, 7, 12, 30))


def test_tojson_devuelve_markup():
    """Markup: el resultado ya es seguro de incrustar y el `| safe` sobra.

    Si devolviera `str`, Jinja lo volvería a escapar como HTML y el JSON
    llegaría al navegador con `&#34;` en vez de comillas.
    """
    assert isinstance(tojson({"a": 1}), Markup)


def test_tojson_sigue_produciendo_json_valido():
    import json

    assert json.loads(tojson({"a": [1, 2], "b": "texto"})) == {"a": [1, 2], "b": "texto"}
