"""
tools/candle_bank.py — Banco de velas persistente (spec 002, RF-1 a RF-2d).

Parte 1 (T11): mecánica pura de lectura/escritura atómica de un `{TF}.csv` y
la fusión por `time`, sin borrar ni alterar velas existentes (RF-1, RF-1b).
Parte 2 (T12): candado por símbolo. Nada acá toca todavía la verificación del
reloj por superposición (T13) o por referencias (T14), el filtro de estación
de horario (T15) ni `status.json` (T15) -- esas partes se agregan en tareas
siguientes, sobre estas mismas funciones.

Formato del CSV: idéntico al que ya lee `tools.p2_backtest.CsvOHLCProvider`
(`time,open,high,low,close`, `time` = hora de apertura en GT naive), ordenado
y sin `time` repetido (plan.md §2.3, INV-6) -- se reusa
`tools.p2_backtest.REQUIRED_CSV_COLUMNS` para no divergir del formato asumido.
"""
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pandas as pd

from tools.p2_backtest import REQUIRED_CSV_COLUMNS

# Columnas del CSV, en el orden en que se escriben (mismo orden que ya
# producen los exports existentes -- no es un requisito de CsvOHLCProvider,
# que lee por nombre, pero mantiene los CSV legibles y diffeables a mano).
CANDLE_CSV_COLUMNS = ("time", "open", "high", "low", "close")


def bank_csv_path(bank_dir: str, timeframe: str) -> str:
    """Ruta del CSV de una temporalidad dentro del directorio de banco de UN
    símbolo (p.ej. `${CANDLE_BANK_DIR}/XAUUSD`). No crea nada."""
    return os.path.join(bank_dir, f"{timeframe}.csv")


def read_candle_csv(path: str) -> pd.DataFrame:
    """
    Lee un CSV de velas. Si no existe, devuelve un DataFrame vacío con las
    columnas del formato -- un banco nuevo, o una TF que todavía no tiene
    ninguna vela, no es un error.
    """
    if not os.path.exists(path):
        return pd.DataFrame({c: pd.Series(dtype="float64" if c != "time" else "datetime64[ns]")
                              for c in CANDLE_CSV_COLUMNS})

    df = pd.read_csv(path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = REQUIRED_CSV_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"CSV {path} no tiene las columnas requeridas {sorted(missing)} "
            f"-- formato asumido: {sorted(REQUIRED_CSV_COLUMNS)}."
        )
    df["time"] = pd.to_datetime(df["time"])
    return df.sort_values("time").reset_index(drop=True)


def write_candle_csv_atomic(path: str, df: pd.DataFrame) -> None:
    """
    Escribe `df` en `path` de forma atómica: a un archivo temporal en el
    MISMO directorio y `os.replace()` para el swap final. `os.replace()` es
    atómico dentro del mismo filesystem (POSIX), así que un fallo a mitad de
    camino (proceso matado, disco lleno al escribir) nunca deja el CSV
    destino a medio escribir -- o queda el archivo viejo completo, o el
    nuevo completo, nunca una mezcla (RF-1c, plan.md decisión T2).
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_candle_bank_", suffix=".csv")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            df.to_csv(f, columns=list(CANDLE_CSV_COLUMNS), index=False)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def merge_candle_frames(bank_df: pd.DataFrame, incoming_df: pd.DataFrame) -> pd.DataFrame:
    """
    Une `bank_df` (lo que ya está en el banco) con `incoming_df` (lo que trajo
    el export), por `time`. RF-1: ninguna vela del banco desaparece ni cambia,
    y la cobertura hacia atrás (el `time` más viejo) nunca se reduce, porque
    es una unión, nunca una resta. RF-1b: si un `time` está en los dos, se
    conserva la fila del banco -- `bank_df` va primero en el `concat` y
    `drop_duplicates(keep="first")` se queda con esa.
    """
    combined = pd.concat([bank_df, incoming_df], ignore_index=True)
    combined = combined.drop_duplicates(subset="time", keep="first")
    return combined.sort_values("time").reset_index(drop=True)


def merge_timeframe_into_bank(bank_dir: str, timeframe: str, incoming_csv_path: str) -> pd.DataFrame:
    """
    Fusiona el CSV entrante de una temporalidad con el `{TF}.csv` del banco en
    `bank_dir`, de forma atómica, y devuelve el DataFrame resultante (ya
    escrito). No hace ninguna verificación de reloj ni toma ningún candado --
    eso es responsabilidad de quien llame (T12-T15 lo envuelven sobre esta
    función).
    """
    bank_path = bank_csv_path(bank_dir, timeframe)
    bank_df = read_candle_csv(bank_path)
    incoming_df = read_candle_csv(incoming_csv_path)
    merged = merge_candle_frames(bank_df, incoming_df)
    write_candle_csv_atomic(bank_path, merged)
    return merged


# --------------------------------------------------------------------------
# Candado por símbolo (T12, RF-1d) -- mismo patrón que tools/backup.py:122-174
# (acquire_backup_lock/release_backup_lock/_is_pid_alive), adaptado a
# context manager: acá el candado protege una fusión dentro de una llamada de
# librería, no un script de proceso completo, así que en vez de sys.exit(2)
# se levanta una excepción que quien llame (candle_sync.py, T20; los
# subcomandos candles, T21) atrapa para mostrar el mensaje "Candle export
# skipped: ..." sin matar el proceso.
# --------------------------------------------------------------------------

LOCK_STALE_HOURS = 2.0


class CandleBankLockedError(RuntimeError):
    """Ya hay una fusión en curso para este símbolo (RF-1d): el banco no se toca."""


def _lock_path(bank_dir: str) -> str:
    return os.path.join(bank_dir, ".lock")


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # el proceso existe, solo no es nuestro
    return True


@contextmanager
def acquire_bank_lock(bank_dir: str, stale_hours: float = LOCK_STALE_HOURS):
    """
    Toma `{bank_dir}/.lock` (PID + hora, mismo formato que tools/backup.py) y
    lo libera automáticamente al salir del `with`, aunque el cuerpo lance una
    excepción.

    Si ya hay un candado vigente (proceso vivo y antigüedad < `stale_hours`),
    levanta `CandleBankLockedError` **sin escribir nada** -- ni el candado ni
    el banco se tocan (RF-1d). Si el candado es huérfano (proceso muerto,
    venció, o el archivo está corrupto/ilegible), lo sobrescribe, igual que
    `tools.backup.acquire_backup_lock`.
    """
    lock_path = _lock_path(bank_dir)
    if os.path.exists(lock_path):
        pid = None
        started_at = None
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                lock_data = json.load(f)
            pid = lock_data["pid"]
            started_at = datetime.fromisoformat(lock_data["started_at"])
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            pass  # candado corrupto/ilegible: se trata como huérfano, igual que backup.py

        if pid is not None:
            pid_alive = _is_pid_alive(pid)
            age = datetime.now(timezone.utc) - started_at if started_at else None
            is_stale = (not pid_alive) or (age is not None and age > timedelta(hours=stale_hours))
            if not is_stale:
                raise CandleBankLockedError(
                    f"El banco en {bank_dir!r} ya tiene una fusión en curso "
                    f"(pid={pid}, started_at={started_at}). Se cancela sin tocar el banco."
                )

    os.makedirs(bank_dir, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}, f)
    try:
        yield
    finally:
        try:
            os.remove(lock_path)
        except FileNotFoundError:
            pass
