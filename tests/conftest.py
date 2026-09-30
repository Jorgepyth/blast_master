"""
Aislamiento de la suite (RF-16, RF-16b, spec 002).

Dos capas independientes:

1. **Fixture autouse `_isolated_cwd`**: cada test arranca en su propio `tmp_path`
   vacío (`monkeypatch.chdir`), nunca en la raíz del repo. Cualquier ruta relativa
   como `.data/...` que use el código bajo prueba cae dentro de ese `tmp_path`.
2. **Audit hook de proceso**, instalado una sola vez al importar este archivo (no se
   puede quitar, por diseño de `sys.addaudithook`). Cubre lo que la capa 1 no cubre:
   código que corre antes de que un test tenga oportunidad de `chdir` (imports a
   nivel de módulo, por ejemplo) o que use una ruta absoluta. Audita los eventos
   `open`, `sqlite3.connect`, `os.mkdir`, `os.rename` (también `os.replace`),
   `os.remove` y `os.rmdir`, y hace fallar la operación (con la excepción
   propagándose hasta el test) si la ruta resuelta cae bajo el `.data/` real de
   este repo o bajo `/mnt/c/` (baseline H19; nunca tocar el filesystem de Windows
   ni las DBs reales desde un test). Los de directorios se sumaron el 2026-09-30:
   un test con la configuración sin parchear creó `.data/candle_bank/BTCUSD` con
   `os.makedirs`, que no pasa por `open`.

Ver specs/002-auto-resolucion-velas/spec.md, RF-16/RF-16b, y plan.md decisión T10.
"""
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FORBIDDEN_DATA_DIR = str(_REPO_ROOT / ".data") + os.sep
_FORBIDDEN_MNT_C = "/mnt/c" + os.sep


class ForbiddenTestPathError(RuntimeError):
    """Un test (o el código que ejercita) tocó una ruta real que RF-16 prohíbe."""


def _resolve_path_argument(raw_path):
    """Devuelve la ruta absoluta de `raw_path`, o None si no hay nada que chequear.

    `raw_path` llega tal cual del evento de auditoría: puede ser str, bytes,
    PathLike, un file descriptor (int) o, en sqlite3, el propio texto ':memory:'.
    """
    if isinstance(raw_path, bytes):
        text = raw_path.decode(errors="replace")
    elif isinstance(raw_path, (str, os.PathLike)):
        text = os.fspath(raw_path)
    else:
        # File descriptors (int) u otros argumentos sin ruta: nada que chequear.
        return None
    if text == ":memory:" or text.startswith("file::memory:"):
        return None
    try:
        return os.path.abspath(text)
    except (TypeError, ValueError):
        return None


# Evento de auditoría -> posiciones de sus argumentos que son rutas.
_GUARDED_EVENTS = {
    "open": (0,),
    "sqlite3.connect": (0,),
    "os.mkdir": (0,),
    "os.rename": (0, 1),  # os.rename y os.replace: origen y destino
    "os.remove": (0,),    # os.remove y os.unlink
    "os.rmdir": (0,),
}


def _guard_forbidden_paths(event: str, args: tuple) -> None:
    positions = _GUARDED_EVENTS.get(event)
    if positions is None:
        return
    for position in positions:
        if position < len(args):
            _check_forbidden_path(event, args[position])


def _check_forbidden_path(event: str, raw_path) -> None:
    resolved = _resolve_path_argument(raw_path)
    if resolved is None:
        return

    resolved_dir = resolved + os.sep
    if resolved == str(_REPO_ROOT / ".data") or resolved_dir.startswith(_FORBIDDEN_DATA_DIR):
        raise ForbiddenTestPathError(
            f"RF-16: un test tocó el .data/ real del repo via {event}() -> {resolved!r}. "
            "Usá tmp_path o :memory:."
        )
    if resolved == "/mnt/c" or resolved_dir.startswith(_FORBIDDEN_MNT_C):
        raise ForbiddenTestPathError(
            f"RF-16: un test tocó /mnt/c via {event}() -> {resolved!r}. "
            "Usá tmp_path o :memory:."
        )


# Se instala una sola vez, al importar conftest.py (arranque de la sesión de
# pytest). No se puede desinstalar (sys.addaudithook no ofrece "remove"), y no
# hace falta: queda activo para toda la sesión, que es justo lo que RF-16 pide.
sys.addaudithook(_guard_forbidden_paths)


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    """RF-16: todo test arranca en su propio tmp_path vacío, nunca en la raíz del repo."""
    monkeypatch.chdir(tmp_path)
