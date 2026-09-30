"""
windows_export/export_p2_ohlc.py — P2_systematic: exportador OHLC de
solo lectura contra MT5, para correr con el Python NATIVO de Windows
(fuera del entorno conda de este repo; MetaTrader5 es Windows-only).

Produce exactamente 7 archivos, uno por temporalidad -- NO uno por trade
-- porque tools/p2_backtest.py:CsvOHLCProvider lee un único CSV continuo
por temporalidad y filtra en memoria por trade (ver plan de la sesión que
escribió este script, sección "Hallazgo crítico #2"). Cada archivo cubre
el rango de fechas combinado de TODOS los trade_id en alcance, no un trade
individual.

Símbolo MT5: confirmado "XAUUSD" (exacto, sin sufijo) por el usuario --
es el default de --symbol, pero el flag se mantiene override-able por si
cambia de broker/cuenta en el futuro.

Zona horaria: MT5 devuelve `time` en HORA DEL SERVIDOR del broker,
codificada como si fuera UTC -- NO en UTC real, pese a lo que sugiere la
documentación oficial. Las versiones anteriores de este script la trataban
como UTC y el CSV salía corrido: verificado empíricamente el 2026-09-22
contra las entradas llenadas reales de la cuenta XAUUSD, que solo caían
dentro de su vela 1H de MT5 en 12/29 casos sin corregir, y en 29/34 con
+3h (servidor UTC+3, horario de verano). El pico en +3h era idéntico en
los 5 meses de la muestra.

Por eso ahora: server_time -> UTC real (restando el offset del servidor)
-> hora de Guatemala naive (UTC-6, sin DST, convención de todo el repo:
tools/database.py, entry_time). El offset del servidor se detecta del
último tick del símbolo, o se pasa explícito con --server-utc-offset.

HORARIO DE VERANO (DST): los brokers mueven el reloj del servidor con el
horario de verano (típicamente UTC+2 en invierno, UTC+3 en verano). Con
`--dst-rule none` (el default, como siempre) se aplica UN offset a todas
las velas, así que las de la estación opuesta a la del momento del export
quedan corridas 1h. Con `--dst-rule us` o `eu` (spec 002, RF-15b, N34)
cada vela se convierte con el offset vigente EN LA FECHA de esa vela: el
offset detectado/pasado es el de "ahora", y de él se deduce el de invierno
(base) restando 1h si "ahora" está en horario de verano. Las velas que
caen en la hora del cambio (`DST_TRANSITION_HOUR`) son ambiguas y se
descartan, informando cuántas fueron. La hora exacta del cambio del
servidor y la regla real del broker son [NO VERIFICADO] hasta el spike de
interoperabilidad (T22). tools/p2_backtest.py:calibrate_clock_offset() lo
verifica contra las entradas reales y bloquea el reporte si el reloj no cuadra.

Solo funciones de LECTURA de MetaTrader5 (copy_rates_range, symbol_info_tick,
account_info). PROHIBIDO:
order_send, order_check, o cualquier función de escritura -- no se
importan ni se usan en este archivo.

DIRECTORIO POR CORRIDA (spec 002, RF-15, plan T2): con `--per-run-dir`,
`--out-dir` es la carpeta BASE y cada corrida escribe en un directorio nuevo
`{out-dir}/{SIMBOLO}/{run_id}/` (run_id = fecha-hora UTC, `YYYYmmddTHHMMSS`,
con sufijo `-1`, `-2`... si dos corridas caen en el mismo segundo), así que
una corrida nunca pisa los archivos de otra ni deja un directorio mezclado
si falla a mitad. La ruta se imprime en una línea `RUN_DIR: <ruta>`. Sin el
flag, los CSV se escriben directo en `--out-dir`, como siempre. En los dos
modos, cada CSV se escribe de forma atómica (temporal + os.replace): un
fallo a mitad de escritura nunca deja un CSV a medias. Una corrida que
falla deja su directorio con las TF que sí terminaron, para poder auditarla;
quien orquesta (tools/candle_sync.py) se guía por el código de salida.

Uso:
    python export_p2_ohlc.py --out-dir C:\\ruta\\destino ^
        --min-anchor "2026-05-18 12:15:00" --max-anchor "2026-09-03 18:19:00"

    (--symbol es opcional, default XAUUSD; --min-anchor/--max-anchor son
    OBLIGATORIOS y deben reconfirmarse a mano contra la DB antes de correr
    -- ver jupyter/p2_systematic_task_plan.md, sección "auto-fetch de
    min/max-anchor" para por qué esto sigue siendo manual, a propósito,
    en esta versión del script).

No requiere nada del repo Linux -- es standalone, corre con el Python de
Windows que tenga instalado `pip install MetaTrader5 pandas`.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# MetaTrader5 es Windows-only -- este import falla en Linux/WSL2 a propósito,
# es la señal de que este script se está corriendo en el entorno equivocado.
import MetaTrader5 as mt5

UTC = timezone.utc
GT_OFFSET_HOURS = 6  # Guatemala = UTC-6, sin horario de verano.
DEFAULT_SYMBOL = "XAUUSD"  # confirmado exacto (sin sufijo) contra Market Watch.

# Timeframe MT5 <-> nombre de archivo esperado por CsvOHLCProvider
# (tools/p2_backtest.py:TIMEFRAMES, ".env.template:21-28"). Constantes
# verificadas contra la documentación oficial de MetaTrader5
# (mql5.com/en/docs/python_metatrader5/mt5copyratesfrom_py) -- TIMEFRAME_H12
# existe, no es una suposición. TIMEFRAME_M5/TIMEFRAME_M1: banco de velas
# nuevo de la spec 002 (RF-15) -- mismas dos temporalidades que
# tools/p2_backtest.py:TIMEFRAME_MINUTES ganó en T9. TIMEFRAME_H2: P2 banco v2
# (jupyter/p2_banco_v2/), solo bajo pedido -- ver ON_DEMAND_TIMEFRAMES.
TIMEFRAME_MAP: Dict[str, int] = {
    "1W": mt5.TIMEFRAME_W1,
    "1D": mt5.TIMEFRAME_D1,
    "12H": mt5.TIMEFRAME_H12,
    "4H": mt5.TIMEFRAME_H4,
    "2H": mt5.TIMEFRAME_H2,
    "1H": mt5.TIMEFRAME_H1,
    "30M": mt5.TIMEFRAME_M30,
    "15M": mt5.TIMEFRAME_M15,
    "5M": mt5.TIMEFRAME_M5,
    "1M": mt5.TIMEFRAME_M1,
}

# Las 9 temporalidades, de más lenta a más rápida -- el orden en que se
# exportan por defecto (lo que recibe el banco de velas de la spec 002).
ALL_EXPORT_TIMEFRAMES: Tuple[str, ...] = ("1W", "1D", "12H", "4H", "1H", "30M", "15M", "5M", "1M")

# Temporalidades que se exportan SOLO si se piden con --timeframes: el export por
# defecto no las trae y el banco de velas no las conoce (tools/candle_bank.py:
# ALL_BANK_TIMEFRAMES). 2H: P2 banco v2, que lee el CSV desde la carpeta del export.
ON_DEMAND_TIMEFRAMES: Tuple[str, ...] = ("2H",)

# Duración de cada vela en minutos -- usado por exclude_forming_bar() para
# el candado anti-repainting (mismo principio que el pipeline Linux).
TIMEFRAME_MINUTES: Dict[str, int] = {
    "1W": 7 * 24 * 60,
    "1D": 24 * 60,
    "12H": 12 * 60,
    "4H": 4 * 60,
    "2H": 2 * 60,
    "1H": 60,
    "30M": 30,
    "15M": 15,
    "5M": 5,
    "1M": 1,
}

REQUIRED_COLUMNS = ["time", "open", "high", "low", "close"]
MIN_BARS_PER_TF = 800  # mismo umbral que tools/p2_backtest.py:MIN_BARS_PER_TF
# Margen sobre el span "ingenuo" (min_bars * duración de vela). Debe cubrir al menos
# 7/5 = 1.4 solo por los fines de semana sin velas (mercado cerrado sáb/dom), más
# feriados -- 1.25 daba ~700 velas reales previas al anchor, no 800, y dejaba
# snapshot_incompleto=True en TODOS los trades vía la TF 1D (verificado contra el
# CSV real 2026-09-21: 705 previas al min-anchor, 784 al max-anchor).
BACKWARD_MARGIN = 2.0
# Colchón fijo adicional, en días calendario. El margen proporcional solo no
# alcanza en temporalidades intradía cortas: con una ventana de pocos días, un
# fin de semana se come una fracción enorme del rango (con 1.5, el export real
# del 2026-09-22 trajo 782 velas de 30M y 600 de 5M antes del primer anchor,
# ambas por debajo de las 800 exigidas). El colchón desacopla ese piso del
# largo de la vela.
BACKWARD_BUFFER_DAYS = 7


@dataclass
class ExportResult:
    timeframe: str
    path: Path
    rows: int
    first_time: Optional[datetime]
    last_time: Optional[datetime]
    discarded_dst: int = 0  # velas de la hora del cambio de horario, descartadas (RF-15b)


def utc_to_gt_naive(series: pd.Series) -> pd.Series:
    """
    Convierte una Serie de datetimes tz-aware UTC (como los devuelve
    pandas.to_datetime(..., unit='s', utc=True) sobre el campo `time` de
    MT5) a naive-hora-de-Guatemala -- la misma convención que usa el resto
    de este sistema (tools/database.py, entry_time). Resta 6 horas y
    descarta el tzinfo explícitamente (no un shift implícito).
    """
    return (series - pd.Timedelta(hours=GT_OFFSET_HOURS)).dt.tz_localize(None)


def gt_naive_to_utc(dt_gt_naive: datetime) -> datetime:
    """
    Inverso de utc_to_gt_naive, para construir date_from/date_to (que MT5
    exige en UTC) a partir de un timestamp naive-GT como los que guarda
    este repo (tools/database.py, entry_time).
    """
    return dt_gt_naive.replace(tzinfo=UTC) + timedelta(hours=GT_OFFSET_HOURS)


def server_time_to_utc(epoch_seconds: pd.Series, server_utc_offset_hours: float) -> pd.Series:
    """
    El campo `time` de MT5 es el reloj de pared del SERVIDOR expresado en
    segundos desde 1970 como si fuera UTC. Para obtener UTC real hay que
    restarle el offset del servidor. Devuelve una Serie tz-aware UTC.
    """
    return pd.to_datetime(epoch_seconds, unit="s", utc=True) - pd.Timedelta(hours=server_utc_offset_hours)


def gt_naive_to_server(dt_gt_naive: datetime, server_utc_offset_hours: float) -> datetime:
    """
    Timestamp naive-GT -> reloj del servidor, etiquetado UTC, que es como
    MT5 interpreta los date_from/date_to de copy_rates_range (mismo reloj
    en el que devuelve las velas). Inverso de la conversión de lectura.
    """
    return gt_naive_to_utc(dt_gt_naive) + timedelta(hours=server_utc_offset_hours)


# --- Horario de verano del servidor, vela por vela (spec 002, RF-15b, N34) ----
#
# Este script es standalone (corre con el Python de Windows, sin importar nada
# del repo), así que las fechas de cambio de EE.UU./UE se calculan acá y no se
# importan de tools/candle_bank.py. tests/test_export_p2_ohlc.py verifica que
# las dos implementaciones coinciden, para que no diverjan sin que se note.

DST_RULES = ("us", "eu", "none")

# Hora (reloj de pared del servidor) del día del cambio a la que cae el salto:
# la vela cuyo `time` está en [hora, hora+1h) de ese día es ambigua y se
# descarta. [NO VERIFICADO] -- candidato hasta el spike (T22); un broker puede
# cambiar a otra hora, y la regla de DST_RULES es la de EE.UU./UE, no
# necesariamente la del broker.
DST_TRANSITION_HOUR = 2

_SUNDAY = 6


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return date(year, month, 1 + (weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    next_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = next_first - timedelta(days=1)
    return last_day - timedelta(days=(last_day.weekday() - weekday) % 7)


def dst_transition_dates(year: int, dst_rule: str) -> Tuple[date, date]:
    """(inicio, fin) del horario de verano de `year` según `dst_rule`:
    `us` = 2do domingo de marzo -> 1er domingo de noviembre; `eu` = último
    domingo de marzo -> último domingo de octubre."""
    if dst_rule == "us":
        return _nth_weekday_of_month(year, 3, _SUNDAY, 2), _nth_weekday_of_month(year, 11, _SUNDAY, 1)
    if dst_rule == "eu":
        return _last_weekday_of_month(year, 3, _SUNDAY), _last_weekday_of_month(year, 10, _SUNDAY)
    raise ValueError(f"dst_rule desconocida: {dst_rule!r} (esperado {DST_RULES}).")


def dst_masks(server_naive: pd.Series, dst_rule: str) -> Tuple[pd.Series, pd.Series]:
    """
    Para una Serie de datetimes naive en reloj del servidor, devuelve
    `(is_dst, ambiguous)` (dos Series booleanas). Con `dst_rule="none"`, todo
    False. Con `us`/`eu`:
      - `ambiguous`: cae en [DST_TRANSITION_HOUR, +1h) del día de inicio o de
        fin del horario de verano -- no se sabe con qué offset se etiquetó;
      - `is_dst`: cae después de la hora del cambio de inicio y antes de la
        del cambio de fin (y no es ambigua).
    """
    is_dst = pd.Series(False, index=server_naive.index)
    ambiguous = pd.Series(False, index=server_naive.index)
    if dst_rule == "none" or server_naive.empty:
        return is_dst, ambiguous

    hour = pd.Timedelta(hours=1)
    for year in server_naive.dt.year.unique():
        start_date, end_date = dst_transition_dates(int(year), dst_rule)
        gap_start = pd.Timestamp(start_date) + pd.Timedelta(hours=DST_TRANSITION_HOUR)
        fall_start = pd.Timestamp(end_date) + pd.Timedelta(hours=DST_TRANSITION_HOUR)
        in_year = server_naive.dt.year == year
        ambiguous |= in_year & (
            ((server_naive >= gap_start) & (server_naive < gap_start + hour))
            | ((server_naive >= fall_start) & (server_naive < fall_start + hour))
        )
        is_dst |= in_year & (server_naive >= gap_start + hour) & (server_naive < fall_start)
    return is_dst, ambiguous


def base_offset_from_current(current_offset_hours: float, now_server_naive: datetime, dst_rule: str) -> float:
    """
    Offset "base" (de invierno) del servidor a partir del offset de AHORA
    (detectado del último tick o pasado con --server-utc-offset): resta 1h si
    "ahora" está en horario de verano según `dst_rule`. Con `none`, el offset
    de ahora tal cual. Falla si "ahora" cae justo en la hora del cambio.
    """
    if dst_rule == "none":
        return current_offset_hours
    is_dst, ambiguous = dst_masks(pd.Series([pd.Timestamp(now_server_naive)]), dst_rule)
    if bool(ambiguous.iloc[0]):
        raise ValueError(
            "El momento actual cae en la hora del cambio de horario de verano del servidor; "
            "no se puede deducir el offset base. Reintentá en una hora, o usá --dst-rule none."
        )
    return current_offset_hours - (1 if bool(is_dst.iloc[0]) else 0)


def server_time_to_utc_dst(
    epoch_seconds: pd.Series, base_offset_hours: float, dst_rule: str
) -> Tuple[pd.Series, pd.Series]:
    """
    Como `server_time_to_utc`, pero con el offset vigente en la fecha de CADA
    vela: `base_offset_hours` (invierno) + 1h si la vela cae en horario de
    verano según `dst_rule`. Devuelve `(utc_tz_aware, ambiguous)`: la segunda
    marca las velas de la hora del cambio, que quien llame debe descartar.
    Con `dst_rule="none"` da exactamente lo mismo que `server_time_to_utc` con
    un solo offset, y `ambiguous` es todo False.
    """
    server_naive = pd.to_datetime(epoch_seconds, unit="s")
    is_dst, ambiguous = dst_masks(server_naive, dst_rule)
    offsets = pd.to_timedelta(base_offset_hours + is_dst.astype(int), unit="h")
    return (server_naive - offsets).dt.tz_localize("UTC"), ambiguous


# Antigüedad máxima aceptable del último tick para inferir el offset. Con el
# mercado abierto el tick tiene segundos; si tiene más, el mercado está
# cerrado (fin de semana) y la diferencia no mide el offset sino la pausa.
MAX_TICK_STALENESS_SECONDS = 15 * 60


def infer_server_utc_offset(tick_epoch: float, now_utc_epoch: float,
                            max_staleness_seconds: float = MAX_TICK_STALENESS_SECONDS) -> int:
    """
    Offset del servidor en horas enteras, a partir del último tick (reloj
    del servidor) y la hora UTC real actual. Falla si el tick está viejo o
    si el offset resultante es absurdo, en vez de devolver un valor malo.
    """
    diff = tick_epoch - now_utc_epoch
    hours = round(diff / 3600)
    residual = diff - hours * 3600
    if abs(residual) > max_staleness_seconds:
        raise RuntimeError(
            f"No se pudo inferir el offset del servidor: el último tick está "
            f"{abs(residual) / 60:.0f} min desfasado de una hora entera (máx. "
            f"{max_staleness_seconds / 60:.0f}). Probablemente el mercado está cerrado. "
            "Corré el export con el mercado abierto, o pasá --server-utc-offset "
            "(la hora del servidor se ve en el encabezado de Market Watch)."
        )
    if not -12 <= hours <= 14:
        raise RuntimeError(f"Offset de servidor inferido fuera de rango: {hours}h.")
    return hours


def account_server_name() -> Optional[str]:
    """
    Nombre del servidor de la cuenta abierta en el terminal (p.ej.
    `ICMarketsSC-Demo`), de `mt5.account_info()`, que es de solo lectura.
    `None` si no hay sesión. Lo usa la herencia del reloj del banco (spec 002,
    N43): dos símbolos del mismo servidor comparten el reloj.
    """
    info = mt5.account_info()
    server = getattr(info, "server", None) if info is not None else None
    return server.strip() if isinstance(server, str) and server.strip() else None


def detect_server_utc_offset(symbol: str) -> int:
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(
            f"symbol_info_tick({symbol}) devolvió None ({mt5.last_error()}). "
            "Verificar que el símbolo esté visible en Market Watch, o pasar --server-utc-offset."
        )
    return infer_server_utc_offset(float(tick.time), datetime.now(UTC).timestamp())


def exclude_forming_bar(df: pd.DataFrame, timeframe_minutes: int, now_utc: datetime) -> pd.DataFrame:
    """
    Candado anti-repainting -- mismo principio que el pipeline Linux
    (CsvOHLCProvider filtra `time < as_of` estrictamente). Excluye
    cualquier vela cuyo cierre (open_time + duración del TF) sea posterior
    a `now_utc`, es decir, la vela todavía en formación al momento de
    exportar. Una vela cuyo cierre coincide exactamente con now_utc SÍ se
    incluye (<=, no <).
    """
    bar_close = df["time"] + pd.Timedelta(minutes=timeframe_minutes)
    return df[bar_close <= pd.Timestamp(now_utc)].copy()


def compute_backward_start(min_anchor_gt: datetime, timeframe: str, min_bars: int = MIN_BARS_PER_TF) -> datetime:
    """
    Fecha de inicio (en hora GT naive -- se convierte a UTC antes de
    llamar a MT5) para asegurar >= min_bars velas antes del anchor más
    temprano, con un margen (BACKWARD_MARGIN) mas un colchon fijo
    (BACKWARD_BUFFER_DAYS) para fines de semana/feriados donde
    no hay velas intradía (aplica sobre todo a 1D/12H/4H/1H; para 1W no
    hace daño incluir el margen igual).
    """
    span = {
        "1W": timedelta(weeks=min_bars),
        "1D": timedelta(days=min_bars),
        "12H": timedelta(hours=min_bars * 12),
        "4H": timedelta(hours=min_bars * 4),
        "2H": timedelta(hours=min_bars * 2),
        "1H": timedelta(hours=min_bars),
        "30M": timedelta(minutes=min_bars * 30),
        "15M": timedelta(minutes=min_bars * 15),
        "5M": timedelta(minutes=min_bars * 5),
        "1M": timedelta(minutes=min_bars * 1),
    }[timeframe]
    return min_anchor_gt - span * BACKWARD_MARGIN - timedelta(days=BACKWARD_BUFFER_DAYS)


def atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    """
    Escribe `df` en `path` de forma atómica: a un temporal en el MISMO
    directorio y `os.replace()` al final (atómico dentro de un filesystem).
    Un fallo a mitad de escritura no deja `path` a medias -- queda el archivo
    anterior completo (o ninguno), y el temporal se borra (RF-15, plan T2,
    baseline H14: antes `to_csv` escribía directo sobre el destino).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_export_", suffix=".csv")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            df.to_csv(f, index=False)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.remove(tmp_name)
        except FileNotFoundError:
            pass
        raise


def make_run_dir(base_dir: Path, symbol: str, now_utc: Optional[datetime] = None) -> Tuple[str, Path]:
    """
    Crea `{base_dir}/{symbol}/{run_id}/` y devuelve `(run_id, ruta)`. `run_id`
    es `YYYYmmddTHHMMSS` en UTC; si ya existe (dos corridas en el mismo
    segundo) prueba `-1`, `-2`... La creación usa `mkdir` sin `exist_ok`, que es
    atómico, así que dos corridas simultáneas nunca terminan en el mismo
    directorio. Los sufijos ordenan después del run_id base, o sea que el
    orden alfabético sigue siendo el cronológico.
    """
    now = now_utc if now_utc is not None else datetime.now(UTC)
    stem = now.strftime("%Y%m%dT%H%M%S")
    parent = base_dir / symbol
    parent.mkdir(parents=True, exist_ok=True)
    attempt = 0
    while True:
        run_id = stem if attempt == 0 else f"{stem}-{attempt}"
        run_dir = parent / run_id
        try:
            run_dir.mkdir()
        except FileExistsError:
            attempt += 1
            continue
        return run_id, run_dir


# Tope de barras por llamada a copy_rates_range. MEDIDO contra MT5 real el
# 2026-09-29 (terminal con maxbars=100000): devuelve bien hasta ~57 700 barras
# (XAUUSD 1M, 60 días) y falla con `(-2, 'Terminal: Invalid params')` en rangos
# de ~70 000 o más (5M a 365 días, 1M a 90 días). Con el rango que pide este
# script (desde 800 velas antes del análisis más viejo), el 1M solo ya son
# ~140 000 barras: sin partir el rango, el export fallaba siempre en 1M. 30 000
# deja margen, incluso para un terminal con menos `maxbars`.
MAX_BARS_PER_CALL = 30_000


def split_range(date_from: datetime, date_to: datetime, timeframe_minutes: int,
                max_bars: int = MAX_BARS_PER_CALL) -> List[Tuple[datetime, datetime]]:
    """
    Parte `[date_from, date_to]` en tramos consecutivos, sin solaparse, cada uno
    de a lo sumo `max_bars` velas de `timeframe_minutes` (contando el mercado
    abierto 24/7, así que en la práctica traen menos). `copy_rates_range` es
    inclusivo en los dos extremos, por eso cada tramo termina un segundo antes
    de donde empieza el siguiente. Un rango corto queda en un único tramo.
    """
    span = timedelta(minutes=timeframe_minutes * max_bars)
    chunks: List[Tuple[datetime, datetime]] = []
    start = date_from
    while start <= date_to:
        end = min(start + span - timedelta(seconds=1), date_to)
        chunks.append((start, end))
        start = end + timedelta(seconds=1)
    return chunks or [(date_from, date_to)]


def fetch_rates(symbol: str, timeframe: str, date_from_utc: datetime, date_to_utc: datetime) -> pd.DataFrame:
    """
    Todas las velas de `symbol`/`timeframe` en el rango, pedidas por tramos
    (`split_range`) y unidas, ordenadas y sin `time` repetido. Un tramo que MT5
    rechaza (`None`) levanta `RuntimeError`; uno sin velas simplemente no aporta.
    Un DataFrame vacío significa "MT5 no tiene nada en todo el rango".
    """
    mt5_tf = TIMEFRAME_MAP[timeframe]
    frames = []
    for chunk_from, chunk_to in split_range(date_from_utc, date_to_utc, TIMEFRAME_MINUTES[timeframe]):
        rates = mt5.copy_rates_range(symbol, mt5_tf, chunk_from, chunk_to)
        if rates is None:
            raise RuntimeError(
                f"copy_rates_range devolvió None para {symbol}/{timeframe} "
                f"({chunk_from} -> {chunk_to}). Error MT5: {mt5.last_error()}. "
                "Verificar: símbolo visible en Market Watch, terminal logueado, "
                "rango de fechas dentro del historial disponible."
            )
        if len(rates):
            frames.append(pd.DataFrame(rates))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)


def export_timeframe(symbol: str, timeframe: str, date_from_utc: datetime, date_to_utc: datetime,
                     out_dir: Path, server_utc_offset_hours: float = 0.0,
                     dst_rule: str = "none") -> ExportResult:
    """
    Un archivo por temporalidad, usando copy_rates_range (rango inclusivo
    en ambos extremos -- "open time >= date_from" y "open time <= date_to"
    -- orden cronológico ascendente; verificado contra la doc oficial de
    MetaTrader5, no asumido de memoria). date_from_utc/date_to_utc deben
    pasarse en UTC -- MT5 lo requiere así (doc oficial: "Python usa la
    zona horaria local; MT5 guarda en UTC sin shift").

    `server_utc_offset_hours` es el offset de TODA la corrida con
    `dst_rule="none"`, y el offset BASE (de invierno) con `us`/`eu`, donde cada
    vela suma +1h si cae en horario de verano y las de la hora del cambio se
    descartan (RF-15b, N34).
    """
    df = fetch_rates(symbol, timeframe, date_from_utc, date_to_utc)
    if df.empty:
        raise RuntimeError(
            f"copy_rates_range para {symbol}/{timeframe} devolvió 0 velas "
            f"en el rango {date_from_utc} -> {date_to_utc}. Puede ser "
            "historial insuficiente en este terminal MT5 para esa "
            "temporalidad -- ver checklist manual, punto de profundidad "
            "de historial (jupyter/p2_systematic_task_plan.md)."
        )

    utc_times, ambiguous = server_time_to_utc_dst(df["time"], server_utc_offset_hours, dst_rule)
    df["time"] = utc_times
    discarded_dst = int(ambiguous.sum())
    if discarded_dst:
        print(
            f"AVISO: {timeframe}: {discarded_dst} vela(s) en la hora del cambio de horario de "
            f"verano ({dst_rule}) descartadas por ambiguas.",
            file=sys.stderr,
        )
        df = df[~ambiguous.to_numpy()]
    df = exclude_forming_bar(df, TIMEFRAME_MINUTES[timeframe], datetime.now(UTC))
    df["time"] = utc_to_gt_naive(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    if len(df) < MIN_BARS_PER_TF:
        print(
            f"AVISO: {timeframe} solo trajo {len(df)} velas (< {MIN_BARS_PER_TF}) "
            "-- algunos trades quedarán con snapshot_incompleto=True para esta "
            "temporalidad. No es un error del script, es historial real "
            "insuficiente en este terminal.",
            file=sys.stderr,
        )

    out = df[REQUIRED_COLUMNS]
    out_path = out_dir / f"{timeframe}.csv"
    atomic_write_csv(out, out_path)

    return ExportResult(
        timeframe=timeframe,
        path=out_path,
        rows=len(out),
        first_time=out["time"].iloc[0] if len(out) else None,
        last_time=out["time"].iloc[-1] if len(out) else None,
        discarded_dst=discarded_dst,
    )


def parse_timeframes_arg(raw: Optional[str]) -> Tuple[str, ...]:
    """
    Parsea `--timeframes` ("1H,30M,15M" -> ("1H", "30M", "15M")). Sin valor
    (`None` o cadena vacía), las `ALL_EXPORT_TIMEFRAMES` de siempre (las 9):
    las de `ON_DEMAND_TIMEFRAMES` (2H) solo salen si se piden por nombre.
    Levanta `ValueError`, con la lista de las que no existen, si alguna
    temporalidad pedida no está en `TIMEFRAME_MAP` -- función pura para poder
    probarla sin tocar MT5 (mismo criterio que `compute_backward_start`).
    """
    if not raw:
        return ALL_EXPORT_TIMEFRAMES
    requested = tuple(tf.strip() for tf in raw.split(",") if tf.strip())
    unknown = [tf for tf in requested if tf not in TIMEFRAME_MAP]
    if unknown:
        valid = list(ALL_EXPORT_TIMEFRAMES) + list(ON_DEMAND_TIMEFRAMES)
        raise ValueError(f"--timeframes desconocidas: {unknown}. Válidas: {valid}.")
    return requested


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--symbol", default=DEFAULT_SYMBOL,
        help=f"Símbolo EXACTO como aparece en Market Watch de MT5 (default confirmado: {DEFAULT_SYMBOL}; "
             "override-able si cambia de broker/cuenta).",
    )
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--per-run-dir", action="store_true",
        help="Escribe en un directorio nuevo por corrida, {out-dir}/{SIMBOLO}/{run_id}/ (spec 002, RF-15), "
             "en vez de directo en --out-dir. Imprime la ruta en una línea 'RUN_DIR: <ruta>'.",
    )
    parser.add_argument(
        "--min-anchor", required=True,
        help="MIN(timestamp_entry) real, hora GT naive, formato 'YYYY-MM-DD HH:MM:SS'. "
             "OBLIGATORIO reconfirmar contra la DB antes de correr -- este script NO lo "
             "consulta solo (ver jupyter/p2_systematic_task_plan.md, ítem pendiente "
             "'auto-fetch de min/max-anchor').",
    )
    parser.add_argument(
        "--max-anchor", required=True,
        help="MAX(timestamp_entry) real, mismo formato -- mismo requisito de reconfirmación manual.",
    )
    parser.add_argument(
        "--server-utc-offset", type=float, default=None,
        help="Offset del reloj del servidor del broker respecto de UTC, en horas (p.ej. 3). "
             "Por defecto se detecta del último tick del símbolo, lo que exige mercado abierto.",
    )
    parser.add_argument(
        "--dst-rule", choices=DST_RULES, default="none",
        help="Calendario de horario de verano del servidor del broker (spec 002, RF-15b): 'us' (2do "
             "domingo de marzo -> 1er domingo de noviembre), 'eu' (último domingo de marzo -> último "
             "domingo de octubre) o 'none' (un solo offset para toda la corrida, como siempre; es el "
             "default). Con us/eu el offset detectado/pasado se toma como el de AHORA y cada vela se "
             "convierte con el offset de su fecha.",
    )
    parser.add_argument(
        "--timeframes", default=None,
        help="Temporalidades a exportar, separadas por coma (p.ej. '1H,30M,15M'), en vez de las "
             f"{len(ALL_EXPORT_TIMEFRAMES)} de siempre ({','.join(ALL_EXPORT_TIMEFRAMES)}). Uso: T20 "
             "(candle_sync.py) puede pedir solo un subconjunto en un catch-up, o el spike de "
             "interoperabilidad (T22) puede probar con una sola TF sin esperar las 9. "
             f"Solo bajo pedido: {','.join(ON_DEMAND_TIMEFRAMES)} (P2 banco v2; no va al banco de velas).",
    )
    args = parser.parse_args(argv)

    try:
        timeframes_to_export = parse_timeframes_arg(args.timeframes)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    if not args.per_run_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)

    if not mt5.initialize():
        print(f"mt5.initialize() falló: {mt5.last_error()}", file=sys.stderr)
        return 1

    try:
        if args.server_utc_offset is None:
            server_offset = detect_server_utc_offset(args.symbol)
            origen = "detectado del último tick"
        else:
            server_offset = args.server_utc_offset
            origen = "pasado por --server-utc-offset"
        print(f"Reloj del servidor: UTC{server_offset:+g} ({origen}).")

        now_server_naive = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=server_offset)
        try:
            base_offset = base_offset_from_current(server_offset, now_server_naive, args.dst_rule)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        if args.dst_rule == "none":
            print("Regla DST: none (un solo offset para toda la corrida).\n")
        else:
            print(f"Regla DST: {args.dst_rule} -> offset base (invierno) UTC{base_offset:+g}; "
                  "cada vela se convierte con el offset de su fecha.\n")

        # Para la herencia del reloj en el banco (spec 002, N43): candle_sync lee estas dos líneas.
        server_name = account_server_name()
        if server_name:
            print(f"SERVER: {server_name}")
        print(f"BASE_UTC_OFFSET: {base_offset:g}", flush=True)

        # El directorio por corrida se crea recién acá, con MT5 ya inicializado y el
        # reloj resuelto: si algo de lo anterior falla, no queda un directorio vacío.
        if args.per_run_dir:
            run_id, target_dir = make_run_dir(args.out_dir, args.symbol)
            print(f"RUN_ID: {run_id}")
            print(f"RUN_DIR: {target_dir}\n", flush=True)
        else:
            target_dir = args.out_dir

        min_anchor = datetime.strptime(args.min_anchor, "%Y-%m-%d %H:%M:%S")
        max_anchor = datetime.strptime(args.max_anchor, "%Y-%m-%d %H:%M:%S")
        now_gt = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=GT_OFFSET_HOURS)
        date_to_gt = max(max_anchor, now_gt)  # nunca pedir menos que "ahora"

        results = []
        skipped: List[str] = []
        for tf in timeframes_to_export:
            start_gt = compute_backward_start(min_anchor, tf)
            date_from_srv = gt_naive_to_server(start_gt, server_offset)
            date_to_srv = gt_naive_to_server(date_to_gt, server_offset)
            print(f"[{tf}] pidiendo {date_from_srv:%Y-%m-%d %H:%M} -> {date_to_srv:%Y-%m-%d %H:%M} (reloj servidor) ...")
            try:
                result = export_timeframe(
                    args.symbol, tf, date_from_srv, date_to_srv, target_dir, base_offset, args.dst_rule
                )
            except RuntimeError as exc:
                # RF-15: si MT5 no entrega una temporalidad (poca historia de 1M, rango
                # rechazado), se informa y se sigue con las demás -- cada CSV es independiente.
                print(f"AVISO: {tf} no se pudo exportar, se sigue con las demás: {exc}", file=sys.stderr)
                print(f"SKIPPED_TF: {tf}")
                skipped.append(tf)
                continue
            print(
                f"[{tf}] {result.rows} velas escritas en {result.path} "
                f"({result.first_time} .. {result.last_time})"
                + (f"; {result.discarded_dst} descartadas por el cambio de horario" if result.discarded_dst else "")
            )
            results.append(result)

        print("\nResumen:")
        for r in results:
            print(f"  {r.timeframe}: {r.rows} filas")
        for tf in skipped:
            print(f"  {tf}: NO exportada")

    finally:
        mt5.shutdown()

    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
