"""Cliente de la API de Bitbucket: commits, PRs/issues abiertos y estado de CI.

Lo que Bitbucket **no** da, y por qué:

- **Estrellas.** No existen: Bitbucket tiene "watchers", que mide otra cosa. El
  campo se queda sin poner, y la política de `sync.CAMPOS_REMOTOS` conserva lo
  que hubiera en vez de borrarlo.
- **Antigüedad del PR más viejo.** El endpoint de pull requests no permite
  ordenar por fecha de creación y devolver solo el primero, así que sacarlo
  costaría paginar.
- **Actividad semanal.** No hay equivalente a `/stats/participation`.

El estado de CI (Pipelines) sí está soportado desde [PD-M20]. Requiere que el app
password tenga el alcance `pipeline:read`; sin él la petición devuelve 403 y
simplemente no se fija el campo.

La tabla completa de qué da cada forge está en `capacidades.py`, y el panel de
estado la publica para que "0 estrellas" y "no aplica" dejen de verse igual.
"""
import logging

import httpx

from ..config import settings
from . import cuota, forge_errors

logger = logging.getLogger(__name__)
BASE_URL = "https://api.bitbucket.org/2.0"
PROVEEDOR = "bitbucket"

# Vocabulario de Bitbucket -> el que usa el resto de la app (el de GitHub).
# `CI_BAD` de alerts.py y del router espera "failure"/"error"/"cancelled".
RESULTADO_PIPELINE = {
    "SUCCESSFUL": "success",
    "FAILED": "failure",
    "ERROR": "error",
    "STOPPED": "cancelled",
}


def _auth():
    """BITBUCKET_TOKEN debe tener el formato usuario:app_password."""
    if settings.bitbucket_token and ":" in settings.bitbucket_token:
        user, pwd = settings.bitbucket_token.split(":", 1)
        return (user, pwd)
    return None


def _estado_del_ultimo_pipeline(client: httpx.Client, owner_repo: str) -> str | None:
    """Estado del pipeline más reciente, traducido al vocabulario de la app.

    Devuelve None si no hay pipelines o si el app password no tiene
    `pipeline:read`: en los dos casos la respuesta es "no lo sé", no "no hay
    fallo", y la política de campos conserva entonces el valor anterior.
    """
    resp = client.get(
        f"{BASE_URL}/repositories/{owner_repo}/pipelines/",
        params={"sort": "-created_on", "pagelen": 1},
    )
    if resp.status_code != 200:
        return None
    values = resp.json().get("values") or []
    if not values:
        return None
    state = values[0].get("state") or {}
    resultado = (state.get("result") or {}).get("name")
    if resultado:
        return RESULTADO_PIPELINE.get(resultado, resultado.lower())
    # Sin `result` el pipeline sigue vivo (PENDING, IN_PROGRESS...). El resto de
    # la app trata como "en ejecución" todo lo que no está en CI_BAD ni CI_GOOD.
    return "running" if state.get("name") else None


def get_repo_info(owner_repo: str) -> dict:
    info: dict = {}
    # Bitbucket limita a 1.000 peticiones/hora y cada consulta gasta cinco, así
    # que agotarla es fácil. No manda cabeceras de cuota: el corte se apoya en el
    # 429 que anota `cuota.anotar_error`.
    if cuota.agotada(PROVEEDOR):
        info["error"] = cuota.mensaje(PROVEEDOR, "Bitbucket")
        return info
    try:
        with httpx.Client(auth=_auth(), timeout=10) as client:
            repo = client.get(f"{BASE_URL}/repositories/{owner_repo}")
            cuota.recordar(PROVEEDOR, repo)
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

            estado = _estado_del_ultimo_pipeline(client, owner_repo)
            if estado:
                info["ci_status"] = estado
    except Exception as exc:  # noqa: BLE001  el mensaje concreto lo pone forge_errors
        logger.warning("Fallo al consultar Bitbucket para %s: %s", owner_repo, exc)
        cuota.anotar_error(PROVEEDOR, exc)
        info["error"] = forge_errors.describe(
            exc, "Bitbucket", "BITBUCKET_TOKEN", bool(settings.bitbucket_token)
        )
    return info
