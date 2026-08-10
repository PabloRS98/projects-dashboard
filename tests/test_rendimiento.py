"""Coste del camino más caliente de la app: pintar el dashboard.

Ver [PD-M1], [PD-M2] y [PD-M18]. Van juntos: mismo fichero y misma petición.
Importa porque el buscador dispara una consulta completa cada 300 ms
(`hx-trigger="input from:#q delay:300ms"`), así que lo que cueste `GET /lista` se
paga por pulsación de tecla.
"""
import pytest
from sqlalchemy import event

from app.database import engine
from app.models import Project, TaskItem, utcnow
from app.routers.projects import _summary


@pytest.fixture
def contador_sql():
    """Cuenta las sentencias SQL que emite el motor mientras dura el bloque."""
    sentencias: list[str] = []

    def _registrar(conn, cursor, statement, parameters, context, executemany):
        sentencias.append(statement)

    event.listen(engine, "before_cursor_execute", _registrar)
    yield sentencias
    event.remove(engine, "before_cursor_execute", _registrar)


@pytest.fixture
def veinte_proyectos(db):
    for i in range(20):
        p = Project(name="proyecto-%02d" % i, last_commit_date=utcnow())
        db.add(p)
        db.flush()
        db.add_all([
            TaskItem(project_id=p.id, text="tarea A", order=0),
            TaskItem(project_id=p.id, text="tarea B", order=1, done=True),
        ])
    db.commit()


def _selects(sentencias) -> int:
    return sum(1 for s in sentencias if s.lstrip().upper().startswith("SELECT"))


def test_el_dashboard_no_hace_n_mas_uno_por_tareas(client, veinte_proyectos, contador_sql):
    """Cada tarjeta pinta sus tareas dos veces (el contador de pendientes y la
    lista), y `Project.tasks` es lazy: eran 1 + 20 consultas, y otras 21 por cada
    pulsación en el buscador."""
    contador_sql.clear()
    assert client.get("/").status_code == 200

    # Sin selectinload serían 21+; con él, la de proyectos y la de tareas.
    assert _selects(contador_sql) <= 4


def test_el_fragmento_de_lista_tampoco(client, veinte_proyectos, contador_sql):
    """Es el que se pide cada 300 ms mientras se teclea."""
    contador_sql.clear()
    assert client.get("/lista?q=proyecto").status_code == 200
    assert _selects(contador_sql) <= 4


def test_el_numero_de_consultas_no_crece_con_los_proyectos(client, db, contador_sql):
    """La prueba de que es constante y no "pocas para 20"."""
    def _medir():
        contador_sql.clear()
        client.get("/")
        return _selects(contador_sql)

    for i in range(5):
        db.add(Project(name="p%d" % i))
    db.commit()
    con_cinco = _medir()

    for i in range(5, 40):
        db.add(Project(name="p%d" % i))
    db.commit()
    con_cuarenta = _medir()

    assert con_cinco == con_cuarenta


# --------------------------------------------------------------------------
# _summary: mismo contrato, un solo recorrido
# --------------------------------------------------------------------------

def test_summary_recorre_la_lista_una_sola_vez():
    """Once comprensiones independientes × dos llamadas por petición eran 22
    recorridos, y varios predicados no son gratuitos: `sync_error` construye una
    lista y hace un join, y `_is_stale` resta datetimes."""
    class _Contador(list):
        def __init__(self, items):
            super().__init__(items)
            self.recorridos = 0

        def __iter__(self):
            self.recorridos += 1
            return super().__iter__()

    proyectos = _Contador([
        Project(name="a", open_prs=2, stars=5, todo_count=3, has_uncommitted_changes=True),
        Project(name="b", open_issues=1, ci_status="failure", oldest_open_pr_days=40),
    ])
    _summary(proyectos, 30, 7)
    assert proyectos.recorridos == 1


def test_summary_devuelve_lo_mismo_que_antes():
    """El contrato no cambia: es el criterio de aceptación del hallazgo."""
    proyectos = [
        Project(name="a", open_prs=2, open_issues=1, todo_count=3, stars=5,
                has_uncommitted_changes=True, last_commit_date=utcnow()),
        Project(name="b", ci_status="failure", oldest_open_pr_days=40,
                local_path_missing=True, local_error="perdida"),
        Project(name="c"),
    ]
    resumen = _summary(proyectos, 30, 7)
    assert resumen == {
        "total": 3, "prs": 2, "issues": 1, "todos": 3, "estrellas": 5,
        "cambios": 1, "errores": 1, "rutas_perdidas": 1,
        "parados": 0, "ci_rojo": 1, "prs_estancados": 1,
    }


def test_summary_de_una_lista_vacia():
    resumen = _summary([], 30, 7)
    assert resumen["total"] == 0
    assert all(valor == 0 for valor in resumen.values())
