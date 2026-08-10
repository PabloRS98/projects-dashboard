# Registro de cambios

Cada entrada lleva el ID del hallazgo de la auditoría del 6 de agosto de 2026
(`Auditorias/03-projects-dashboard.md`) que cierra, para poder ir del cambio al
razonamiento completo sin buscarlo.

## Sin publicar

### Seguridad

- **[PD-A1]** El filtro `tojson` de las plantillas vuelve a escapar. Se
  sobrescribía con un `json.dumps` crudo para poder serializar fechas, y de paso
  se perdía el escapado de `<`, `>`, `&` y `'` que hace el nativo de Jinja. Hoy
  ninguna plantilla usaba el filtro, así que no había vulnerabilidad — pero la
  primera gráfica que incrustara datos en un `<script>` habría abierto un XSS
  almacenado desde el nombre o la descripción de un proyecto, que se
  autorrellenan desde la API de GitHub. Ahora usa `htmlsafe_json_dumps`
  conservando `default=str`, y `tests/test_templating.py` fija la propiedad.
