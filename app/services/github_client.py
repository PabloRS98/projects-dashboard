"""Cliente de la API de GitHub: commits, issues/PRs abiertos, estado de CI (Actions),
descripción y web del repo (para el escaparate), antigüedad del PR abierto más viejo,
actividad semanal y listado de los repos de la cuenta del token.

Los errores se traducen a un diagnóstico concreto (`describe_http_error`). La versión
anterior devolvía un único mensaje genérico para cualquier fallo, así que un token
caducado, un repo privado sin permiso y una cuota agotada eran indistinguibles desde
la interfaz: el usuario leía "revisa el nombre owner/repo o el token" sin manera de
saber cuál de las dos cosas mirar.
"""
import logging
import re
import threading
from datetime import UTC, datetime

import httpx

from ..config import settings
from . import forge_errors

logger = logging.getLogger(__name__)
BASE_URL = "https://api.github.com"

# Cliente compartido a nivel de módulo. Antes se creaba y destruía uno por
# proyecto, así que se rehacía el handshake TLS con api.github.com cada vez: con
# 50 proyectos, 50 handshakes evitables. `httpx.Client` es seguro entre hilos,
# así que vale tal cual para el ThreadPoolExecutor de `sync_all_remote`.
#
# Se guarda junto al token con el que se creó: las cabeceras se fijan al
# construirlo, así que si la configuración se recarga en caliente hay que
# rehacerlo o seguiría autenticando con el token viejo.
_cliente: httpx.Client | None = None
_cliente_token: str | None = None
_cliente_lock = threading.Lock()


def _client() -> httpx.Client:
    global _cliente, _cliente_token
    token = settings.github_token or ""
    with _cliente_lock:
        if _cliente is None or _cliente_token != token:
            if _cliente is not None:
                _cliente.close()
            _cliente = httpx.Client(headers=_headers(), timeout=10)
            _cliente_token = token
        return _cliente


def cerrar_cliente() -> None:
    """Cierra el cliente compartido. Para el apagado de la app y las pruebas."""
    global _cliente, _cliente_token
    with _cliente_lock:
        if _cliente is not None:
            _cliente.close()
        _cliente = None
        _cliente_token = None

# Última cuota vista, para el panel de estado. La API la devuelve en cada
# respuesta, así que no hace falta gastar una petición extra en consultarla.
rate_limit: dict = {"remaining": None, "limit": None, "reset": None, "checked_at": None}


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


def _remember_rate_limit(response: httpx.Response) -> None:
    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining is None:
        return
    reset = response.headers.get("X-RateLimit-Reset")
    try:
        rate_limit["remaining"] = int(remaining)
        rate_limit["limit"] = int(response.headers.get("X-RateLimit-Limit") or 0) or None
        rate_limit["reset"] = (
            datetime.fromtimestamp(int(reset), UTC).replace(tzinfo=None) if reset else None
        )
        rate_limit["checked_at"] = datetime.now(UTC).replace(tzinfo=None)
    except (TypeError, ValueError):
        pass


def quota_exhausted() -> bool:
    """True si la última respuesta dejó la cuota a cero y aún no ha llegado el reset.

    Permite abortar un ciclo de sincronización entero en cuanto GitHub deja de
    responder, en vez de gastar una petición fallida por proyecto.
    """
    if rate_limit["remaining"] is None or rate_limit["remaining"] > 0:
        return False
    reset = rate_limit["reset"]
    return bool(reset and reset > datetime.now(UTC).replace(tzinfo=None))


def _quota_message() -> str:
    reset = rate_limit["reset"]
    if reset:
        return "Cuota de la API de GitHub agotada (se repone a las %s UTC)" % reset.strftime("%H:%M")
    return "Cuota de la API de GitHub agotada"


def describe_http_error(exc: Exception) -> str:
    """Traduce un fallo de httpx al mensaje que verá el usuario en la tarjeta."""
    return forge_errors.describe(exc, "GitHub", "GITHUB_TOKEN", bool(settings.github_token))


def _count_from_link_header(response: httpx.Response) -> int | None:
    """Con per_page=1, el numero de la 'ultima pagina' del header Link equivale al total."""
    link = response.headers.get("Link", "")
    if 'rel="last"' not in link:
        return None
    match = re.search(r"[?&]page=(\d+)>; rel=\"last\"", link)
    return int(match.group(1)) if match else None


def get_repo_info(owner_repo: str) -> dict:
    info: dict = {}
    if quota_exhausted():
        info["error"] = _quota_message()
        return info
    try:
        client = _client()
        repo = client.get(f"{BASE_URL}/repos/{owner_repo}")
        _remember_rate_limit(repo)
        repo.raise_for_status()
        repo_data = repo.json()
        info["stars"] = repo_data.get("stargazers_count")
        info["branch"] = repo_data.get("default_branch")
        # Escaparate: descripción y web publicada (homepage) del repo
        info["description"] = repo_data.get("description") or None
        info["homepage"] = repo_data.get("homepage") or None

        commits = client.get(f"{BASE_URL}/repos/{owner_repo}/commits", params={"per_page": 1})
        _remember_rate_limit(commits)
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
        _remember_rate_limit(pulls)
        if pulls.status_code == 200:
            pull_list = pulls.json()
            count = _count_from_link_header(pulls)
            info["open_prs"] = count if count is not None else len(pull_list)
            if pull_list:
                info["oldest_open_pr_days"] = _days_since(pull_list[0].get("created_at"))

        total_open = repo_data.get("open_issues_count") or 0
        info["open_issues"] = max(total_open - (info.get("open_prs") or 0), 0)

        runs = client.get(f"{BASE_URL}/repos/{owner_repo}/actions/runs", params={"per_page": 1})
        _remember_rate_limit(runs)
        if runs.status_code == 200:
            run_list = runs.json().get("workflow_runs", [])
            if run_list:
                info["ci_status"] = run_list[0].get("conclusion") or run_list[0].get("status")
    except Exception as exc:  # noqa: BLE001  el mensaje concreto lo pone describe_http_error
        logger.warning("Fallo al consultar GitHub para %s: %s", owner_repo, exc)
        info["error"] = describe_http_error(exc)
    return info


def get_commit_weeks(owner_repo: str, weeks: int = 12) -> list[int] | None:
    """Commits por semana del último año, recortados a las `weeks` últimas.

    Es la única forma de dar actividad a un proyecto solo-remoto (sin clon local).
    El endpoint de estadísticas se calcula en diferido: cuando GitHub aún no lo
    tiene listo responde 202 con cuerpo vacío, y entonces se devuelve None para
    reintentarlo en la siguiente pasada en vez de guardar una serie de ceros.
    """
    if quota_exhausted():
        return None
    try:
        resp = _client().get(f"{BASE_URL}/repos/{owner_repo}/stats/participation")
        _remember_rate_limit(resp)
        if resp.status_code != 200:
            return None
        data = resp.json().get("all")
        if not isinstance(data, list) or not data:
            return None
        return [int(n) for n in data[-weeks:]]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fallo al pedir actividad de %s: %s", owner_repo, exc)
        return None


def list_user_repos(max_pages: int = 5) -> list[dict] | dict:
    """Repos de la cuenta del token (propios y de organizaciones), o {'error': ...}.

    Es lo que permite que el escaparate se rellene solo: sin esto, un repo que no
    esté clonado en esta máquina no aparece hasta que alguien lo da de alta a mano.
    Se pagina con tope para no dispararse en cuentas con cientos de repos.
    """
    if not settings.github_token:
        return {"error": "Sin GITHUB_TOKEN no se pueden listar los repos de la cuenta"}
    repos: list[dict] = []
    affiliation = "owner,collaborator,organization_member"
    try:
        client = _client()
        for page in range(1, max_pages + 1):
            resp = client.get(
                f"{BASE_URL}/user/repos",
                params={"per_page": 100, "page": page, "affiliation": affiliation},
                # Timeout propio: listar 100 repos es más caro que consultar uno,
                # y el cliente compartido va con los 10 s del caso normal.
                timeout=15,
            )
            _remember_rate_limit(resp)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            repos.extend(batch)
            if len(batch) < 100:
                break
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fallo al listar los repos de la cuenta: %s", exc)
        return {"error": describe_http_error(exc)}
    return repos
