"""Avisos por Telegram (opcional). Si no hay token/chat configurados, no hace nada.

El usuario crea un bot con @BotFather, mete su token en TELEGRAM_BOT_TOKEN y el
id de su chat en TELEGRAM_CHAT_ID (se lo da @userinfobot, por ejemplo)."""
import html
import logging

import httpx

from ..config import settings
from ._logging_utils import log_fallo_api

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def esc(valor: str) -> str:
    """Escapa un dato para meterlo en el HTML de los mensajes.

    Los mensajes se envían con `parse_mode: "HTML"`, así que un nombre con `&`,
    `<` o `>` produce `400 Bad Request: can't parse entities` y el aviso no llega
    nunca. No es un caso raro: GitHub admite `&` en el nombre de un repositorio y
    una carpeta local puede llamarse como sea.

    `quote=False` a propósito: Telegram solo decodifica `&lt;`, `&gt;`, `&amp;` y
    `&quot;`, no las entidades numéricas, así que escapar el apóstrofo dejaría
    "Pablo&#x27;s repo" tal cual en el mensaje.
    """
    return html.escape(valor, quote=False)


def send_message(text: str) -> bool:
    """Envía un mensaje al chat configurado. Devuelve True si se envió."""
    if not is_configured():
        return False
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001  un aviso que falla no puede tumbar el job
        # Sin `logger.exception`: el token va en la ruta de la URL y el mensaje
        # de la excepción la incluye entera.
        log_fallo_api(logger, "Fallo al enviar aviso de Telegram", exc=exc)
        return False
