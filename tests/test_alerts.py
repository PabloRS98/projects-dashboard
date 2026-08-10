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


@pytest.fixture
def envios_fallidos(monkeypatch):
    """Telegram configurado pero con todos los envíos fallando.

    Es lo que pasa de verdad cuando el mensaje lleva HTML inválido: Telegram
    responde `400 Bad Request: can't parse entities` y `send_message` devuelve
    False.
    """
    intentos: list[str] = []
    monkeypatch.setattr(telegram, "is_configured", lambda: True)
    monkeypatch.setattr(telegram, "send_message", lambda texto: intentos.append(texto) and False)
    return intentos


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


# --------------------------------------------------------------------------
# Escapado del HTML: los mensajes van con parse_mode HTML. Ver [PD-A6].
# --------------------------------------------------------------------------

def test_el_aviso_de_ci_escapa_el_nombre_del_proyecto(db, enviados):
    """`&` en el nombre es válido en GitHub y en una carpeta local, y rompe el
    parse_mode HTML de Telegram."""
    db.add(Project(name="foo&bar", ci_status="failure"))
    db.commit()

    alerts.check_alerts(db)
    assert "foo&amp;bar" in enviados[0]


def test_un_estado_de_ci_desconocido_no_dispara_aviso(db, enviados):
    """`ci_status` se escapa igual que el nombre, pero por esta vía no puede
    colarse HTML: el aviso solo salta si el valor está en el conjunto cerrado
    `CI_BAD`, que lo llenan los clientes de forge. El escapado está por si un
    proveedor nuevo devolviera texto libre."""
    db.add(Project(name="x", ci_status="<b>failure"))
    db.commit()

    assert alerts.check_alerts(db) == 0
    assert enviados == []


def test_el_aviso_de_pr_estancado_escapa_el_nombre(db, enviados):
    db.add(Project(name="a<b>c", oldest_open_pr_days=30))
    db.commit()

    alerts.check_alerts(db)
    assert "a&lt;b&gt;c" in enviados[0]


def test_el_aviso_de_parado_escapa_el_nombre(db, enviados):
    db.add(Project(name="dormido & co", last_commit_date=_viejo(90)))
    db.commit()

    alerts.check_alerts(db)
    assert "dormido &amp; co" in enviados[0]


def test_el_resumen_diario_escapa_los_nombres(db, enviados):
    db.add(Project(name="R&D", last_commit_date=_viejo(90)))
    db.commit()

    alerts.daily_summary(db)
    assert "R&amp;D" in enviados[-1]


# --------------------------------------------------------------------------
# El flag de dedup solo se marca si el envío funcionó. Ver [PD-A6].
# --------------------------------------------------------------------------

def test_el_flag_de_ci_no_se_marca_si_el_envio_falla(db, envios_fallidos):
    """Marcarlo igualmente dejaba el aviso silenciado hasta que la condición se
    rearmara: un proyecto podía no avisar nunca de su CI en rojo."""
    p = Project(name="roto", ci_status="failure")
    db.add(p)
    db.commit()

    assert alerts.check_alerts(db) == 0
    assert p.ci_notified is False

    # Siguiente ciclo: tiene que reintentarlo.
    alerts.check_alerts(db)
    assert len(envios_fallidos) == 2


def test_el_flag_de_pr_estancado_no_se_marca_si_el_envio_falla(db, envios_fallidos):
    p = Project(name="conpr", oldest_open_pr_days=30)
    db.add(p)
    db.commit()

    alerts.check_alerts(db)
    assert p.pr_stale_notified is False


def test_el_flag_de_parado_no_se_marca_si_el_envio_falla(db, envios_fallidos):
    p = Project(name="dormido", last_commit_date=_viejo(90))
    db.add(p)
    db.commit()

    alerts.check_alerts(db)
    assert p.stale_notified is False
