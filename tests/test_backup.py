"""Copia de seguridad de la base y rotación de las copias antiguas. Ver [PD-A5].

`existing[:-0]` es `existing[:0]` —lista vacía—, no "todos": con `BACKUP_KEEP=0`
no se borraba ningún backup, justo lo contrario de la intención. El mismo bug
que `media-catalog` ya había arreglado en el mismo fichero copiado.
"""
import os

import pytest

from app.services import scheduler


@pytest.fixture
def backups(tmp_path, monkeypatch):
    """Directorio de backups con cinco copias viejas ya creadas."""
    destino = tmp_path / "backups"
    destino.mkdir()
    for dia in range(1, 6):
        (destino / ("projects-2026080%d.db" % dia)).write_bytes(b"viejo")
    monkeypatch.setattr(scheduler.settings, "db_path", str(tmp_path / "projects.db"))
    return destino


def _copias(directorio) -> list[str]:
    return sorted(os.listdir(directorio))


def test_backup_keep_cero_conserva_solo_el_ultimo(backups, monkeypatch, db):
    """Con 0 se conserva al menos el que se acaba de crear: un directorio de
    backups vacío no le sirve a nadie."""
    monkeypatch.setattr(scheduler.settings, "backup_keep", 0)

    creado = scheduler.backup_database(str(backups / "projects-20260807.db"))

    assert _copias(backups) == ["projects-20260807.db"]
    assert os.path.exists(creado)


def test_backup_keep_tres_conserva_tres(backups, monkeypatch, db):
    monkeypatch.setattr(scheduler.settings, "backup_keep", 3)

    scheduler.backup_database(str(backups / "projects-20260807.db"))

    assert _copias(backups) == [
        "projects-20260804.db", "projects-20260805.db", "projects-20260807.db",
    ]


def test_el_backup_es_una_base_que_se_puede_abrir(tmp_path, db):
    """Lo que exige el plan antes de cualquier migración: que el fichero abra."""
    import sqlite3

    from app.models import Project

    db.add(Project(name="para-el-backup"))
    db.commit()

    destino = str(tmp_path / "copia.db")
    scheduler.backup_database(destino)

    con = sqlite3.connect(destino)
    try:
        nombres = [fila[0] for fila in con.execute("SELECT name FROM projects")]
    finally:
        con.close()
    assert "para-el-backup" in nombres


def test_fuera_del_directorio_de_backups_no_se_rota_nada(tmp_path, monkeypatch, db):
    """La rotación solo actúa en el directorio estándar: un backup a mano no
    puede llevarse por delante los ficheros del directorio destino."""
    monkeypatch.setattr(scheduler.settings, "backup_keep", 0)
    otro = tmp_path / "otro-sitio"
    otro.mkdir()
    (otro / "projects-20260101.db").write_bytes(b"no me toques")

    scheduler.backup_database(str(otro / "projects-20260807.db"))

    assert _copias(otro) == ["projects-20260101.db", "projects-20260807.db"]
