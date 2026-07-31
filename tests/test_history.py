"""Histórico diario y actividad por semanas."""
import json
import subprocess
from datetime import date, timedelta

import pytest

from app.models import Project, ProjectSnapshot, utcnow
from app.services import history, local_scanner


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


# --------------------------------------------------------------------------
# Commits por semana
# --------------------------------------------------------------------------

def test_commits_por_semana_cuenta_el_commit_reciente(tmp_path):
    ruta = tmp_path / "repo"
    ruta.mkdir()
    _git(ruta, "init", "-q", "-b", "main")
    _git(ruta, "config", "user.email", "t@e.tld")
    _git(ruta, "config", "user.name", "T")
    (ruta / "a.txt").write_text("1\n")
    _git(ruta, "add", ".")
    _git(ruta, "commit", "-q", "-m", "uno")

    semanas = local_scanner.get_commit_weeks(str(ruta), weeks=12)
    assert len(semanas) == 12
    assert semanas[-1] == 1        # el cubo final es la ventana de los últimos 7 días
    assert sum(semanas) == 1


def test_ruta_sin_repo_devuelve_serie_de_ceros(tmp_path):
    assert local_scanner.get_commit_weeks(str(tmp_path), weeks=12) == [0] * 12


def test_week_counts_tolera_json_corrupto():
    assert Project(name="x", commit_weeks="{no es json").week_counts() == []
    assert Project(name="x", commit_weeks=None).week_counts() == []
    assert Project(name="x", commit_weeks="[1, 2, 3]").week_counts() == [1, 2, 3]


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------

def test_snapshot_guarda_una_fila_por_proyecto(db):
    db.add_all([
        Project(name="a", open_prs=2, stars=10, commit_weeks=json.dumps([0] * 11 + [4])),
        Project(name="b", open_issues=1),
    ])
    db.commit()

    assert history.take_snapshot(db) == 2
    filas = db.query(ProjectSnapshot).all()
    assert len(filas) == 2
    a = next(f for f in filas if f.project_id == 1)
    assert a.commits_7d == 4          # último cubo de commit_weeks
    assert a.open_prs == 2
    assert a.stars == 10


def test_repetir_el_snapshot_del_dia_actualiza_en_vez_de_duplicar(db):
    p = Project(name="a", stars=10)
    db.add(p)
    db.commit()

    history.take_snapshot(db)
    p.stars = 25
    db.commit()
    history.take_snapshot(db)

    filas = db.query(ProjectSnapshot).all()
    assert len(filas) == 1
    assert filas[0].stars == 25


def test_el_snapshot_poda_el_historico_muy_viejo(db):
    p = Project(name="a")
    db.add(p)
    db.commit()
    viejo = date.today() - timedelta(days=history.KEEP_DAYS + 5)
    db.add(ProjectSnapshot(project_id=p.id, day=viejo, stars=1))
    db.commit()

    history.take_snapshot(db)
    assert db.query(ProjectSnapshot).filter(ProjectSnapshot.day == viejo).count() == 0


def test_borrar_el_proyecto_se_lleva_su_historico(db):
    """SQLite no aplica claves foráneas por defecto: el borrado va por la relación."""
    p = Project(name="a")
    db.add(p)
    db.commit()
    history.take_snapshot(db)
    assert db.query(ProjectSnapshot).count() == 1

    db.delete(p)
    db.commit()
    assert db.query(ProjectSnapshot).count() == 0


# --------------------------------------------------------------------------
# Series para las gráficas
# --------------------------------------------------------------------------

def test_series_suma_por_dia(db):
    db.add_all([Project(name="a"), Project(name="b")])
    db.commit()
    hoy = date.today()
    ayer = hoy - timedelta(days=1)
    db.add_all([
        ProjectSnapshot(project_id=1, day=ayer, open_prs=1),
        ProjectSnapshot(project_id=2, day=ayer, open_prs=2),
        ProjectSnapshot(project_id=1, day=hoy, open_prs=5),
    ])
    db.commit()

    assert history.series(db, "open_prs", days=30) == [(ayer, 3), (hoy, 5)]


def test_series_rechaza_un_campo_no_permitido(db):
    """El nombre acaba en un getattr sobre el modelo: lista blanca obligatoria."""
    with pytest.raises(ValueError):
        history.series(db, "notes")


def test_actividad_remota_solo_toca_proyectos_sin_clon_local(db, monkeypatch):
    db.add_all([
        Project(name="remoto", remote_provider="github", remote_repo="p/remoto"),
        Project(name="local", local_path="/x", remote_provider="github", remote_repo="p/local"),
    ])
    db.commit()

    pedidos: list[str] = []

    def _weeks(repo, weeks=12):
        pedidos.append(repo)
        return [1] * 12

    monkeypatch.setattr(history.github_client, "get_commit_weeks", _weeks)
    assert history.refresh_remote_activity(db) == 1
    assert pedidos == ["p/remoto"]


def test_actividad_remota_no_guarda_ceros_cuando_github_aun_calcula(db, monkeypatch):
    """El endpoint de estadísticas responde 202 mientras calcula: se reintenta luego."""
    p = Project(name="remoto", remote_provider="github", remote_repo="p/remoto")
    db.add(p)
    db.commit()

    monkeypatch.setattr(history.github_client, "get_commit_weeks", lambda repo, weeks=12: None)
    assert history.refresh_remote_activity(db) == 0
    assert p.commit_weeks is None


def test_days_since_commit_se_guarda_en_el_snapshot(db):
    db.add(Project(name="a", last_commit_date=utcnow() - timedelta(days=10)))
    db.commit()
    history.take_snapshot(db)
    assert db.query(ProjectSnapshot).one().days_since_commit == 10
