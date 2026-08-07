"""El descubrimiento, que es la operación más cara, fuera de la petición.

Ver [PD-A3] y [PD-M17]. Van juntos: mismo fichero y misma causa raíz — el
descubrimiento hacía el trabajo de sincronización que ya hacen los ciclos
periódicos, y lo hacía dentro del POST y con un solo commit al final.
"""
import subprocess
import threading

import pytest
from fastapi import BackgroundTasks

from app.models import Project
from app.routers.projects import scan_local_repos
from app.services import discovery, sync


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def cinco_repos(tmp_path, monkeypatch):
    """Cinco repos git de verdad bajo una base común, listos para descubrir."""
    for i in range(5):
        ruta = tmp_path / ("repo-%d" % i)
        ruta.mkdir()
        _git(ruta, "init", "-q", "-b", "main")
        _git(ruta, "config", "user.email", "test@ejemplo.tld")
        _git(ruta, "config", "user.name", "Test")
        (ruta / "a.txt").write_text("hola\n")
        _git(ruta, "add", ".")
        _git(ruta, "commit", "-q", "-m", "inicial")
        _git(ruta, "remote", "add", "origin", "https://github.com/pablo/repo-%d.git" % i)
    monkeypatch.setattr(discovery.settings, "local_repos_base_path", str(tmp_path))
    monkeypatch.setattr(discovery.settings, "auto_import_github", False)
    return tmp_path


# --------------------------------------------------------------------------
# [PD-A3] Fuera de la petición
# --------------------------------------------------------------------------

def test_escanear_no_hace_el_trabajo_dentro_de_la_peticion(monkeypatch):
    """El handler solo encola: responde sin esperar al disco ni a la red.

    Se llama a la función directamente en vez de por el cliente de pruebas
    porque el TestClient ejecuta las tareas de fondo dentro de la propia
    llamada, así que medir el tiempo desde fuera no distinguiría una
    implementación de la otra.
    """
    llamadas = []
    monkeypatch.setattr(discovery, "run_discovery", lambda db: llamadas.append(1))

    tareas = BackgroundTasks()
    respuesta = scan_local_repos(tareas)

    assert respuesta.status_code == 303
    assert llamadas == []          # nada de trabajo durante la petición
    assert len(tareas.tasks) == 1  # ...pero queda encolado


def test_escanear_avisa_de_que_va_en_segundo_plano(client):
    respuesta = client.post("/escanear", headers={"Origin": "http://testserver"})
    assert respuesta.status_code == 303
    assert "marcha" in respuesta.headers["set-cookie"].lower()


def test_el_descubrimiento_commitea_por_proyecto(cinco_repos, db, monkeypatch):
    """Con un solo commit al final, una excepción a mitad tiraba todo el trabajo.

    Un timeout del proxy en la versión síncrona hacía exactamente esto.
    """
    procesados = []
    original = sync.sync_local

    def _falla_en_el_tercero(project):
        procesados.append(project.name)
        if len(procesados) == 3:
            raise RuntimeError("disco lleno")
        return original(project)

    monkeypatch.setattr(discovery, "sync_local", _falla_en_el_tercero)

    with pytest.raises(RuntimeError):
        discovery.discover_local(db)

    db.rollback()
    assert db.query(Project).count() == 2


# --------------------------------------------------------------------------
# [PD-M17] El descubrimiento no sincroniza el remoto
# --------------------------------------------------------------------------

def test_el_descubrimiento_no_hace_peticiones_remotas(cinco_repos, db, monkeypatch):
    """`scheduler.py` programa el descubrimiento ANTES que los dos ciclos de sync
    precisamente para que ellos hagan el trabajo. Llamarlo aquí lo duplicaba: 5
    repos nuevos eran 20 peticiones HTTP secuenciales dentro del POST.

    Se parchea `sync.sync_remote` y no un nombre de `discovery`: `sync_project`
    lo resuelve como global de su propio módulo, así que este contador cazaba
    igual la llamada indirecta que hacía el código anterior.
    """
    remotos = []
    monkeypatch.setattr(sync, "sync_remote", lambda p: remotos.append(p.name))

    resultado = discovery.discover_local(db)

    assert resultado["nuevos"] == 5
    assert remotos == []


def test_el_descubrimiento_si_hace_el_sync_local(cinco_repos, db):
    """Es barato (solo disco) y evita que el repo recién descubierto salga en
    blanco hasta el siguiente ciclo."""
    discovery.discover_local(db)

    creado = db.query(Project).filter(Project.name == "repo-0").one()
    assert creado.branch == "main"
    assert creado.last_commit_sha


def test_el_alta_desde_github_no_sincroniza_en_el_bucle(db, monkeypatch):
    from app.services import github_client

    monkeypatch.setattr(discovery.settings, "local_repos_base_path", "/no/existe")
    monkeypatch.setattr(discovery.settings, "auto_import_github", True)
    monkeypatch.setattr(discovery.settings, "github_token", "tok")
    monkeypatch.setattr(github_client, "list_user_repos", lambda: [
        {"full_name": "pablo/uno", "name": "uno"},
        {"full_name": "pablo/dos", "name": "dos"},
    ])
    remotos = []
    monkeypatch.setattr(sync, "sync_remote", lambda p: remotos.append(p.name))

    resultado = discovery.discover_remote(db)

    assert resultado["nuevos"] == 2
    assert remotos == []


# --------------------------------------------------------------------------
# [PD-A3] Guarda de concurrencia
# --------------------------------------------------------------------------

def test_dos_descubrimientos_a_la_vez_solo_ejecutan_uno(db, monkeypatch):
    """El botón manual y el job periódico pueden coincidir. Sin guarda, dos
    recorridos del disco y dos tandas de peticiones a la vez."""
    dentro = threading.Event()
    seguir = threading.Event()
    ejecuciones = []

    def _lento(db_):
        ejecuciones.append(1)
        dentro.set()
        seguir.wait(timeout=5)
        return {"nuevos": 0, "enlazados": 0, "perdidos": 0}

    monkeypatch.setattr(discovery, "discover_local", _lento)
    monkeypatch.setattr(discovery, "discover_remote", lambda db_: {"nuevos": 0, "error": None})

    from app.database import SessionLocal

    def _en_otro_hilo():
        otra = SessionLocal()
        try:
            discovery.run_discovery(otra)
        finally:
            otra.close()

    hilo = threading.Thread(target=_en_otro_hilo)
    hilo.start()
    dentro.wait(timeout=5)

    segundo = discovery.run_discovery(db)   # mientras el primero sigue dentro
    assert segundo["ya_en_marcha"] is True

    seguir.set()
    hilo.join(timeout=5)
    assert len(ejecuciones) == 1


def test_tras_terminar_se_puede_volver_a_descubrir(db, monkeypatch):
    """La guarda se libera aunque el descubrimiento falle."""
    monkeypatch.setattr(discovery, "discover_local", lambda db_: (_ for _ in ()).throw(RuntimeError("x")))

    with pytest.raises(RuntimeError):
        discovery.run_discovery(db)

    monkeypatch.setattr(
        discovery, "discover_local", lambda db_: {"nuevos": 0, "enlazados": 0, "perdidos": 0}
    )
    monkeypatch.setattr(discovery, "discover_remote", lambda db_: {"nuevos": 0, "error": None})
    assert discovery.run_discovery(db)["ya_en_marcha"] is False
