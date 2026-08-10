"""Validación de lo que llega por formulario. Ver [PD-M10], [PD-M11] y [PD-M12].

Los tres son el mismo descuido: el HTML valida en el cliente y el servidor se
fiaba. Un POST directo, una extensión del navegador o un formulario manipulado se
lo saltan.
"""
import pytest

from app.models import Project
from app.security import ruta_local_valida

from .conftest import SAME_ORIGIN

CAMPOS = {"name": "x", "local_path": "", "remote_provider": "", "remote_repo": "",
          "description": "", "homepage_url": "", "tags": ""}


def _alta(client, **cambios):
    return client.post("/nuevo", data={**CAMPOS, **cambios}, headers=SAME_ORIGIN)


# --------------------------------------------------------------------------
# [PD-M10] local_path acaba en os.walk y en la lectura del README
# --------------------------------------------------------------------------

@pytest.fixture
def base_de_repos(tmp_path, monkeypatch):
    from app import security

    (tmp_path / "mi-repo").mkdir()
    monkeypatch.setattr(security.settings, "local_repos_base_path", str(tmp_path))
    return tmp_path


def test_local_path_dentro_de_la_base_se_acepta(base_de_repos):
    ruta = str(base_de_repos / "mi-repo")
    assert ruta_local_valida(ruta) == ruta


def test_la_propia_base_se_acepta(base_de_repos):
    assert ruta_local_valida(str(base_de_repos)) is not None


@pytest.mark.parametrize("hostil", ["/", "/etc", "C:\\Windows"])
def test_local_path_fuera_de_la_base_se_rechaza(base_de_repos, hostil):
    """Con `local_path = /`, la app recorría y leía ficheros de todo el
    contenedor: `scan_todos` abre todo lo que tenga extensión conocida y
    `read_readme` muestra el primer fichero que empiece por "readme"."""
    assert ruta_local_valida(hostil) is None


def test_local_path_con_traversal_se_rechaza(base_de_repos):
    assert ruta_local_valida(str(base_de_repos / ".." / "etc")) is None


def test_local_path_vacio_es_none(base_de_repos):
    assert ruta_local_valida("") is None
    assert ruta_local_valida(None) is None


def test_el_alta_rechaza_una_ruta_fuera_de_la_base(client, db, base_de_repos):
    _alta(client, name="malo", local_path="/")
    creado = db.query(Project).filter(Project.name == "malo").one()
    assert creado.local_path is None


def test_la_edicion_tambien_valida_la_ruta(client, db, base_de_repos, project):
    client.post("/%d/editar" % project.id,
                data={**CAMPOS, "name": project.name, "local_path": "/"},
                headers=SAME_ORIGIN)
    db.expire_all()
    assert db.get(Project, project.id).local_path is None


def test_una_ruta_que_ya_estaba_guardada_fuera_de_la_base_no_se_borra_en_silencio(
    db, base_de_repos, caplog
):
    """El cambio no debe hacer desaparecer datos sin avisar: se registra."""
    import logging

    from app.security import avisar_rutas_fuera_de_la_base

    db.add(Project(name="viejo", local_path="/sitio/raro"))
    db.commit()

    with caplog.at_level(logging.WARNING):
        fuera = avisar_rutas_fuera_de_la_base(db)

    assert fuera == 1
    assert "viejo" in caplog.text
    # Y la ruta sigue ahí: solo se avisa.
    assert db.query(Project).one().local_path == "/sitio/raro"


# --------------------------------------------------------------------------
# [PD-M11] remote_provider
# --------------------------------------------------------------------------

def test_proveedor_desconocido_no_se_guarda(client, db):
    _alta(client, name="raro", remote_provider="evil")
    assert db.query(Project).filter(Project.name == "raro").one().remote_provider is None


@pytest.mark.parametrize("prov", ["github", "gitlab", "bitbucket"])
def test_los_proveedores_soportados_si_se_guardan(client, db, prov):
    _alta(client, name="p-%s" % prov, remote_provider=prov, remote_repo="a/b")
    assert db.query(Project).filter(Project.name == "p-%s" % prov).one().remote_provider == prov


def test_el_proveedor_se_normaliza(client, db):
    _alta(client, name="mayus", remote_provider="  GitHub ", remote_repo="a/b")
    assert db.query(Project).filter(Project.name == "mayus").one().remote_provider == "github"


def test_la_edicion_tambien_valida_el_proveedor(client, db, project):
    client.post("/%d/editar" % project.id,
                data={**CAMPOS, "name": project.name, "remote_provider": "evil"},
                headers=SAME_ORIGIN)
    db.expire_all()
    assert db.get(Project, project.id).remote_provider is None


# --------------------------------------------------------------------------
# [PD-M12] nombre en blanco
# --------------------------------------------------------------------------

def test_no_se_crea_un_proyecto_sin_nombre(client, db):
    """`Form(...)` exige que el campo esté, pero "   " lo satisface. Tras el
    strip quedaba un proyecto sin nombre: enlace vacío imposible de pulsar,
    primero en el orden alfabético y avisos de Telegram con el nombre en blanco."""
    respuesta = _alta(client, name="   ")
    assert respuesta.status_code == 303
    assert db.query(Project).count() == 0


def test_el_alta_sin_nombre_explica_por_que(client):
    respuesta = _alta(client, name="   ")
    assert "nombre" in respuesta.headers["set-cookie"].lower()


def test_no_se_puede_dejar_un_proyecto_sin_nombre(client, db, project):
    client.post("/%d/editar" % project.id,
                data={**CAMPOS, "name": "  "}, headers=SAME_ORIGIN)
    db.expire_all()
    assert db.get(Project, project.id).name == "demo"


def test_un_nombre_normal_sigue_funcionando(client, db):
    _alta(client, name="  con espacios  ")
    assert db.query(Project).one().name == "con espacios"
