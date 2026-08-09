"""Vista de tendencias. Ver [PD-A2] y N1.

El subsistema de histórico estaba entero —modelo, job diario, poda, cascada y
tests— y ninguna vista lo leía: `series()` solo se invocaba desde sus propias
pruebas. Esto es lo que le faltaba para servir para algo.
"""
from datetime import date, timedelta

import pytest

from app.models import Project, ProjectSnapshot
from app.routers.projects import _tendencias
from app.services import history


@pytest.fixture
def con_historico(db):
    """Dos proyectos con 10 días de histórico, con una tendencia clara."""
    uno = Project(name="alfa")
    dos = Project(name="beta")
    db.add_all([uno, dos])
    db.commit()
    hoy = date.today()
    for i in range(10):
        dia = hoy - timedelta(days=9 - i)
        db.add(ProjectSnapshot(
            project_id=uno.id, day=dia,
            open_prs=i, open_issues=2, todo_count=40 - i * 3, commits_7d=i,
        ))
        db.add(ProjectSnapshot(
            project_id=dos.id, day=dia,
            open_prs=1, open_issues=0, todo_count=5, commits_7d=0,
        ))
    db.commit()
    return uno, dos


# --------------------------------------------------------------------------
# Serie por proyecto
# --------------------------------------------------------------------------

def test_la_serie_de_un_proyecto_solo_trae_la_suya(db, con_historico):
    uno, _ = con_historico
    puntos = history.project_series(db, uno.id, "open_prs", days=30)
    assert [valor for _, valor in puntos] == list(range(10))


def test_la_serie_de_un_proyecto_respeta_la_lista_blanca(db, con_historico):
    uno, _ = con_historico
    with pytest.raises(ValueError):
        history.project_series(db, uno.id, "notes", days=30)


def test_la_serie_de_un_proyecto_sin_historico_esta_vacia(db):
    p = Project(name="nuevo")
    db.add(p)
    db.commit()
    assert history.project_series(db, p.id, "open_prs") == []


# --------------------------------------------------------------------------
# La vista de estado
# --------------------------------------------------------------------------

def test_estado_muestra_la_tendencia(client, con_historico):
    texto = client.get("/estado").text
    assert "Tendencias" in texto
    assert "PRs abiertos" in texto
    assert "trend" in texto          # el SVG de la serie


def test_estado_dice_que_falta_historico_en_vez_de_pintar_una_recta(client, db):
    """Con 0 o 1 puntos no hay tendencia. Pintar una línea plana parecería un
    dato real, y el subsistema empieza a acumular desde cero."""
    db.add(Project(name="solo"))
    db.commit()
    texto = client.get("/estado").text
    assert "Todavía no hay histórico" in texto
    assert "<polyline" not in texto


def test_la_tendencia_dice_cuanto_ha_cambiado(db, con_historico):
    """"Los TODOs bajaron de 45 a 18" es la frase que justifica el subsistema:
    sin el delta, la gráfica es decorativa."""
    todos = next(t for t in _tendencias(db) if t["campo"] == "todo_count")
    assert todos["primero"] == 45     # alfa 40 + beta 5
    assert todos["actual"] == 18      # alfa 40-27 + beta 5
    assert todos["delta"] == -27


def test_el_delta_cuenta_los_dias_que_hay_y_no_la_ventana_pedida(db, con_historico):
    """Con 10 días de histórico, "-27 en 90 días" es falso e invita a leer la
    pendiente como trimestral."""
    todos = next(t for t in _tendencias(db) if t["campo"] == "todo_count")
    assert todos["dias"] == 9         # 10 puntos = 9 días de separación


def test_la_ficha_del_proyecto_muestra_su_evolucion(client, con_historico):
    uno, _ = con_historico
    texto = client.get("/proyecto/%d" % uno.id).text
    assert "Evolución" in texto
    assert "trend" in texto


def test_la_ficha_de_un_proyecto_sin_historico_no_pinta_grafica(client, db):
    p = Project(name="nuevo")
    db.add(p)
    db.commit()
    texto = client.get("/proyecto/%d" % p.id).text
    assert "<polyline" not in texto


# --------------------------------------------------------------------------
# Regresión: lo que ya existía sigue igual
# --------------------------------------------------------------------------

def test_la_serie_global_respeta_la_lista_blanca_de_campos(db):
    with pytest.raises(ValueError):
        history.series(db, "notes")


def test_la_serie_global_suma_todos_los_proyectos(db, con_historico):
    puntos = history.series(db, "open_prs", days=30)
    # alfa va de 0 a 9 y beta aporta 1 cada día.
    assert [valor for _, valor in puntos] == [i + 1 for i in range(10)]
