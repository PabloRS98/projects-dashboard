"""Límites del conteo de TODOs. Ver [PD-M9].

`scan_todos` recorría el árbol entero sin límite de tamaño por fichero, sin tope
de ficheros y sin presupuesto de tiempo, y casaba "TODO" dentro de palabras. Con
un bundle minificado de 10 MB en una sola línea, esa línea entra en memoria como
una única cadena.
"""
import time

from app.services import local_scanner


def _fichero(base, nombre: str, contenido: str):
    ruta = base / nombre
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


# --------------------------------------------------------------------------
# Palabra completa
# --------------------------------------------------------------------------

def test_scan_todos_casa_palabras_completas(tmp_path):
    """"TODOS_LOS_CASOS", "METODOS" o "TODO_EXTENSIONS" no son pendientes.

    El propio local_scanner.py se contaba a sí mismo por esto.
    """
    _fichero(tmp_path, "a.py", "TODOS_LOS_USUARIOS = 1\nMETODOS = 2\nTODO_EXTENSIONS = {}\n")
    assert local_scanner.scan_todos(str(tmp_path))["count"] == 0


def test_scan_todos_sigue_contando_los_de_verdad(tmp_path):
    _fichero(tmp_path, "a.py", "# TODO: arreglar esto\nx = 1\n# FIXME urgente\n")
    resultado = local_scanner.scan_todos(str(tmp_path))
    assert resultado["count"] == 2
    assert resultado["items"][0]["line"] == 1


def test_scan_todos_no_distingue_el_separador(tmp_path):
    _fichero(tmp_path, "a.py", "# TODO(pablo): con parentesis\n# TODO: normal\n")
    assert local_scanner.scan_todos(str(tmp_path))["count"] == 2


# --------------------------------------------------------------------------
# Límite de tamaño por fichero
# --------------------------------------------------------------------------

def test_scan_todos_ignora_ficheros_grandes(tmp_path):
    """Un bundle minificado va en una sola línea: el iterador de líneas lo carga
    entero en memoria buscando el salto que no llega."""
    _fichero(tmp_path, "bundle.js", "var x=1;// TODO\n" + ("a" * 2_000_000))
    _fichero(tmp_path, "normal.js", "// TODO: este sí cuenta\n")

    resultado = local_scanner.scan_todos(str(tmp_path))
    assert resultado["count"] == 1
    assert resultado["items"][0]["file"] == "normal.js"


def test_el_limite_de_tamano_es_configurable(tmp_path):
    _fichero(tmp_path, "a.py", "# TODO: pequeño\n")
    assert local_scanner.scan_todos(str(tmp_path), max_file_bytes=5)["count"] == 0


# --------------------------------------------------------------------------
# Tope de ficheros y presupuesto de tiempo
# --------------------------------------------------------------------------

def test_scan_todos_respeta_el_tope_de_ficheros(tmp_path):
    for i in range(20):
        _fichero(tmp_path, "f%d.py" % i, "# TODO: pendiente\n")

    resultado = local_scanner.scan_todos(str(tmp_path), max_files=5)
    assert resultado["count"] == 5
    assert resultado["parcial"] is True


def test_un_escaneo_completo_no_se_marca_parcial(tmp_path):
    _fichero(tmp_path, "a.py", "# TODO: uno\n")
    assert local_scanner.scan_todos(str(tmp_path))["parcial"] is False


def test_scan_todos_respeta_el_presupuesto_de_tiempo(tmp_path):
    for i in range(200):
        _fichero(tmp_path, "f%d.py" % i, "# TODO: pendiente\n")

    inicio = time.monotonic()
    resultado = local_scanner.scan_todos(str(tmp_path), max_seconds=0)
    assert time.monotonic() - inicio < 2
    assert resultado["parcial"] is True


# --------------------------------------------------------------------------
# Directorios ignorados
# --------------------------------------------------------------------------

def test_se_ignoran_los_directorios_de_artefactos(tmp_path):
    """`target`, `vendor`, `.next`, `coverage`… son salida de compilación: sus
    TODOs no son del proyecto y pueden ser cientos de miles."""
    for carpeta in ("target", "vendor", ".next", "coverage", ".tox", ".mypy_cache"):
        _fichero(tmp_path, "%s/x.py" % carpeta, "# TODO: no es mio\n")
    _fichero(tmp_path, "src/x.py", "# TODO: este si\n")

    resultado = local_scanner.scan_todos(str(tmp_path))
    assert resultado["count"] == 1


def test_max_results_limita_los_items_pero_no_el_recuento(tmp_path):
    """Comportamiento existente que no cambia: el contador sigue siendo exacto."""
    for i in range(10):
        _fichero(tmp_path, "f%d.py" % i, "# TODO: pendiente\n")

    resultado = local_scanner.scan_todos(str(tmp_path), max_results=3)
    assert resultado["count"] == 10
    assert len(resultado["items"]) == 3
