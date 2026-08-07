"""Descubrimiento automático: deducción del remoto desde git y alta de proyectos.

Es la pieza que evita que el usuario tenga que teclear 'owner/repo' proyecto a
proyecto, así que se prueba contra repos git reales (para el remoto local) y con
la API de GitHub simulada (para el alta desde la cuenta).
"""
import subprocess

import pytest

from app.models import Project
from app.services import discovery, github_client, local_scanner
from app.services.sync import link_remote_from_git, provider_from_url, remote_from_url


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo_con_remoto(tmp_path):
    """Repo git con un commit y un `origin` de GitHub configurado."""
    ruta = tmp_path / "proyecto-x"
    ruta.mkdir()
    _git(ruta, "init", "-q", "-b", "main")
    _git(ruta, "config", "user.email", "test@ejemplo.tld")
    _git(ruta, "config", "user.name", "Test")
    (ruta / "a.txt").write_text("hola\n")
    _git(ruta, "add", ".")
    _git(ruta, "commit", "-q", "-m", "inicial")
    _git(ruta, "remote", "add", "origin", "https://github.com/pablo/proyecto-x.git")
    return ruta


# --------------------------------------------------------------------------
# Deducción del proveedor a partir de la URL del remoto
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url,esperado", [
    ("https://github.com/pablo/repo.git", "github"),
    ("https://www.github.com/pablo/repo", "github"),
    ("git@github.com:pablo/repo.git", "github"),
    ("ssh://git@gitlab.com/grupo/sub/repo.git", "gitlab"),
    ("https://bitbucket.org/pablo/repo", "bitbucket"),
])
def test_provider_desde_url(url, esperado):
    assert provider_from_url(url) == esperado


@pytest.mark.parametrize("url", [
    None, "", "https://gitlab.miempresa.tld/grupo/repo.git",  # self-hosted: no se adivina
    "https://ejemplo.tld/algo", "no-es-una-url",
])
def test_provider_desconocido_es_none(url):
    assert provider_from_url(url) is None


def test_remote_from_url_devuelve_proveedor_y_repo():
    assert remote_from_url("git@github.com:pablo/repo.git") == ("github", "pablo/repo")
    assert remote_from_url("https://gitlab.com/grupo/sub/repo") == ("gitlab", "grupo/sub/repo")


def test_remote_from_url_rechaza_lo_que_no_tiene_forma_de_repo():
    # Host reconocido pero sin 'owner/repo' detrás: no vale como remoto.
    assert remote_from_url("https://github.com/") == (None, None)


# --------------------------------------------------------------------------
# Enlazado del remoto leyendo el propio repositorio
# --------------------------------------------------------------------------

def test_lee_el_remoto_del_repo(repo_con_remoto):
    assert local_scanner.get_remote_url(str(repo_con_remoto)) == (
        "https://github.com/pablo/proyecto-x.git"
    )


def test_enlaza_remoto_desde_git(repo_con_remoto):
    p = Project(name="proyecto-x", local_path=str(repo_con_remoto))
    assert link_remote_from_git(p) is True
    assert p.remote_provider == "github"
    assert p.remote_repo == "pablo/proyecto-x"


def test_no_pisa_el_remoto_que_puso_el_usuario(repo_con_remoto):
    """Lo que escribe el usuario manda sobre lo que diga `origin`."""
    p = Project(
        name="proyecto-x", local_path=str(repo_con_remoto),
        remote_provider="gitlab", remote_repo="otro/sitio",
    )
    assert link_remote_from_git(p) is False
    assert p.remote_repo == "otro/sitio"


def test_no_enlaza_si_la_ruta_local_ha_desaparecido(repo_con_remoto):
    p = Project(name="x", local_path=str(repo_con_remoto), local_path_missing=True)
    assert link_remote_from_git(p) is False


def test_repo_sin_origin_no_enlaza(tmp_path):
    ruta = tmp_path / "suelto"
    ruta.mkdir()
    _git(ruta, "init", "-q", "-b", "main")
    p = Project(name="suelto", local_path=str(ruta))
    assert link_remote_from_git(p) is False
    assert p.remote_provider is None


# --------------------------------------------------------------------------
# Alta automática
# --------------------------------------------------------------------------

def test_descubrimiento_local_da_de_alta_con_su_remoto(db, repo_con_remoto, monkeypatch):
    monkeypatch.setattr(discovery.settings, "local_repos_base_path", str(repo_con_remoto.parent))
    monkeypatch.setattr(discovery.settings, "auto_import_github", False)

    resultado = discovery.run_discovery(db)

    assert resultado["nuevos"] == 1
    creado = db.query(Project).one()
    assert creado.remote_provider == "github"
    assert creado.remote_repo == "pablo/proyecto-x"


def test_descubrimiento_local_es_idempotente(db, repo_con_remoto, monkeypatch):
    monkeypatch.setattr(discovery.settings, "local_repos_base_path", str(repo_con_remoto.parent))
    monkeypatch.setattr(discovery.settings, "auto_import_github", False)

    discovery.run_discovery(db)
    segundo = discovery.run_discovery(db)

    assert segundo["nuevos"] == 0
    assert db.query(Project).count() == 1


def test_alta_automatica_desde_github(db, monkeypatch):
    monkeypatch.setattr(discovery.settings, "local_repos_base_path", "/no/existe")
    monkeypatch.setattr(discovery.settings, "auto_import_github", True)
    monkeypatch.setattr(discovery.settings, "github_token", "tok")
    monkeypatch.setattr(github_client, "list_user_repos", lambda: [
        {"full_name": "pablo/uno", "name": "uno"},
        {"full_name": "pablo/dos", "name": "dos"},
    ])

    resultado = discovery.run_discovery(db)

    assert resultado["remotos_nuevos"] == 2
    assert {p.remote_repo for p in db.query(Project).all()} == {"pablo/uno", "pablo/dos"}


def test_no_duplica_un_repo_que_ya_esta_por_su_clon_local(db, monkeypatch):
    """El repo ya está en el panel gracias al clon local: la API no debe duplicarlo.

    GitHub no distingue mayúsculas en los nombres, así que la comparación tampoco.
    """
    db.add(Project(name="uno", local_path="/x/uno", remote_provider="github", remote_repo="Pablo/Uno"))
    db.commit()

    monkeypatch.setattr(discovery.settings, "local_repos_base_path", "/no/existe")
    monkeypatch.setattr(discovery.settings, "auto_import_github", True)
    monkeypatch.setattr(discovery.settings, "github_token", "tok")
    monkeypatch.setattr(github_client, "list_user_repos", lambda: [{"full_name": "pablo/uno", "name": "uno"}])

    resultado = discovery.run_discovery(db)

    assert resultado["remotos_nuevos"] == 0
    assert db.query(Project).count() == 1


def test_sin_token_no_intenta_el_alta_remota(db, monkeypatch):
    monkeypatch.setattr(discovery.settings, "local_repos_base_path", "/no/existe")
    monkeypatch.setattr(discovery.settings, "auto_import_github", True)
    monkeypatch.setattr(discovery.settings, "github_token", "")

    def _no_llamar():
        raise AssertionError("no debería consultarse la API sin token")

    monkeypatch.setattr(github_client, "list_user_repos", _no_llamar)
    assert discovery.run_discovery(db)["remotos_nuevos"] == 0


def test_el_error_de_github_no_tumba_el_descubrimiento_local(db, repo_con_remoto, monkeypatch):
    """Si la API falla, los repos locales tienen que darse de alta igualmente."""
    monkeypatch.setattr(discovery.settings, "local_repos_base_path", str(repo_con_remoto.parent))
    monkeypatch.setattr(discovery.settings, "auto_import_github", True)
    monkeypatch.setattr(discovery.settings, "github_token", "tok")
    fallo = {"error": "GITHUB_TOKEN inválido o caducado (401)"}
    monkeypatch.setattr(github_client, "list_user_repos", lambda: fallo)

    resultado = discovery.run_discovery(db)

    assert resultado["nuevos"] == 1
    assert resultado["remotos_nuevos"] == 0
    assert "401" in resultado["remote_error"]
