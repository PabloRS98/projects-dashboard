"""Sincronización de proyectos, partida en dos mitades independientes:

- `sync_local`  → estado git de la copia local (barato; el scheduler lo corre ~cada 15 min).
- `sync_remote` → API de GitHub/GitLab/Bitbucket (rate-limited; ~cada 60 min).

Cada mitad gestiona su propio campo de error (`local_error` / `remote_error`) para que
un ciclo no borre el error del otro. `sync_project` corre ambas (alta/edición/sync manual).
"""
import logging
import re
from datetime import datetime

from ..models import Project, utcnow
from . import local_scanner, github_client, gitlab_client, bitbucket_client

logger = logging.getLogger(__name__)

REMOTE_CLIENTS = {
    "github": github_client,
    "gitlab": gitlab_client,
    "bitbucket": bitbucket_client,
}


def normalize_remote_repo(spec: str | None) -> str | None:
    """Devuelve la ruta 'owner/repo' que esperan los clientes de API a partir de lo
    que escriba el usuario: 'owner/repo', URL https (https://github.com/owner/repo[.git])
    o remoto SSH (git@github.com:owner/repo.git). Conserva rutas anidadas (grupos de GitLab)."""
    if not spec:
        return None
    spec = spec.strip()
    ssh = re.match(r"^(?:ssh://)?git@[^:/]+[:/](.+)$", spec)
    https = re.match(r"^https?://[^/]+/(.+)$", spec)
    if ssh:
        spec = ssh.group(1)
    elif https:
        spec = https.group(1)
    spec = spec.strip("/")
    if spec.endswith(".git"):
        spec = spec[:-4]
    return spec or None


def _parse_remote_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def sync_local(project: Project) -> None:
    """Actualiza rama, último commit, cambios sin commitear y TODOs desde la copia local."""
    project.local_error = None
    project.local_path_missing = False
    if not project.local_path:
        return

    local_info = local_scanner.get_git_status(project.local_path)
    if "error" in local_info:
        project.local_error = local_info["error"]
        project.local_path_missing = bool(local_info.get("missing"))
        if project.local_path_missing:
            # Estado local ya no comprobable: no dejar datos viejos engañosos
            project.has_uncommitted_changes = False
        return

    project.branch = local_info.get("branch")
    project.last_commit_sha = local_info.get("last_commit_sha")
    project.last_commit_message = local_info.get("last_commit_message")
    project.last_commit_date = local_info.get("last_commit_date")
    project.has_uncommitted_changes = local_info.get("has_uncommitted_changes", False)

    todo_info = local_scanner.scan_todos(project.local_path)
    project.todo_count = todo_info.get("count", 0)


def sync_remote(project: Project) -> None:
    """Actualiza stars, issues/PRs, CI, descripción y web desde la API del proveedor."""
    project.remote_error = None
    if not (project.remote_provider and project.remote_repo):
        return

    # Auto-corrige remotos guardados como URL completa (datos previos a la normalización)
    project.remote_repo = normalize_remote_repo(project.remote_repo)

    client_module = REMOTE_CLIENTS.get(project.remote_provider)
    if not client_module:
        project.remote_error = "Proveedor remoto no soportado"
        return

    remote_info = client_module.get_repo_info(project.remote_repo)
    if remote_info.get("error"):
        project.remote_error = remote_info["error"]
        return

    project.stars = remote_info.get("stars")
    project.open_issues = remote_info.get("open_issues")
    project.open_prs = remote_info.get("open_prs")
    project.oldest_open_pr_days = remote_info.get("oldest_open_pr_days")
    if remote_info.get("ci_status"):
        project.ci_status = remote_info.get("ci_status")

    # Escaparate: autorrellenar descripción y web SOLO si están vacías (los edita el usuario)
    if not project.description and remote_info.get("description"):
        project.description = remote_info["description"]
    if not project.homepage_url and remote_info.get("homepage"):
        project.homepage_url = remote_info["homepage"]

    # Si no hay copia local, usamos los datos de commit/rama del remoto
    if not project.local_path:
        project.branch = remote_info.get("branch") or project.branch
        project.last_commit_sha = remote_info.get("last_commit_sha") or project.last_commit_sha
        project.last_commit_message = (
            remote_info.get("last_commit_message") or project.last_commit_message
        )
        parsed_date = _parse_remote_date(remote_info.get("last_commit_date"))
        if parsed_date:
            project.last_commit_date = parsed_date


def sync_project(project: Project) -> None:
    """Sincronización completa (local + remoto). Para alta, edición y sync manual."""
    sync_local(project)
    sync_remote(project)
    project.last_synced_at = utcnow()
