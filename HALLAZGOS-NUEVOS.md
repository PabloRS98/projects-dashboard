# Hallazgos nuevos

Defectos encontrados **mientras se aplicaba** la auditoría del 6 de agosto de
2026, fuera del alcance de los hallazgos que se estaban corrigiendo. Se anotan
aquí en vez de arreglarse sobre la marcha, según la regla 3 del método
(`Auditorias/00-INDICE-Y-METODO.md`): no ampliar el alcance de un PR.

---

### [PD-X1] El descubrimiento no tiene lista de exclusión de directorios

- **Severidad:** BAJO
- **Ubicación:** `app/services/local_scanner.py::discover_repos`
- **Categoría:** corrección
- **Encontrado al:** medir [PD-M9] sobre los repositorios reales

`discover_repos` recorre `LOCAL_REPOS_BASE_PATH` hasta `PROJECTS_SCAN_DEPTH`
niveles y da de alta **cualquier** carpeta que contenga un `.git`. No hay forma
de excluir nada.

Consecuencia concreta: una copia de seguridad de un repositorio hecha dentro de
la carpeta escaneada —por ejemplo `Aplicaciones Servidor/Backups/…`, que queda a
profundidad 3— se descubre como un proyecto más. El panel muestra entonces el
mismo proyecto dos veces, uno de ellos apuntando a una copia congelada, con su
propio recuento de TODOs y su propia actividad.

`scan_todos` sí tiene `IGNORED_DIRS`; `discover_repos` no tiene el equivalente.

**Corrección propuesta.** Una lista de exclusión configurable
(`PROJECTS_SCAN_EXCLUDE`, separada por comas) aplicada en `_scan`, con un valor
por defecto que cubra los sospechosos habituales (`Backups`, `backup`,
`.Trash`). Alternativa más simple: reutilizar `IGNORED_DIRS`.

- **Criterio de aceptación:** un repositorio git dentro de una carpeta excluida
  no aparece en `discover_repos`.
