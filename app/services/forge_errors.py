"""Traducción de fallos HTTP a un diagnóstico accionable, común a los tres forges.

Los clientes capturaban cualquier excepción y devolvían siempre la misma frase
("no se pudo conectar, revisa el nombre o el token"). Con eso, un token caducado,
un repo privado sin permiso, una cuota agotada y un corte de red se veían igual en
la tarjeta, y no había forma de saber qué mirar. Aquí se distinguen, porque la
acción que tiene que tomar el usuario es distinta en cada caso.
"""
from datetime import UTC, datetime

import httpx

# Cabeceras de cuota: GitHub y GitLab usan las X-RateLimit-*; Bitbucket no las manda.
REMAINING_HEADERS = ("X-RateLimit-Remaining", "RateLimit-Remaining")
RESET_HEADERS = ("X-RateLimit-Reset", "RateLimit-Reset")


def _first_header(response: httpx.Response, names) -> str | None:
    for name in names:
        value = response.headers.get(name)
        if value is not None:
            return value
    return None


def _reset_clock(response: httpx.Response) -> str | None:
    """Hora (UTC) a la que se repone la cuota, formateada, o None."""
    raw = _first_header(response, RESET_HEADERS)
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw), UTC).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return None


def is_quota_error(response: httpx.Response) -> bool:
    status = response.status_code
    if status == 429:
        return True
    return status == 403 and _first_header(response, REMAINING_HEADERS) == "0"


def describe(exc: Exception, forge: str, token_var: str, has_token: bool) -> str:
    """Mensaje para el usuario a partir de la excepción de httpx.

    `forge` es el nombre visible ("GitHub"), `token_var` la variable de entorno que
    tendría que revisar y `has_token` si hay alguna configurada: sin token, un 404
    casi siempre significa "es privado", y con token significa "el nombre está mal
    o al token le falta alcance". Son arreglos distintos.
    """
    if isinstance(exc, httpx.TimeoutException):
        return "%s no respondió a tiempo" % forge
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        status = response.status_code
        if status == 401:
            return "%s inválido o caducado (401)" % token_var
        if is_quota_error(response):
            clock = _reset_clock(response)
            if clock:
                return "Cuota de la API de %s agotada (se repone a las %s UTC)" % (forge, clock)
            return "Cuota de la API de %s agotada" % forge
        if status == 403:
            return "%s deniega el acceso (403): %s no tiene permiso sobre este repo" % (forge, token_var)
        if status == 404:
            if not has_token:
                return "Repo no encontrado (404): si es privado, hace falta %s" % token_var
            return "Repo no encontrado (404): revisa 'owner/repo' o el alcance de %s" % token_var
        if 500 <= status < 600:
            return "%s está caído o con problemas (%d)" % (forge, status)
        return "%s respondió %d" % (forge, status)
    if isinstance(exc, httpx.RequestError):
        return "No se pudo conectar con %s (red)" % forge
    return "Fallo inesperado consultando %s" % forge
