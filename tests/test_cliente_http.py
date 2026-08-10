"""Cliente HTTP compartido y refresco de actividad en paralelo.

Ver [PD-M8] y [PD-M14]. Van juntos porque los dos son el mismo problema —trabajo
de red hecho de uno en uno cuando podría reutilizarse o solaparse— y tocan el
mismo cliente.
"""
import json
import threading
import time
from datetime import date

import httpx
import pytest

from app.models import Project, ProjectSnapshot
from app.services import github_client, history


@pytest.fixture(autouse=True)
def cliente_limpio():
    """El cliente compartido es estado de módulo: se descarta entre pruebas."""
    github_client.cerrar_cliente()
    github_client.rate_limit.update(
        {"remaining": None, "limit": None, "reset": None, "checked_at": None}
    )
    yield
    github_client.cerrar_cliente()


# --------------------------------------------------------------------------
# [PD-M8] Cliente compartido
# --------------------------------------------------------------------------

def test_el_cliente_http_se_reutiliza(monkeypatch):
    """Se creaba y destruía un httpx.Client por proyecto, así que se rehacía el
    handshake TLS con api.github.com cada vez. Con 50 proyectos, 50 handshakes
    evitables."""
    creados = []
    original = httpx.Client

    def _contar(*args, **kwargs):
        creados.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _contar)

    assert github_client._client() is github_client._client()
    assert len(creados) == 1


def test_el_cliente_lleva_el_token_de_la_configuracion(monkeypatch):
    monkeypatch.setattr(github_client.settings, "github_token", "secreto")
    cliente = github_client._client()
    assert cliente.headers["Authorization"] == "Bearer secreto"


def test_si_cambia_el_token_el_cliente_se_rehace(monkeypatch):
    """Las cabeceras se fijan al crear el cliente: sin invalidarlo, un token
    cambiado en caliente no se aplicaría nunca."""
    monkeypatch.setattr(github_client.settings, "github_token", "viejo")
    primero = github_client._client()

    monkeypatch.setattr(github_client.settings, "github_token", "nuevo")
    segundo = github_client._client()

    assert segundo is not primero
    assert segundo.headers["Authorization"] == "Bearer nuevo"


def test_el_cliente_es_seguro_entre_hilos(monkeypatch):
    """Se usa desde el ThreadPoolExecutor de sync_all_remote: si cada hilo creara
    el suyo, el objetivo del cambio se perdería."""
    monkeypatch.setattr(github_client.settings, "github_token", "tok")
    obtenidos = []
    barrera = threading.Barrier(5)

    def _pedir():
        barrera.wait()
        obtenidos.append(github_client._client())

    hilos = [threading.Thread(target=_pedir) for _ in range(5)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert len({id(c) for c in obtenidos}) == 1


# --------------------------------------------------------------------------
# [PD-M14] Refresco de la actividad remota
# --------------------------------------------------------------------------

@pytest.fixture
def cinco_remotos(db):
    for i in range(5):
        db.add(Project(name="remoto-%d" % i, remote_provider="github",
                       remote_repo="pablo/remoto-%d" % i))
    db.commit()


def test_la_actividad_remota_se_refresca_en_paralelo(db, cinco_remotos, monkeypatch):
    """Una petición por proyecto en serie, con 10 s de timeout: con 40 repos
    importados eran hasta 7 minutos dentro del job de las 4:30."""
    def _lento(owner_repo, weeks=12):
        time.sleep(0.3)
        return [1] * weeks

    monkeypatch.setattr(github_client, "get_commit_weeks", _lento)

    inicio = time.monotonic()
    actualizados = history.refresh_remote_activity(db)
    transcurrido = time.monotonic() - inicio

    assert actualizados == 5
    # En serie serían 1,5 s. Con 5 hilos, algo más de 0,3 s.
    assert transcurrido < 1.0


def test_el_refresco_guarda_la_actividad_de_cada_proyecto(db, cinco_remotos, monkeypatch):
    monkeypatch.setattr(github_client, "get_commit_weeks", lambda r, weeks=12: [3] * weeks)

    history.refresh_remote_activity(db)

    db.expire_all()
    for p in db.query(Project).all():
        assert json.loads(p.commit_weeks) == [3] * 12


def test_un_202_se_reintenta_en_la_misma_pasada(db, monkeypatch):
    """El endpoint de estadísticas se calcula en diferido y devuelve 202 con
    cuerpo vacío. Sin reintento, la "siguiente pasada" son 24 horas: un repo
    recién importado tardaba días en enseñar su actividad."""
    db.add(Project(name="nuevo", remote_provider="github", remote_repo="pablo/nuevo"))
    db.commit()

    intentos = []

    def _primero_vacio(owner_repo, weeks=12):
        intentos.append(owner_repo)
        return None if len(intentos) == 1 else [2] * weeks

    monkeypatch.setattr(github_client, "get_commit_weeks", _primero_vacio)
    monkeypatch.setattr(history, "ESPERA_REINTENTO", 0)

    assert history.refresh_remote_activity(db) == 1
    assert len(intentos) == 2


def test_no_se_reintenta_indefinidamente(db, monkeypatch):
    """Un solo reintento: si GitHub sigue calculando, se deja para mañana."""
    db.add(Project(name="nuevo", remote_provider="github", remote_repo="pablo/nuevo"))
    db.commit()

    intentos = []
    monkeypatch.setattr(github_client, "get_commit_weeks",
                        lambda r, weeks=12: intentos.append(r))
    monkeypatch.setattr(history, "ESPERA_REINTENTO", 0)

    assert history.refresh_remote_activity(db) == 0
    assert len(intentos) == 2


def test_el_refresco_commitea_por_proyecto(db, cinco_remotos, monkeypatch):
    """Con un commit al final, un fallo a mitad tiraba las peticiones ya hechas."""
    hechos = []

    def _falla_en_el_tercero(owner_repo, weeks=12):
        hechos.append(owner_repo)
        if len(hechos) == 3:
            raise RuntimeError("corte de red")
        return [1] * weeks

    monkeypatch.setattr(github_client, "get_commit_weeks", _falla_en_el_tercero)

    history.refresh_remote_activity(db)

    db.expire_all()
    guardados = [p for p in db.query(Project).all() if p.commit_weeks]
    assert len(guardados) >= 2


def test_el_snapshot_diario_sigue_funcionando(db, cinco_remotos):
    """Regresión: refresh_remote_activity corre dentro del job del snapshot."""
    filas = history.take_snapshot(db, day=date(2026, 8, 7))
    assert filas == 5
    assert db.query(ProjectSnapshot).count() == 5
