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

Verificado con Windows real (2026-09-28, T20b, `tests/test_candle_sync_windows_
interop.py`, opt-in): `exit $LASTEXITCODE` conserva el código de salida, las
comillas simples con `''` entregan bien argumentos con espacios y apóstrofes, y
matar `powershell.exe` desde WSL NO mata al programa de Windows que lanzó (por
eso el `WINPID` y `taskkill /T`). [NO VERIFICADO] todavía: el exportador real
(MT5, el Python de Windows con `MetaTrader5`), que cubre el spike (T22). Los
tests normales usan un exportador falso y nunca lanzan `powershell.exe`.

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
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from sqlalchemy import select  # noqa: E402

import config.auto_resolution as cfg  # noqa: E402
from tools.candle_bank import (  # noqa: E402
    INHERITED_PREFIX,
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
_WINPID_LINE = re.compile(r"^WINPID:\s*(\d+)", re.MULTILINE)
_SKIPPED_TF_LINE = re.compile(r"^SKIPPED_TF:\s*(\S+)\s*$", re.MULTILINE)
# N43: servidor de la cuenta y desfase base del export, para la herencia del reloj.
_SERVER_LINE = re.compile(r"^SERVER:[ \t]*(\S(?:.*\S)?)[ \t\r]*$", re.MULTILINE)
_BASE_UTC_OFFSET_LINE = re.compile(r"^BASE_UTC_OFFSET:\s*([-+]?\d+(?:\.\d+)?)\s*$", re.MULTILINE)

# powershell.exe arranca con el directorio actual de quien lo lanza; desde WSL eso
# es una ruta UNC (`\\wsl.localhost\...`) que cmd.exe rechaza ("no se permiten
# rutas UNC"). Se lanza desde una unidad de Windows, que existe siempre.
WINDOWS_CWD = "/mnt/c"
_TASKKILL_COMMAND = ("taskkill.exe",)


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


def powershell_argv(call_parts: Sequence[str]) -> List[str]:
    """
    argv que corre `call_parts` (un ejecutable de Windows y sus argumentos)
    desde `powershell.exe`. La primera línea que imprime es `WINPID: <n>`, el PID
    de Windows del propio PowerShell: como matar `powershell.exe` desde WSL NO
    mata al programa que lanzó (verificado con Windows real), esa línea permite
    matar el árbol entero con `taskkill /T` al vencer el timeout. Antes fija
    `PYTHONIOENCODING=utf-8`: sin eso el Python de Windows escribe sus mensajes
    en cp1252 y las tildes llegaban como `�` (visto en el spike, 2026-09-29). Termina con
    `exit $LASTEXITCODE`: sin eso PowerShell aplasta cualquier código a 1
    (verificado). Solo comillas simples: las dobles se estropean en el camino
    WSL -> línea de comandos de Windows.
    """
    call = " ".join(_ps_quote(part) for part in call_parts)
    script = f"$env:PYTHONIOENCODING = 'utf-8'; Write-Output ('WINPID: ' + $PID); & {call}; exit $LASTEXITCODE"
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]


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
    Windows). Ver `powershell_argv` (PID de Windows, código de salida). `--server-utc-offset` no se pasa: el exportador lo detecta
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
    return powershell_argv([windows_python, exporter_win_path, *exporter_args])


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
    # Solo con timeout y PID de Windows conocido: None = se mató el árbol; str = por qué no se pudo.
    windows_kill_error: Optional[str] = None

    @property
    def run_id(self) -> Optional[str]:
        match = _RUN_ID_LINE.search(self.stdout)
        return match.group(1) if match else None

    @property
    def skipped_timeframes(self) -> List[str]:
        """TF que el exportador no pudo traer y salteó (RF-15); el resto sí se exportó."""
        return _SKIPPED_TF_LINE.findall(self.stdout)

    @property
    def windows_pid(self) -> Optional[int]:
        match = _WINPID_LINE.search(self.stdout)
        return int(match.group(1)) if match else None

    @property
    def server(self) -> Optional[str]:
        """Servidor de la cuenta abierta en MT5 (línea `SERVER:`, N43)."""
        match = _SERVER_LINE.search(self.stdout)
        return match.group(1) if match else None

    @property
    def base_utc_offset(self) -> Optional[float]:
        """Desfase de invierno del servidor con que se convirtió el export (línea `BASE_UTC_OFFSET:`, N43)."""
        match = _BASE_UTC_OFFSET_LINE.search(self.stdout)
        return float(match.group(1)) if match else None


def kill_windows_tree(
    windows_pid: int,
    taskkill_command: Sequence[str] = _TASKKILL_COMMAND,
    timeout_s: float = 15,
) -> Optional[str]:
    """
    `taskkill /F /T /PID <pid>`: mata el proceso de Windows y todo lo que lanzó
    (para el exportador, `powershell.exe` y el Python de Windows). Devuelve
    `None` si lo logró, o el motivo si no. Nunca levanta.
    """
    try:
        result = subprocess.run(
            [*taskkill_command, "/F", "/T", "/PID", str(windows_pid)],
            capture_output=True, timeout=timeout_s, cwd=WINDOWS_CWD if os.path.isdir(WINDOWS_CWD) else None)
    except FileNotFoundError:
        return f"{taskkill_command[0]} not found"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{type(exc).__name__}: {exc}"
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip() or result.stdout.decode("utf-8", "replace").strip()
        return f"taskkill exited {result.returncode}: {detail[:120]}"
    return None


def _drain(stream, sink: List[bytes]) -> None:
    """Va juntando lo que escribe el hijo, para poder leer la línea `WINPID:` con el proceso todavía vivo."""
    try:
        for chunk in iter(lambda: stream.read1(4096), b""):
            sink.append(chunk)
    except (OSError, ValueError):
        pass


def _decode(chunks: List[bytes]) -> str:
    # `errors="replace"`: los mensajes de Windows traen acentos en una codepage
    # que no es UTF-8, y un decode estricto rompería toda la sincronización.
    return b"".join(chunks).decode("utf-8", errors="replace")


def run_exporter(
    command: Sequence[str],
    timeout_s: float,
    cwd: Optional[str] = None,
    windows_killer=kill_windows_tree,
) -> ExporterRun:
    """
    Corre `command` con `timeout_s`. El hijo arranca en su propia sesión para
    poder matar su grupo de procesos de WSL al vencer el timeout. Pero matar
    `powershell.exe` desde WSL NO mata al Python de Windows que lanzó
    (verificado): por eso, si el hijo imprimió `WINPID: <n>` (ver
    `powershell_argv`), se mata además ese árbol de Windows con
    `windows_killer(n)`. Sin línea `WINPID` (un exportador que no corre bajo
    PowerShell) no hay nada de Windows que matar.
    """
    try:
        proc = subprocess.Popen(
            list(command), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, cwd=cwd,
        )
    except OSError as exc:
        return ExporterRun(returncode=None, launch_error=f"{type(exc).__name__}: {exc}")

    out_chunks: List[bytes] = []
    err_chunks: List[bytes] = []
    readers = [threading.Thread(target=_drain, args=(proc.stdout, out_chunks), daemon=True),
               threading.Thread(target=_drain, args=(proc.stderr, err_chunks), daemon=True)]
    for reader in readers:
        reader.start()

    timed_out = False
    kill_error: Optional[str] = None
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        match = _WINPID_LINE.search(_decode(out_chunks))
        if match:
            kill_error = windows_killer(int(match.group(1)))
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
    for reader in readers:
        reader.join(timeout=5)
    return ExporterRun(
        returncode=proc.returncode, stdout=_decode(out_chunks), stderr=_decode(err_chunks),
        timed_out=timed_out, windows_kill_error=kill_error,
    )


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
    windows_killer,
) -> SyncResult:
    def failed(reason: str, run_id: Optional[str] = None) -> SyncResult:
        return SyncResult(symbol=mt5_symbol, result=cfg.REASON_EXPORT_FAILED, run_id=run_id, error=reason)

    cwd = None
    if export_command is None:
        cwd = WINDOWS_CWD  # el comando de PowerShell no puede arrancar desde una ruta UNC de WSL
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

    run = run_exporter(export_command, timeout_s, cwd=cwd, windows_killer=windows_killer)
    if run.launch_error:
        return failed(f"exporter_not_launched: {run.launch_error}")
    if run.timed_out:
        note = ""
        if run.windows_pid is not None:
            note = (f"; Windows process tree {run.windows_pid} killed" if run.windows_kill_error is None
                    else f"; could NOT kill Windows process {run.windows_pid} ({run.windows_kill_error})")
        return failed(f"timeout: the exporter did not finish in {timeout_s:g}s{note}", run.run_id)
    if run.returncode != 0:
        detail = _tail(run.stderr) or _tail(run.stdout)
        return failed(f"exporter_exit_{run.returncode}: {detail}".rstrip(": "), run.run_id)

    run_id = run.run_id
    incoming_dir = os.path.join(incoming_root, mt5_symbol, run_id) if run_id else None
    if not run_id or not os.path.isdir(incoming_dir):
        return failed("no_run_dir: the exporter finished OK but reported no usable RUN_ID", run_id)

    result = merge_incoming_run(
        bank_dir, incoming_dir, mt5_symbol, run_id, accounts_data_dir,
        export_moment=now_gt, dst_rule=dst_rule,
        real_accounts=real_accounts, symbol_map=symbol_map,
        server=run.server, base_utc_offset=run.base_utc_offset,
    )
    if result.result == SYNC_RESULT_MERGED and run.skipped_timeframes:
        result.error = f"exporter skipped timeframes: {', '.join(run.skipped_timeframes)}"
    return result


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
    windows_killer=kill_windows_tree,
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
                    export_command, windows_python, exporter_win_path, real_accounts, symbol_map, now_gt,
                    windows_killer)
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
        verified_by = result.verified_by or ""
        how = (f"clock inherited from {verified_by[len(INHERITED_PREFIX):]}" if verified_by.startswith(INHERITED_PREFIX)
               else f"clock verified by {result.verified_by}")
        line = f"Candle export merged for {result.symbol} ({how}; {added})"
        return f"{line} -- {result.error}" if result.error else line
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
