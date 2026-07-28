"""Cliente de la API de GitLab: commits, issues/MRs abiertos y estado del ultimo pipeline."""
import logging
from urllib.parse import quote

import httpx

from ..config import settings

logger = logging.getLogger(__name__)
BASE_URL = "https://gitlab.com/api/v4"


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
    project_id = quote(owner_repo, safe="")
    try:
        with httpx.Client(headers=_headers(), timeout=10) as client:
            repo = client.get(f"{BASE_URL}/projects/{project_id}")
            repo.raise_for_status()
            repo_data = repo.json()
            info["stars"] = repo_data.get("star_count")
            info["branch"] = repo_data.get("default_branch")
            info["description"] = repo_data.get("description") or None
            info["homepage"] = repo_data.get("web_url") or None

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
    except Exception:
        logger.exception("Fallo al consultar GitLab para %s", owner_repo)
        info["error"] = "No se pudo conectar con GitLab (revisa el nombre owner/repo o el token)"
    return info
