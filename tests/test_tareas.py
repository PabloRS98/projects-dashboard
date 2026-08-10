"""Integridad de la checklist de tareas. Ver [PD-A4].

La tarea se creaba y commiteaba antes de que nadie comprobara que el proyecto
existía: la comprobación estaba en `_tasks_fragment`, que corre después. La fila
quedaba colgando de un `project_id` inexistente, y como SQLite no aplica las
claves foráneas sin el PRAGMA, ahí se quedaba.
"""
from app.database import limpiar_tareas_huerfanas
from app.models import Project, TaskItem

from .conftest import SAME_ORIGIN


def test_no_se_crea_tarea_de_un_proyecto_inexistente(client, db):
    respuesta = client.post("/9999/tareas", data={"text": "algo"}, headers=SAME_ORIGIN)
    assert respuesta.status_code == 404
    assert db.query(TaskItem).count() == 0


def test_la_tarea_de_un_proyecto_existente_si_se_crea(client, db, project):
    respuesta = client.post(
        "/%d/tareas" % project.id, data={"text": "escribir el test"}, headers=SAME_ORIGIN
    )
    assert respuesta.status_code == 200
    assert db.query(TaskItem).one().text == "escribir el test"


def test_borrar_proyecto_borra_sus_tareas(client, db, project):
    db.add(TaskItem(project_id=project.id, text="pendiente"))
    db.commit()

    client.post("/%d/eliminar" % project.id, headers=SAME_ORIGIN)

    assert db.query(Project).count() == 0
    assert db.query(TaskItem).count() == 0


def test_la_limpieza_borra_las_tareas_huerfanas_que_ya_estaban(db):
    """Las filas que dejó el fallo anterior no se van solas: había que barrerlas.

    Se insertan a mano imitando lo que hacía el endpoint roto, porque por la vía
    normal ya no se pueden crear.
    """
    vivo = Project(name="vivo")
    db.add(vivo)
    db.commit()
    db.add_all([
        TaskItem(project_id=vivo.id, text="se queda"),
        TaskItem(project_id=9999, text="huérfana"),
    ])
    db.commit()

    assert limpiar_tareas_huerfanas() == 1

    db.expire_all()
    assert [t.text for t in db.query(TaskItem).all()] == ["se queda"]


def test_la_limpieza_no_toca_nada_si_no_hay_huerfanas(db, project):
    db.add(TaskItem(project_id=project.id, text="sana"))
    db.commit()

    assert limpiar_tareas_huerfanas() == 0
    assert db.query(TaskItem).count() == 1
