"""Sincronización de proyectos, partida en dos mitades independientes:

- `sync_local`  → estado git de la copia local (barato; el scheduler lo corre ~cada 15 min).
- `sync_remote` → API de GitHub/GitLab/Bitbucket (rate-limited; ~cada 60 min).

Cada mitad gestiona su propio campo de error (`local_error` / `remote_error`) para que
un ciclo no borre el error del otro. `sync_project` corre ambas (alta/edición/sync manual).
"""
import json
import logging
import re
from datetime import datetime
from urllib.parse import urlparse

from ..models import Project, utcnow
from ..security import safe_external_url
from . import bitbucket_client, github_client, gitlab_client, local_scanner

logger = logging.getLogger(__name__)

REMOTE_CLIENTS = {
    "github": github_client,
    "gitlab": gitlab_client,
    "bitbucket": bitbucket_client,
}

# Host del remoto -> proveedor soportado. Cubre los dominios públicos; una
# instancia self-hosted (gitlab.miempresa.tld) no se adivina y se deja al usuario.
PROVIDER_HOSTS = {
    "github.com": "github",
    "www.github.com": "github",
    "gitlab.com": "gitlab",
    "www.gitlab.com": "gitlab",
    "bitbucket.org": "bitbucket",
    "www.bitbucket.org": "bitbucket",
}

COMMIT_WEEKS = 12

# 'owner/repo', admitiendo subgrupos anidados de GitLab ('grupo/sub/repo').
# Cada segmento se limita a caracteres válidos en un nombre de repo, lo que de
# paso descarta '.' y '..': el valor se interpola en la ruta de la URL de la API
# y, sin este filtro, un 'owner/repo/../../../user' salía de /repos/ y llegaba a
# otro endpoint distinto llevándose el token en la cabecera.
_REPO_SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9._-]*"
VALID_REMOTE_REPO = re.compile(r"^%s(?:/%s)+$" % (_REPO_SEGMENT, _REPO_SEGMENT))


def normalize_remote_repo(spec: str | None) -> str | None:
    """Devuelve la ruta 'owner/repo' que esperan los clientes de API a partir de lo
    que escriba el usuario: 'owner/repo', URL https (https://github.com/owner/repo[.git])
    o remoto SSH (git@github.com:owner/repo.git). Conserva rutas anidadas (grupos de GitLab).

    Devuelve None si el resultado no tiene forma de 'owner/repo' válido.
    """
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
    if not spec or not VALID_REMOTE_REPO.match(spec):
        return None
    return spec


def provider_from_url(url: str | None) -> str | None:
    """Proveedor deducido de la URL de un remoto git, o None si no se reconoce.

    Acepta las dos formas en que git guarda un remoto: https://host/owner/repo y
    git@host:owner/repo (esta última no es una URL válida para urlparse, así que
    el host se extrae a mano).
    """
    if not url:
        return None
    url = url.strip()
    if url.startswith("git@"):
        host = url[4:].split(":", 1)[0].split("/", 1)[0]
    else:
        host = urlparse(url).hostname or ""
    return PROVIDER_HOSTS.get(host.lower())


def remote_from_url(url: str | None) -> tuple[str | None, str | None]:
    """(proveedor, 'owner/repo') a partir de la URL de un remoto. (None, None) si no aplica."""
    provider = provider_from_url(url)
    if not provider:
        return None, None
    repo = normalize_remote_repo(url)
    return (provider, repo) if repo else (None, None)


def link_remote_from_git(project: Project) -> bool:
    """Rellena proveedor y owner/repo leyendo el remoto `origin` del repo local.

    Solo actúa si el proyecto aún no tiene remoto: si el usuario lo puso o lo
    corrigió a mano, su valor manda sobre lo que diga `origin`. Devuelve True si
    ha cambiado algo. Es lo que hace que un repo recién descubierto traiga sus
    estrellas, PRs y CI sin que nadie teclee 'owner/repo'.
    """
    if project.remote_provider and project.remote_repo:
        return False
    if not project.local_path or project.local_path_missing:
        return False

    provider, repo = remote_from_url(local_scanner.get_remote_url(project.local_path))
    if not (provider and repo):
        return False

    project.remote_provider = provider
    project.remote_repo = repo
    return True


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

    # El remoto vive en el propio repo: leerlo aquí evita que el usuario tenga
    # que teclear 'owner/repo' proyecto por proyecto para ver datos del forge.
    link_remote_from_git(project)

    # Actividad para el sparkline. Un solo `git log`, no toca el árbol de trabajo.
    project.commit_weeks = json.dumps(
        local_scanner.get_commit_weeks(project.local_path, COMMIT_WEEKS)
    )

    # scan_todos recorre el árbol entero leyendo cada fichero línea a línea, y
    # este ciclo corre cada pocos minutos por proyecto. El conteo solo puede
    # cambiar si cambió el código, así que se reaprovecha mientras el HEAD sea
    # el mismo. Con cambios sin commitear se rescanea igualmente: ahí el SHA no
    # se mueve pero el contenido sí.
    sha = project.last_commit_sha
    needs_scan = (
        project.todo_count is None
        or not sha
        or project.todo_scanned_sha != sha
        or project.has_uncommitted_changes
    )
    if needs_scan:
        todo_info = local_scanner.scan_todos(project.local_path)
        project.todo_count = todo_info.get("count", 0)
        project.todo_scanned_sha = sha


def sync_remote(project: Project) -> None:
    """Actualiza stars, issues/PRs, CI, descripción y web desde la API del proveedor."""
    project.remote_error = None
    if not (project.remote_provider and project.remote_repo):
        return

    # Auto-corrige remotos guardados como URL completa (datos previos a la normalización)
    normalized = normalize_remote_repo(project.remote_repo)
    if not normalized:
        project.remote_error = "Remoto inválido: se espera 'owner/repo'"
        return
    project.remote_repo = normalized

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
    if not project.homepage_url:
        # La homepage viene de la API remota y acaba en un href, así que se
        # valida el esquema igual que si la hubiera escrito el usuario.
        project.homepage_url = safe_external_url(remote_info.get("homepage"))

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
