"""
tools/auto_export.py — Los disparos del export automático de velas (spec 002, T52 a T54, RF-20 a RF-20f, N31).

El export en sí es `tools/candle_sync.py`, que corre como proceso aparte (plan.md decisión T8): un hilo dentro del CLI
moriría al cerrarlo y mezclaría su salida con la de Rich. Este módulo solo lo lanza y, cuando hace falta, espera:

- **En segundo plano** (`start_export`): al guardar un unified analysis (RF-20) y al abrir el CLI (RF-20d). El proceso
  sigue solo aunque el CLI se cierre (`start_new_session`), y lo que pasó queda en `candle_export.log` y en el
  `status.json` de cada símbolo (`candles status`).
- **Con espera acotada** (`start_export(wait=True)` + `wait_for_export`): al abrir un Efficiency o Tactical Audit
  (RF-20b) y antes del reporte y del backfill (RF-20c). Espera hasta `AUTO_EXPORT_WAIT_S`; si no termina, sigue con las
  velas que ya tiene el banco y el export continúa de fondo.

Varios símbolos van en **un solo proceso, uno detrás del otro** (`candle_sync --symbol A --symbol B`), para no abrirle
a MT5 varias sesiones a la vez. Un símbolo que ya tiene un export en curso (su candado está tomado) no se lanza otra vez
(RF-20f); si hay espera, se espera a que ese otro termine.

Con `AUTO_EXPORT` apagado no se lanza nada ni se lee ninguna DB (N31). Nada de esto levanta hacia el CLI (INV-2).
"""
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional

import config.auto_resolution as cfg
from tools.candle_bank import bank_lock_held

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
Popen = subprocess.Popen  # los tests lo reemplazan acá, sin tocar el `subprocess.Popen` del resto del CLI

# Procesos lanzados por este CLI que todavía no se cosecharon: `poll()` en cada lanzamiento cosecha a los que ya
# terminaron, para que no queden como zombis mientras el CLI siga abierto.
_RUNNING: List[subprocess.Popen] = []


@dataclass
class ExportLaunch:
    """Lo que hizo un disparo. `launched`: los símbolos que exporta `process`, en orden. `in_progress`: los que ya
    tenían otro export en curso y no se lanzaron (RF-20f). `launch_error`: el proceso no se pudo lanzar."""
    process: Optional[subprocess.Popen]
    launched: List[str]
    in_progress: List[str]
    launch_error: Optional[str] = None


def enabled() -> bool:
    return bool(cfg.AUTO_EXPORT)


def log_path() -> str:
    return os.path.join(cfg.ACCOUNTS_DATA_DIR, cfg.AUTO_EXPORT_LOG_NAME)


def symbols_for_assets(assets: Iterable[Optional[str]], symbol_map: Optional[Mapping[str, str]] = None) -> List[str]:
    """Los símbolos MT5 de `assets`, sin repetir y en orden. Un asset que no está en `MT5_SYMBOL_MAP` no se exporta."""
    symbol_map = cfg.MT5_SYMBOL_MAP if symbol_map is None else symbol_map
    return list(dict.fromkeys(symbol_map[asset] for asset in assets if asset in symbol_map))


def export_command(symbols: List[str], log: str) -> List[str]:
    argv = [sys.executable, "-m", "tools.candle_sync"]
    for symbol in symbols:
        argv += ["--symbol", symbol]
    return argv + ["--log", log]


def _child_env() -> Dict[str, str]:
    """El proceso de fondo usa las mismas rutas que este CLI, y encuentra los módulos del repo desde cualquier cwd."""
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT_DIR + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["ACCOUNTS_DATA_DIR"] = cfg.ACCOUNTS_DATA_DIR
    env["CANDLE_BANK_DIR"] = cfg.CANDLE_BANK_DIR
    env["MT5_INCOMING_DIR"] = cfg.MT5_INCOMING_DIR
    return env


def _bank_dir(symbol: str) -> str:
    return os.path.join(cfg.CANDLE_BANK_DIR, symbol)


def start_export(symbols: Iterable[Optional[str]]) -> Optional[ExportLaunch]:
    """
    Lanza el export de `symbols` (símbolos MT5) en un proceso aparte y vuelve enseguida. `None` si `AUTO_EXPORT` está
    apagado o no hay ningún símbolo. Los símbolos con un export en curso no se lanzan (RF-20f); si son todos, no se
    lanza ningún proceso.
    """
    if not enabled():
        return None
    symbols = list(dict.fromkeys(symbol for symbol in symbols if symbol))
    if not symbols:
        return None
    _RUNNING[:] = [process for process in _RUNNING if process.poll() is None]
    in_progress = [symbol for symbol in symbols if bank_lock_held(_bank_dir(symbol))]
    to_launch = [symbol for symbol in symbols if symbol not in in_progress]
    launch = ExportLaunch(None, to_launch, in_progress)
    if not to_launch:
        return launch
    log = log_path()
    try:
        stderr = open(log, "ab")
    except OSError:
        stderr = subprocess.DEVNULL
    try:
        launch.process = Popen(
            export_command(to_launch, log), cwd=ROOT_DIR, env=_child_env(), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True)
    except (OSError, ValueError) as exc:
        launch.launch_error = f"sync_not_launched: {exc}"
        launch.launched = []
        return launch
    finally:
        if stderr is not subprocess.DEVNULL:
            stderr.close()
    _RUNNING.append(launch.process)
    return launch
