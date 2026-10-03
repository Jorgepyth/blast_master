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
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Mapping, Optional

from sqlalchemy import distinct, select

import config.auto_resolution as cfg
from tools.candle_bank import SyncResult, bank_lock_held, read_bank_status

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
Popen = subprocess.Popen  # los tests lo reemplazan acá, sin tocar el `subprocess.Popen` del resto del CLI
POLL_S = 0.5

# Procesos lanzados por este CLI que todavía no se cosecharon. Guardar la referencia evita que el pipe de un export con
# espera se cierre antes de que el proceso termine, y `poll()` en cada lanzamiento cosecha a los que ya terminaron.
_RUNNING: List[subprocess.Popen] = []


@dataclass
class ExportLaunch:
    """Lo que hizo un disparo. `launched`: los símbolos que exporta `process`, en orden. `in_progress`: los que ya
    tenían otro export en curso y no se lanzaron (RF-20f). `launch_error`: el proceso no se pudo lanzar."""
    process: Optional[subprocess.Popen]
    launched: List[str]
    in_progress: List[str]
    launch_error: Optional[str] = None
    lines: List[str] = field(default_factory=list)  # las líneas del proceso, una por símbolo (solo con espera)
    reader: Optional[threading.Thread] = None


def enabled() -> bool:
    return bool(cfg.AUTO_EXPORT)


def log_path() -> str:
    return os.path.join(cfg.ACCOUNTS_DATA_DIR, cfg.AUTO_EXPORT_LOG_NAME)


def symbols_for_assets(assets: Iterable[Optional[str]], symbol_map: Optional[Mapping[str, str]] = None) -> List[str]:
    """Los símbolos MT5 de `assets`, sin repetir y en orden. Un asset que no está en `MT5_SYMBOL_MAP` no se exporta."""
    symbol_map = cfg.MT5_SYMBOL_MAP if symbol_map is None else symbol_map
    return list(dict.fromkeys(symbol_map[asset] for asset in assets if asset in symbol_map))


def account_symbols(accounts: Optional[Mapping[str, str]] = None, accounts_data_dir: Optional[str] = None,
                    symbol_map: Optional[Mapping[str, str]] = None) -> List[str]:
    """Los símbolos MT5 de las cuentas (por defecto, `REAL_ACCOUNTS`), en el orden de las cuentas (RF-20c, RF-20d):
    los `asset` distintos de cada `unified_department`, leídos en `mode=ro` pidiendo solo esa columna. Una DB que no
    existe se saltea."""
    from tools.database import UnifiedDepartment
    from tools.p2_backtest import open_readonly_session

    accounts = cfg.REAL_ACCOUNTS if accounts is None else accounts
    accounts_data_dir = cfg.ACCOUNTS_DATA_DIR if accounts_data_dir is None else accounts_data_dir
    assets: List[str] = []
    for db_name in accounts.values():
        db_path = os.path.join(accounts_data_dir, db_name)
        if not os.path.exists(db_path):
            continue
        session = open_readonly_session(db_path)
        try:
            assets += sorted(row[0] for row in session.execute(select(distinct(UnifiedDepartment.asset))).all()
                             if row[0] is not None)
        finally:
            session.close()
    return symbols_for_assets(assets, symbol_map)


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


def _drain(launch: ExportLaunch) -> None:
    for raw in iter(launch.process.stdout.readline, b""):
        line = raw.decode("utf-8", errors="replace").strip()
        if line:
            launch.lines.append(line)


def start_export(symbols: Iterable[Optional[str]], wait: bool = False) -> Optional[ExportLaunch]:
    """
    Lanza el export de `symbols` (símbolos MT5) en un proceso aparte y vuelve enseguida. `None` si `AUTO_EXPORT` está
    apagado o no hay ningún símbolo. Los símbolos con un export en curso no se lanzan (RF-20f); si son todos, no se
    lanza ningún proceso. Con `wait=True` la salida del proceso se lee de fondo, para `wait_for_export`.
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
            stdout=subprocess.PIPE if wait else subprocess.DEVNULL, stderr=stderr, start_new_session=True)
    except (OSError, ValueError) as exc:
        launch.launch_error = f"sync_not_launched: {exc}"
        launch.launched = []
        return launch
    finally:
        if stderr is not subprocess.DEVNULL:
            stderr.close()
    _RUNNING.append(launch.process)
    if wait:
        launch.reader = threading.Thread(target=_drain, args=(launch,), daemon=True)
        launch.reader.start()
    return launch


def _pending(launch: ExportLaunch) -> List[str]:
    pending: List[str] = []
    if launch.process is not None and (launch.process.poll() is None
                                       or (launch.reader is not None and launch.reader.is_alive())):
        pending += launch.launched[len(launch.lines):]
    pending += [symbol for symbol in launch.in_progress if bank_lock_held(_bank_dir(symbol))]
    return pending


def _status_line(symbol: str) -> str:
    """Cómo terminó el export de `symbol` que ya estaba en curso, según su `status.json`."""
    from tools.candle_sync import format_result_line

    try:
        status = read_bank_status(_bank_dir(symbol)) or {}
    except (ValueError, OSError):
        status = {}
    last = status.get("last_export") or {}
    if not last.get("result"):
        return f"Candle export finished for {symbol} (started earlier); see candles status"
    result = SyncResult(symbol=symbol, result=last["result"], verified_by=status.get("verified_by"),
                        bars_added=last.get("bars_added") or {}, run_id=last.get("run_id"),
                        error=status.get("last_error"))
    return f"{format_result_line(result)} (started earlier)"


def _skipped(why: str, pending: List[str]) -> str:
    return (f"Candle export skipped: {why} ({', '.join(pending)}); continuing with the candles in the bank, the export "
            f"keeps running in the background")


def wait_for_export(launch: Optional[ExportLaunch], wait_s: float,
                    progress: Optional[Callable[[List[str], float], None]] = None,
                    clock: Callable[[], float] = time.monotonic,
                    sleep: Callable[[float], None] = time.sleep) -> List[str]:
    """
    Espera a que terminen los exports de `launch`, como mucho `wait_s` segundos, y devuelve las líneas para mostrar:
    una por símbolo que terminó y, si alguno no terminó, una "Candle export skipped: ..." (RF-20b, RF-20e). `progress`
    recibe en cada vuelta los símbolos pendientes y los segundos transcurridos. Ctrl+C corta la espera, no el export.
    """
    if launch is None:
        return []
    if launch.launch_error:
        return [f"Candle export skipped: {launch.launch_error}"]
    start = clock()
    pending = _pending(launch)
    try:
        while pending:
            elapsed = clock() - start
            if elapsed >= wait_s:
                break
            if progress is not None:
                progress(pending, elapsed)
            sleep(POLL_S)
            pending = _pending(launch)
        interrupted = False
    except KeyboardInterrupt:
        interrupted = True
        pending = _pending(launch)
    lines = list(launch.lines)
    process_done = (launch.process is not None and launch.process.poll() is not None
                    and not (launch.reader is not None and launch.reader.is_alive()))
    if process_done:
        missing = launch.launched[len(launch.lines):]
        if missing:
            lines.append(f"Candle export skipped: the export process ended without a result "
                         f"(exit {launch.process.returncode}; see {log_path()}) ({', '.join(missing)})")
    lines += [_status_line(symbol) for symbol in launch.in_progress if symbol not in pending]
    if pending:
        lines.append(_skipped("wait cancelled with Ctrl+C" if interrupted else f"not finished in {wait_s:g}s",
                              pending))
    return lines
