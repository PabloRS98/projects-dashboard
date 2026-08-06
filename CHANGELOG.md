# Registro de cambios

Cada entrada lleva el ID del hallazgo de la auditoría del 6 de agosto de 2026
(`Auditorias/03-projects-dashboard.md`) que cierra, para poder ir del cambio al
razonamiento completo sin buscarlo.

## Sin publicar

### Corrección

- **[PD-A4]** `add_task` comprueba que el proyecto existe antes de crear la
  tarea. La comprobación vivía en `_tasks_fragment`, que corre al final, así que
  la fila ya estaba creada y commiteada colgando de un `project_id` inexistente.
  Se llega con dos pestañas abiertas: se borra el proyecto en una y se añade una
  tarea en la otra. Además, `TaskItem.project_id` pasa a `ondelete="CASCADE"` y
  el arranque barre las tareas huérfanas que dejó el fallo, que no se limpiaban
  solas porque SQLite no aplica las claves foráneas sin el PRAGMA — y como
  reasigna los ids, un proyecto nuevo podía heredar las tareas de uno borrado.

### Seguridad

- **[PD-A1]** El filtro `tojson` de las plantillas vuelve a escapar. Se
  sobrescribía con un `json.dumps` crudo para poder serializar fechas, y de paso
  se perdía el escapado de `<`, `>`, `&` y `'` que hace el nativo de Jinja. Hoy
  ninguna plantilla usaba el filtro, así que no había vulnerabilidad — pero la
  primera gráfica que incrustara datos en un `<script>` habría abierto un XSS
  almacenado desde el nombre o la descripción de un proyecto, que se
  autorrellenan desde la API de GitHub. Ahora usa `htmlsafe_json_dumps`
  conservando `default=str`, y `tests/test_templating.py` fija la propiedad.
