"""Control de cuota común a los tres forges.

`github_client` tenía un sistema completo —recordar la cuota de cada respuesta,
exponerla en el panel de estado y **abortar el ciclo entero** en cuanto llega a
cero— y los otros dos no tenían nada. GitLab.com limita a 2.000 peticiones por
minuto y Bitbucket a 1.000 por hora; cada `get_repo_info` de GitLab hace 5
peticiones y el de Bitbucket 5, así que con `REMOTE_WORKERS = 5` y 20 proyectos
de GitLab son 100 peticiones en segundos.

`forge_errors.is_quota_error` ya sabía detectar el 429 o el 403 con
`RateLimit-Remaining: 0` y traducirlo bien. Lo que faltaba era que alguien
cortara: se seguían gastando peticiones fallidas y cada proyecto acababa con su
`remote_error` puesto por la misma causa.

Esto es refactorización, no funcionalidad nueva: el mecanismo ya existía y estaba
probado, solo estaba en un sitio de tres.
"""
import threading
from datetime import UTC, datetime

import httpx

from . import forge_errors

# Estado por proveedor. Es de módulo a propósito: se comparte entre los hilos del
# ThreadPoolExecutor, que es justo donde hace falta cortar a la vez para todos.
_estado: dict[str, dict] = {}
_lock = threading.Lock()


def _hueco(proveedor: str) -> dict:
    return _estado.setdefault(
        proveedor, {"remaining": None, "limit": None, "reset": None, "checked_at": None}
    )


def estado(proveedor: str) -> dict:
    """Última cuota conocida de ese forge, para el panel de estado."""
    with _lock:
        return dict(_hueco(proveedor))


def recordar(proveedor: str, response: httpx.Response) -> None:
    """Guarda la cuota que venga en las cabeceras de la respuesta.

    La API la devuelve en cada respuesta, así que no hace falta gastar una
    petición extra en consultarla. Bitbucket no manda estas cabeceras: entonces
    no se guarda nada y el corte se apoya solo en `marcar_agotada`.
    """
    remaining = forge_errors._first_header(response, forge_errors.REMAINING_HEADERS)
    if remaining is None:
        return
    reset = forge_errors._first_header(response, forge_errors.RESET_HEADERS)
    limite = forge_errors._first_header(
        response, ("X-RateLimit-Limit", "RateLimit-Limit")
    )
    try:
        with _lock:
            datos = _hueco(proveedor)
            datos["remaining"] = int(remaining)
            datos["limit"] = int(limite or 0) or None
            datos["reset"] = (
                datetime.fromtimestamp(int(reset), UTC).replace(tzinfo=None) if reset else None
            )
            datos["checked_at"] = datetime.now(UTC).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        pass


def marcar_agotada(proveedor: str, response: httpx.Response | None = None) -> None:
    """Marca la cuota como agotada aunque el forge no mande cabeceras.

    Bitbucket devuelve 429 sin `RateLimit-*`, así que sin esto no habría forma de
    saber que hay que parar. Cuando no dice cuándo se repone, se asume una hora,
    que es su ventana.
    """
    with _lock:
        datos = _hueco(proveedor)
        datos["remaining"] = 0
        datos["checked_at"] = datetime.now(UTC).replace(tzinfo=None)
        if datos["reset"] is None or datos["reset"] <= datetime.now(UTC).replace(tzinfo=None):
            reset = None
            if response is not None:
                crudo = forge_errors._first_header(response, forge_errors.RESET_HEADERS)
                if crudo:
                    try:
                        reset = datetime.fromtimestamp(int(crudo), UTC).replace(tzinfo=None)
                    except (TypeError, ValueError, OSError):
                        reset = None
            if reset is None:
                from datetime import timedelta

                reset = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
            datos["reset"] = reset


def agotada(proveedor: str) -> bool:
    """True si la última respuesta dejó la cuota a cero y aún no ha llegado el reset.

    Permite abortar un ciclo de sincronización entero en cuanto el forge deja de
    responder, en vez de gastar una petición fallida por proyecto.
    """
    with _lock:
        datos = _hueco(proveedor)
        if datos["remaining"] is None or datos["remaining"] > 0:
            return False
        reset = datos["reset"]
        return bool(reset and reset > datetime.now(UTC).replace(tzinfo=None))


def mensaje(proveedor: str, forge: str) -> str:
    reset = estado(proveedor)["reset"]
    if reset:
        return "Cuota de la API de %s agotada (se repone a las %s UTC)" % (
            forge, reset.strftime("%H:%M")
        )
    return "Cuota de la API de %s agotada" % forge


def anotar_error(proveedor: str, exc: Exception) -> None:
    """Si el fallo fue por cuota, deja el proveedor marcado para que el resto del
    ciclo no lo intente."""
    respuesta = getattr(exc, "response", None)
    if respuesta is not None and forge_errors.is_quota_error(respuesta):
        marcar_agotada(proveedor, respuesta)


def reiniciar() -> None:
    """Olvida todo. Solo para las pruebas."""
    with _lock:
        _estado.clear()
