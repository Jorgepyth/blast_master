"""
tools/candle_bank.py — Banco de velas persistente (spec 002, RF-1 a RF-2d).

Parte 1 (T11): mecánica pura de lectura/escritura atómica de un `{TF}.csv` y
la fusión por `time`, sin borrar ni alterar velas existentes (RF-1, RF-1b).
Parte 2 (T12): candado por símbolo. Parte 3 (T13): verificación del reloj por
superposición. Parte 4 (T14): verificación por referencias, para cuando no
hubo superposición (típicamente, el primer export de un símbolo). Parte 5
(T15): filtro por estación de horario, `status.json`, y una fusión con chequeo
de que ninguna vela del banco se pierda o cambie (si el chequeo falla, no
escribe nada -- el banco queda exactamente como estaba). Parte 6 (T16):
`import_legacy()`, que puebla el banco la primera vez desde los CSV que ya
existían antes de esta spec. Parte 7 (T20): `merge_incoming_run()`, la
orquestación de un export NUEVO ("verificar → filtrar → fusionar"), que
compone las piezas anteriores. NO toma el candado por símbolo: quien la llama
(`tools/candle_sync.py`) ya lo tiene, porque el candado tiene que tomarse
ANTES de lanzar el exportador (RF-20f), no solo alrededor de la fusión.

Formato del CSV: idéntico al que ya lee `tools.p2_backtest.CsvOHLCProvider`
(`time,open,high,low,close`, `time` = hora de apertura en GT naive), ordenado
y sin `time` repetido (plan.md §2.3, INV-6) -- se reusa
`tools.p2_backtest.REQUIRED_CSV_COLUMNS` para no divergir del formato asumido.
"""
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy import select

from config.auto_resolution import (
    MT5_SYMBOL_MAP,
    OVERLAP_MIN_BARS,
    REAL_ACCOUNTS,
    REASON_CLOCK_MISALIGNED,
    REASON_CLOCK_UNVERIFIED,
    REASON_EXPORT_FAILED,
)
from tools.database import TacticalAudit, UnifiedDepartment
from tools.p2_backtest import (
    CLOCK_MIN_ENTRIES,
    CLOCK_MISALIGNMENT_MARGIN,
    CLOCK_TIMEFRAME_PREFERENCE,
    CsvOHLCProvider,
    REQUIRED_CSV_COLUMNS,
    count_entries_in_range,
    evaluate_clock_entries,
    open_readonly_session,
)

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


# --------------------------------------------------------------------------
# Verificación del reloj por superposición (T13, RF-2d, N32)
# --------------------------------------------------------------------------

# TF en las que se compara la superposición -- 4H, 12H, 1D y 1W quedan afuera
# porque no necesitan verificación de reloj: el desfase de 1h es despreciable
# para EMA/ADX en esas TF (N39, RF-15c).
OVERLAP_TIMEFRAMES: Tuple[str, ...] = ("1H", "30M", "15M", "5M", "1M")

# Tolerancia relativa de la comparación de precios en la superposición (plan.md
# decisión T20): el formato numérico del CSV puede cambiar entre versiones de
# pandas sin que cambie el precio, así que compararlos como texto exacto daría
# falsos `clock_misaligned`.
OVERLAP_PRICE_TOLERANCE = 1e-9

_OVERLAP_COMPARE_COLUMNS = ("open", "high", "low", "close")


@dataclass
class OverlapVerification:
    """Resultado de comparar el banco contra un export en las TF que se
    superponen. `verified` y `misaligned` nunca son True los dos a la vez: un
    `mismatched_timeframe` encontrado en cualquier TF invalida cualquier
    `verified_timeframe` que se haya visto antes (RF-2d: "si alguna no
    coincide, informa clock_misaligned", sin excepción)."""
    verified: bool
    misaligned: bool
    verified_timeframe: Optional[str]
    mismatched_timeframe: Optional[str]
    overlap_counts: Dict[str, int] = field(default_factory=dict)


def _bars_overlap_match(
    bank_df: pd.DataFrame,
    incoming_df: pd.DataFrame,
    tolerance: float = OVERLAP_PRICE_TOLERANCE,
) -> Tuple[int, bool]:
    """
    Para una única TF: cuántas velas de `bank_df` e `incoming_df` comparten
    `time` (la superposición), y si TODAS coinciden en open/high/low/close
    dentro de `tolerance` (relativa, contra el valor del banco). Con 0
    superposición, `all_match=True` por vacuidad -- no hay nada que
    desalinear, así que esta TF no aporta ni verificación ni desalineo.
    """
    overlap = bank_df.merge(incoming_df, on="time", suffixes=("_bank", "_incoming"))
    n = len(overlap)
    if n == 0:
        return 0, True

    for col in _OVERLAP_COMPARE_COLUMNS:
        bank_col = overlap[f"{col}_bank"]
        incoming_col = overlap[f"{col}_incoming"]
        diff = (bank_col - incoming_col).abs()
        allowed = tolerance * bank_col.abs()
        if not bool((diff <= allowed).all()):
            return n, False
    return n, True


def verify_overlap(
    bank_dir: str,
    incoming_dir: str,
    timeframes: Tuple[str, ...] = OVERLAP_TIMEFRAMES,
    min_bars: int = OVERLAP_MIN_BARS,
    tolerance: float = OVERLAP_PRICE_TOLERANCE,
) -> OverlapVerification:
    """
    RF-2d: recorre `timeframes` (1H, 30M, 15M, 5M, 1M, en ese orden de
    prioridad) comparando `{bank_dir}/{TF}.csv` contra `{incoming_dir}/{TF}.csv`.

    - Si en CUALQUIER TF las velas superpuestas no coinciden todas, corta ahí
      mismo: `misaligned=True`, sin seguir mirando las demás TF -- una sola
      discrepancia invalida el export entero (RF-2d).
    - Si no hay ninguna discrepancia, `verified=True` con la primera TF (en
      orden de prioridad) que tuvo al menos `min_bars` velas superpuestas, todas
      coincidentes.
    - Si no hay discrepancias pero ninguna TF llegó a `min_bars` de
      superposición (incluido el caso de cero superposición en todas, típico
      del primer export de un símbolo), ni `verified` ni `misaligned`: hace
      falta la verificación por referencias (T14).
    """
    overlap_counts: Dict[str, int] = {}
    verified_timeframe: Optional[str] = None
    mismatched_timeframe: Optional[str] = None

    for tf in timeframes:
        bank_df = read_candle_csv(bank_csv_path(bank_dir, tf))
        incoming_df = read_candle_csv(bank_csv_path(incoming_dir, tf))
        n, all_match = _bars_overlap_match(bank_df, incoming_df, tolerance)
        overlap_counts[tf] = n

        if n > 0 and not all_match:
            mismatched_timeframe = tf
            break
        if n >= min_bars and verified_timeframe is None:
            verified_timeframe = tf

    verified = verified_timeframe is not None and mismatched_timeframe is None
    misaligned = mismatched_timeframe is not None
    return OverlapVerification(
        verified=verified,
        misaligned=misaligned,
        verified_timeframe=verified_timeframe if verified else None,
        mismatched_timeframe=mismatched_timeframe,
        overlap_counts=overlap_counts,
    )


# --------------------------------------------------------------------------
# Verificación del reloj por referencias (T14, RF-2, RF-2b, N29)
# --------------------------------------------------------------------------

@dataclass
class ReferenceVerification:
    """
    Resultado de verificar el reloj de un export por referencias (fills +
    Mark Price), para cuando `verify_overlap()` no alcanzó a decidir (típico
    del primer export de un símbolo, sin nada todavía en el banco).

    `aligned` sigue la misma semántica que `ClockCalibration.aligned`
    (`tools.p2_backtest`): `None` cuando no hay TF de referencia disponible en
    el export entrante, no cuando faltan referencias en el rango -- ese caso
    ya lo cubre `verified=False` con `n_in_range < min_references`.
    """
    verified: bool
    n_in_range: int
    aligned: Optional[bool]
    best_offset: Optional[int]
    timeframe: Optional[str]
    rate_by_offset: Dict[int, float] = field(default_factory=dict)


def gather_reference_entries(
    mt5_symbol: str,
    accounts_data_dir: str,
    real_accounts: Optional[Dict[str, str]] = None,
    symbol_map: Optional[Dict[str, str]] = None,
    include_mark_price: bool = True,
) -> List[Tuple[datetime, float]]:
    """
    RF-2b: junta los fills (`entry_time`, `entry_price`, `order_filled=True`)
    y los Mark Price no retroactivos (`created_at`, `mark_price`) de las
    cuentas de `real_accounts` (default `REAL_ACCOUNTS`) cuyo `asset` mapea a
    `mt5_symbol` según `symbol_map` (default `MT5_SYMBOL_MAP`), leídas en
    `mode=ro` (`tools.p2_backtest.open_readonly_session` -- nunca migra la DB
    real). Una cuenta cuyo archivo no existe se saltea sin error. Mismo
    formato `(timestamp, precio)` que ya esperan `evaluate_clock_entries()` y
    `count_entries_in_range()` (T10).
    """
    real_accounts = REAL_ACCOUNTS if real_accounts is None else real_accounts
    symbol_map = MT5_SYMBOL_MAP if symbol_map is None else symbol_map
    matching_tickers = [ticker for ticker, sym in symbol_map.items() if sym == mt5_symbol]
    if not matching_tickers:
        return []

    entries: List[Tuple[datetime, float]] = []
    for db_filename in real_accounts.values():
        db_path = os.path.join(accounts_data_dir, db_filename)
        if not os.path.exists(db_path):
            continue
        session = open_readonly_session(db_path)
        try:
            entries += list(session.execute(
                select(TacticalAudit.entry_time, TacticalAudit.entry_price)
                .join(UnifiedDepartment, TacticalAudit.trade_id == UnifiedDepartment.id)
                .where(
                    TacticalAudit.order_filled == True,  # noqa: E712
                    TacticalAudit.entry_time.isnot(None),
                    TacticalAudit.entry_price > 0,
                    UnifiedDepartment.asset.in_(matching_tickers),
                )
            ).all())
            if include_mark_price:
                entries += list(session.execute(
                    select(UnifiedDepartment.created_at, UnifiedDepartment.mark_price)
                    .where(
                        UnifiedDepartment.mark_price > 0,
                        UnifiedDepartment.is_backdated.isnot(True),
                        UnifiedDepartment.asset.in_(matching_tickers),
                    )
                ).all())
        finally:
            session.close()
    return entries


def verify_by_references(
    mt5_symbol: str,
    incoming_dir: str,
    export_start: datetime,
    export_end: datetime,
    accounts_data_dir: str,
    real_accounts: Optional[Dict[str, str]] = None,
    symbol_map: Optional[Dict[str, str]] = None,
    min_references: int = CLOCK_MIN_ENTRIES,
    offsets: Sequence[int] = range(-6, 7),
) -> ReferenceVerification:
    """
    RF-2, RF-2b, N29: cuando `verify_overlap()` no verificó ni desalineó (sin
    superposición todavía), junta las referencias del símbolo
    (`gather_reference_entries`) y verifica el reloj del export entrante en
    `incoming_dir`.

    Verificado (`verified=True`) solo si al menos `min_references` referencias
    caen dentro de `[export_start, export_end]` **y** `evaluate_clock_entries()`
    da alineado -- misma regla de decisión de `aligned` que
    `calibrate_clock_offset` (offset 0 óptimo, o dentro de
    `CLOCK_MISALIGNMENT_MARGIN` del mejor).
    """
    entries = gather_reference_entries(mt5_symbol, accounts_data_dir, real_accounts, symbol_map)
    n_in_range = count_entries_in_range(entries, export_start, export_end)

    provider = CsvOHLCProvider(incoming_dir)
    tf = next((t for t in CLOCK_TIMEFRAME_PREFERENCE if provider.has_timeframe(t)), None)

    if n_in_range < min_references or tf is None:
        return ReferenceVerification(
            verified=False, n_in_range=n_in_range, aligned=None,
            best_offset=None, timeframe=tf, rate_by_offset={},
        )

    rates = evaluate_clock_entries(entries, provider, tf, offsets)
    best = max(rates, key=lambda o: (rates[o], -abs(o)))
    aligned = best == 0 or rates.get(0, 0.0) >= rates[best] - CLOCK_MISALIGNMENT_MARGIN

    return ReferenceVerification(
        verified=aligned,
        n_in_range=n_in_range,
        aligned=aligned,
        best_offset=best,
        timeframe=tf,
        rate_by_offset=rates,
    )


# --------------------------------------------------------------------------
# Filtro por estación de horario (T15, RF-15c, N39)
# --------------------------------------------------------------------------
#
# Mientras BROKER_DST_RULE no esté verificado -- hoy no hay ninguna bandera
# de "verificado" en el sistema, así que este filtro está activo siempre --
# en 1H y en las TF más finas (las mismas que OVERLAP_TIMEFRAMES) solo se
# fusionan las velas cuya fecha cae en la misma estación de horario de
# verano que el momento del export. 4H, 12H, 1D y 1W se fusionan completas:
# el desfase de 1h ahí es despreciable para EMA/ADX, y los modelos de
# P2_LOG_MODELS necesitan 800 velas cerradas por TF.

def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """La n-ésima ocurrencia de `weekday` (0=lunes...6=domingo) en year-month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return date(year, month, 1 + offset + 7 * (n - 1))


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    next_month_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = next_month_first - timedelta(days=1)
    return last_day - timedelta(days=(last_day.weekday() - weekday) % 7)


def _dst_active_on(d: date, dst_rule: str) -> bool:
    """
    ¿`d` cae en horario de verano según `dst_rule`? Fechas de cambio:
      - "us": 2do domingo de marzo al 1er domingo de noviembre.
      - "eu": último domingo de marzo al último domingo de octubre.
    "none" no llega acá -- lo filtra `filter_by_export_season` antes.
    """
    SUNDAY = 6
    if dst_rule == "us":
        start = _nth_weekday_of_month(d.year, 3, SUNDAY, 2)
        end = _nth_weekday_of_month(d.year, 11, SUNDAY, 1)
    elif dst_rule == "eu":
        start = _last_weekday_of_month(d.year, 3, SUNDAY)
        end = _last_weekday_of_month(d.year, 10, SUNDAY)
    else:
        raise ValueError(f"BROKER_DST_RULE desconocida: {dst_rule!r} (esperado us/eu/none)")
    return start <= d < end


def filter_by_export_season(
    df: pd.DataFrame,
    timeframe: str,
    export_moment: datetime,
    dst_rule: str,
    season_timeframes: Tuple[str, ...] = OVERLAP_TIMEFRAMES,
) -> pd.DataFrame:
    """
    RF-15c, N39: si `timeframe` está en `season_timeframes` (1H y más finas) y
    `dst_rule` no es `"none"`, deja solo las velas de `df` cuya fecha cae en la
    MISMA estación de horario de verano que `export_moment`. Para las demás TF
    (4H, 12H, 1D, 1W), o con `dst_rule == "none"` (sin distinción de estación),
    devuelve `df` sin tocar.
    """
    if timeframe not in season_timeframes or dst_rule == "none":
        return df
    export_dst = _dst_active_on(export_moment.date(), dst_rule)
    mask = df["time"].apply(lambda t: _dst_active_on(t.date(), dst_rule) == export_dst)
    return df[mask].reset_index(drop=True)


# --------------------------------------------------------------------------
# status.json (T15, plan.md §2.3)
# --------------------------------------------------------------------------

STATUS_JSON_FILENAME = "status.json"


def status_json_path(bank_dir: str) -> str:
    return os.path.join(bank_dir, STATUS_JSON_FILENAME)


def build_status_payload(
    symbol: str,
    clock: str,
    verified_by: Optional[str],
    dst_rule: str,
    run_id: Optional[str],
    result: str,
    bars_added: Dict[str, int],
    last_error: Optional[str] = None,
) -> dict:
    """Arma el dict de `status.json` con la forma exacta del ejemplo de
    plan.md §2.3. `clock`: `"verified"`, o uno de los `REASON_*` de
    `config.auto_resolution` (`clock_misaligned`, `clock_unverified`, ...)."""
    return {
        "symbol": symbol,
        "clock": clock,
        "verified_by": verified_by,
        "dst_rule": dst_rule,
        "last_export": {"run_id": run_id, "result": result, "bars_added": dict(bars_added)},
        "last_error": last_error,
    }


def read_bank_status(bank_dir: str) -> Optional[dict]:
    """`None` si el símbolo todavía no tiene `status.json` (nunca se fusionó nada)."""
    path = status_json_path(bank_dir)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_bank_status(bank_dir: str, status: dict) -> None:
    """Escribe `status.json` de forma atómica (temporal + `os.replace`), mismo
    patrón que `write_candle_csv_atomic`."""
    os.makedirs(bank_dir, exist_ok=True)
    path = status_json_path(bank_dir)
    fd, tmp_path = tempfile.mkstemp(dir=bank_dir, prefix=".tmp_status_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# --------------------------------------------------------------------------
# Fusión con chequeo de no-regresión (T15, RF-1, RF-1c)
# --------------------------------------------------------------------------

def _assert_bank_not_regressed(bank_before: pd.DataFrame, merged: pd.DataFrame, timeframe: str) -> None:
    """
    Confirma que `merged` contiene, sin ningún cambio, cada vela de
    `bank_before` (RF-1: "sin borrar ni alterar ninguna vela existente"). Por
    construcción, `merge_candle_frames` ya garantiza esto -- esta función es
    una red de seguridad extra, para que un bug futuro en la fusión no pueda
    corromper el banco en silencio: si algo no cuadra, levanta `RuntimeError`
    ANTES de escribir nada (RF-1c).
    """
    check = bank_before.merge(
        merged, on="time", how="left", suffixes=("_before", "_after"), indicator=True
    )
    missing = check[check["_merge"] != "both"]
    if len(missing):
        raise RuntimeError(
            f"{timeframe}: el banco nuevo perdió {len(missing)} vela(s) que ya estaban -- no se escribe nada."
        )
    for col in _OVERLAP_COMPARE_COLUMNS:
        if not (check[f"{col}_before"] == check[f"{col}_after"]).all():
            raise RuntimeError(
                f"{timeframe}: una vela existente cambió de valor en la fusión -- no se escribe nada."
            )


def merge_timeframe_into_bank_checked(bank_dir: str, timeframe: str, incoming_df: pd.DataFrame) -> int:
    """
    Como `merge_timeframe_into_bank`, pero recibe `incoming_df` ya en memoria
    (para que quien llame pueda aplicarle `filter_by_export_season` antes) y
    verifica con `_assert_bank_not_regressed` antes de escribir. Si el chequeo
    falla, no se escribe el CSV -- el banco queda byte a byte igual a como
    estaba, y la excepción se propaga para que quien orqueste la fusión de
    varias TF decida qué hacer con las demás. Devuelve cuántas velas se
    agregaron.
    """
    bank_path = bank_csv_path(bank_dir, timeframe)
    bank_before = read_candle_csv(bank_path)
    merged = merge_candle_frames(bank_before, incoming_df)
    _assert_bank_not_regressed(bank_before, merged, timeframe)
    write_candle_csv_atomic(bank_path, merged)
    return len(merged) - len(bank_before)


# --------------------------------------------------------------------------
# Import inicial del banco (T16, RF-2c)
# --------------------------------------------------------------------------

# El 5M.csv actual de XAU es anterior a la corrección del reloj del
# 2026-09-22 (baseline H11) -- no se importa aunque el resto del símbolo
# verifique. Se informa como excluido; el archivo original no se toca.
LEGACY_IMPORT_EXCLUSIONS: FrozenSet[Tuple[str, str]] = frozenset({("XAUUSD", "5M")})
LEGACY_EXCLUSION_REASON = "legacy_precontamination"


@dataclass
class LegacyImportResult:
    """`aligned` sigue la semántica de `ClockCalibration.aligned`: `None` sin
    referencias/TF suficientes para decidir, `False` desalineado. Con
    `aligned` distinto de `True`, `imported` queda vacío y CADA TF presente en
    el origen aparece en `excluded`, sin excepción -- si no se puede confiar
    en el reloj del símbolo, no se importa nada de él (RF-2c)."""
    symbol: str
    aligned: Optional[bool]
    imported: Dict[str, int] = field(default_factory=dict)
    excluded: Dict[str, str] = field(default_factory=dict)


def _timeframes_present_in(directory: str) -> List[str]:
    """Nombres de TF (`{TF}.csv` sin la extensión) presentes en `directory`,
    ignorando archivos ocultos (`.lock`, `.tmp_*`) y `status.json`."""
    if not os.path.isdir(directory):
        return []
    return sorted(
        filename[: -len(".csv")]
        for filename in os.listdir(directory)
        if filename.endswith(".csv") and not filename.startswith(".")
    )


def _time_range_across_timeframes(
    directory: str, timeframes: Sequence[str]
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """`(min, max)` de `time` entre los CSV de `timeframes` presentes en
    `directory`. `(None, None)` si ninguno tiene ninguna vela."""
    mins: List[datetime] = []
    maxs: List[datetime] = []
    for tf in timeframes:
        df = read_candle_csv(bank_csv_path(directory, tf))
        if len(df):
            mins.append(df["time"].min())
            maxs.append(df["time"].max())
    if not mins:
        return None, None
    return min(mins), max(maxs)


def import_legacy(
    symbol: str,
    legacy_dir: str,
    bank_dir: str,
    accounts_data_dir: str,
    real_accounts: Optional[Dict[str, str]] = None,
    symbol_map: Optional[Dict[str, str]] = None,
    offsets: Sequence[int] = range(-6, 7),
    min_entries: int = CLOCK_MIN_ENTRIES,
    exclusions: FrozenSet[Tuple[str, str]] = LEGACY_IMPORT_EXCLUSIONS,
    *,
    export_moment: datetime,
    dst_rule: str,
) -> LegacyImportResult:
    """
    RF-2c: cuando se crea el banco de `symbol` por primera vez, importa los
    CSV que ya existían en `legacy_dir` (los actuales de `MT5Exports/{symbol}/`)
    a `bank_dir`, **solo si el reloj del símbolo verifica** -- reusa
    `verify_by_references()` (T14) sobre TODO el rango de fechas que cubre
    `legacy_dir`, así que "verificado" acá significa lo mismo que en un export
    nuevo: `min_entries` referencias dentro de ese rango y `evaluate_clock_entries`
    alineado. `legacy_dir` es de **solo lectura** -- ninguna función que se
    llama acá escribe ahí.

    Excepción explícita (RF-2c, baseline H11): aunque `symbol` verifique,
    cualquier `(symbol, TF)` en `exclusions` (por defecto, solo XAU/5M) se
    excluye igual.

    Estación de horario (plan.md decisión T17, RF-15c): los CSV legacy los
    escribió el exportador viejo, con UN solo offset para toda la corrida, así que
    en 1H y más finas las velas de la estación opuesta a la del export tienen 1 h
    de error. Por eso, igual que en un export nuevo, se pasan por
    `filter_by_export_season(export_moment, dst_rule)` y solo entran las de la
    misma estación. `export_moment` y `dst_rule` son obligatorios a propósito: en
    T16 se omitió este filtro y en la primera corrida real dejó 18 velas de 1H de
    marzo con 1 h de corrimiento en el banco de XAUUSD (2026-09-29).
    """
    real_accounts = REAL_ACCOUNTS if real_accounts is None else real_accounts
    symbol_map = MT5_SYMBOL_MAP if symbol_map is None else symbol_map

    present = _timeframes_present_in(legacy_dir)
    if not present:
        return LegacyImportResult(symbol=symbol, aligned=None)

    export_start, export_end = _time_range_across_timeframes(legacy_dir, present)
    ref = verify_by_references(
        symbol, legacy_dir, export_start, export_end, accounts_data_dir,
        real_accounts=real_accounts, symbol_map=symbol_map,
        min_references=min_entries, offsets=offsets,
    )

    if not ref.verified:
        reason = "clock_misaligned" if ref.aligned is False else "clock_unverified"
        return LegacyImportResult(
            symbol=symbol, aligned=ref.aligned, imported={},
            excluded={tf: reason for tf in present},
        )

    imported: Dict[str, int] = {}
    excluded: Dict[str, str] = {}
    for tf in present:
        if (symbol, tf) in exclusions:
            excluded[tf] = LEGACY_EXCLUSION_REASON
            continue
        incoming_df = read_candle_csv(bank_csv_path(legacy_dir, tf))
        incoming_df = filter_by_export_season(incoming_df, tf, export_moment, dst_rule)
        imported[tf] = merge_timeframe_into_bank_checked(bank_dir, tf, incoming_df)

    return LegacyImportResult(symbol=symbol, aligned=True, imported=imported, excluded=excluded)


# --------------------------------------------------------------------------
# Fusión de un export nuevo (T20, plan.md §3.8, RF-1, RF-1c, RF-2, RF-2d)
# --------------------------------------------------------------------------

# Las 9 temporalidades que guarda el banco (RF-15), de más lenta a más rápida.
ALL_BANK_TIMEFRAMES: Tuple[str, ...] = ("1W", "1D", "12H", "4H", "1H", "30M", "15M", "5M", "1M")

SYNC_RESULT_MERGED = "merged"
# Otro proceso ya tenía el candado del símbolo (RF-1d, RF-20f): no se hizo nada.
SYNC_RESULT_LOCKED = "locked"

# Estado de la regla de horario de verano en status.json (plan.md §2.3). No hay
# ninguna verificación de la regla en el sistema todavía -- la fija el spike (T22).
DST_RULE_STATUS_LABEL = "unverified"


@dataclass
class SyncResult:
    """
    Resultado de sincronizar un símbolo. `result` es `SYNC_RESULT_MERGED`,
    `SYNC_RESULT_LOCKED`, o uno de los `REASON_*` de `config.auto_resolution`
    (`export_failed`, `clock_misaligned`, `clock_unverified`). `error` lleva el
    detalle legible de todo lo que no fue `merged`. Salvo `merged`, ninguna
    vela del banco cambia por lo que reporta este resultado (RF-1c) -- salvo
    en el caso poco probable de una falla a mitad de la fusión, en el que las
    TF ya fusionadas conservan sus velas nuevas (correctas) y `bars_added`
    dice cuáles.
    """
    symbol: str
    result: str
    verified_by: Optional[str] = None
    bars_added: Dict[str, int] = field(default_factory=dict)
    run_id: Optional[str] = None
    error: Optional[str] = None


def merge_incoming_run(
    bank_dir: str,
    incoming_dir: str,
    mt5_symbol: str,
    run_id: Optional[str],
    accounts_data_dir: str,
    export_moment: datetime,
    dst_rule: str,
    real_accounts: Optional[Dict[str, str]] = None,
    symbol_map: Optional[Dict[str, str]] = None,
    min_references: int = CLOCK_MIN_ENTRIES,
    overlap_min_bars: int = OVERLAP_MIN_BARS,
    offsets: Sequence[int] = range(-6, 7),
) -> SyncResult:
    """
    Plan.md §3.8, pasos 2 a 7, para el export de `incoming_dir` (`{TF}.csv` por
    temporalidad). **Quien llama tiene que tener el candado del símbolo.**
    Nunca levanta: todo lo que sale mal vuelve como `SyncResult`.

    1. Verifica el reloj por superposición (`verify_overlap`). Una vela distinta
       en cualquier TF -> `clock_misaligned`, sin fusionar nada.
    2. Sin superposición suficiente, por referencias (`verify_by_references`,
       sobre el rango de fechas del export): `clock_misaligned` si el reloj está
       corrido, `clock_unverified` si faltan referencias.
    3. Verificado: para cada TF presente, filtra por estación de horario
       (`filter_by_export_season`, en 1H y más finas) y fusiona con
       `merge_timeframe_into_bank_checked`, TF por TF.
    """
    def result(kind: str, **kw) -> SyncResult:
        return SyncResult(symbol=mt5_symbol, result=kind, run_id=run_id, **kw)

    present = [tf for tf in ALL_BANK_TIMEFRAMES if os.path.exists(bank_csv_path(incoming_dir, tf))]
    if not present:
        return result(REASON_EXPORT_FAILED, error="empty_export: no {TF}.csv in the incoming run")
    export_start, export_end = _time_range_across_timeframes(incoming_dir, present)
    if export_start is None:
        return result(REASON_EXPORT_FAILED, error="empty_export: the incoming CSVs have no candles")

    overlap = verify_overlap(bank_dir, incoming_dir, min_bars=overlap_min_bars)
    if overlap.misaligned:
        return result(
            REASON_CLOCK_MISALIGNED,
            error=f"overlapping candles differ from the bank in {overlap.mismatched_timeframe}",
        )
    if overlap.verified:
        verified_by = "overlap"
    else:
        ref = verify_by_references(
            mt5_symbol, incoming_dir, export_start, export_end, accounts_data_dir,
            real_accounts=real_accounts, symbol_map=symbol_map,
            min_references=min_references, offsets=offsets,
        )
        if not ref.verified:
            if ref.aligned is False:
                return result(
                    REASON_CLOCK_MISALIGNED,
                    error=f"reference prices fit best with candles shifted {ref.best_offset:+d}h",
                )
            return result(
                REASON_CLOCK_UNVERIFIED,
                error=f"{ref.n_in_range} reference prices in the exported range, need {min_references}",
            )
        verified_by = "references"

    bars_added: Dict[str, int] = {}
    current_tf: Optional[str] = None
    try:
        for current_tf in present:
            incoming_df = read_candle_csv(bank_csv_path(incoming_dir, current_tf))
            incoming_df = filter_by_export_season(incoming_df, current_tf, export_moment, dst_rule)
            bars_added[current_tf] = merge_timeframe_into_bank_checked(bank_dir, current_tf, incoming_df)
    except Exception as exc:  # noqa: BLE001 -- INV-2: nada se propaga hacia el wizard
        return result(
            REASON_EXPORT_FAILED, verified_by=verified_by, bars_added=bars_added,
            error=f"merge failed on {current_tf}: {exc}",
        )
    return result(SYNC_RESULT_MERGED, verified_by=verified_by, bars_added=bars_added)


def write_sync_status(bank_dir: str, sync: SyncResult) -> None:
    """
    Escribe `status.json` reflejando `sync` (plan.md §2.3). `clock` describe la
    confianza en el reloj del símbolo: `"verified"` solo si una fusión lo
    verificó; un export que no verifica NO le quita esa confianza a un banco
    que ya la tenía (sus velas no cambiaron), y sin confianza previa queda con
    el motivo (`clock_misaligned`/`clock_unverified`). Un `export_failed` no
    dice nada del reloj: conserva lo que había.
    """
    prior = read_bank_status(bank_dir) or {}
    prior_clock = prior.get("clock")
    if sync.result == SYNC_RESULT_MERGED:
        clock, verified_by = "verified", sync.verified_by
    elif prior_clock == "verified":
        clock, verified_by = "verified", prior.get("verified_by")
    elif sync.result in (REASON_CLOCK_MISALIGNED, REASON_CLOCK_UNVERIFIED):
        clock, verified_by = sync.result, None
    else:
        clock, verified_by = prior_clock or REASON_CLOCK_UNVERIFIED, None
    write_bank_status(bank_dir, build_status_payload(
        sync.symbol, clock, verified_by, DST_RULE_STATUS_LABEL, sync.run_id,
        sync.result, sync.bars_added, sync.error,
    ))


# --------------------------------------------------------------------------
# Lo que usan los subcomandos `candles` de cli/main.py (T21)
# --------------------------------------------------------------------------

@dataclass
class BankStatusRow:
    """Una fila de `candles status`. `clock` es `None` si el símbolo todavía no
    tiene `status.json` (nunca se exportó ni se importó nada)."""
    symbol: str
    clock: Optional[str]
    verified_by: Optional[str]
    last_run_id: Optional[str]
    last_result: Optional[str]
    last_error: Optional[str]
    timeframes: List[str]


def collect_bank_status(bank_root: str, symbols: Sequence[str]) -> List[BankStatusRow]:
    """Estado de cada símbolo de `symbols` en `bank_root`: lo que dice su
    `status.json` más las TF que tienen CSV en el banco, en el orden de
    `ALL_BANK_TIMEFRAMES`. Solo lectura; un `status.json` ilegible se informa en
    `clock` en vez de romper."""
    rows: List[BankStatusRow] = []
    for symbol in symbols:
        bank_dir = os.path.join(bank_root, symbol)
        present = set(_timeframes_present_in(bank_dir))
        timeframes = [tf for tf in ALL_BANK_TIMEFRAMES if tf in present]
        try:
            status = read_bank_status(bank_dir)
        except (ValueError, OSError):
            rows.append(BankStatusRow(symbol, "status.json unreadable", None, None, None, None, timeframes))
            continue
        if status is None:
            rows.append(BankStatusRow(symbol, None, None, None, None, None, timeframes))
            continue
        last = status.get("last_export") or {}
        rows.append(BankStatusRow(
            symbol, status.get("clock"), status.get("verified_by"),
            last.get("run_id"), last.get("result"), status.get("last_error"), timeframes,
        ))
    return rows


LEGACY_IMPORT_RUN_ID = "legacy_import"


def import_legacy_with_status(
    symbol: str,
    legacy_dir: str,
    bank_dir: str,
    accounts_data_dir: str,
    real_accounts: Optional[Dict[str, str]] = None,
    symbol_map: Optional[Dict[str, str]] = None,
    export_moment: Optional[datetime] = None,
    dst_rule: Optional[str] = None,
) -> LegacyImportResult:
    """
    `import_legacy()` (RF-2c) con el candado del símbolo y `status.json`: lo que
    llama `candles import-legacy`. `dst_rule` sale de `BROKER_DST_RULE` y
    `export_moment` es "ahora" (hora GT naive) si no se pasan. Sin CSV legacy no toca nada (ni siquiera
    crea el directorio del banco). Si el símbolo ya tiene un export en curso,
    levanta `CandleBankLockedError`. `status.json` queda con
    `run_id="legacy_import"` y, si el reloj verificó, `verified_by="references"`.
    """
    if not _timeframes_present_in(legacy_dir):
        return LegacyImportResult(symbol=symbol, aligned=None)
    import config.auto_resolution as auto_cfg  # en la llamada, para leer el valor vigente
    dst_rule = auto_cfg.BROKER_DST_RULE if dst_rule is None else dst_rule
    if export_moment is None:
        export_moment = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=6)  # GT naive
    with acquire_bank_lock(bank_dir):
        result = import_legacy(
            symbol, legacy_dir, bank_dir, accounts_data_dir,
            real_accounts=real_accounts, symbol_map=symbol_map,
            export_moment=export_moment, dst_rule=dst_rule,
        )
        if result.aligned is True:
            sync = SyncResult(symbol, SYNC_RESULT_MERGED, verified_by="references",
                              bars_added=dict(result.imported), run_id=LEGACY_IMPORT_RUN_ID)
        elif result.aligned is False:
            sync = SyncResult(symbol, REASON_CLOCK_MISALIGNED, run_id=LEGACY_IMPORT_RUN_ID,
                              error="legacy CSVs: the clock does not fit the reference prices")
        else:
            sync = SyncResult(symbol, REASON_CLOCK_UNVERIFIED, run_id=LEGACY_IMPORT_RUN_ID,
                              error="legacy CSVs: not enough reference prices to verify the clock")
        write_sync_status(bank_dir, sync)
    return result
