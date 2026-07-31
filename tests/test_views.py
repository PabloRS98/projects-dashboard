"""Vistas nuevas: búsqueda, filtros combinables, orden, tabla, /estado y /tv."""
from datetime import timedelta

import pytest

from app.models import Project, utcnow
from app.routers.projects import _matches_search, _staleness


@pytest.fixture
def catalogo(db):
    """Un proyecto por cada situación que la interfaz tiene que saber distinguir."""
    db.add_all([
        Project(name="alfa", description="panel de finanzas", tags="web",
                last_commit_date=utcnow(), open_prs=2, stars=5, todo_count=3),
        Project(name="beta", remote_repo="pablo/beta-cli", remote_provider="github",
                last_commit_date=utcnow() - timedelta(days=400), stars=50),
        Project(name="gamma", ci_status="failure", has_uncommitted_changes=True,
                last_commit_date=utcnow() - timedelta(days=1), todo_count=9),
        Project(name="delta", oldest_open_pr_days=40, open_prs=1,
                last_commit_date=utcnow() - timedelta(days=2)),
        Project(name="epsilon", local_error="La ruta local ya no existe",
                local_path_missing=True, local_path="/se/fue"),
    ])
    db.commit()
    return db


# --------------------------------------------------------------------------
# Búsqueda
# --------------------------------------------------------------------------

def test_busca_en_nombre_descripcion_tags_y_repo():
    p = Project(name="alfa", description="panel de finanzas", tags="web, cli",
                remote_repo="pablo/alfa-cli")
    assert _matches_search(p, "alfa")
    assert _matches_search(p, "finanzas")
    assert _matches_search(p, "web")
    assert _matches_search(p, "pablo/alfa")
    assert not _matches_search(p, "inexistente")


def test_la_busqueda_exige_todas_las_palabras_y_no_distingue_mayusculas():
    p = Project(name="Alfa", description="panel de finanzas")
    assert _matches_search(p, "ALFA panel")
    assert not _matches_search(p, "alfa inexistente")


def test_busqueda_vacia_no_filtra():
    assert _matches_search(Project(name="x"), "")


def test_la_busqueda_llega_a_la_pagina(client, catalogo):
    texto = client.get("/?q=gamma").text
    assert "gamma" in texto
    assert ">alfa<" not in texto


# --------------------------------------------------------------------------
# Filtros combinables
# --------------------------------------------------------------------------

def test_filtros_se_combinan_en_and(client, catalogo):
    """gamma tiene CI en rojo y cambios sin commitear; delta solo PR estancado."""
    texto = client.get("/?filtro=ci-rojo&filtro=cambios").text
    assert "gamma" in texto
    assert "delta" not in texto


def test_filtro_de_pr_estancado(client, catalogo):
    texto = client.get("/?filtro=pr-estancado").text
    assert "delta" in texto
    assert "gamma" not in texto


def test_filtro_de_ruta_perdida(client, catalogo):
    texto = client.get("/?filtro=ruta-perdida").text
    assert "epsilon" in texto
    assert "alfa" not in texto


def test_un_filtro_desconocido_se_ignora_en_vez_de_romper(client, catalogo):
    respuesta = client.get("/?filtro=inventado")
    assert respuesta.status_code == 200
    assert "alfa" in respuesta.text


def test_la_busqueda_se_combina_con_el_filtro(client, catalogo):
    """gamma no tiene PR estancado, así que la intersección es vacía."""
    texto = client.get("/lista?q=gamma&filtro=pr-estancado").text
    assert "Ningún proyecto cumple" in texto
    assert "project-card" not in texto


# --------------------------------------------------------------------------
# Orden
# --------------------------------------------------------------------------

def test_orden_por_actividad_pone_los_recientes_primero(client, catalogo):
    texto = client.get("/?orden=commit").text
    assert texto.index(">alfa<") < texto.index(">gamma<") < texto.index(">delta<")


def test_orden_por_estrellas(db, client):
    """El orden actúa DENTRO de cada grupo: la agrupación por estado va primero."""
    db.add_all([
        Project(name="pocas", stars=5, last_commit_date=utcnow()),
        Project(name="muchas", stars=50, last_commit_date=utcnow()),
    ])
    db.commit()
    texto = client.get("/?orden=estrellas").text
    assert texto.index(">muchas<") < texto.index(">pocas<")


def test_la_agrupacion_por_estado_manda_sobre_el_orden(client, catalogo):
    """beta tiene más estrellas que alfa, pero está parado y baja al grupo de parados."""
    texto = client.get("/?orden=estrellas").text
    assert texto.index(">alfa<") < texto.index(">beta<")


def test_un_orden_desconocido_cae_en_el_por_defecto(client, catalogo):
    assert client.get("/?orden=inventado").status_code == 200


def test_los_proyectos_sin_fecha_de_commit_no_rompen_el_orden(db, client):
    """Sin centinela, comparar None con int reventaba la vista entera."""
    db.add_all([Project(name="sinfecha"), Project(name="confecha", last_commit_date=utcnow())])
    db.commit()
    respuesta = client.get("/?orden=commit")
    assert respuesta.status_code == 200
    assert _staleness(Project(name="x")) > _staleness(Project(name="y", last_commit_date=utcnow()))


# --------------------------------------------------------------------------
# Vistas
# --------------------------------------------------------------------------

def test_vista_tabla_renderiza_una_tabla(client, catalogo):
    texto = client.get("/?vista=tabla").text
    assert "table" in texto and "alfa" in texto


def test_el_fragmento_de_lista_no_trae_la_pagina_entera(client, catalogo):
    """Es lo que pide HTMX: solo la lista, sin cabecera ni barra de control."""
    texto = client.get("/lista?q=alfa").text
    assert "alfa" in texto
    assert "<html" not in texto
    assert 'id="controls"' not in texto


def test_el_recuento_refleja_lo_filtrado(client, catalogo):
    assert "1 de 5 proyectos" in client.get("/lista?q=alfa").text
    assert "5 de 5 proyectos" in client.get("/lista").text


def test_estado_lista_los_jobs_y_la_configuracion(client):
    texto = client.get("/estado").text
    assert "Descubrimiento de proyectos" in texto
    assert "Sincronización remota (API)" in texto
    assert "Carpeta escaneada" in texto


def test_estado_muestra_los_proyectos_con_error(client, catalogo):
    texto = client.get("/estado").text
    assert "epsilon" in texto
    assert "La ruta local ya no existe" in texto


def test_tv_solo_saca_lo_que_necesita_atencion(client, catalogo):
    texto = client.get("/tv").text
    assert "gamma" in texto      # CI en rojo
    assert "beta" in texto       # parado
    assert "delta" in texto      # PR estancado
    assert ">alfa<" not in texto  # sano: no sale


def test_tv_sin_incidencias_dice_que_todo_va_bien(db, client):
    db.add(Project(name="sano", last_commit_date=utcnow()))
    db.commit()
    assert "Todo en orden" in client.get("/tv").text


def test_tv_ignora_los_archivados(db, client):
    db.add(Project(name="jubilado", is_archived=True, ci_status="failure"))
    db.commit()
    assert "jubilado" not in client.get("/tv").text
