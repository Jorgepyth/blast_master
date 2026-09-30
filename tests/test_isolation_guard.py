"""
Meta-test del guard de aislamiento (RF-16, spec 002, tarea T4).

`tests/conftest.py` instala un audit hook que debería hacer fallar cualquier
test que toque el `.data/` real del repo o `/mnt/c/`. Probarlo desde adentro
de la propia suite no alcanza: el hook ya está activo para el proceso entero,
así que no hay forma de "desinstalarlo" para un test negativo. Este test corre
`pytest` en un **subproceso** aparte, contra un archivo temporal (escrito y
borrado acá mismo) con dos tests deliberadamente rotos — uno abre
`<repo>/.data/x.db` con ruta absoluta (para saltarse la capa 1, el `chdir` a
`tmp_path`) y el otro abre `/mnt/c/x` — y confirma que el subproceso los
reporta como fallos, citando `ForbiddenTestPathError`.

Sin mocks: si alguien afloja el guard (cambia el evento auditado, la
comparación de rutas, o lo saca de `conftest.py`), este test deja de pasar.
"""
import os
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_META_TEST_PATH = os.path.join(_REPO_ROOT, "tests", "test_zz_isolation_meta_tmp.py")
_TARGET_DATA_FILE = os.path.join(_REPO_ROOT, ".data", "x.db")
_TARGET_DATA_DIR = os.path.join(_REPO_ROOT, ".data", "candle_bank", "ZZPROBE")

_META_TEST_CONTENT = f"""
import os


def test_touches_repo_data_dir():
    open({_TARGET_DATA_FILE!r}, "w").close()


def test_touches_mnt_c():
    open("/mnt/c/x", "w").close()


def test_creates_a_directory_in_repo_data_dir():
    os.makedirs({_TARGET_DATA_DIR!r})


def test_moves_a_file_into_repo_data_dir(tmp_path):
    source = tmp_path / "y"
    source.write_text("y")
    os.replace(source, {_TARGET_DATA_FILE!r})


def test_deletes_in_repo_data_dir():
    os.remove({_TARGET_DATA_FILE!r})
"""


def _cleanup_meta_test_artifacts():
    if os.path.exists(_META_TEST_PATH):
        os.remove(_META_TEST_PATH)
    pycache_dir = os.path.join(_REPO_ROOT, "tests", "__pycache__")
    if os.path.isdir(pycache_dir):
        for name in os.listdir(pycache_dir):
            if "test_zz_isolation_meta_tmp" in name:
                os.remove(os.path.join(pycache_dir, name))


def test_conftest_guard_fails_offending_tests():
    # Defensivo: si una corrida anterior se cortó a mitad de camino, limpiá primero.
    _cleanup_meta_test_artifacts()

    with open(_META_TEST_PATH, "w") as f:
        f.write(_META_TEST_CONTENT)

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", _META_TEST_PATH],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        _cleanup_meta_test_artifacts()

    output = result.stdout + result.stderr

    # El propio .data/x.db nunca debió llegar a crearse: el hook aborta open()
    # antes del syscall real, sin importar si el directorio .data/ existe.
    assert not os.path.exists(_TARGET_DATA_FILE), (
        "el guard no frenó la escritura a tiempo:\n" + output
    )
    assert not os.path.exists(_TARGET_DATA_DIR), "el guard no frenó la creación del directorio:\n" + output

    assert result.returncode == 1, f"se esperaba returncode 1 (fallos, no error de colección):\n{output}"
    assert "5 failed" in output, output
    for name in ("test_touches_repo_data_dir", "test_touches_mnt_c", "test_creates_a_directory_in_repo_data_dir",
                 "test_moves_a_file_into_repo_data_dir", "test_deletes_in_repo_data_dir"):
        assert name in output, output
    assert output.count("ForbiddenTestPathError") >= 5, output
    assert "ForbiddenTestPathError" in output, output
