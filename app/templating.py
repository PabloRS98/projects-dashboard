"""Instancia compartida de Jinja2Templates con filtros personalizados."""
import json
from datetime import datetime, timezone

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["tojson"] = lambda value: json.dumps(value, default=str)


def timeago(value: datetime | None) -> str:
    """'hace 5 min', 'hace 3 h', 'hace 2 días' o la fecha si es antiguo."""
    if not value:
        return "-"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
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
