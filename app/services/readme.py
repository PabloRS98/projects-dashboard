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


def render(text: str) -> str:
    """Markdown -> HTML saneado por lista blanca."""
    html = md.markdown(text or "", extensions=["fenced_code", "tables", "sane_lists"])
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
    )
