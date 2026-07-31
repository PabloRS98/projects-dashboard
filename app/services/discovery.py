"""Descubrimiento automático de proyectos, sin configuración por parte del usuario.

Dos fuentes, deliberadamente independientes:

- `discover_local`  → repos git bajo `LOCAL_REPOS_BASE_PATH`. Además de darlos de
  alta, deduce el forge y el `owner/repo` leyendo el remoto `origin` del propio
  repositorio: el dato ya estaba ahí, y tenerlo evita que el usuario escriba a
  mano el remoto de cada proyecto para que las tarjetas dejen de salir vacías.
- `discover_remote` → repos de la cuenta de GitHub del token. Cubre el escaparate
  de lo que no está clonado en esta máquina.

Ambas son idempotentes: se pueden ejecutar cada pocas horas sin duplicar nada.
"""
import logging
import os

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Project
from . import github_client, local_scanner
from .sync import link_remote_from_git, normalize_remote_repo, remote_from_url, sync_project

logger = logging.getLogger(__name__)


def discover_local(db: Session) -> dict:
    """Da de alta los repos git nuevos y marca los que han perdido su ruta.

    Devuelve {'nuevos': n, 'enlazados': n, 'perdidos': n}. Es la lógica que antes
    vivía en el router: sacarla permite que la ejecuten igual el botón manual y
    el job periódico.
    """
    existing_paths = {
        p.local_path
        for p in db.query(Project).filter(Project.local_path.isnot(None)).all()
    }
    nuevos = 0
    for repo in local_scanner.discover_repos(settings.local_repos_base_path):
        if repo["local_path"] in existing_paths:
            continue
        provider, remote_repo = remote_from_url(repo.get("remote_url"))
        project = Project(
            name=repo["name"],
            local_path=repo["local_path"],
            remote_provider=provider,
            remote_repo=remote_repo,
        )
        db.add(project)
        db.flush()
        sync_project(project)
        nuevos += 1

    # Proyectos ya registrados: enlazar remoto si les falta y revisar la ruta.
    enlazados = 0
    perdidos = 0
    for project in db.query(Project).filter(Project.local_path.isnot(None)).all():
        missing = not os.path.isdir(project.local_path)
        if missing and not project.local_path_missing:
            project.local_path_missing = True
            project.local_error = "La ruta local ya no existe"
            project.has_uncommitted_changes = False
            perdidos += 1
            continue
        if not missing and project.local_path_missing:
            sync_project(project)  # la ruta ha vuelto (disco montado de nuevo, etc.)
        if link_remote_from_git(project):
            enlazados += 1

    db.commit()
    return {"nuevos": nuevos, "enlazados": enlazados, "perdidos": perdidos}


def discover_remote(db: Session) -> dict:
    """Da de alta los repos de la cuenta de GitHub que aún no estén en el panel.

    Requiere `GITHUB_TOKEN`: sin él la API no sabe de quién son los repos.
    Los que ya existen (por ruta local con remoto enlazado, o por alta manual) se
    saltan comparando 'owner/repo' sin distinguir mayúsculas, que es como GitHub
    trata los nombres.
    """
    if not settings.auto_import_github:
        return {"nuevos": 0, "error": None}
    if not settings.github_token:
        return {"nuevos": 0, "error": None}

    repos = github_client.list_user_repos()
    if isinstance(repos, dict) and repos.get("error"):
        return {"nuevos": 0, "error": repos["error"]}

    known = {
        p.remote_repo.lower()
        for p in db.query(Project).filter(Project.remote_repo.isnot(None)).all()
        if p.remote_repo
    }
    nuevos = 0
    for repo in repos:
        full_name = repo.get("full_name")
        if not full_name or full_name.lower() in known:
            continue
        normalized = normalize_remote_repo(full_name)
        if not normalized:
            continue
        project = Project(
            name=repo.get("name") or normalized.split("/")[-1],
            remote_provider="github",
            remote_repo=normalized,
        )
        db.add(project)
        db.flush()
        sync_project(project)
        known.add(normalized.lower())
        nuevos += 1

    db.commit()
    return {"nuevos": nuevos, "error": None}


def run_discovery(db: Session) -> dict:
    """Descubrimiento completo (local + remoto). Lo usan el job y el botón."""
    result = discover_local(db)
    remote = discover_remote(db)
    result["remotos_nuevos"] = remote["nuevos"]
    result["remote_error"] = remote["error"]
    logger.info("Descubrimiento: %s", result)
    return result
