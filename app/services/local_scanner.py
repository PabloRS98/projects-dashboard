"""Escaneo de repositorios git locales: deteccion automatica y estado (rama, ultimo commit,
cambios sin commitear, TODOs/FIXMEs en el codigo)."""
import logging
import os
import re
import subprocess
import time
from datetime import date, datetime

logger = logging.getLogger(__name__)

IGNORED_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
    ".idea", ".vscode",
    # Salida de compilación y dependencias de otros ecosistemas: sus TODOs no
    # son del proyecto y pueden ser cientos de miles. Sin esto, un repo de Rust
    # con `target/` o uno de Go con `vendor/` multiplicaba el recuento y el
    # tiempo de escaneo por lo que ocupara su directorio de artefactos.
    "target", "vendor", ".next", ".nuxt", "coverage", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".gradle", "Pods", "bin", "obj",
}
TODO_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb",
    ".php", ".c", ".cpp", ".h", ".md", ".html", ".css",
}

# Palabra completa: "TODO" suelto casaba dentro de TODOS_LOS_USUARIOS, METODOS o
# TODO_EXTENSIONS. De hecho este mismo fichero se contaba a sí mismo.
TODO_PATTERN = re.compile(r"\b(?:TODO|FIXME)\b")

# Un fichero mayor que esto no se lee. Los bundles minificados van en una sola
# línea, así que el iterador de líneas mete el fichero entero en memoria como una
# única cadena buscando un salto que no llega. 1 MB de código fuente escrito a
# mano no existe; lo que pasa de ahí es generado.
MAX_FILE_BYTES = 1_000_000

# Topes de recorrido. No son límites de corrección sino de tiempo: un repo con un
# submódulo grande o un directorio de assets puede tardar minutos, y esto corría
# dentro de la petición. Cuando se alcanzan, el resultado se marca `parcial`.
MAX_FILES = 20_000
MAX_SECONDS = 20.0


def discover_repos(root_path: str) -> list[dict]:
    """Busca subcarpetas de root_path (hasta la profundidad indicada por settings.projects_scan_depth)
    que contengan un repositorio git."""
    results = []
    if not root_path or not os.path.isdir(root_path):
        return results

    from ..config import settings
    max_depth = max(1, settings.projects_scan_depth)

    def _scan(path: str, current_depth: int):
        if current_depth > max_depth:
            return
        try:
            for entry in sorted(os.listdir(path)):
                if entry in IGNORED_DIRS:
                    continue
                full_path = os.path.join(path, entry)
                if os.path.isdir(full_path):
                    if os.path.isdir(os.path.join(full_path, ".git")):
                        rel_name = os.path.relpath(full_path, root_path).replace("\\", "/")
                        results.append({
                            "name": rel_name,
                            "local_path": full_path,
                            "remote_url": get_remote_url(full_path),
                        })
                    else:
                        _scan(full_path, current_depth + 1)
        except Exception:
            logger.exception("Fallo al escanear carpeta %s", path)

    _scan(root_path, 1)
    return results


def _run_git(path: str, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", path] + args,
            capture_output=True, timeout=10,
            # git escribe los mensajes de commit en UTF-8. Con `text=True` a
            # secas, Python los decodifica con la codificación del sistema, que
            # en Windows es cp1252: un "—" salía como "â€”". `replace` evita
            # además que un byte suelto raro tumbe la sincronización entera.
            text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()
    except Exception:
        logger.exception("Fallo ejecutando git %s en %s", args, path)
        return None


def get_git_status(path: str) -> dict:
    """Devuelve rama actual, último commit y si hay cambios sin commitear.
    Si la ruta ya no existe, marca `missing` para que la UI lo distinga."""
    if not path or not os.path.isdir(path):
        return {"error": "La ruta local ya no existe", "missing": True}
    if not os.path.isdir(os.path.join(path, ".git")):
        return {"error": "La ruta existe pero no es un repositorio git"}

    branch = _run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    log_line = _run_git(path, ["log", "-1", "--format=%H%x1f%s%x1f%ci"])
    status_output = _run_git(path, ["status", "--porcelain"])

    sha = message = None
    commit_date = None
    if log_line:
        parts = log_line.split("\x1f")
        if len(parts) == 3:
            sha, message, date_str = parts
            try:
                commit_date = datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                commit_date = None

    return {
        "branch": branch,
        "last_commit_sha": sha,
        "last_commit_message": message,
        "last_commit_date": commit_date,
        "has_uncommitted_changes": bool(status_output),
    }


def get_remote_url(path: str) -> str | None:
    """URL del remoto 'origin' del repo, o None si no lo tiene.

    Es lo que permite que un repo descubierto en disco quede enlazado con su
    forge sin que el usuario teclee 'owner/repo': el dato ya está en el propio
    repositorio, solo había que leerlo.
    """
    if not path or not os.path.isdir(os.path.join(path, ".git")):
        return None
    return _run_git(path, ["remote", "get-url", "origin"]) or None


def get_commit_weeks(path: str, weeks: int = 12) -> list[int]:
    """Commits por semana en las últimas `weeks` semanas, del más antiguo al más
    reciente. Alimenta el sparkline de actividad de la tarjeta.

    Un único `git log` con las fechas en formato corto: barato incluso en repos
    grandes, porque no toca el árbol de trabajo.
    """
    empty = [0] * weeks
    if not path or not os.path.isdir(os.path.join(path, ".git")):
        return empty
    out = _run_git(path, ["log", "--since=%d.weeks" % weeks, "--format=%cs"])
    if not out:
        return empty

    today = date.today()
    counts = empty[:]
    for line in out.splitlines():
        try:
            commit_day = date.fromisoformat(line.strip())
        except ValueError:
            continue
        # 0 = semana en curso; weeks-1 = la más antigua dentro de la ventana.
        index = (today - commit_day).days // 7
        if 0 <= index < weeks:
            counts[weeks - 1 - index] += 1
    return counts


def get_recent_commits(path: str, limit: int = 15) -> list[dict]:
    """Últimos commits del repo local: sha corto, asunto, autor y fecha. [] si falla."""
    if not path or not os.path.isdir(os.path.join(path, ".git")):
        return []
    out = _run_git(path, ["log", "-%d" % limit, "--format=%h%x1f%s%x1f%an%x1f%ci"])
    if not out:
        return []
    commits = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        sha, subject, author, date_str = parts
        date = None
        try:
            date = datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            date = None
        commits.append({"sha": sha, "subject": subject, "author": author, "date": date})
    return commits


def read_readme(path: str, max_chars: int = 40000) -> str | None:
    """Contenido del README del repo (prefiere .md). None si no hay o no se puede leer."""
    if not path or not os.path.isdir(path):
        return None
    try:
        candidates = [e for e in os.listdir(path)
                      if e.lower().startswith("readme") and os.path.isfile(os.path.join(path, e))]
        if not candidates:
            return None
        candidates.sort(key=lambda e: (not e.lower().endswith(".md"), e.lower()))
        with open(os.path.join(path, candidates[0]), encoding="utf-8", errors="ignore") as f:
            return f.read(max_chars)
    except Exception:
        logger.exception("Fallo al leer el README de %s", path)
        return None


def scan_todos(
    path: str,
    max_results: int = 500,
    max_file_bytes: int = MAX_FILE_BYTES,
    max_files: int = MAX_FILES,
    max_seconds: float = MAX_SECONDS,
) -> dict:
    """Cuenta y lista comentarios TODO/FIXME en los archivos de texto del proyecto.

    Devuelve `{"count", "items", "parcial"}`. `parcial` avisa de que se alcanzó
    el tope de ficheros o el de tiempo y el recuento se quedó corto: es
    preferible un número incompleto y señalado a que el escaneo se coma minutos.

    `max_results` limita los items que se guardan, no el recuento: el contador
    sigue siendo exacto mientras no se marque `parcial`.
    """
    if not path or not os.path.isdir(path):
        return {"count": 0, "items": [], "parcial": False}

    items = []
    count = 0
    vistos = 0
    parcial = False
    limite = time.monotonic() + max_seconds

    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for filename in filenames:
            ext = os.path.splitext(filename)[1]
            if ext not in TODO_EXTENSIONS:
                continue
            if vistos >= max_files or time.monotonic() > limite:
                parcial = True
                break
            file_path = os.path.join(dirpath, filename)
            try:
                if os.path.getsize(file_path) > max_file_bytes:
                    continue
                vistos += 1
                with open(file_path, encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, start=1):
                        if TODO_PATTERN.search(line):
                            count += 1
                            if len(items) < max_results:
                                items.append({
                                    "file": os.path.relpath(file_path, path),
                                    "line": line_num,
                                    "text": line.strip()[:200],
                                })
            except Exception:
                logger.debug("No se pudo leer %s al contar TODOs", file_path)
                continue
        if parcial:
            break

    if parcial:
        logger.info("Conteo de TODOs parcial en %s: %d ficheros revisados", path, vistos)
    return {"count": count, "items": items, "parcial": parcial}
