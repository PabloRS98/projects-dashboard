"""Utilidades de seguridad transversales: redirecciones seguras, validación de
URLs de usuario y protección CSRF.

El modelo de amenaza es una app mono-usuario que puede quedar expuesta en LAN o
VPN. No hay multi-tenencia, así que no hay control de acceso por recurso; lo que
sí hay que cubrir es que un sitio de terceros no pueda dirigir el navegador del
usuario contra esta app (CSRF) ni usarla como trampolín (open redirect).
"""
from urllib.parse import urlparse, urlunparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

# Métodos que no cambian estado: se dejan pasar sin comprobar origen.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

# Esquemas admitidos en enlaces que el usuario introduce (web / deploy).
SAFE_URL_SCHEMES = {"http", "https"}


def safe_redirect_path(url: str | None, fallback: str = "/") -> str:
    """Reduce `url` a una ruta interna de esta misma app.

    Las vistas usan la cabecera `Referer` para devolver al usuario a la página
    de la que venía, pero esa cabecera la controla quien envía la petición: sin
    filtrar, un `Referer: https://evil.tld/x` convertía cualquier POST en un
    open redirect. Aquí se descarta el esquema y el host y se conserva solo
    ruta + query, así que el destino siempre cae dentro del sitio.
    """
    if not url:
        return fallback
    parsed = urlparse(url)
    # Sin netloc y empezando por "/" ya es relativa. Ojo: "//evil.tld/x" es
    # protocolo-relativa y urlparse sí le asigna netloc, así que cae aquí.
    path = urlunparse(("", "", parsed.path or "/", parsed.params, parsed.query, ""))
    if not path.startswith("/") or path.startswith("//"):
        return fallback
    return path


def safe_external_url(url: str | None) -> str | None:
    """Devuelve `url` solo si es http(s) absoluta; si no, None.

    Se aplica a `homepage_url`, que acaba en un atributo href. El autoescape de
    Jinja escapa HTML pero no valida esquemas, así que un `javascript:...`
    llegaría intacto al navegador y ejecutaría al hacer clic. El campo además se
    autorrellena desde la API remota, así que no basta con validar en el cliente.
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme.lower() not in SAFE_URL_SCHEMES or not parsed.netloc:
        return None
    return url


class CSRFMiddleware(BaseHTTPMiddleware):
    """Rechaza peticiones que cambian estado cuyo origen no sea este mismo sitio.

    Todas las mutaciones van por POST con formularios y sin token, y con
    ENABLE_AUTH el navegador reenvía las credenciales HTTP Basic automáticamente
    en peticiones cross-site: sin esta comprobación, cualquier web visitada podía
    borrar proyectos. Los navegadores actuales mandan `Origin` en todo POST, así
    que comparar contra el Host cubre el caso real sin necesidad de tokens.

    Si no llega ni `Origin` ni `Referer` se rechaza: un navegador siempre manda
    al menos uno en un POST, de modo que solo afecta a clientes fuera del
    navegador (curl, scripts), que pueden añadir la cabecera si lo necesitan.
    """

    def __init__(self, app, trusted_hosts: set[str] | None = None):
        super().__init__(app)
        self.trusted_hosts = trusted_hosts or set()

    def _host_matches(self, request, candidate: str) -> bool:
        netloc = urlparse(candidate).netloc
        if not netloc:
            return False
        if netloc in self.trusted_hosts:
            return True
        # Detrás de un proxy inverso, Host lleva el nombre público que ve el
        # navegador, que es justo con el que se construye Origin.
        return netloc == request.headers.get("host", "")

    async def dispatch(self, request, call_next):
        if request.method in SAFE_METHODS:
            return await call_next(request)

        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        source = origin or referer
        if not source or not self._host_matches(request, source):
            return PlainTextResponse(
                "Origen no permitido (posible CSRF)", status_code=403
            )
        return await call_next(request)
