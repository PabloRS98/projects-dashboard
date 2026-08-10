"""Un solo diálogo de edición para toda la lista. Ver [PD-M3].

Se renderizaba un `<dialog>` completo por proyecto: 40 líneas de marcado y 7
campos de formulario cada uno. Con 30 proyectos, 1.200 líneas y 210 elementos de
formulario en el DOM para usar uno como mucho — y se pagaba en cada carga del
dashboard y en cada pulsación del buscador, porque `/lista` devuelve la lista
entera.
"""
from app.models import Project

from .conftest import SAME_ORIGIN


def _crear(db, cuantos: int):
    for i in range(cuantos):
        db.add(Project(name="proyecto-%02d" % i))
    db.commit()


def test_la_lista_no_repite_el_dialogo_por_proyecto(client, db):
    _crear(db, 10)
    texto = client.get("/lista").text
    assert texto.count("<dialog") <= 1


def test_la_lista_no_repite_los_campos_del_formulario(client, db):
    """Lo que de verdad pesa son los inputs, no la etiqueta dialog."""
    _crear(db, 10)
    texto = client.get("/lista").text
    assert texto.count('name="remote_repo"') <= 1


def test_el_formulario_de_edicion_se_sirve_aparte(client, db, project):
    respuesta = client.get("/%d/editar-form" % project.id)
    assert respuesta.status_code == 200
    assert 'action="/%d/editar"' % project.id in respuesta.text
    assert 'name="remote_repo"' in respuesta.text


def test_el_formulario_trae_los_datos_del_proyecto(client, db):
    p = Project(name="con datos", remote_provider="gitlab", remote_repo="grupo/repo",
                tags="web, cli", homepage_url="https://ejemplo.tld")
    db.add(p)
    db.commit()

    texto = client.get("/%d/editar-form" % p.id).text
    assert 'value="con datos"' in texto
    assert 'value="grupo/repo"' in texto
    assert 'value="web, cli"' in texto
    assert 'value="gitlab" selected' in texto


def test_el_formulario_de_un_proyecto_inexistente_da_404(client):
    assert client.get("/9999/editar-form").status_code == 404


def test_el_formulario_no_trae_la_pagina_entera(client, project):
    """Es un fragmento para HTMX, como `/lista`."""
    texto = client.get("/%d/editar-form" % project.id).text
    assert "<html" not in texto


def test_el_boton_de_editar_apunta_al_dialogo_compartido(client, db):
    import re

    _crear(db, 3)
    texto = client.get("/lista").text
    assert texto.count('data-open-dialog="#dlg-project"') == 3
    # Ya no hay un diálogo por id. Se busca el patrón exacto y no la subcadena
    # "dlg-project-", que aparece legítimamente en "#dlg-project-body".
    assert re.search(r'id="dlg-project-\d', texto) is None


def test_editar_sigue_funcionando(client, db, project):
    client.post(
        "/%d/editar" % project.id,
        data={"name": "renombrado", "local_path": "", "remote_provider": "",
              "remote_repo": "", "description": "", "homepage_url": "", "tags": ""},
        headers=SAME_ORIGIN,
    )
    db.expire_all()
    assert db.get(Project, project.id).name == "renombrado"


def test_la_ficha_sigue_teniendo_su_formulario_inline(client, project):
    """En la ficha solo hay un proyecto, así que el formulario va directo: pedirlo
    por HTMX sería un viaje de más para nada."""
    texto = client.get("/proyecto/%d" % project.id).text
    assert 'name="remote_repo"' in texto
    assert "dlg-edit" in texto
