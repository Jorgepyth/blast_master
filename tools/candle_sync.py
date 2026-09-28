"""
tools/candle_sync.py — Proceso que trae velas nuevas de MT5 al banco (spec 002,
T20, RF-20e, RF-20f, RF-1c).

Corre como proceso aparte, igual que el sync a Notion (plan.md decisión T8): un
hilo dentro del CLI moriría al cerrarlo y mezclaría su salida con la de Rich.
Para UN símbolo MT5:

  1. toma el candado del símbolo -- ANTES de lanzar nada, así un segundo export
     del mismo símbolo ni siquiera arranca (RF-1d, RF-20f);
  2. arma el comando del exportador de Windows desde la configuración y lo corre
     vía `powershell.exe` con un timeout (`EXPORT_TIMEOUT_S`);
  3. si el exportador terminó bien, fusiona su corrida con el banco
     (`tools.candle_bank.merge_incoming_run`, que verifica el reloj);
  4. escribe `status.json` y suelta el candado.

Nada de esto lanza hacia quien llama (INV-2): todo lo que sale mal vuelve como
`SyncResult` con un motivo, y el banco queda intacto (RF-1c). Un export que no
llega a fusionar se muestra como una línea "Candle export skipped: <motivo>".

[NO VERIFICADO] El armado del comando de PowerShell (comillas, `exit
$LASTEXITCODE`) y la llamada WSL -> powershell.exe -> Python de Windows solo se
pueden probar con Windows y MT5 abiertos: los cubre el spike (T22). Los tests
usan un exportador falso.

Códigos de salida (plan.md §4, mismos que `candles export`):
  0 = se fusionó;  2 = el reloj no se verificó (clock_unverified/clock_misaligned);
  6 = el export falló o se saltó (export_failed, o ya había otro en curso).
"""
import argparse
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from sqlalchemy import select  # noqa: E402

import config.auto_resolution as cfg  # noqa: E402
from tools.candle_bank import (  # noqa: E402
    CandleBankLockedError,
    SYNC_RESULT_LOCKED,
    SYNC_RESULT_MERGED,
    SyncResult,
    acquire_bank_lock,
    merge_incoming_run,
    write_sync_status,
)
from tools.database import UnifiedDepartment  # noqa: E402
from tools.p2_backtest import open_readonly_session  # noqa: E402

EXIT_MERGED = 0
EXIT_NOT_VERIFIED = 2
EXIT_EXPORT_FAILED = 6

_ANCHOR_FORMAT = "%Y-%m-%d %H:%M:%S"
_GT_OFFSET_HOURS = 6  # Guatemala = UTC-6, sin horario de verano (convención del repo).
_RUN_ID_LINE = re.compile(r"^RUN_ID:\s*(\S+)\s*$", re.MULTILINE)


# --------------------------------------------------------------------------
# Comando del exportador
# --------------------------------------------------------------------------

def wsl_to_windows_path(path: str) -> str:
    """`/mnt/c/Users/x/_incoming` -> `C:\\Users\\x\\_incoming`. Solo rutas bajo
    `/mnt/<unidad>/`: cualquier otra no la ve el Python de Windows por letra de
    unidad, y no se adivina una conversión (ValueError)."""
    match = re.fullmatch(r"/mnt/([a-zA-Z])(/.*)?", path)
    if not match:
        raise ValueError(f"{path!r} is not under /mnt/<drive>/, so the Windows exporter can't write there")
    drive, rest = match.group(1).upper(), (match.group(2) or "")
    return f"{drive}:" + rest.replace("/", "\\") + ("" if rest else "\\")


def _ps_quote(value: str) -> str:
    """Entrecomilla para PowerShell con comillas simples (se duplican las internas)."""
    return "'" + value.replace("'", "''") + "'"


def build_export_command(
    mt5_symbol: str,
    windows_python: str,
    exporter_win_path: str,
    incoming_dir: str,
    min_anchor: datetime,
    max_anchor: datetime,
    dst_rule: str,
    timeframes: Optional[Sequence[str]] = None,
) -> List[str]:
    """
    Arma el argv que lanza el exportador de Windows desde WSL: `powershell.exe`
    llama al Python de Windows con `--per-run-dir` (una carpeta nueva por
    corrida bajo `incoming_dir`, que es una ruta WSL y acá se pasa como ruta de
    Windows). Termina con `exit $LASTEXITCODE` para conservar el código de salida
    del exportador. `--server-utc-offset` no se pasa: el exportador lo detecta
    del último tick, y con el mercado cerrado falla (RF-20e).
    """
    exporter_args = [
        "--symbol", mt5_symbol,
        "--out-dir", wsl_to_windows_path(incoming_dir),
        "--per-run-dir",
        "--min-anchor", min_anchor.strftime(_ANCHOR_FORMAT),
        "--max-anchor", max_anchor.strftime(_ANCHOR_FORMAT),
        "--dst-rule", dst_rule,
    ]
    if timeframes:
        exporter_args += ["--timeframes", ",".join(timeframes)]
    call = " ".join(_ps_quote(part) for part in [windows_python, exporter_win_path, *exporter_args])
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", f"& {call}; exit $LASTEXITCODE"]


# --------------------------------------------------------------------------
# Ejecución con timeout
# --------------------------------------------------------------------------

@dataclass
class ExporterRun:
    returncode: Optional[int]
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    launch_error: Optional[str] = None

    @property
    def run_id(self) -> Optional[str]:
        match = _RUN_ID_LINE.search(self.stdout)
        return match.group(1) if match else None


def run_exporter(command: Sequence[str], timeout_s: float) -> ExporterRun:
    """
    Corre `command` con `timeout_s`. El hijo arranca en su propia sesión para
    poder matar TODO su grupo de procesos al vencer el timeout (`powershell.exe`
    y lo que haya lanzado); con `subprocess.run(timeout=)` solo se mata al hijo
    directo. Nota: matar `powershell.exe` desde WSL puede no matar al Python de
    Windows que lanzó -- si sigue corriendo, solo va a terminar de escribir su
    carpeta de corrida, que nadie fusiona [NO VERIFICADO, T22].
    """
    try:
        proc = subprocess.Popen(
            list(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
    except OSError as exc:
        return ExporterRun(returncode=None, launch_error=f"{type(exc).__name__}: {exc}")
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = proc.communicate()
        return ExporterRun(returncode=proc.returncode, stdout=stdout or "", stderr=stderr or "", timed_out=True)
    return ExporterRun(returncode=proc.returncode, stdout=stdout or "", stderr=stderr or "")


def _tail(text: str, limit: int = 200) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1][:limit] if lines else ""


# --------------------------------------------------------------------------
# Anclas desde las DBs de cuenta (solo lectura)
# --------------------------------------------------------------------------

def derive_export_anchors(
    mt5_symbol: str,
    accounts_data_dir: str,
    now_gt: datetime,
    real_accounts: Optional[Dict[str, str]] = None,
    symbol_map: Optional[Dict[str, str]] = None,
) -> Tuple[datetime, datetime]:
    """
    `(min_anchor, max_anchor)` en hora GT naive para el exportador: el
    `created_at` más viejo y más nuevo de los análisis de `REAL_ACCOUNTS` cuyo
    `asset` mapea a `mt5_symbol`, leídos en `mode=ro` pidiendo solo esas
    columnas (una DB sin migrar no rompe). El exportador pide velas hacia atrás
    desde `min_anchor`. Sin análisis, ambos son `now_gt`.
    """
    real_accounts = cfg.REAL_ACCOUNTS if real_accounts is None else real_accounts
    symbol_map = cfg.MT5_SYMBOL_MAP if symbol_map is None else symbol_map
    tickers = [ticker for ticker, sym in symbol_map.items() if sym == mt5_symbol]
    times: List[datetime] = []
    for db_filename in real_accounts.values():
        db_path = os.path.join(accounts_data_dir, db_filename)
        if not tickers or not os.path.exists(db_path):
            continue
        session = open_readonly_session(db_path)
        try:
            rows = session.execute(
                select(UnifiedDepartment.created_at).where(UnifiedDepartment.asset.in_(tickers))
            ).all()
        finally:
            session.close()
        times += [row[0] for row in rows if row[0] is not None]
    if not times:
        return now_gt, now_gt
    return min(times), max(times)


# --------------------------------------------------------------------------
# Sincronización de un símbolo
# --------------------------------------------------------------------------

def _now_gt() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=_GT_OFFSET_HOURS)


def _launch_and_merge(
    mt5_symbol: str,
    bank_dir: str,
    incoming_root: str,
    accounts_data_dir: str,
    dst_rule: str,
    timeout_s: float,
    export_command: Optional[Sequence[str]],
    windows_python: Optional[str],
    exporter_win_path: Optional[str],
    real_accounts: Optional[Dict[str, str]],
    symbol_map: Optional[Dict[str, str]],
    now_gt: datetime,
) -> SyncResult:
    def failed(reason: str, run_id: Optional[str] = None) -> SyncResult:
        return SyncResult(symbol=mt5_symbol, result=cfg.REASON_EXPORT_FAILED, run_id=run_id, error=reason)

    if export_command is None:
        if not windows_python or not exporter_win_path:
            return failed("exporter_not_configured: set WINDOWS_PYTHON and EXPORTER_WIN_PATH in .env")
        min_anchor, max_anchor = derive_export_anchors(
            mt5_symbol, accounts_data_dir, now_gt, real_accounts, symbol_map)
        try:
            export_command = build_export_command(
                mt5_symbol, windows_python, exporter_win_path,
                incoming_root, min_anchor, max_anchor, dst_rule)
        except ValueError as exc:
            return failed(f"incoming_dir_not_on_windows_drive: {exc}")

    run = run_exporter(export_command, timeout_s)
    if run.launch_error:
        return failed(f"exporter_not_launched: {run.launch_error}")
    if run.timed_out:
        return failed(f"timeout: the exporter did not finish in {timeout_s:g}s", run.run_id)
    if run.returncode != 0:
        detail = _tail(run.stderr) or _tail(run.stdout)
        return failed(f"exporter_exit_{run.returncode}: {detail}".rstrip(": "), run.run_id)

    run_id = run.run_id
    incoming_dir = os.path.join(incoming_root, mt5_symbol, run_id) if run_id else None
    if not run_id or not os.path.isdir(incoming_dir):
        return failed("no_run_dir: the exporter finished OK but reported no usable RUN_ID", run_id)

    return merge_incoming_run(
        bank_dir, incoming_dir, mt5_symbol, run_id, accounts_data_dir,
        export_moment=now_gt, dst_rule=dst_rule,
        real_accounts=real_accounts, symbol_map=symbol_map,
    )


def sync_symbol(
    mt5_symbol: str,
    bank_root: Optional[str] = None,
    incoming_root: Optional[str] = None,
    accounts_data_dir: Optional[str] = None,
    dst_rule: Optional[str] = None,
    timeout_s: Optional[float] = None,
    export_command: Optional[Sequence[str]] = None,
    windows_python: Optional[str] = None,
    exporter_win_path: Optional[str] = None,
    real_accounts: Optional[Dict[str, str]] = None,
    symbol_map: Optional[Dict[str, str]] = None,
    now_gt: Optional[datetime] = None,
) -> SyncResult:
    """
    Trae y fusiona las velas de `mt5_symbol` (p.ej. `"XAUUSD"`). Todos los
    parámetros salen de `config.auto_resolution` si no se pasan (se leen en el
    momento de la llamada); `export_command` reemplaza al comando de
    PowerShell (los tests pasan un exportador falso). Nunca levanta.

    Con el candado del símbolo tomado devuelve `locked` sin lanzar nada y sin
    tocar `status.json` (RF-20f). En cualquier otro caso deja `status.json`
    reflejando el resultado (`write_sync_status`).
    """
    bank_root = cfg.CANDLE_BANK_DIR if bank_root is None else bank_root
    incoming_root = cfg.MT5_INCOMING_DIR if incoming_root is None else incoming_root
    accounts_data_dir = cfg.ACCOUNTS_DATA_DIR if accounts_data_dir is None else accounts_data_dir
    dst_rule = cfg.BROKER_DST_RULE if dst_rule is None else dst_rule
    timeout_s = cfg.EXPORT_TIMEOUT_S if timeout_s is None else timeout_s
    windows_python = cfg.WINDOWS_PYTHON if windows_python is None else windows_python
    exporter_win_path = cfg.EXPORTER_WIN_PATH if exporter_win_path is None else exporter_win_path
    now_gt = _now_gt() if now_gt is None else now_gt
    bank_dir = os.path.join(bank_root, mt5_symbol)

    try:
        with acquire_bank_lock(bank_dir):
            try:
                result = _launch_and_merge(
                    mt5_symbol, bank_dir, incoming_root, accounts_data_dir, dst_rule, timeout_s,
                    export_command, windows_python, exporter_win_path, real_accounts, symbol_map, now_gt)
            except Exception as exc:  # noqa: BLE001 -- INV-2: nada se propaga hacia quien llama
                result = SyncResult(
                    symbol=mt5_symbol, result=cfg.REASON_EXPORT_FAILED,
                    error=f"unexpected_error: {type(exc).__name__}: {exc}")
            try:
                write_sync_status(bank_dir, result)
            except Exception as exc:  # noqa: BLE001
                result.error = f"{result.error or ''} (status.json not written: {exc})".strip()
            return result
    except CandleBankLockedError as exc:
        return SyncResult(symbol=mt5_symbol, result=SYNC_RESULT_LOCKED, error=f"export_in_progress: {exc}")


# --------------------------------------------------------------------------
# Salida y proceso
# --------------------------------------------------------------------------

def exit_code_for(result: SyncResult) -> int:
    if result.result == SYNC_RESULT_MERGED:
        return EXIT_MERGED
    if result.result in (cfg.REASON_CLOCK_UNVERIFIED, cfg.REASON_CLOCK_MISALIGNED):
        return EXIT_NOT_VERIFIED
    return EXIT_EXPORT_FAILED


def format_result_line(result: SyncResult) -> str:
    """Una línea en inglés (N30). `export_failed` y `locked` son los "skipped"
    de RF-20e/RF-20f."""
    if result.result == SYNC_RESULT_MERGED:
        added = ", ".join(f"{tf}: +{n}" for tf, n in result.bars_added.items()) or "no new candles"
        return f"Candle export merged for {result.symbol} (clock verified by {result.verified_by}; {added})"
    if result.result == SYNC_RESULT_LOCKED:
        return f"Candle export skipped: export_in_progress ({result.symbol})"
    if result.result == cfg.REASON_EXPORT_FAILED:
        return f"Candle export skipped: {result.error}"
    return f"Candle export not merged for {result.symbol}: {result.result} ({result.error})"


def main(argv: Optional[Sequence[str]] = None, **sync_overrides) -> int:
    """`sync_overrides` se pasan tal cual a `sync_symbol` (los tests fijan rutas
    y el exportador falso; el proceso real no pasa ninguno)."""
    parser = argparse.ArgumentParser(description="Trae velas nuevas de MT5 al banco (un símbolo).")
    parser.add_argument("--symbol", required=True, help="Símbolo MT5, p.ej. XAUUSD.")
    parser.add_argument(
        "--wait-seconds", type=float, default=None,
        help="Cuánto espera este proceso al exportador antes de rendirse (default: EXPORT_TIMEOUT_S).",
    )
    args = parser.parse_args(argv)
    if args.wait_seconds is not None:
        sync_overrides.setdefault("timeout_s", args.wait_seconds)
    result = sync_symbol(args.symbol, **sync_overrides)
    print(format_result_line(result))
    return exit_code_for(result)


if __name__ == "__main__":
    raise SystemExit(main())
