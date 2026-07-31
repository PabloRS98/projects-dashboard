"""Renderizado del README a HTML para la ficha del proyecto.

Los README pueden venir de repos de terceros clonados, así que el HTML se sanea
con nh3 (binding de ammonia) por LISTA BLANCA: solo sobreviven las etiquetas y
atributos enumerados aquí, y los esquemas de URL permitidos.

La versión anterior filtraba por lista negra con expresiones regulares y era
evitable de varias formas (`javajavascript:script:` se reconstruía al eliminar
la coincidencia interior, `<img/onerror=...>` esquivaba el `\\s` exigido antes
del manejador, y `<script src=...>` sin cierre no casaba el patrón). La lista
blanca elimina la categoría entera en lugar de tapar cada agujero.
"""
import re
from urllib.parse import urlparse

import markdown as md
import nh3

# Etiquetas que tienen sentido en un README y no ejecutan nada.
ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr", "div", "span", "blockquote", "pre", "code",
    "strong", "b", "em", "i", "u", "s", "del", "ins", "sup", "sub",
    "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption",
    "a", "img",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan", "align"},
    "th": {"colspan", "rowspan", "align", "scope"},
    # Markdown marca el lenguaje de los bloques de código como class="language-x"
    "code": {"class"},
    "pre": {"class"},
    "span": {"class"},
    "div": {"class"},
}

# Sin "javascript", obviamente; tampoco "data" (data:text/html ejecuta script).
ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}

# Dónde vive el contenido crudo de cada forge, para reconstruir las rutas
# relativas de las imágenes.
RAW_BASES = {
    "github.com": "https://raw.githubusercontent.com/%s/%s/%s",
    "gitlab.com": "https://gitlab.com/%s/-/raw/%s/%s",
    "bitbucket.org": "https://bitbucket.org/%s/raw/%s/%s",
}


def _raw_base(repo_url: str | None, branch: str | None) -> tuple[str, str, str] | None:
    """(plantilla, 'owner/repo', rama) del forge, o None si no se puede deducir."""
    if not repo_url:
        return None
    parsed = urlparse(repo_url)
    template = RAW_BASES.get((parsed.hostname or "").lower())
    if not template:
        return None
    owner_repo = parsed.path.strip("/")
    if not owner_repo:
        return None
    return template, owner_repo, branch or "HEAD"


def absolutize(html: str, repo_url: str | None, branch: str | None) -> str:
    """Convierte los `src` relativos de las imágenes en URLs al contenido crudo del repo.

    Un README que enseña capturas las referencia como `docs/captura.png`, relativo
    a la raíz del repositorio. Servido desde esta app esa ruta no existe, así que
    las imágenes salían todas rotas (solo se veían las de badges, que ya venían
    con URL absoluta). Aquí se reescriben apuntando al forge.

    Se hace ANTES del saneado, para que lo que se inyecta pase también por nh3.
    """
    base = _raw_base(repo_url, branch)
    if not base:
        return html
    template, owner_repo, ref = base

    def _fix(match) -> str:
        prefix, url, suffix = match.group(1), match.group(2), match.group(3)
        # Absolutas, protocolo-relativas y anclas se dejan como están.
        if not url or "://" in url or url.startswith(("//", "#", "data:", "mailto:")):
            return match.group(0)
        return "%s%s%s" % (prefix, template % (owner_repo, ref, url.lstrip("./")), suffix)

    return re.sub(r'(<img\b[^>]*?\bsrc=")([^"]*)(")', _fix, html, flags=re.IGNORECASE)


def render(text: str, repo_url: str | None = None, branch: str | None = None) -> str:
    """Markdown -> HTML saneado por lista blanca."""
    html = md.markdown(text or "", extensions=["fenced_code", "tables", "sane_lists"])
    html = absolutize(html, repo_url, branch)
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
    )
