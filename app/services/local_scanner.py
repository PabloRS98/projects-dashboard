"""Escaneo de repositorios git locales: deteccion automatica y estado (rama, ultimo commit,
cambios sin commitear, TODOs/FIXMEs en el codigo)."""
import logging
import os
import subprocess
from datetime import datetime

logger = logging.getLogger(__name__)

IGNORED_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", ".idea", ".vscode"}
TODO_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".rb",
    ".php", ".c", ".cpp", ".h", ".md", ".html", ".css",
}


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
                        results.append({"name": rel_name, "local_path": full_path})
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
            capture_output=True, text=True, timeout=10,
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
        with open(os.path.join(path, candidates[0]), "r", encoding="utf-8", errors="ignore") as f:
            return f.read(max_chars)
    except Exception:
        logger.exception("Fallo al leer el README de %s", path)
        return None


def scan_todos(path: str, max_results: int = 500) -> dict:
    """Cuenta y lista comentarios TODO/FIXME en los archivos de texto del proyecto."""
    if not path or not os.path.isdir(path):
        return {"count": 0, "items": []}

    items = []
    count = 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for filename in filenames:
            ext = os.path.splitext(filename)[1]
            if ext not in TODO_EXTENSIONS:
                continue
            file_path = os.path.join(dirpath, filename)
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, start=1):
                        if "TODO" in line or "FIXME" in line:
                            count += 1
                            if len(items) < max_results:
                                items.append({
                                    "file": os.path.relpath(file_path, path),
                                    "line": line_num,
                                    "text": line.strip()[:200],
                                })
            except Exception:
                continue
    return {"count": count, "items": items}
