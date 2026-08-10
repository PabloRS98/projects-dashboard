"""Hallazgos BAJOS con consecuencia observable. Ver [PD-B3], [PD-B7], [PD-B8], [PD-B9].

De los once BAJOS, estos son los que se pueden fijar por test. El resto son
comentarios, renombrados y niveles de log, y se verifican leyendo.
"""
import pytest

from app.models import CI_BAD, CI_GOOD, Project

# --------------------------------------------------------------------------
# [PD-B7] CI_BAD estaba duplicado en dos módulos
# --------------------------------------------------------------------------

def test_los_estados_de_ci_viven_en_un_solo_sitio():
    from app.routers import projects as router
    from app.services import alerts

    assert router.CI_BAD is CI_BAD
    assert alerts.CI_BAD is CI_BAD
    assert alerts.CI_GOOD is CI_GOOD


def test_los_dos_conjuntos_no_se_solapan():
    assert not (CI_BAD & CI_GOOD)


# --------------------------------------------------------------------------
# [PD-B8] El punto de CI de la ficha usaba un conjunto distinto al del macro
# --------------------------------------------------------------------------

@pytest.mark.parametrize("estado", sorted(CI_BAD))
def test_la_ficha_pinta_en_rojo_todos_los_estados_malos(client, db, estado):
    """`detail.html` tenía su propia versión escrita a mano, sin `cancelled` ni
    `timed_out`: un pipeline cancelado salía en gris en la ficha y en rojo en la
    tarjeta, para el mismo proyecto."""
    p = Project(name="roto-%s" % estado, ci_status=estado)
    db.add(p)
    db.commit()

    texto = client.get("/proyecto/%d" % p.id).text
    assert 'ci-dot bad' in texto


@pytest.mark.parametrize("estado", sorted(CI_GOOD))
def test_la_ficha_pinta_en_verde_todos_los_estados_buenos(client, db, estado):
    p = Project(name="ok-%s" % estado, ci_status=estado)
    db.add(p)
    db.commit()
    assert "ci-dot ok" in client.get("/proyecto/%d" % p.id).text


def test_la_tarjeta_y_la_ficha_coinciden(client, db):
    """El mismo proyecto no puede verse de dos colores según la pantalla."""
    p = Project(name="cancelado", ci_status="cancelled")
    db.add(p)
    db.commit()

    assert "ci-dot bad" in client.get("/").text
    assert "ci-dot bad" in client.get("/proyecto/%d" % p.id).text


# --------------------------------------------------------------------------
# [PD-B9] La clase CSS del grupo salía del título visible
# --------------------------------------------------------------------------

def test_la_clase_del_grupo_no_depende_del_texto_visible(client, db):
    """`{{ title|lower }}` funcionaba por casualidad: los cuatro nombres son
    ASCII y sin espacios. Cambiar "Parados" por "Sin actividad" habría roto el
    CSS en silencio."""
    db.add(Project(name="x", is_favorite=True))
    db.commit()

    texto = client.get("/").text
    assert "group-head favoritos" in texto      # clave estable, no el título


# --------------------------------------------------------------------------
# [PD-B3] week_counts() se deserializaba cuatro veces por ficha
# --------------------------------------------------------------------------

def test_la_ficha_deserializa_la_actividad_una_sola_vez(client, db, monkeypatch):
    import json

    p = Project(name="con actividad", commit_weeks=json.dumps([1, 2, 3]))
    db.add(p)
    db.commit()

    llamadas = []
    original = Project.week_counts

    def _contar(self):
        llamadas.append(self.id)
        return original(self)

    monkeypatch.setattr(Project, "week_counts", _contar)
    client.get("/proyecto/%d" % p.id)

    assert llamadas.count(p.id) == 1
