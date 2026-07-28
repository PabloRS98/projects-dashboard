"""Cliente de la API de Bitbucket: commits y PRs/issues abiertos.
Nota: el estado de CI (Pipelines) no esta soportado en esta version por requerir
permisos adicionales; queda como posible extension futura."""
import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)
BASE_URL = "https://api.bitbucket.org/2.0"


def _auth():
    """BITBUCKET_TOKEN debe tener el formato usuario:app_password."""
    if settings.bitbucket_token and ":" in settings.bitbucket_token:
        user, pwd = settings.bitbucket_token.split(":", 1)
        return (user, pwd)
    return None


def get_repo_info(owner_repo: str) -> dict:
    info: dict = {}
    try:
        with httpx.Client(auth=_auth(), timeout=10) as client:
            repo = client.get(f"{BASE_URL}/repositories/{owner_repo}")
            repo.raise_for_status()
            repo_data = repo.json()
            info["branch"] = (repo_data.get("mainbranch") or {}).get("name")
            info["description"] = repo_data.get("description") or None
            info["homepage"] = (repo_data.get("website") or None)

            commits = client.get(f"{BASE_URL}/repositories/{owner_repo}/commits")
            if commits.status_code == 200:
                values = commits.json().get("values", [])
                if values:
                    c = values[0]
                    info["last_commit_sha"] = c.get("hash")
                    info["last_commit_message"] = (c.get("message") or "").split("\n")[0]
                    info["last_commit_date"] = c.get("date")

            prs = client.get(f"{BASE_URL}/repositories/{owner_repo}/pullrequests", params={"state": "OPEN"})
            if prs.status_code == 200:
                data = prs.json()
                info["open_prs"] = data.get("size", len(data.get("values", [])))

            issues = client.get(
                f"{BASE_URL}/repositories/{owner_repo}/issues", params={"q": 'state="new"'}
            )
            if issues.status_code == 200:
                info["open_issues"] = issues.json().get("size")
    except Exception:
        logger.exception("Fallo al consultar Bitbucket para %s", owner_repo)
        info["error"] = "No se pudo conectar con Bitbucket (revisa el nombre owner/repo o el token)"
    return info
