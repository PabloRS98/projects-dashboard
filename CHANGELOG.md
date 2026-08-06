# Registro de cambios

Cada entrada lleva el ID del hallazgo de la auditoría del 6 de agosto de 2026
(`Auditorias/03-projects-dashboard.md`) que cierra, para poder ir del cambio al
razonamiento completo sin buscarlo.

## Sin publicar

### Corrección

- **[PD-A5]** `BACKUP_KEEP=0` vuelve a significar lo que dice. `existing[:-0]`
  es `existing[:0]` —lista vacía—, no "todos", así que configurarlo a 0 no
  borraba ningún backup: exactamente lo contrario de la intención, y el disco
  llenándose. Ahora con 0 se conserva al menos la copia recién creada. Es el
  mismo bug que `media-catalog` ya había arreglado en el mismo fichero copiado;
  se trae su línea y su comentario tal cual.

- **[PD-A6]** Los avisos de Telegram escapan el nombre del proyecto, y el flag
  de deduplicación solo se marca si el envío funcionó. Los mensajes van con
  `parse_mode: "HTML"` y el nombre se interpolaba crudo: un repositorio llamado
  `foo&bar` —válido en GitHub, y el nombre puede venir también de la carpeta en
  disco— hacía que Telegram respondiera `400 can't parse entities`. El fallo era
  silencioso y, como el flag se marcaba fuera del `if`, ese proyecto no volvía a
  avisar de su CI en rojo hasta que la condición se rearmara. Además, el token
  del bot dejaba de aparecer en los logs: iba en la ruta de la URL y
  `logger.exception` volcaba el traceback entero.

- **[PD-A4]** `add_task` comprueba que el proyecto existe antes de crear la
  tarea. La comprobación vivía en `_tasks_fragment`, que corre al final, así que
  la fila ya estaba creada y commiteada colgando de un `project_id` inexistente.
  Se llega con dos pestañas abiertas: se borra el proyecto en una y se añade una
  tarea en la otra. Además, `TaskItem.project_id` pasa a `ondelete="CASCADE"` y
  el arranque barre las tareas huérfanas que dejó el fallo, que no se limpiaban
  solas porque SQLite no aplica las claves foráneas sin el PRAGMA — y como
  reasigna los ids, un proyecto nuevo podía heredar las tareas de uno borrado.

### Seguridad

- **[PD-A7]** Cabeceras de seguridad HTTP: `Content-Security-Policy`,
  `X-Content-Type-Options` y `Referrer-Policy`. La app renderiza HTML generado
  desde el Markdown de repositorios de terceros, y el saneado por lista blanca
  con `nh3` era la única capa. `img-src` se deja abierto a `https:` a propósito,
  porque los README traen badges de dominios arbitrarios; `script-src` va
  acotado a `'self'`, que es lo que corta la exfiltración.

- **[PD-A1]** El filtro `tojson` de las plantillas vuelve a escapar. Se
  sobrescribía con un `json.dumps` crudo para poder serializar fechas, y de paso
  se perdía el escapado de `<`, `>`, `&` y `'` que hace el nativo de Jinja. Hoy
  ninguna plantilla usaba el filtro, así que no había vulnerabilidad — pero la
  primera gráfica que incrustara datos en un `<script>` habría abierto un XSS
  almacenado desde el nombre o la descripción de un proyecto, que se
  autorrellenan desde la API de GitHub. Ahora usa `htmlsafe_json_dumps`
  conservando `default=str`, y `tests/test_templating.py` fija la propiedad.
