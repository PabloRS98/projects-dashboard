"""Sincronización local contra un repositorio git de verdad."""
import subprocess

import pytest

from app.models import Project
from app.services import local_scanner
from app.services.sync import sync_local


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """Repositorio git mínimo con un commit y un TODO."""
    ruta = tmp_path / "miproyecto"
    ruta.mkdir()
    _git(ruta, "init", "-q", "-b", "main")
    _git(ruta, "config", "user.email", "test@ejemplo.tld")
    _git(ruta, "config", "user.name", "Test")
    (ruta / "codigo.py").write_text("# TODO: pendiente\nprint(1)\n")
    (ruta / "README.md").write_text("# Mi proyecto\n\nDescripción.\n")
    _git(ruta, "add", ".")
    _git(ruta, "commit", "-q", "-m", "primer commit")
    return ruta


def test_estado_git(repo):
    estado = local_scanner.get_git_status(str(repo))
    assert estado["branch"] == "main"
    assert estado["last_commit_message"] == "primer commit"
    assert estado["has_uncommitted_changes"] is False
    assert estado["last_commit_date"] is not None


def test_estado_detecta_cambios_sin_commitear(repo):
    (repo / "codigo.py").write_text("print(2)\n")
    assert local_scanner.get_git_status(str(repo))["has_uncommitted_changes"] is True


def test_ruta_desaparecida(tmp_path):
    estado = local_scanner.get_git_status(str(tmp_path / "no-existe"))
    assert estado["missing"] is True
    assert "error" in estado


def test_carpeta_sin_git(tmp_path):
    estado = local_scanner.get_git_status(str(tmp_path))
    assert "error" in estado
    assert not estado.get("missing")


def test_descubrimiento_de_repos(repo):
    encontrados = local_scanner.discover_repos(str(repo.parent))
    assert [r["name"] for r in encontrados] == ["miproyecto"]


def test_conteo_de_todos(repo):
    assert local_scanner.scan_todos(str(repo))["count"] == 1


def test_lectura_de_readme(repo):
    assert "Mi proyecto" in local_scanner.read_readme(str(repo))


def test_sync_local_rellena_el_proyecto(repo):
    p = Project(name="x", local_path=str(repo))
    sync_local(p)
    assert p.local_error is None
    assert p.branch == "main"
    assert p.todo_count == 1
    assert p.last_commit_sha


def test_sync_local_marca_ruta_perdida(tmp_path):
    p = Project(name="x", local_path=str(tmp_path / "no-existe"))
    sync_local(p)
    assert p.local_path_missing is True
    assert p.has_uncommitted_changes is False
    assert p.local_error


def test_los_todos_no_se_reescanean_si_el_head_no_cambia(repo, monkeypatch):
    """scan_todos recorre el árbol entero: debe evitarse cuando nada ha cambiado."""
    p = Project(name="x", local_path=str(repo))
    sync_local(p)
    assert p.todo_count == 1

    llamadas = []
    original = local_scanner.scan_todos
    monkeypatch.setattr(
        local_scanner, "scan_todos",
        lambda *a, **k: (llamadas.append(1), original(*a, **k))[1],
    )

    sync_local(p)
    assert llamadas == [], "se reescaneó pese a no haber cambios"

    # Un commit nuevo mueve el HEAD y sí obliga a recontar.
    (repo / "otro.py").write_text("# TODO: dos\n# FIXME: tres\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "segundo")
    sync_local(p)
    assert llamadas == [1]
    assert p.todo_count == 3


def test_con_cambios_sin_commitear_si_se_reescanea(repo):
    """El SHA no se mueve, pero el contenido del árbol sí puede haber cambiado."""
    p = Project(name="x", local_path=str(repo))
    sync_local(p)
    (repo / "codigo.py").write_text("# TODO: a\n# TODO: b\n")
    sync_local(p)
    assert p.todo_count == 2
