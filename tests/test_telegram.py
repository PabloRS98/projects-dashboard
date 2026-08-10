"""Cliente de Telegram: escapado del HTML y fuga del token en los logs.

Ver [PD-A6]. El token va en la RUTA de la URL, así que cualquier cosa que
registre la excepción cruda de httpx lo vuelca al log del contenedor, que con el
driver `json-file` persiste en disco.
"""
import logging

import httpx
import pytest

from app.services import telegram


@pytest.fixture
def configurado(monkeypatch):
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "123456:SECRETO-DE-VERDAD")
    monkeypatch.setattr(telegram.settings, "telegram_chat_id", "42")


# --------------------------------------------------------------------------
# Escapado
# --------------------------------------------------------------------------

@pytest.mark.parametrize("crudo,esperado", [
    ("foo&bar", "foo&amp;bar"),
    ("<script>", "&lt;script&gt;"),
    ("a & b < c", "a &amp; b &lt; c"),
])
def test_esc_escapa_lo_que_rompe_el_parse_mode_html(crudo, esperado):
    assert telegram.esc(crudo) == esperado


def test_esc_no_toca_el_apostrofo():
    """quote=False a propósito: Telegram no decodifica las entidades numéricas,
    así que escaparlo dejaría "Pablo&#x27;s repo" tal cual en el mensaje."""
    assert telegram.esc("Pablo's repo") == "Pablo's repo"


# --------------------------------------------------------------------------
# El token no puede acabar en el log
# --------------------------------------------------------------------------

def test_el_log_de_telegram_no_contiene_el_token(configurado, monkeypatch, caplog):
    """`raise_for_status()` lanza una excepción cuyo mensaje lleva la URL entera."""
    def _post_que_falla(*args, **kwargs):
        peticion = httpx.Request("POST", args[0] if args else kwargs["url"])
        respuesta = httpx.Response(401, request=peticion)
        respuesta.raise_for_status()

    monkeypatch.setattr(httpx, "post", _post_que_falla)

    with caplog.at_level(logging.DEBUG):
        assert telegram.send_message("hola") is False

    assert "SECRETO-DE-VERDAD" not in caplog.text
    assert "401" in caplog.text          # el diagnóstico útil sí se conserva


def test_un_timeout_se_registra_sin_traceback(configurado, monkeypatch, caplog):
    def _post_que_expira(*args, **kwargs):
        raise httpx.ConnectTimeout("agotado")

    monkeypatch.setattr(httpx, "post", _post_que_expira)

    with caplog.at_level(logging.DEBUG):
        assert telegram.send_message("hola") is False

    assert "ConnectTimeout" in caplog.text
    assert "SECRETO-DE-VERDAD" not in caplog.text
