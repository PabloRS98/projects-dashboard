# Registro de cambios

Cada entrada lleva el ID del hallazgo de la auditoría del 6 de agosto de 2026
(`Auditorias/03-projects-dashboard.md`) que cierra, para poder ir del cambio al
razonamiento completo sin buscarlo.

## Sin publicar

### Nuevo

- **[PD-A2]** Vista de tendencias, en `/estado` (agregada de todo el panel) y en
  la ficha de cada proyecto (la suya). Cierra el hallazgo por el camino A:
  completar el subsistema en vez de retirarlo. Había un modelo `ProjectSnapshot`,
  un job diario, poda a 400 días, cascada de borrado y su suite de tests, y
  **ninguna vista leía nada**: `series()` solo se invocaba desde sus propios
  tests. Ahora se pintan cuatro métricas —commits, PRs, issues y TODOs— con la
  cifra de hoy, cuánto ha cambiado y desde cuándo. Sin librerías de gráficos: una
  polilínea SVG, igual que el `sparkline` que ya existía.

  Con menos de dos puntos no se dibuja nada y se dice por qué: una recta
  horizontal parecería un dato estable cuando lo que pasa es que el histórico
  todavía está vacío. El delta cuenta **los días que hay de dato**, no la ventana
  pedida, porque "−195 en 90 días" con dos semanas de histórico es falso.

### Rendimiento

- **[PD-M3]** Un solo `<dialog>` de edición para toda la lista, con el
  formulario cargado por HTMX al abrirlo. Se renderizaba uno completo por
  proyecto: con 30 proyectos, el HTML de `/lista` pasa de 335 KB a **265 KB** y
  de 270 elementos de formulario a **60**. Se pagaba también en cada pulsación
  del buscador. De paso, el formulario refleja ahora el estado actual del
  proyecto y no el que tenía cuando se pintó la página. El comentario original
  justificaba que los diálogos viajaran *con* la lista —y tenía razón—, pero no
  que fueran N; el nuevo sigue viajando con ella.

- **[PD-M1]**, **[PD-M2]** y **[PD-M18]** El camino del dashboard pasa de 41
  consultas SQL por petición a **2**, y de 54 ms a 43 ms. `Project.tasks` era
  lazy y cada tarjeta la tocaba dos veces, así que eran 1 + N consultas por
  carga — y otras 1 + N por cada pulsación en el buscador, que refresca cada
  300 ms. Se resuelve con `selectinload`. Además `_summary` recorre la lista una
  vez en lugar de once, y como `_view_context` lo llama dos veces por petición,
  eran 22 recorridos. Y queda escrito por qué el filtrado se hace en Python y
  cuándo habría que revisarlo, que antes no estaba en ninguna parte.

- **[PD-M9]** `scan_todos` deja de recorrer el árbol sin límites: se salta los
  ficheros de más de 1 MB (un bundle minificado va en una sola línea, así que el
  iterador de líneas lo carga entero en memoria), amplía los directorios
  ignorados con los de artefactos de otros ecosistemas (`target`, `vendor`,
  `.next`, `coverage`…), y para al llegar a 20.000 ficheros o 20 segundos
  marcando el resultado como parcial. Y cuenta **palabras completas**: `TODO`
  suelto casaba dentro de `TODOS_LOS_USUARIOS`, `METODOS` o `TODO_EXTENSIONS`,
  hasta el punto de que este propio repositorio se contaba a sí mismo. Medido
  sobre los repos reales, el recuento de `projects-dashboard` pasa de 46 TODOs a
  19: los 27 de diferencia eran falsos positivos.

- **[PD-M13]** Sincronizar un proyecto concreto pasa a segundo plano, como ya
  hacía "Sincronizar todo". Era el botón que más se pulsa y el único que dejaba
  el navegador colgado hasta 40 s. La justificación de que fuera síncrono era
  devolver el error concreto en el flash, pero no hace falta: `sync_local` y
  `sync_remote` ya persisten el error en el proyecto y la tarjeta lo pinta.

- **[PD-A3]** y **[PD-M17]** El descubrimiento sale de la petición y deja de
  duplicar el trabajo de los ciclos de sincronización. `/escanear` era la
  operación más cara de la app —recorrido del disco, `git` por repo, conteo de
  TODOs y hasta cinco páginas de la API de GitHub— y corría entera dentro del
  POST: en la primera ejecución sobre una cuenta con treinta repos, minutos de
  pantalla en blanco, justo cuando más se usa el botón. Ahora se encola y el
  resultado aparece en `/estado`, que ya mostraba el job `discovery`.

  Además, el descubrimiento ya no llama a `sync_remote`: `scheduler.py` lo
  programa deliberadamente antes que los dos ciclos de sync para que sean ellos
  los que sincronicen, así que hacerlo aquí eran 4 peticiones HTTP por repo
  nuevo repetidas. Se conserva `sync_local`, que es solo disco. Y se commitea
  por proyecto en vez de una sola vez al final, para que una excepción a mitad
  del bucle no tire todo el trabajo hecho. Guarda de concurrencia para que el
  botón y el job periódico no se solapen.

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

### Infraestructura

- **[PD-A9]** *(parcial)* Se documenta en el README el procedimiento de cifrado
  y descifrado del `.env` con SOPS, y por qué el respaldo de la clave `age`
  importa más que el propio cifrado. **El cifrado en sí queda pendiente**: en la
  máquina de desarrollo no están instalados `sops` ni `age`, y la clave privada
  de `../.sops.yaml` no aparece en ninguna de las rutas estándar. Cifrar sin
  tener localizada la clave produciría un `.env.enc` que nadie puede abrir.

- **[PD-A8]** Se añade `.dockerignore`, que no existía. El `Dockerfile` solo
  copia `requirements.txt` y `app/`, así que nada de lo excluido llegaba a la
  imagen — pero el directorio entero viajaba al daemon en cada build y se
  guardaba en la caché de capas, incluidos el `.env` con el token de GitHub y
  las bases de datos. Y el día que alguien escriba un `COPY . .`, esos secretos
  acaban dentro de una imagen que además se construye en CI. Contexto medido:
  de 5.095 ficheros y 110 MB a **50 ficheros y 0,3 MB**.

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
