"""Mensajes flash vía cookie: el backend redirige con una cookie efímera
y app.js la muestra como toast al cargar la página siguiente."""
import json
from urllib.parse import quote

from fastapi.responses import RedirectResponse

from .security import safe_redirect_path


def redirect_flash(
    url: str, message: str, category: str = "success", status_code: int = 303
) -> RedirectResponse:
    """RedirectResponse (patrón PRG) + cookie 'flash' con el mensaje para el toast.

    El destino pasa siempre por `safe_redirect_path`: varias vistas alimentan
    `url` con la cabecera `Referer`, que es de quien envía la petición, así que
    se filtra aquí (un único punto de paso) en vez de en cada vista.
    """
    response = RedirectResponse(safe_redirect_path(url), status_code=status_code)
    response.set_cookie(
        "flash",
        quote(json.dumps({"m": message, "c": category})),
        max_age=8,
        path="/",
        samesite="lax",
    )
    return response
