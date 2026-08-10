"""Ayuda para loguear fallos de red sin filtrar credenciales.

El token del bot de Telegram va en la RUTA de la URL
(`https://api.telegram.org/bot<TOKEN>/sendMessage`). Cuando la petición falla,
`httpx.Response.raise_for_status()` lanza una excepción cuyo mensaje incluye la
URL COMPLETA, token incluido:

    Client error '401 Unauthorized' for url
    'https://api.telegram.org/bot123456:SUPER_SECRETO/sendMessage'

`logger.exception(...)` vuelca esa excepción tal cual al log, y con
`docker-compose.yml` usando el driver `json-file`, ese log persiste en disco y
acaba fácilmente pegado en un issue de GitHub. Esta función registra solo el
código HTTP (o el tipo de excepción si no hay respuesta, p. ej. un timeout),
nunca la excepción cruda.

Portado de `media-catalog/app/services/_logging_utils.py`, donde se escribió
contra el mismo problema con las API keys de TMDB, RAWG y Google Books.
"""
import logging


def log_fallo_api(logger: logging.Logger, mensaje: str, *args, exc: Exception) -> None:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    sufijo = " (HTTP %s)" % status if status else " (%s)" % type(exc).__name__
    logger.warning(mensaje + sufijo, *args)
