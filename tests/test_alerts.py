"""Avisos de Telegram: condiciones y, sobre todo, que no se repitan.

El riesgo real de estos avisos no es que no lleguen, sino que lleguen cada hora
para la misma incidencia hasta que uno silencia el bot. Por eso la mayoría de
estas pruebas van sobre los flags de deduplicación.
"""
from datetime import timedelta

import pytest

from app.models import Project, utcnow
from app.services import alerts, telegram


@pytest.fixture
def enviados(monkeypatch):
    """Telegram configurado y con los mensajes recogidos en una lista."""
    mensajes: list[str] = []
    monkeypatch.setattr(telegram, "is_configured", lambda: True)
    monkeypatch.setattr(telegram, "send_message", lambda texto: mensajes.append(texto) or True)
    return mensajes


def _viejo(dias: int):
    return utcnow() - timedelta(days=dias)


# --------------------------------------------------------------------------
# Sin Telegram configurado
# --------------------------------------------------------------------------

def test_sin_telegram_no_avisa_ni_marca_los_flags(db, monkeypatch):
    """Los flags no se tocan: al configurar el bot después, el estado actual se avisa."""
    monkeypatch.setattr(telegram, "is_configured", lambda: False)
    p = Project(name="roto", ci_status="failure")
    db.add(p)
    db.commit()

    assert alerts.check_alerts(db) == 0
    assert p.ci_notified is False


# --------------------------------------------------------------------------
# CI en rojo
# --------------------------------------------------------------------------

def test_avisa_de_ci_en_rojo_una_sola_vez(db, enviados):
    p = Project(name="roto", ci_status="failure")
    db.add(p)
    db.commit()

    assert alerts.check_alerts(db) == 1
    assert alerts.check_alerts(db) == 0        # segunda pasada: ya avisado
    assert len(enviados) == 1
    assert "roto" in enviados[0]


def test_ci_en_verde_rearma_el_aviso(db, enviados):
    p = Project(name="roto", ci_status="failure")
    db.add(p)
    db.commit()
    alerts.check_alerts(db)

    p.ci_status = "success"
    db.commit()
    alerts.check_alerts(db)
    assert p.ci_notified is False

    # Vuelve a romperse: tiene que avisar otra vez.
    p.ci_status = "failure"
    db.commit()
    assert alerts.check_alerts(db) == 1


# --------------------------------------------------------------------------
# PR estancado y proyecto parado
# --------------------------------------------------------------------------

def test_avisa_de_pr_estancado_y_deja_de_hacerlo_al_cerrarse(db, enviados):
    p = Project(name="conpr", oldest_open_pr_days=30)
    db.add(p)
    db.commit()

    assert alerts.check_alerts(db) == 1
    assert "30" in enviados[0]

    p.oldest_open_pr_days = None   # PR cerrado
    db.commit()
    alerts.check_alerts(db)
    assert p.pr_stale_notified is False


def test_avisa_de_proyecto_parado(db, enviados):
    db.add(Project(name="dormido", last_commit_date=_viejo(90)))
    db.commit()
    assert alerts.check_alerts(db) == 1
    assert "dormido" in enviados[0]


def test_un_proyecto_archivado_no_se_considera_parado(db, enviados):
    db.add(Project(name="viejo", is_archived=True, last_commit_date=_viejo(400)))
    db.commit()
    assert alerts.check_alerts(db) == 0


def test_proyecto_sin_fecha_de_commit_no_dispara_aviso(db, enviados):
    """Un proyecto recién dado de alta no tiene datos: no es lo mismo que estar parado."""
    db.add(Project(name="nuevo"))
    db.commit()
    assert alerts.check_alerts(db) == 0


# --------------------------------------------------------------------------
# Resumen diario
# --------------------------------------------------------------------------

def test_resumen_diario_resume_el_estado(db, enviados):
    db.add_all([
        Project(name="a", open_prs=2, has_uncommitted_changes=True),
        Project(name="dormido", last_commit_date=_viejo(90)),
        Project(name="roto", remote_error="401"),
    ])
    db.commit()

    assert alerts.daily_summary(db) is True
    texto = enviados[-1]
    assert "3 proyectos" in texto
    assert "dormido" in texto
    assert "1 con error" in texto


def test_resumen_diario_no_se_envia_sin_proyectos(db, enviados):
    assert alerts.daily_summary(db) is False
    assert enviados == []
