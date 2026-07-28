"""Instancia compartida de Jinja2Templates con filtros personalizados."""
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import StrictUndefined

# Ruta absoluta derivada del propio módulo: con una ruta relativa, la app solo
# arranca si el directorio de trabajo es la raíz del repositorio.
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# StrictUndefined: una variable ausente lanza error en vez de renderizar vacío.
# Con el comportamiento por defecto, referenciar algo que el router no pasa es
# `Undefined`, que es falsy, así que un `{% if %}` sobre ella falla en silencio
# y el bloque simplemente no se pinta: un fallo así puede pasar meses sin verse.
templates.env.undefined = StrictUndefined
templates.env.filters["tojson"] = lambda value: json.dumps(value, default=str)


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
