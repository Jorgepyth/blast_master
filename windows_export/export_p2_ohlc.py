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

LIMITACIÓN (DST): se aplica UN offset a todas las velas. Los brokers
mueven el reloj del servidor con el horario de verano (típicamente UTC+2
en invierno, UTC+3 en verano), así que las velas de la estación opuesta a
la del momento del export quedan corridas 1h. Para 1D/1W es irrelevante y
para el calentamiento de EMA/ADX, despreciable. Lo que sí importa es que
las velas alrededor de cada anchor estén bien alineadas: si los anchors
caen en otra estación que la del export, pasá --server-utc-offset a mano.
tools/p2_backtest.py:calibrate_clock_offset() lo verifica contra las
entradas reales en cada corrida y bloquea el reporte si el reloj no cuadra.

Solo funciones de LECTURA de MetaTrader5 (copy_rates_range). PROHIBIDO:
order_send, order_check, o cualquier función de escritura -- no se
importan ni se usan en este archivo.

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
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

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
# existe, no es una suposición.
TIMEFRAME_MAP: Dict[str, int] = {
    "1W": mt5.TIMEFRAME_W1,
    "1D": mt5.TIMEFRAME_D1,
    "12H": mt5.TIMEFRAME_H12,
    "4H": mt5.TIMEFRAME_H4,
    "1H": mt5.TIMEFRAME_H1,
    "30M": mt5.TIMEFRAME_M30,
    "15M": mt5.TIMEFRAME_M15,
}

# Duración de cada vela en minutos -- usado por exclude_forming_bar() para
# el candado anti-repainting (mismo principio que el pipeline Linux).
TIMEFRAME_MINUTES: Dict[str, int] = {
    "1W": 7 * 24 * 60,
    "1D": 24 * 60,
    "12H": 12 * 60,
    "4H": 4 * 60,
    "1H": 60,
    "30M": 30,
    "15M": 15,
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
        "1H": timedelta(hours=min_bars),
        "30M": timedelta(minutes=min_bars * 30),
        "15M": timedelta(minutes=min_bars * 15),
    }[timeframe]
    return min_anchor_gt - span * BACKWARD_MARGIN - timedelta(days=BACKWARD_BUFFER_DAYS)


def export_timeframe(symbol: str, timeframe: str, date_from_utc: datetime, date_to_utc: datetime,
                     out_dir: Path, server_utc_offset_hours: float = 0.0) -> ExportResult:
    """
    Un archivo por temporalidad, usando copy_rates_range (rango inclusivo
    en ambos extremos -- "open time >= date_from" y "open time <= date_to"
    -- orden cronológico ascendente; verificado contra la doc oficial de
    MetaTrader5, no asumido de memoria). date_from_utc/date_to_utc deben
    pasarse en UTC -- MT5 lo requiere así (doc oficial: "Python usa la
    zona horaria local; MT5 guarda en UTC sin shift").
    """
    mt5_tf = TIMEFRAME_MAP[timeframe]
    rates = mt5.copy_rates_range(symbol, mt5_tf, date_from_utc, date_to_utc)
    if rates is None:
        raise RuntimeError(
            f"copy_rates_range devolvió None para {symbol}/{timeframe} "
            f"({date_from_utc} -> {date_to_utc}). Error MT5: {mt5.last_error()}. "
            "Verificar: símbolo visible en Market Watch, terminal logueado, "
            "rango de fechas dentro del historial disponible."
        )

    df = pd.DataFrame(rates)
    if df.empty:
        raise RuntimeError(
            f"copy_rates_range para {symbol}/{timeframe} devolvió 0 velas "
            f"en el rango {date_from_utc} -> {date_to_utc}. Puede ser "
            "historial insuficiente en este terminal MT5 para esa "
            "temporalidad -- ver checklist manual, punto de profundidad "
            "de historial (jupyter/p2_systematic_task_plan.md)."
        )

    df["time"] = server_time_to_utc(df["time"], server_utc_offset_hours)
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
    out.to_csv(out_path, index=False)

    return ExportResult(
        timeframe=timeframe,
        path=out_path,
        rows=len(out),
        first_time=out["time"].iloc[0] if len(out) else None,
        last_time=out["time"].iloc[-1] if len(out) else None,
    )


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--symbol", default=DEFAULT_SYMBOL,
        help=f"Símbolo EXACTO como aparece en Market Watch de MT5 (default confirmado: {DEFAULT_SYMBOL}; "
             "override-able si cambia de broker/cuenta).",
    )
    parser.add_argument("--out-dir", required=True, type=Path)
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
    args = parser.parse_args(argv)

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
        print(f"Reloj del servidor: UTC{server_offset:+g} ({origen}).\n")

        min_anchor = datetime.strptime(args.min_anchor, "%Y-%m-%d %H:%M:%S")
        max_anchor = datetime.strptime(args.max_anchor, "%Y-%m-%d %H:%M:%S")
        now_gt = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=GT_OFFSET_HOURS)
        date_to_gt = max(max_anchor, now_gt)  # nunca pedir menos que "ahora"

        results = []
        for tf in ("1W", "1D", "12H", "4H", "1H", "30M", "15M"):
            start_gt = compute_backward_start(min_anchor, tf)
            date_from_srv = gt_naive_to_server(start_gt, server_offset)
            date_to_srv = gt_naive_to_server(date_to_gt, server_offset)
            print(f"[{tf}] pidiendo {date_from_srv:%Y-%m-%d %H:%M} -> {date_to_srv:%Y-%m-%d %H:%M} (reloj servidor) ...")
            result = export_timeframe(args.symbol, tf, date_from_srv, date_to_srv, args.out_dir, server_offset)
            print(
                f"[{tf}] {result.rows} velas escritas en {result.path} "
                f"({result.first_time} .. {result.last_time})"
            )
            results.append(result)

        print("\nResumen:")
        for r in results:
            print(f"  {r.timeframe}: {r.rows} filas")

    finally:
        mt5.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
