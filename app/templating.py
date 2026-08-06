"""Instancia compartida de Jinja2Templates con filtros personalizados."""
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import StrictUndefined
from jinja2.utils import htmlsafe_json_dumps

# Ruta absoluta derivada del propio módulo: con una ruta relativa, la app solo
# arranca si el directorio de trabajo es la raíz del repositorio.
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# StrictUndefined: una variable ausente lanza error en vez de renderizar vacío.
# Con el comportamiento por defecto, referenciar algo que el router no pasa es
# `Undefined`, que es falsy, así que un `{% if %}` sobre ella falla en silencio
# y el bloque simplemente no se pinta: un fallo así puede pasar meses sin verse.
templates.env.undefined = StrictUndefined


def _tojson(value):
    """`tojson` que serializa fechas (default=str) SIN perder el escapado de Jinja.

    El filtro nativo no sabe serializar date/datetime —y las series del histórico
    van llenas—, que es por lo que aquí se sobrescribía. Pero un `json.dumps` a
    secas no escapa nada: deja pasar "</script>" y permite cerrar el bloque
    <script> desde cualquier dato guardado. Los candidatos son inmediatos y
    ninguno hace falta que lo escriba el usuario: `Project.name` sale también del
    nombre de la carpeta en disco y de la API de GitHub, y `Project.description`
    se autorrellena desde GitHub en cada sincronización.

    `htmlsafe_json_dumps` escapa `<`, `>`, `&` y `'` como `\\uXXXX` y devuelve
    `Markup`, así que el resultado ya es seguro de incrustar y el `| safe` de las
    plantillas sobra. Hoy ninguna plantilla usa el filtro; la primera gráfica que
    se añada abriría el agujero sin que nadie lo mirara, porque "es solo un
    filtro de JSON". Ver tests/test_templating.py.
    """
    return htmlsafe_json_dumps(value, dumps=lambda v, **kw: json.dumps(v, default=str, **kw))


templates.env.filters["tojson"] = _tojson


def timeago(value: datetime | None) -> str:
    """'hace 5 min', 'hace 3 h', 'hace 2 días' o la fecha si es antiguo."""
    if not value:
        return "-"
    now = datetime.now(UTC).replace(tzinfo=None)
    seconds = (now - value).total_seconds()
    if seconds < 0:
        return value.strftime("%d/%m/%Y")
    if seconds < 90:
        return "ahora mismo"
    if seconds < 3600:
        return "hace %d min" % (seconds // 60)
    if seconds < 86400:
        return "hace %d h" % (seconds // 3600)
    if seconds < 7 * 86400:
        return "hace %d días" % (seconds // 86400)
    return value.strftime("%d/%m/%Y")


templates.env.filters["timeago"] = timeago
