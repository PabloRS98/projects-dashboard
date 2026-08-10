"""Qué pasa cuando el proveedor no da un campo, y actividad de los solo-remoto.

Ver [PD-M6], [PD-M5] y [PD-M4]. Había dos políticas distintas en el mismo bloque
de código y ninguna escrita: `ci_status` conservaba el valor anterior si el
proveedor no lo daba, y los otros cuatro campos lo ponían a `None`.
"""
import json

import pytest

from app.models import Project
from app.services import github_client, sync


class _Proveedor:
    """Cliente de forge falso: devuelve exactamente el diccionario que se le da."""

    def __init__(self, info):
        self.info = info
        self.llamadas = 0

    def get_repo_info(self, owner_repo):
        self.llamadas += 1
        return dict(self.info)


@pytest.fixture
def con_proveedor(monkeypatch):
    def _instalar(info, proveedor="github"):
        falso = _Proveedor(info)
        monkeypatch.setitem(sync.REMOTE_CLIENTS, proveedor, falso)
        return falso
    return _instalar


def _proyecto_remoto(**kwargs):
    return Project(name="x", remote_provider="github", remote_repo="pablo/x", **kwargs)


# --------------------------------------------------------------------------
# [PD-M6] Política: campo ausente = dato que el proveedor no tiene
# --------------------------------------------------------------------------

@pytest.mark.parametrize("campo,valor_previo", [
    ("stars", 42),
    ("open_issues", 7),
    ("open_prs", 3),
    ("oldest_open_pr_days", 40),
    ("ci_status", "failure"),
])
def test_un_campo_que_el_proveedor_no_da_no_borra_el_valor_anterior(
    con_proveedor, campo, valor_previo
):
    """Un proveedor que no soporta un campo no está diciendo "cero": no está
    diciendo nada. Pisar el dato con None borraba información real — un proyecto
    migrado de GitHub a Bitbucket perdía sus estrellas en el primer sync."""
    con_proveedor({"branch": "main"})
    p = _proyecto_remoto(**{campo: valor_previo})

    sync.sync_remote(p)

    assert getattr(p, campo) == valor_previo


@pytest.mark.parametrize("campo,antes,ahora", [
    ("stars", 42, 50),
    ("open_issues", 7, 0),
    ("open_prs", 3, 0),
    ("oldest_open_pr_days", 40, None),
    ("ci_status", "failure", "success"),
])
def test_un_campo_que_el_proveedor_si_da_se_escribe(con_proveedor, campo, antes, ahora):
    """Incluida la clave con valor 0 o None: eso sí es información. Si se cierran
    todos los PRs, `open_prs` tiene que bajar a 0 y el aviso de PR estancado
    tiene que apagarse."""
    con_proveedor({campo: ahora})
    p = _proyecto_remoto(**{campo: antes})

    sync.sync_remote(p)

    assert getattr(p, campo) == ahora


def test_al_cerrarse_el_ultimo_pr_se_apaga_el_aviso_de_estancado(con_proveedor):
    """El caso que la política B podría haber roto: sin que el cliente diga
    explícitamente "ya no hay PR viejo", el proyecto se quedaría marcado como
    estancado para siempre."""
    con_proveedor({"open_prs": 0, "oldest_open_pr_days": None})
    p = _proyecto_remoto(open_prs=2, oldest_open_pr_days=40)

    sync.sync_remote(p)

    assert p.open_prs == 0
    assert p.oldest_open_pr_days is None


def test_github_dice_explicitamente_que_no_quedan_prs_abiertos(monkeypatch):
    """Para que la política funcione, el cliente tiene que distinguir "no lo sé"
    de "no hay". GitHub sí lo sabe."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        ruta = request.url.path
        if ruta.endswith("/pulls"):
            return httpx.Response(200, json=[])          # ningún PR abierto
        if ruta.endswith("/commits"):
            return httpx.Response(200, json=[])
        if ruta.endswith("/actions/runs"):
            return httpx.Response(200, json={"workflow_runs": []})
        return httpx.Response(200, json={"open_issues_count": 0})

    transporte = httpx.MockTransport(handler)
    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda *a, **k: original(*a, **{**k, "transport": transporte})
    )
    github_client.cerrar_cliente()

    info = github_client.get_repo_info("pablo/x")
    assert info["open_prs"] == 0
    assert "oldest_open_pr_days" in info
    assert info["oldest_open_pr_days"] is None
    github_client.cerrar_cliente()


# --------------------------------------------------------------------------
# [PD-M5] GitLab no tiene web publicada
# --------------------------------------------------------------------------

def test_gitlab_no_pone_la_url_del_repo_como_web(monkeypatch):
    """`web_url` de GitLab es la URL del repositorio, no la web del proyecto.
    Guardarla pintaba dos enlaces al mismo sitio en la tarjeta, uno etiquetado
    "web"."""
    import httpx

    from app.services import gitlab_client

    def handler(request: httpx.Request) -> httpx.Response:
        if "/projects/" in request.url.path and request.url.path.endswith("repo"):
            return httpx.Response(200, json={})
        return httpx.Response(200, json={
            "star_count": 3, "default_branch": "main",
            "web_url": "https://gitlab.com/grupo/repo",
        })

    transporte = httpx.MockTransport(handler)
    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda *a, **k: original(*a, **{**k, "transport": transporte})
    )

    info = gitlab_client.get_repo_info("grupo/repo")
    assert info.get("homepage") is None


def test_se_limpia_la_web_incorrecta_ya_guardada(con_proveedor):
    """Como `homepage_url` solo se rellenaba si estaba vacía, el valor malo se
    quedaba para siempre salvo edición manual."""
    con_proveedor({}, "gitlab")
    p = Project(name="x", remote_provider="gitlab", remote_repo="grupo/repo",
                homepage_url="https://gitlab.com/grupo/repo")

    sync.sync_remote(p)

    assert p.homepage_url is None


def test_no_se_toca_una_web_de_verdad(con_proveedor):
    con_proveedor({}, "gitlab")
    p = Project(name="x", remote_provider="gitlab", remote_repo="grupo/repo",
                homepage_url="https://mi-proyecto.tld")

    sync.sync_remote(p)

    assert p.homepage_url == "https://mi-proyecto.tld"


# --------------------------------------------------------------------------
# [PD-M4] Actividad de los proyectos solo-remoto
# --------------------------------------------------------------------------

def test_un_proyecto_remoto_nuevo_trae_su_actividad(con_proveedor, monkeypatch):
    """Un repo dado de alta desde GitHub aparecía sin sparkline hasta las 4:30
    del día siguiente. El usuario ve una tarjeta a medias y no sabe por qué."""
    con_proveedor({"branch": "main"})
    monkeypatch.setattr(github_client, "get_commit_weeks", lambda r, weeks=12: [2] * weeks)

    p = _proyecto_remoto()
    sync.sync_remote(p)

    assert json.loads(p.commit_weeks) == [2] * 12


def test_no_se_repide_la_actividad_si_ya_esta(con_proveedor, monkeypatch):
    """Es una petición cara: solo la primera vez. El refresco diario mantiene."""
    con_proveedor({})
    llamadas = []
    monkeypatch.setattr(github_client, "get_commit_weeks",
                        lambda r, weeks=12: llamadas.append(r) or [1] * weeks)

    p = _proyecto_remoto(commit_weeks=json.dumps([5] * 12))
    sync.sync_remote(p)

    assert llamadas == []
    assert json.loads(p.commit_weeks) == [5] * 12


def test_un_proyecto_con_clon_local_no_pide_la_actividad(con_proveedor, monkeypatch):
    """Los que tienen copia local la sacan de `git log`, que es gratis."""
    con_proveedor({})
    llamadas = []
    monkeypatch.setattr(github_client, "get_commit_weeks",
                        lambda r, weeks=12: llamadas.append(r) or [1] * weeks)

    p = _proyecto_remoto(local_path="/x/y")
    sync.sync_remote(p)

    assert llamadas == []


def test_solo_github_da_actividad_por_api(monkeypatch):
    """GitLab y Bitbucket no tienen equivalente a /stats/participation."""
    llamadas = []
    monkeypatch.setattr(github_client, "get_commit_weeks",
                        lambda r, weeks=12: llamadas.append(r) or [1] * weeks)
    monkeypatch.setitem(sync.REMOTE_CLIENTS, "gitlab", _Proveedor({}))

    p = Project(name="x", remote_provider="gitlab", remote_repo="grupo/x")
    sync.sync_remote(p)

    assert llamadas == []
