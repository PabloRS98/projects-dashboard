"""Cliente de la API de GitLab: commits, issues/MRs abiertos y estado del ultimo pipeline."""
import logging
from urllib.parse import quote

import httpx

from ..config import settings
from . import cuota, forge_errors

logger = logging.getLogger(__name__)
BASE_URL = "https://gitlab.com/api/v4"
PROVEEDOR = "gitlab"


def _headers() -> dict:
    headers = {}
    if settings.gitlab_token:
        headers["PRIVATE-TOKEN"] = settings.gitlab_token
    return headers


def _total(response: httpx.Response) -> int | None:
    total = response.headers.get("X-Total")
    if total is None:
        return None
    try:
        return int(total)
    except ValueError:
        return None


def get_repo_info(owner_repo: str) -> dict:
    info: dict = {}
    # Corta antes de salir a la red: con REMOTE_WORKERS=5 y 20 proyectos de
    # GitLab, seguir intentándolo son 100 peticiones fallidas en segundos y todos
    # los proyectos acaban con el mismo error.
    if cuota.agotada(PROVEEDOR):
        info["error"] = cuota.mensaje(PROVEEDOR, "GitLab")
        return info
    project_id = quote(owner_repo, safe="")
    try:
        with httpx.Client(headers=_headers(), timeout=10) as client:
            repo = client.get(f"{BASE_URL}/projects/{project_id}")
            cuota.recordar(PROVEEDOR, repo)
            repo.raise_for_status()
            repo_data = repo.json()
            info["stars"] = repo_data.get("star_count")
            info["branch"] = repo_data.get("default_branch")
            info["description"] = repo_data.get("description") or None
            # Sin "homepage" a propósito. Aquí se guardaba `web_url`, que es la
            # URL del propio repositorio en GitLab, no la web publicada del
            # proyecto: la tarjeta acababa con dos enlaces al mismo sitio, uno
            # etiquetado "web". La API de /projects/:id no expone nada
            # equivalente al `homepage` de GitHub, así que lo honesto es no dar
            # el dato y dejar que lo ponga el usuario. (GitLab Pages sí sería una
            # web de verdad, pero está en /projects/:id/pages y pide permisos
            # extra; no compensa por ahora.)

            commits = client.get(
                f"{BASE_URL}/projects/{project_id}/repository/commits", params={"per_page": 1}
            )
            if commits.status_code == 200 and commits.json():
                c = commits.json()[0]
                info["last_commit_sha"] = c.get("id")
                info["last_commit_message"] = c.get("title") or ""
                info["last_commit_date"] = c.get("committed_date")

            issues = client.get(
                f"{BASE_URL}/projects/{project_id}/issues",
                params={"state": "opened", "per_page": 1},
            )
            info["open_issues"] = _total(issues)

            mrs = client.get(
                f"{BASE_URL}/projects/{project_id}/merge_requests",
                params={"state": "opened", "per_page": 1},
            )
            info["open_prs"] = _total(mrs)

            pipelines = client.get(f"{BASE_URL}/projects/{project_id}/pipelines", params={"per_page": 1})
            if pipelines.status_code == 200 and pipelines.json():
                info["ci_status"] = pipelines.json()[0].get("status")
    except Exception as exc:  # noqa: BLE001  el mensaje concreto lo pone forge_errors
        logger.warning("Fallo al consultar GitLab para %s: %s", owner_repo, exc)
        cuota.anotar_error(PROVEEDOR, exc)
        info["error"] = forge_errors.describe(
            exc, "GitLab", "GITLAB_TOKEN", bool(settings.gitlab_token)
        )
    return info
