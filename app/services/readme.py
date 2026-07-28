"""Renderizado del README a HTML para la ficha del proyecto.

Los repos son del propio usuario y la app es mono-usuario en LAN, pero como se
pueden clonar repos de terceros se hace una limpieza defensiva: se quitan
<script>/<style>/<iframe>, los manejadores on*= y las URLs javascript:.
"""
import re

import markdown as md

_STRIP_TAGS = re.compile(r"<(script|style|iframe|object|embed)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_ON_HANDLERS = re.compile(r"""\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)


def render(text: str) -> str:
    """Markdown -> HTML saneado (headings, código, tablas, listas)."""
    html = md.markdown(text or "", extensions=["fenced_code", "tables", "sane_lists"])
    html = _STRIP_TAGS.sub("", html)
    html = _ON_HANDLERS.sub("", html)
    html = re.sub(r"javascript:", "", html, flags=re.IGNORECASE)
    return html
