"""Cliente de la API de GitHub: commits, issues/PRs abiertos, estado de CI (Actions),
descripción y web del repo (para el escaparate) y antigüedad del PR abierto más viejo."""
import logging
import re
from datetime import UTC, datetime

import httpx

from ..config import settings

logger = logging.getLogger(__name__)
BASE_URL = "https://api.github.com"


def _days_since(iso: str | None) -> int | None:
    """Días transcurridos desde una fecha ISO 8601 (con Z), o None si no se puede parsear."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return (datetime.now(UTC) - dt).days


def _headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


def _count_from_link_header(response: httpx.Response) -> int | None:
    """Con per_page=1, el numero de la 'ultima pagina' del header Link equivale al total."""
    link = response.headers.get("Link", "")
    if 'rel="last"' not in link:
        return None
    match = re.search(r"[?&]page=(\d+)>; rel=\"last\"", link)
    return int(match.group(1)) if match else None


def get_repo_info(owner_repo: str) -> dict:
    info: dict = {}
    try:
        with httpx.Client(headers=_headers(), timeout=10) as client:
            repo = client.get(f"{BASE_URL}/repos/{owner_repo}")
            repo.raise_for_status()
            repo_data = repo.json()
            info["stars"] = repo_data.get("stargazers_count")
            info["branch"] = repo_data.get("default_branch")
            # Escaparate: descripción y web publicada (homepage) del repo
            info["description"] = repo_data.get("description") or None
            info["homepage"] = repo_data.get("homepage") or None

            commits = client.get(f"{BASE_URL}/repos/{owner_repo}/commits", params={"per_page": 1})
            if commits.status_code == 200 and commits.json():
                c = commits.json()[0]
                info["last_commit_sha"] = c.get("sha")
                info["last_commit_message"] = (c.get("commit", {}).get("message") or "").split("\n")[0]
                info["last_commit_date"] = c.get("commit", {}).get("committer", {}).get("date")

            # PRs abiertos ordenados por antigüedad ascendente: el primero es el más viejo
            pulls = client.get(
                f"{BASE_URL}/repos/{owner_repo}/pulls",
                params={"state": "open", "per_page": 1, "sort": "created", "direction": "asc"},
            )
            if pulls.status_code == 200:
                pull_list = pulls.json()
                count = _count_from_link_header(pulls)
                info["open_prs"] = count if count is not None else len(pull_list)
                if pull_list:
                    info["oldest_open_pr_days"] = _days_since(pull_list[0].get("created_at"))

            total_open = repo_data.get("open_issues_count") or 0
            info["open_issues"] = max(total_open - (info.get("open_prs") or 0), 0)

            runs = client.get(f"{BASE_URL}/repos/{owner_repo}/actions/runs", params={"per_page": 1})
            if runs.status_code == 200:
                run_list = runs.json().get("workflow_runs", [])
                if run_list:
                    info["ci_status"] = run_list[0].get("conclusion") or run_list[0].get("status")
    except Exception:
        logger.exception("Fallo al consultar GitHub para %s", owner_repo)
        info["error"] = "No se pudo conectar con GitHub (revisa el nombre owner/repo o el token)"
    return info
