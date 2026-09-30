"""
tools/p2_bank_v2.py — P2 banco v2: funciones para evaluar el P2 sistemático con
temporalidades cortas (2H y 5M) sobre el banco de velas. Las usa el cuaderno
jupyter/p2_banco_v2.ipynb.

Replica el protocolo de jupyter/p2_edge_evaluation.ipynb (v2, 2026-09-23) con los
cambios fijados en jupyter/p2_banco_v2/preregistro.md. Las reglas de señal y la
estadística se reutilizan tal cual de tools/p2_backtest.py, tools/edge_evaluation.py
y core/stats_tests.py. Este módulo solo agrega lo que a esos archivos les falta y
no se pueden tocar:
  - 2H en las duraciones de vela (TIMEFRAME_MINUTES no la tiene): BankProvider;
  - el remuestreo 1H -> 2H alineado a la hora del servidor (F2c) y la limpieza de
    las velas repetidas del banco;
  - el primer toque S1/S4 de docs/criterios-de-acierto.md, con la TF más fina;
  - las comparaciones pareadas, las etiquetas de R7 y el procedimiento de 2F.

Ninguna función de acá abre una DB ni un archivo, ni escribe nada: reciben los datos
ya cargados (una sesión de solo lectura, un proveedor de velas, DataFrames) y
devuelven resultados. Abrir las DBs (mode=ro) y elegir las rutas es trabajo del
cuaderno. La única lectura de disco es la de BankProvider, heredada de
CsvOHLCProvider, que lee los CSV del banco la primera vez que se le pide una TF.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from cli.main import determine_market_bias
from core.math_engine import calculate_edge_score
from core.p2_ground_truth import infer_thesis_direction
from core.stats_tests import (
    binomial_test_two_sided,
    mcnemar_exact,
    min_detectable_gap,
    net_score,
    permutation_max_net_test,
    wilson_interval,
)
from tools.database import AnalysisLayer, TacticalAudit, UnifiedDepartment
from tools.edge_evaluation import select_best, simplex_grid, walk_forward_folds
from tools.p2_backtest import (
    CLOCK_MISALIGNMENT_MARGIN,
    MIN_BARS_PER_TF,
    MODEL_A,
    MODEL_B,
    MODEL_C,
    MODEL_D,
    MODEL_E,
    MODEL_F,
    TIMEFRAME_MINUTES,
    ClockCalibration,
    CsvOHLCProvider,
    ModelSpec,
    TFIndicatorSnapshot,
    bias_from_emas,
    compute_score_p2_sistematico,
    confirm_with_di,
    evaluate_clock_entries,
    rescale_p2_sistematico,
    strength_from_adx,
)

# ---------------------------------------------------------------------------
# Constantes del pre-registro (jupyter/p2_banco_v2/preregistro.md)
# ---------------------------------------------------------------------------

# tools/p2_backtest.py:TIMEFRAME_MINUTES no tiene 2H y ese archivo no se toca.
V2_TIMEFRAME_MINUTES: Dict[str, int] = {**TIMEFRAME_MINUTES, "2H": 120}

# Ancla de docs/criterios-de-acierto.md:14-15. El banco v1 usó 15 min.
ANCHOR_LEAD = timedelta(minutes=20)

# Precio de partida y camino del primer toque: de la TF más fina a la más gruesa.
S1_TIMEFRAMES: Tuple[str, ...] = ("1M", "5M", "15M", "30M", "1H")
MAX_HORIZON_1H_BARS = 2160
S4_WINDOW = timedelta(hours=48)

# TF que leen los modelos (para cargar el snapshot de indicadores una sola vez).
MODEL_TIMEFRAMES: Tuple[str, ...] = ("1W", "1D", "12H", "4H", "2H", "1H", "30M", "15M", "5M")

# Motivos de exclusión, en el orden en que se aplican (pre-registro §3).
EXCL_MISSING_LEVELS = "missing_levels"
EXCL_BACKDATED = "backdated"
EXCL_NO_HISTORY = "no_history"
EXCL_LEVELS_SAME_SIDE = "levels_same_side"
IN_SCOPE = "in_scope"

SEG_DIRECTIONAL = "Direccional"
SEG_CHOPPY = "Choppy"

# ---------------------------------------------------------------------------
# Modelos (pre-registro §5)
# ---------------------------------------------------------------------------

TIMEFRAMES_2X: Tuple[str, ...] = ("1D", "2H", "1H", "30M", "5M")

WEIGHTS_2X: Dict[str, Tuple[float, ...]] = {
    "2A": (0.20, 0.20, 0.20, 0.20, 0.20),
    "2B": (0.10, 0.25, 0.25, 0.20, 0.20),
    "2C": (0.20, 0.20, 0.20, 0.30, 0.10),
    "2D": (0.15, 0.30, 0.25, 0.20, 0.10),
    "2E": (0.10, 0.25, 0.30, 0.25, 0.10),
}


def model_2x(name: str, weights: Sequence[float], description: str = "") -> ModelSpec:
    """Un modelo del banco v2: las 5 TF de TIMEFRAMES_2X con las reglas de MODEL_D."""
    if len(weights) != len(TIMEFRAMES_2X):
        raise ValueError(f"{name}: hacen falta {len(TIMEFRAMES_2X)} pesos, llegaron {len(weights)}")
    return ModelSpec(
        name=name,
        label=f"{name} — " + " · ".join(f"{tf} {w:.2f}" for tf, w in zip(TIMEFRAMES_2X, weights)),
        timeframes=TIMEFRAMES_2X,
        weights={tf: float(w) for tf, w in zip(TIMEFRAMES_2X, weights)},
        ema_chain=MODEL_D.ema_chain,
        use_di=MODEL_D.use_di,
        description=description or "Banco v2: reglas de D sobre 1D, 2H, 1H, 30M y 5M.",
    )


MODELS_2X: Tuple[ModelSpec, ...] = tuple(model_2x(n, w) for n, w in WEIGHTS_2X.items())

# D14 (docs/prompts/2026-09-23-p2-consenso-modelo-f-adenda-1.md:125-145): reglas de E.
MODEL_G = ModelSpec(
    name="G",
    label="G — reglas de E, pico en 1H/30M/15M",
    timeframes=MODEL_E.timeframes,
    weights={"1W": 0.10, "1D": 0.10, "12H": 0.10, "4H": 0.10, "1H": 0.20, "30M": 0.20, "15M": 0.20},
    ema_chain=MODEL_E.ema_chain,
    use_di=MODEL_E.use_di,
    description="D14 del 2026-09-23. Definido después de ver los datos.",
)

# TF de D con pesos iguales: aísla el efecto de las TF del de los pesos (Q4).
MODEL_DEQ = ModelSpec(
    name="D-EQ",
    label="D-EQ — TF de D, 1/6 cada una",
    timeframes=MODEL_D.timeframes,
    weights={tf: 1 / 6 for tf in MODEL_D.timeframes},
    ema_chain=MODEL_D.ema_chain,
    use_di=MODEL_D.use_di,
    description="Control de Q4: las TF de D con pesos iguales.",
)


# Familia de comparación de 8.1 (pre-registro §5): 13 variantes. 2F va aparte.
FAMILY: Tuple[ModelSpec, ...] = (MODEL_A, MODEL_B, MODEL_C, MODEL_D, MODEL_E, MODEL_F, MODEL_G, MODEL_DEQ) + MODELS_2X
NAMES_2X: Tuple[str, ...] = tuple(m.name for m in MODELS_2X)
P2_DISC = "P2-DISC"


# ---------------------------------------------------------------------------
# Proveedor de velas con 2H
# ---------------------------------------------------------------------------

OHLC_COLUMNS = ["time", "open", "high", "low", "close"]


class BankProvider(CsvOHLCProvider):
    """
    CsvOHLCProvider con dos agregados, sin cambiar lo que ya hace:
      - duraciones de V2_TIMEFRAME_MINUTES, que suman 2H. Para las demás TF,
        closed_bars y bar_containing dan exactamente lo mismo que el original;
      - TF inyectadas en memoria (add_frame), para leer el 2H nativo desde la
        carpeta del export o el 2H remuestreado, sin copiarlos al banco.
    """

    def __init__(self, base_dir: str, extra_frames: Optional[Dict[str, pd.DataFrame]] = None):
        super().__init__(base_dir)
        self._extra: Dict[str, pd.DataFrame] = {}
        self._arrays: Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        for tf, df in (extra_frames or {}).items():
            self.add_frame(tf, df)

    def add_frame(self, timeframe: str, df: pd.DataFrame) -> None:
        if timeframe not in V2_TIMEFRAME_MINUTES:
            raise KeyError(f"TF sin duración conocida: {timeframe!r}")
        out = df[OHLC_COLUMNS].copy()
        out["time"] = pd.to_datetime(out["time"])
        out = out.sort_values("time").reset_index(drop=True)
        self._extra[timeframe] = out
        self._cache[timeframe] = out
        self._arrays.pop(timeframe, None)

    def has_timeframe(self, timeframe: str) -> bool:
        return timeframe in self._extra or super().has_timeframe(timeframe)

    def frame(self, timeframe: str) -> pd.DataFrame:
        return self._load_tf(timeframe)

    def arrays(self, timeframe: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(aperturas, máximos, mínimos) como arreglos, para recorrer el camino del primer toque."""
        if timeframe not in self._arrays:
            df = self._load_tf(timeframe)
            self._arrays[timeframe] = (df["time"].to_numpy(dtype="datetime64[ns]"),
                                       df["high"].to_numpy(dtype=float), df["low"].to_numpy(dtype=float))
        return self._arrays[timeframe]

    def load_all(self, timeframes: Iterable[str]) -> List[str]:
        """Carga en memoria las TF disponibles: desde acá, todo usa ese snapshot."""
        loaded = []
        for tf in timeframes:
            if self.has_timeframe(tf):
                self._load_tf(tf)
                loaded.append(tf)
        return loaded

    def closed_bars(self, timeframe: str, as_of: datetime) -> pd.DataFrame:
        df = self._load_tf(timeframe)
        return df[df["time"] + pd.Timedelta(minutes=V2_TIMEFRAME_MINUTES[timeframe]) <= pd.Timestamp(as_of)]

    def bar_containing(self, timeframe: str, t: datetime) -> Optional[Tuple[float, float]]:
        if not self.has_timeframe(timeframe):
            return None
        df = self._load_tf(timeframe)
        idx = int(df["time"].searchsorted(pd.Timestamp(t), side="right")) - 1
        if idx < 0:
            return None
        row = df.iloc[idx]
        if pd.Timestamp(t) >= row["time"] + pd.Timedelta(minutes=V2_TIMEFRAME_MINUTES[timeframe]):
            return None
        return float(row["high"]), float(row["low"])

    def n_closed(self, timeframe: str, as_of: datetime) -> int:
        """Cuántas velas de la TF ya cerraron en as_of (0 si la TF no existe)."""
        if not self.has_timeframe(timeframe):
            return 0
        df = self._load_tf(timeframe)
        close_times = df["time"] + pd.Timedelta(minutes=V2_TIMEFRAME_MINUTES[timeframe])
        return int(close_times.searchsorted(pd.Timestamp(as_of), side="right"))


# ---------------------------------------------------------------------------
# Análisis: lectura desde una sesión ya abierta y alcance (pre-registro §3)
# ---------------------------------------------------------------------------

@dataclass
class AnalysisRecord:
    account: str
    trade_id: str
    asset: Optional[str]
    created_at: datetime
    is_backdated: bool
    evp: Optional[float]
    si: Optional[float]
    calc_edge: Optional[float]
    market_bias: Optional[str]
    p2_disc: Optional[int]
    layers: Dict[str, int] = field(default_factory=dict)   # scores P0..P4 del análisis


def _as_float(v) -> Optional[float]:
    return float(v) if v is not None else None


def load_analyses(session: Session, account: str) -> List[AnalysisRecord]:
    """
    Una fila por análisis, con columnas explícitas (CLAUDE.md:154-157: una DB no
    migrada rompe un SELECT de la entidad completa). P2-DISC es el score de la capa
    P2, como en tools/p2_backtest.py:929-935.
    """
    rows = session.execute(
        select(UnifiedDepartment.id, UnifiedDepartment.asset, UnifiedDepartment.created_at,
               UnifiedDepartment.is_backdated, UnifiedDepartment.edge_validation_price,
               UnifiedDepartment.structural_invalidation, UnifiedDepartment.calc_edge,
               UnifiedDepartment.market_bias)
        .order_by(UnifiedDepartment.created_at)
    ).all()
    layers: Dict[str, Dict[str, int]] = {}
    for tid, name, score in session.execute(
        select(AnalysisLayer.trade_id, AnalysisLayer.layer_name, AnalysisLayer.score)
    ).all():
        if score is not None:
            layers.setdefault(tid, {})[name] = score
    return [
        AnalysisRecord(
            account=account, trade_id=r.id, asset=r.asset, created_at=r.created_at,
            is_backdated=bool(r.is_backdated), evp=_as_float(r.edge_validation_price),
            si=_as_float(r.structural_invalidation), calc_edge=_as_float(r.calc_edge),
            market_bias=r.market_bias, p2_disc=layers.get(r.id, {}).get("P2"), layers=layers.get(r.id, {}),
        )
        for r in rows
    ]


def edge_with_p2(rec: AnalysisRecord, p2_value: Optional[int]) -> Optional[float]:
    """
    El calc_edge del análisis con otro P2 y los mismos P0, P1, P3 y P4, con
    core.math_engine.calculate_edge_score. None si falta el P2 o alguna de las otras capas.
    """
    others = [rec.layers.get(k) for k in ("P0", "P1", "P3", "P4")]
    if p2_value is None or any(v is None for v in others):
        return None
    return calculate_edge_score(others[0], others[1], p2_value, others[2], others[3])


@dataclass
class ScopedAnalysis:
    rec: AnalysisRecord
    anchor: Optional[datetime]
    status: str                     # IN_SCOPE o un motivo EXCL_*
    start_price: Optional[float] = None
    start_tf: Optional[str] = None
    thesis: Optional[str] = None    # "long" / "short"
    bias_original: Optional[str] = None
    segment: Optional[str] = None   # SEG_DIRECTIONAL / SEG_CHOPPY


def starting_price(provider: BankProvider, anchor: datetime,
                   timeframes: Sequence[str] = S1_TIMEFRAMES) -> Tuple[Optional[float], Optional[str]]:
    """Cierre de la última vela cerrada en el ancla, en la TF más fina que tenga una."""
    for tf in timeframes:
        if not provider.has_timeframe(tf):
            continue
        bars = provider.closed_bars(tf, anchor)
        if len(bars):
            return float(bars["close"].iloc[-1]), tf
    return None, None


def segment_of(calc_edge: Optional[float]) -> Tuple[Optional[str], Optional[str]]:
    """(bias original, segmento) con determine_market_bias, como §6 del v1."""
    if calc_edge is None:
        return None, None
    bias = determine_market_bias(float(calc_edge))
    return bias, (SEG_CHOPPY if bias == "Choppy / Neutral" else SEG_DIRECTIONAL)


def scope_analysis(rec: AnalysisRecord, provider: BankProvider, lead: timedelta = ANCHOR_LEAD,
                   price_timeframes: Sequence[str] = S1_TIMEFRAMES) -> ScopedAnalysis:
    """Aplica los filtros del pre-registro §3, en orden."""
    bias, seg = segment_of(rec.calc_edge)
    if rec.evp is None or rec.si is None:
        return ScopedAnalysis(rec, None, EXCL_MISSING_LEVELS, bias_original=bias, segment=seg)
    if rec.is_backdated:
        return ScopedAnalysis(rec, None, EXCL_BACKDATED, bias_original=bias, segment=seg)
    anchor = rec.created_at - lead
    price, tf = starting_price(provider, anchor, price_timeframes)
    if price is None:
        return ScopedAnalysis(rec, anchor, EXCL_NO_HISTORY, bias_original=bias, segment=seg)
    thesis = infer_thesis_direction(price, rec.evp, rec.si)
    if thesis is None:
        return ScopedAnalysis(rec, anchor, EXCL_LEVELS_SAME_SIDE, price, tf, None, bias, seg)
    return ScopedAnalysis(rec, anchor, IN_SCOPE, price, tf, thesis, bias, seg)


def backdated_anchor(rec: AnalysisRecord, lead: timedelta = ANCHOR_LEAD) -> datetime:
    """Ancla para la etiqueta Overlap: en un retroactivo, created_at ya es la hora tipeada."""
    return rec.created_at if rec.is_backdated else rec.created_at - lead


# ---------------------------------------------------------------------------
# Reloj (pre-registro §2 y F2b.1)
# ---------------------------------------------------------------------------

def reference_entries(session: Session) -> List[Tuple[datetime, float]]:
    """
    Los mismos precios de referencia que calibrate_clock_offset
    (tools/p2_backtest.py:1143-1160): órdenes llenadas con hora y precio, y
    mark_price de los análisis no retroactivos.
    """
    entries = list(session.execute(
        select(TacticalAudit.entry_time, TacticalAudit.entry_price).where(
            TacticalAudit.order_filled == True,  # noqa: E712
            TacticalAudit.entry_time.isnot(None),
            TacticalAudit.entry_price > 0,
        )
    ).all())
    entries += list(session.execute(
        select(UnifiedDepartment.created_at, UnifiedDepartment.mark_price).where(
            UnifiedDepartment.mark_price > 0,
            UnifiedDepartment.is_backdated.isnot(True),
        )
    ).all())
    return [(t, float(p)) for t, p in entries]


def clock_check_tf(entries: Sequence[Tuple[datetime, float]], provider: BankProvider, timeframe: str,
                   offsets: Sequence[int] = range(-6, 7)) -> ClockCalibration:
    """calibrate_clock_offset en una TF elegida (el original elige 15M/30M/1H solo)."""
    rates = evaluate_clock_entries(entries, provider, timeframe, offsets)
    best = max(rates, key=lambda o: (rates[o], -abs(o)))
    aligned = best == 0 or rates.get(0, 0.0) >= rates[best] - CLOCK_MISALIGNMENT_MARGIN
    return ClockCalibration(timeframe, len(entries), rates, best, aligned)


# ---------------------------------------------------------------------------
# Hora del servidor y remuestreo alineado (F2c)
# ---------------------------------------------------------------------------
#
# Misma definición de horario de verano que windows_export/export_p2_ohlc.py
# (dst_transition_dates, dst_masks): el cambio cae a las DST_TRANSITION_HOUR del
# reloj del servidor. tests/test_p2_bank_v2.py compara las dos implementaciones.
# Ese script no se puede importar acá: importa MetaTrader5 al cargarse.

GT_OFFSET_HOURS = 6
DST_TRANSITION_HOUR = 2
BASE_SERVER_OFFSET_HOURS = 2  # invierno; +1 en horario de verano (servidor UTC+3 medido en el spike)
_SUNDAY = 6


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return date(year, month, 1 + (weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def dst_transition_dates(year: int, dst_rule: str) -> Tuple[date, date]:
    if dst_rule == "us":
        return _nth_weekday(year, 3, _SUNDAY, 2), _nth_weekday(year, 11, _SUNDAY, 1)
    if dst_rule == "eu":
        return _last_weekday(year, 3, _SUNDAY), _last_weekday(year, 10, _SUNDAY)
    raise ValueError(f"dst_rule desconocida: {dst_rule!r}")


def server_is_dst(server_naive: pd.Series, dst_rule: str) -> pd.Series:
    """El `is_dst` de export_p2_ohlc.dst_masks: después de la hora del cambio de primavera y antes del de otoño."""
    out = pd.Series(False, index=server_naive.index)
    if dst_rule == "none" or server_naive.empty:
        return out
    for year in server_naive.dt.year.unique():
        start, end = dst_transition_dates(int(year), dst_rule)
        gap = pd.Timestamp(start) + pd.Timedelta(hours=DST_TRANSITION_HOUR)
        fall = pd.Timestamp(end) + pd.Timedelta(hours=DST_TRANSITION_HOUR)
        out |= (server_naive.dt.year == year) & (server_naive >= gap + pd.Timedelta(hours=1)) & (server_naive < fall)
    return out


def server_is_ambiguous(server_naive: pd.Series, dst_rule: str) -> pd.Series:
    """El `ambiguous` de export_p2_ohlc.dst_masks: la hora del cambio, que el exportador descarta."""
    out = pd.Series(False, index=server_naive.index)
    if dst_rule == "none" or server_naive.empty:
        return out
    hour = pd.Timedelta(hours=1)
    for year in server_naive.dt.year.unique():
        start, end = dst_transition_dates(int(year), dst_rule)
        in_year = server_naive.dt.year == year
        for day in (start, end):
            t0 = pd.Timestamp(day) + pd.Timedelta(hours=DST_TRANSITION_HOUR)
            out |= in_year & (server_naive >= t0) & (server_naive < t0 + hour)
    return out


def gt_to_server(times_gt: pd.Series, dst_rule: str,
                 base_offset_hours: int = BASE_SERVER_OFFSET_HOURS) -> pd.Series:
    """
    Inverso de la conversión del exportador (servidor -> UTC -> GT): una hora GT
    naive vuelve al reloj del servidor. Con horario de verano el servidor va
    base + 1 h adelante de UTC; si esa hora de servidor cae en verano, es la
    buena, y si no, la de invierno. Las velas de la hora del cambio no existen
    en los CSV (el exportador las descarta), así que no hay caso ambiguo.
    """
    times_gt = pd.to_datetime(pd.Series(times_gt)).reset_index(drop=True)
    summer = times_gt + pd.Timedelta(hours=GT_OFFSET_HOURS + base_offset_hours + 1)
    winter = times_gt + pd.Timedelta(hours=GT_OFFSET_HOURS + base_offset_hours)
    return summer.where(server_is_dst(summer, dst_rule), winter)


def aggregate_server_aligned(df: pd.DataFrame, bucket_hours: int, dst_rule: str,
                             base_offset_hours: int = BASE_SERVER_OFFSET_HOURS) -> pd.DataFrame:
    """
    Agrupa velas en tramos de `bucket_hours` que empiezan en múltiplos de esa
    cantidad de horas del reloj del servidor (como arma MT5 las velas H2/H4) y
    devuelve una vela por tramo, etiquetada en GT naive con la hora de apertura
    del tramo: apertura de la primera vela, máximo, mínimo y cierre de la última.
    `n_bars` dice cuántas velas de origen tenía el tramo.

    Un tramo que empieza en la hora del cambio de horario (server_is_ambiguous) se
    descarta, igual que el exportador descarta la vela nativa de esa hora: su etiqueta
    no tiene una hora GT única.
    """
    src = df[OHLC_COLUMNS].copy()
    src["time"] = pd.to_datetime(src["time"])
    src = src.sort_values("time").reset_index(drop=True)
    server = gt_to_server(src["time"], dst_rule, base_offset_hours)
    shift = server - src["time"]                          # 8 h o 9 h según la estación
    bucket_server = server.dt.floor(f"{bucket_hours}h")   # desde 1970-01-01 00:00: hora 0 del servidor
    keep = ~server_is_ambiguous(bucket_server, dst_rule)
    src, shift, bucket_server = src[keep].copy(), shift[keep], bucket_server[keep]
    src["bucket"] = bucket_server - shift
    g = src.groupby("bucket", sort=True)
    out = pd.DataFrame({
        "time": g["time"].first().index,
        "open": g["open"].first().to_numpy(),
        "high": g["high"].max().to_numpy(),
        "low": g["low"].min().to_numpy(),
        "close": g["close"].last().to_numpy(),
        "n_bars": g["time"].count().to_numpy(),
    })
    return out.reset_index(drop=True)


def resample_1h_to_2h(df_1h: pd.DataFrame, dst_rule: str,
                      base_offset_hours: int = BASE_SERVER_OFFSET_HOURS) -> pd.DataFrame:
    """F2c: 2H desde 1H, tramos que empiezan en hora par del servidor."""
    return aggregate_server_aligned(df_1h, 2, dst_rule, base_offset_hours)


def on_server_boundary(times_gt: pd.Series, timeframe: str, dst_rule: str,
                       base_offset_hours: int = BASE_SERVER_OFFSET_HOURS) -> pd.Series:
    """¿La vela abre donde MT5 abre las de esa TF? 1W: domingo 00:00 del servidor; el resto, múltiplo de su duración."""
    server = gt_to_server(pd.to_datetime(pd.Series(times_gt)), dst_rule, base_offset_hours)
    minutes = server.dt.hour * 60 + server.dt.minute
    if timeframe == "1W":
        return (server.dt.dayofweek == _SUNDAY) & (minutes == 0)
    return (minutes % V2_TIMEFRAME_MINUTES[timeframe]) == 0


# Hallazgo del 2026-09-29 (banco de velas de la spec 002): en estas TF, cada vela de
# invierno puede estar DOS veces, con OHLC idéntico y etiquetas separadas por 1 h. Una
# viene del import de los CSV viejos, exportados con un solo offset (+3); la otra, del
# export nuevo con --dst-rule us (+2 en invierno). La fusión deduplica por `time`, así
# que no las ve. 1H y las más finas no tienen el problema: el banco solo guarda las de
# la estación del export (tools/candle_bank.py:filter_by_export_season).
DUPLICATE_PRONE_TIMEFRAMES: Tuple[str, ...] = ("4H", "12H", "1D", "1W")


def drop_relabeled_duplicates(df: pd.DataFrame, timeframe: str, dst_rule: str,
                              base_offset_hours: int = BASE_SERVER_OFFSET_HOURS) -> Tuple[pd.DataFrame, Dict[str, int]]:
    """
    Quita la copia repetida de cada par de velas consecutivas a 1 h de distancia con
    OHLC idéntico: queda la que abre en la frontera del servidor (on_server_boundary).
    Si las dos o ninguna están en la frontera, queda la más nueva y se cuenta como
    "sin resolver". No toca pares con OHLC distinto. Solo lectura: devuelve una copia.
    """
    src = df[OHLC_COLUMNS].copy()
    src["time"] = pd.to_datetime(src["time"])
    src = src.sort_values("time").reset_index(drop=True)
    if len(src) < 2:
        return src, {"pares": 0, "sin_resolver": 0}
    same = np.ones(len(src) - 1, dtype=bool)
    for col in ("open", "high", "low", "close"):
        values = src[col].to_numpy(dtype=float)
        same &= values[1:] == values[:-1]
    one_hour = (src["time"].diff().iloc[1:] == pd.Timedelta(hours=1)).to_numpy()
    pairs = np.where(same & one_hour)[0]
    aligned = on_server_boundary(src["time"], timeframe, dst_rule, base_offset_hours).to_numpy()
    drop, unresolved = [], 0
    for i in pairs:
        if aligned[i] and not aligned[i + 1]:
            drop.append(i + 1)
        elif aligned[i + 1] and not aligned[i]:
            drop.append(i)
        else:
            drop.append(i)
            unresolved += 1
    clean = src.drop(index=drop).reset_index(drop=True)
    return clean, {"pares": int(len(pairs)), "sin_resolver": unresolved}


def compare_series(a: pd.DataFrame, b: pd.DataFrame, tol: float = 1e-9) -> Dict[str, float]:
    """
    F2d: dónde se superponen dos series de la misma TF, qué parte de las horas de
    apertura está en las dos (fronteras alineadas) y qué parte de las velas comunes
    tiene OHLC idéntico.
    """
    sa = a.assign(time=pd.to_datetime(a["time"])).set_index("time")
    sb = b.assign(time=pd.to_datetime(b["time"])).set_index("time")
    lo, hi = max(sa.index.min(), sb.index.min()), min(sa.index.max(), sb.index.max())
    sa, sb = sa[(sa.index >= lo) & (sa.index <= hi)], sb[(sb.index >= lo) & (sb.index <= hi)]
    union = sa.index.union(sb.index)
    common = sa.index.intersection(sb.index)
    same = np.ones(len(common), dtype=bool)
    for col in ("open", "high", "low", "close"):
        same &= np.isclose(sa.loc[common, col].to_numpy(dtype=float), sb.loc[common, col].to_numpy(dtype=float),
                           rtol=0.0, atol=tol)
    return {
        "desde": lo, "hasta": hi,
        "velas_a": int(len(sa)), "velas_b": int(len(sb)), "comunes": int(len(common)),
        "pct_fronteras_alineadas": float(len(common) / len(union)) if len(union) else float("nan"),
        "pct_ohlc_identico": float(same.mean()) if len(common) else float("nan"),
    }


def missing_trading_day_runs(bars: pd.DataFrame, reference: pd.DataFrame,
                             start: datetime, end: datetime) -> List[Tuple[date, date, int]]:
    """
    F2b.3: tramos de días hábiles de referencia (fechas GT con al menos una vela en
    `reference`, el 1H del banco) sin ninguna vela en `bars`, entre start y end.
    Devuelve (primer día, último día, cantidad) de cada tramo.
    """
    ref_days = sorted({t.date() for t in pd.to_datetime(reference["time"])
                       if start.date() <= t.date() <= end.date()})
    have = {t.date() for t in pd.to_datetime(bars["time"])}
    runs: List[Tuple[date, date, int]] = []
    current: List[date] = []
    for d in ref_days:
        if d in have:
            if current:
                runs.append((current[0], current[-1], len(current)))
                current = []
        else:
            current.append(d)
    if current:
        runs.append((current[0], current[-1], len(current)))
    return runs


# ---------------------------------------------------------------------------
# Cobertura (F2)
# ---------------------------------------------------------------------------

def first_time_with_closed(df: pd.DataFrame, timeframe: str, n: int = MIN_BARS_PER_TF) -> Optional[pd.Timestamp]:
    """Primer instante con n velas cerradas: cierre de la n-ésima vela."""
    if len(df) < n:
        return None
    return pd.Timestamp(df["time"].iloc[n - 1]) + pd.Timedelta(minutes=V2_TIMEFRAME_MINUTES[timeframe])


def model_coverage(provider: BankProvider, model: ModelSpec, anchor: datetime,
                   min_bars: int = MIN_BARS_PER_TF) -> Tuple[bool, List[str]]:
    """(cobertura completa, TF que no llegan a min_bars velas cerradas en el ancla)."""
    missing = [tf for tf in model.timeframes if provider.n_closed(tf, anchor) < min_bars]
    return (not missing), missing


# ---------------------------------------------------------------------------
# Primer toque S1 / S4 (pre-registro §4; docs/criterios-de-acierto.md)
# ---------------------------------------------------------------------------

S1_RESOLVED, S1_AMBIGUOUS, S1_OPEN, S1_PENDING = "resolved", "ambiguous", "open", "pending"


@dataclass
class FirstTouch:
    state: str
    direction: Optional[int] = None            # +1: tocó primero el nivel de arriba; -1: el de abajo
    level: Optional[str] = None                # "validation" / "invalidation"
    touch_time: Optional[pd.Timestamp] = None  # apertura de la vela que tocó
    touch_tf: Optional[str] = None
    hours: Optional[float] = None              # del ancla al toque
    path_end: Optional[pd.Timestamp] = None    # hasta dónde se recorrió

    @property
    def within_s4(self) -> bool:
        return (self.state == S1_RESOLVED and self.hours is not None
                and self.hours <= S4_WINDOW.total_seconds() / 3600)


def first_touch_s1(provider: BankProvider, anchor: datetime, evp: float, si: float,
                   timeframes: Sequence[str] = S1_TIMEFRAMES,
                   horizon_bars: int = MAX_HORIZON_1H_BARS, horizon_tf: str = "1H") -> FirstTouch:
    """
    Qué nivel toca primero el precio desde el ancla, recorriendo velas que abren en
    el ancla o después. En cada instante usa la TF más fina de `timeframes` que lo
    cubre (de la primera vela de su archivo al cierre de la última), y cambia de TF
    solo en el borde de la vela más gruesa: sin huecos ni superposición.

      resolved   tocó un solo nivel primero
      ambiguous  una misma vela tocó los dos, en la TF más fina disponible ahí
      open       el horizonte (horizon_bars velas de horizon_tf desde el ancla) terminó sin toque
      pending    las velas se acaban antes del horizonte, sin toque
    """
    upper, lower = max(evp, si), min(evp, si)
    start = pd.Timestamp(anchor)
    t = np.datetime64(start, "ns")
    tfs = [tf for tf in timeframes if provider.has_timeframe(tf) and len(provider.frame(tf))]
    arr = {tf: provider.arrays(tf) for tf in tfs}
    dur = {tf: np.timedelta64(V2_TIMEFRAME_MINUTES[tf], "m") for tf in tfs}
    cov = {tf: (arr[tf][0][0], arr[tf][0][-1] + dur[tf]) for tf in tfs}

    limit = None
    if horizon_tf in arr:
        ht = arr[horizon_tf][0]
        i = int(np.searchsorted(ht, t, side="left"))
        if i + horizon_bars <= len(ht):
            limit = ht[i + horizon_bars - 1] + dur[horizon_tf]

    path_end = t
    while True:
        if limit is not None and t >= limit:
            return FirstTouch(S1_OPEN, path_end=pd.Timestamp(limit))
        active = [tf for tf in tfs if cov[tf][0] <= t < cov[tf][1]]
        if not active:
            later = [cov[tf][0] for tf in tfs if cov[tf][0] > t]
            if not later:
                return FirstTouch(S1_PENDING, path_end=pd.Timestamp(path_end))
            t = min(later)
            continue
        tf = active[0]
        times, highs, lows = arr[tf]
        finer_starts = [cov[f][0] for f in tfs[:tfs.index(tf)] if cov[f][0] > t]
        seg_limit = min(finer_starts) if finer_starts else None
        i0 = int(np.searchsorted(times, t, side="left"))
        i1 = len(times) if seg_limit is None else int(np.searchsorted(times, seg_limit, side="left"))
        cut_by_horizon = False
        if limit is not None:
            ih = int(np.searchsorted(times, limit, side="left"))
            if ih < i1:
                i1, cut_by_horizon = ih, True
        if i1 > i0:
            hit_up = highs[i0:i1] >= upper
            hit_lo = lows[i0:i1] <= lower
            hit = hit_up | hit_lo
            if hit.any():
                j = int(np.argmax(hit))
                when = pd.Timestamp(times[i0 + j])
                hours = (when - start).total_seconds() / 3600
                if hit_up[j] and hit_lo[j]:
                    return FirstTouch(S1_AMBIGUOUS, touch_time=when, touch_tf=tf, hours=hours, path_end=when)
                direction = 1 if hit_up[j] else -1
                level = "validation" if (upper if direction == 1 else lower) == evp else "invalidation"
                return FirstTouch(S1_RESOLVED, direction, level, when, tf, hours, when)
            path_end = times[i1 - 1] + dur[tf]
        reached = path_end if i1 > i0 else t
        if cut_by_horizon:
            t = limit
        elif seg_limit is not None:
            t = max(reached, seg_limit)
        else:
            t = max(reached, cov[tf][1])


def overlap_label(anchor_a: datetime, end_a: datetime,
                  others: Sequence[Tuple[str, datetime]]) -> Optional[Tuple[str, datetime]]:
    """Etiqueta Overlap: el primer análisis B con ancla después de la de A y antes de end_a. Nunca excluye."""
    later = [(bid, t) for bid, t in others if pd.Timestamp(anchor_a) < pd.Timestamp(t) < pd.Timestamp(end_a)]
    return min(later, key=lambda x: x[1]) if later else None


# ---------------------------------------------------------------------------
# Predicciones (pre-registro §5)
# ---------------------------------------------------------------------------

def sign_of(value) -> int:
    """+1 / -1 / 0. None y 0 son "no opina", como pred() de §6 del v1."""
    return 0 if not value else (1 if value > 0 else -1)


def snapshots_at(provider: BankProvider, anchor: datetime,
                 timeframes: Sequence[str] = MODEL_TIMEFRAMES) -> Dict[str, Optional[TFIndicatorSnapshot]]:
    return {tf: provider.get_indicator_snapshot(tf, anchor) for tf in timeframes}


def tf_signal(snap: Optional[TFIndicatorSnapshot], ema_chain: Tuple[int, ...], use_di: bool) -> Optional[float]:
    """Lo que aporta una TF antes de ponderar: dirección (EMAs, con filtro DI si aplica) x fuerza ADX."""
    if snap is None:
        return None
    bias = bias_from_emas(snap, ema_chain)
    if bias is None:
        return None
    if use_di:
        bias = confirm_with_di(bias, snap)
        if bias is None:
            return None
    return bias * strength_from_adx(snap.adx14)


@dataclass
class ModelPrediction:
    model: str
    score: Optional[float]
    rescaled: Optional[int]
    pred: int                       # +1 / -1 / 0 (no opina)
    covered: bool                   # False = le falta cobertura en alguna TF
    missing: Tuple[str, ...]
    signals: Dict[str, Optional[float]]
    bars: Dict[str, int]


def predict_model(snapshots: Dict[str, Optional[TFIndicatorSnapshot]], model: ModelSpec,
                  min_bars: int = MIN_BARS_PER_TF) -> ModelPrediction:
    score, incomplete = compute_score_p2_sistematico(snapshots, model)
    rescaled = rescale_p2_sistematico(score)
    bars = {tf: (snapshots[tf].bars_available if snapshots.get(tf) is not None else 0) for tf in model.timeframes}
    return ModelPrediction(
        model=model.name, score=score, rescaled=rescaled, pred=sign_of(rescaled), covered=not incomplete,
        missing=tuple(tf for tf in model.timeframes if bars[tf] < min_bars),
        signals={tf: tf_signal(snapshots.get(tf), model.ema_chain, model.use_di) for tf in model.timeframes},
        bars=bars,
    )


# ---------------------------------------------------------------------------
# Evaluación de una cuenta: alcance + primer toque + Overlap + snapshots
# ---------------------------------------------------------------------------

@dataclass
class EvalRow:
    scoped: ScopedAnalysis
    touch: Optional[FirstTouch] = None
    overlap: Optional[Tuple[str, datetime]] = None
    snapshots: Optional[Dict[str, Optional[TFIndicatorSnapshot]]] = None

    @property
    def in_scope(self) -> bool:
        return self.scoped.status == IN_SCOPE

    @property
    def resolved(self) -> bool:
        return self.touch is not None and self.touch.state == S1_RESOLVED


def evaluate_account(records: Sequence[AnalysisRecord], provider: BankProvider, lead: timedelta = ANCHOR_LEAD,
                     with_snapshots: bool = True,
                     snapshot_timeframes: Sequence[str] = MODEL_TIMEFRAMES) -> List[EvalRow]:
    """Una fila por análisis de la cuenta, en alcance o no, ordenadas como llegan."""
    others = [(r.trade_id, backdated_anchor(r, lead)) for r in records]
    rows: List[EvalRow] = []
    for rec in records:
        sc = scope_analysis(rec, provider, lead)
        if sc.status != IN_SCOPE:
            rows.append(EvalRow(sc))
            continue
        touch = first_touch_s1(provider, sc.anchor, rec.evp, rec.si)
        end = touch.touch_time if touch.touch_time is not None else touch.path_end
        overlap = overlap_label(sc.anchor, end, [(bid, t) for bid, t in others if bid != rec.trade_id])
        snaps = snapshots_at(provider, sc.anchor, snapshot_timeframes) if with_snapshots else None
        rows.append(EvalRow(sc, touch, overlap, snaps))
    return rows


# ---------------------------------------------------------------------------
# Estadística (pre-registro §7 y §8)
# ---------------------------------------------------------------------------

MIN_N = 20
ALPHA = 0.05

LABEL_SUPERA = "supera"
LABEL_EQUIVALENTE = "equivalente"
LABEL_NO_DISTINGUIBLE = "no distinguible"
LABEL_INFERIOR = "inferior"
LABEL_NO_CONCLUYENTE = "no concluyente"


def _mask(n: int, mask: Optional[np.ndarray]) -> np.ndarray:
    return np.ones(n, dtype=bool) if mask is None else np.asarray(mask, dtype=bool)


@dataclass
class PredictorStats:
    hits: int
    bets: int
    total: int      # análisis resueltos del segmento

    @property
    def net(self) -> int:
        return self.hits - (self.bets - self.hits)

    @property
    def accuracy(self) -> Optional[float]:
        return self.hits / self.bets if self.bets else None

    @property
    def ci(self) -> Tuple[float, float]:
        return wilson_interval(self.hits, self.bets)

    @property
    def opina(self) -> Optional[float]:
        return self.bets / self.total if self.total else None

    @property
    def p_binom(self) -> float:
        return binomial_test_two_sided(self.hits, self.bets)

    @property
    def insufficient(self) -> bool:
        return self.bets < MIN_N


def predictor_stats(pred: np.ndarray, truth: np.ndarray, mask: Optional[np.ndarray] = None) -> PredictorStats:
    """R4, sobre análisis ya resueltos: aciertos y opiniones de un predictor en un segmento."""
    m = _mask(len(pred), mask)
    active = (pred != 0) & m
    return PredictorStats(int((active & (pred == truth)).sum()), int(active.sum()), int(m.sum()))


@dataclass
class Paired:
    n: int          # pares: los dos opinan
    n_disc: int     # pares donde las predicciones difieren (= b + c)
    b: int          # acertó X, falló Y
    c: int          # acertó Y, falló X
    net_x: int
    net_y: int
    p: float        # McNemar exacto
    d: float        # diferencia de acierto, X - Y
    ci: Tuple[float, float]


def paired_compare(x: np.ndarray, y: np.ndarray, truth: np.ndarray, mask: Optional[np.ndarray] = None) -> Paired:
    """R1: X contra Y solo donde los dos opinan (los arreglos ya son de análisis resueltos)."""
    m = (x != 0) & (y != 0) & _mask(len(x), mask)
    n = int(m.sum())
    hx, hy = (x == truth)[m], (y == truth)[m]
    b, c = int((hx & ~hy).sum()), int((~hx & hy).sum())
    net_x, net_y = int(hx.sum() - (~hx).sum()), int(hy.sum() - (~hy).sum())
    if n == 0:
        return Paired(0, 0, 0, 0, 0, 0, 1.0, float("nan"), (float("nan"), float("nan")))
    if b + c == 0:
        ci = (0.0, 0.0)
    else:
        lo, hi = wilson_interval(b, b + c)
        scale = (b + c) / n
        ci = (scale * (2 * lo - 1), scale * (2 * hi - 1))
    return Paired(n, b + c, b, c, net_x, net_y, mcnemar_exact(b, c), (b - c) / n, ci)


def pair_counts(x: np.ndarray, y: np.ndarray, mask: Optional[np.ndarray] = None) -> Tuple[int, int]:
    """(pares, pares con predicción distinta) sin usar la verdad: alcanza para la MDD (R8)."""
    m = (x != 0) & (y != 0) & _mask(len(x), mask)
    return int(m.sum()), int((m & (x != y)).sum())


def mdd_predictor(n_bets: int) -> float:
    """R8: brecha mínima detectable contra 50% con n opiniones (5% / 80%)."""
    return min_detectable_gap(n_bets, 0.5)


def mdd_pair(n_pairs: int, n_disc: int) -> Optional[float]:
    """
    R8 para un par: diferencia de acierto mínima detectable. Con n_disc pares donde
    difieren, la brecha detectable en b/(b+c) es min_detectable_gap(n_disc, 0.5); en
    diferencia de acierto, 2 * brecha * n_disc / n_pairs. None si no hay pares que
    difieran o si ni un reparto total (brecha 0.5) sería detectable.
    """
    if n_pairs == 0 or n_disc == 0:
        return None
    gap = min_detectable_gap(n_disc, 0.5)
    if gap > 0.5:
        return None
    return 2 * gap * n_disc / n_pairs


def label_vs_d(pr: Paired, p_family: Optional[float]) -> str:
    """R7: 2X contra D."""
    if pr.n < MIN_N:
        return LABEL_NO_CONCLUYENTE
    if pr.b > pr.c and pr.p < ALPHA and p_family is not None and p_family < ALPHA:
        return LABEL_SUPERA
    if pr.b < pr.c and pr.p < ALPHA:
        return LABEL_INFERIOR
    return LABEL_NO_DISTINGUIBLE


def label_vs_disc(pr: Paired, p_family: Optional[float]) -> str:
    """Pre-registro §8: 2X contra P2-DISC."""
    if pr.n < MIN_N:
        return LABEL_NO_CONCLUYENTE
    if pr.b > pr.c and pr.p < ALPHA and p_family is not None and p_family < ALPHA:
        return LABEL_SUPERA
    if pr.b < pr.c and pr.p < ALPHA:
        return LABEL_INFERIOR
    if pr.ci[0] <= 0 <= pr.ci[1]:
        return LABEL_EQUIVALENTE
    return LABEL_NO_DISTINGUIBLE


def label_single_comparison(pr: Paired) -> str:
    """Q4 (2A contra D-EQ): una comparación fijada de antemano, sin p de familia."""
    if pr.n < MIN_N:
        return LABEL_NO_CONCLUYENTE
    if pr.b > pr.c and pr.p < ALPHA:
        return LABEL_SUPERA
    if pr.b < pr.c and pr.p < ALPHA:
        return LABEL_INFERIOR
    return LABEL_NO_DISTINGUIBLE


def family_permutation(pred_by_model: Dict[str, np.ndarray], truth: np.ndarray, n_perm: int,
                       seed: int) -> Tuple[Dict[str, int], Dict[str, float], np.ndarray]:
    """
    8.1: permutation_max_net_test sobre la familia y, con el mismo nulo, el p de cada
    modelo ("single-step max-T"): fracción de mezclas cuyo mejor neto de la familia
    iguala o supera al neto de ese modelo. Para el mejor de la familia es el p de la
    función. Devuelve (neto por modelo, p ajustado por modelo, nulo del máximo).
    """
    names = list(pred_by_model)
    matrix = np.vstack([pred_by_model[k] for k in names])
    _, _, null = permutation_max_net_test(matrix, truth, n_perm=n_perm, seed=seed)
    nets = net_score(matrix, truth)
    p_adj = {k: (int(np.sum(null >= nets[i])) + 1) / (n_perm + 1) for i, k in enumerate(names)}
    return {k: int(nets[i]) for i, k in enumerate(names)}, p_adj, null


def best_of(pred_by_model: Dict[str, np.ndarray], truth: np.ndarray, names: Sequence[str] = NAMES_2X) -> str:
    """Mejor 2X: mayor neto; empate, más aciertos; si sigue, el primero en orden alfabético."""
    def key(name: str):
        st = predictor_stats(pred_by_model[name], truth)
        return (-st.net, -st.hits, name)
    return min(names, key=key)


# ---------------------------------------------------------------------------
# 2F: grilla de pesos y selección fuera de muestra (pre-registro §10)
# ---------------------------------------------------------------------------

GRID_STEP = 0.05
WF_BLOCK = 10


def weight_grid(step: float = GRID_STEP, k: int = len(TIMEFRAMES_2X)) -> np.ndarray:
    return simplex_grid(step=step, k=k)


def contribution_matrix(snapshot_list: Sequence[Dict[str, Optional[TFIndicatorSnapshot]]],
                        timeframes: Sequence[str] = TIMEFRAMES_2X,
                        ema_chain: Tuple[int, ...] = MODEL_D.ema_chain, use_di: bool = MODEL_D.use_di,
                        min_bars: int = MIN_BARS_PER_TF) -> Tuple[np.ndarray, np.ndarray]:
    """
    (aportes por TF, cobertura) para una lista de snapshots: lo que cada TF aporta
    antes de ponderar, con las reglas de D. Un análisis sin cobertura completa en las
    5 TF queda con aportes 0 y cobertura False, tenga el peso que tenga cada TF.
    """
    n, k = len(snapshot_list), len(timeframes)
    contrib = np.zeros((n, k))
    covered = np.zeros(n, dtype=bool)
    for i, snaps in enumerate(snapshot_list):
        values = []
        for tf in timeframes:
            snap = snaps.get(tf)
            sig = None if snap is None or snap.bars_available < min_bars else tf_signal(snap, ema_chain, use_di)
            values.append(sig)
        if all(v is not None for v in values):
            contrib[i] = values
            covered[i] = True
    return contrib, covered


def grid_predictions(contrib: np.ndarray, covered: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """
    Predicción (+1/-1/0) de cada combinación de la grilla en cada análisis: matriz
    (análisis x combinaciones). Misma aritmética que compute_score_p2_sistematico +
    rescale_p2_sistematico: suma TF por TF en el orden del modelo, y opina si
    |2 * score| >= 0.5 (redondeo con los empates lejos de cero).
    """
    total = np.zeros((contrib.shape[0], grid.shape[0]))
    for j in range(grid.shape[1]):
        total = total + contrib[:, j][:, None] * grid[:, j][None, :]
    pred = np.where(np.abs(total * 2) >= 0.5, np.sign(total), 0.0).astype(np.int8)
    pred[~covered] = 0
    return pred


def grid_neighbors(weights: Sequence[float], step: float = GRID_STEP) -> List[Tuple[float, ...]]:
    """Vecinos en la grilla a distancia L1 = 2 * step: una TF sube un paso y otra baja uno, sin pesos negativos."""
    units_total = round(1 / step)
    units = [round(w / step) for w in weights]
    out = []
    for up in range(len(units)):
        for down in range(len(units)):
            if up == down or units[down] < 1:
                continue
            u = list(units)
            u[up] += 1
            u[down] -= 1
            out.append(tuple(x / units_total for x in u))
    return out


def grid_index(grid: np.ndarray, weights: Sequence[float]) -> int:
    hits = np.where(np.all(np.isclose(grid, np.asarray(weights, dtype=float)), axis=1))[0]
    if hits.size == 0:
        raise ValueError(f"{tuple(weights)} no está en la grilla")
    return int(hits[0])


def mid_rank_percentile(values: np.ndarray, x: float) -> float:
    """Percentil de x entre values, como rango medio: 100 * (#menores + 0.5 * #iguales) / n."""
    values = np.asarray(values)
    return 100.0 * (float((values < x).sum()) + 0.5 * float((values == x).sum())) / len(values)


@dataclass
class TwoF:
    grid: np.ndarray
    in_sample_net: np.ndarray      # neto dentro de muestra de cada combinación
    final_idx: int
    final_weights: Tuple[float, ...]
    folds: List[Dict[str, object]]
    wf_pred: np.ndarray            # predicción fuera de muestra (0 fuera de los folds de prueba)
    wf_mask: np.ndarray            # True en los análisis de prueba del walk-forward
    loo_pred: np.ndarray
    loo_idx: np.ndarray


def wf_folds(n: int, block: int = WF_BLOCK) -> List[Tuple[range, range]]:
    return walk_forward_folds(n, initial=max(20, n // 2), block=block)


def run_2f(pred_grid: np.ndarray, truth: np.ndarray, grid: np.ndarray, prior: Sequence[float],
           block: int = WF_BLOCK) -> TwoF:
    """
    R6. pred_grid: (análisis x combinaciones), con los análisis ordenados por ancla.
    El resultado de 2F es wf_pred (cada análisis de prueba, con los pesos elegidos
    solo con análisis anteriores), nunca el máximo dentro de muestra.
    """
    n = len(truth)
    C = pred_grid.astype(np.int16) * truth.astype(np.int16)[:, None]
    total = C.sum(axis=0, dtype=np.int64)
    final = select_best(total, grid, prior)

    wf_pred = np.zeros(n, dtype=int)
    wf_mask = np.zeros(n, dtype=bool)
    folds: List[Dict[str, object]] = []
    for train, test in wf_folds(n, block):
        idx = select_best(C[train.start:train.stop].sum(axis=0, dtype=np.int64), grid, prior)
        wf_pred[test.start:test.stop] = pred_grid[test.start:test.stop, idx]
        wf_mask[test.start:test.stop] = True
        folds.append({"train": (train.start, train.stop), "test": (test.start, test.stop), "idx": idx,
                      "weights": tuple(float(w) for w in grid[idx])})

    loo_pred = np.zeros(n, dtype=int)
    loo_idx = np.zeros(n, dtype=int)
    for i in range(n):
        idx = select_best(total - C[i], grid, prior)
        loo_pred[i] = pred_grid[i, idx]
        loo_idx[i] = idx

    return TwoF(grid, total, final, tuple(float(w) for w in grid[final]), folds, wf_pred, wf_mask, loo_pred, loo_idx)


def _prior_penalty(grid: np.ndarray, prior: Sequence[float]) -> np.ndarray:
    """El término de desempate de select_best: 1e-6 por la distancia L1 de cada combinación al prior."""
    return 1e-6 * np.abs(grid - np.asarray(prior)).sum(axis=1)


def _select_best_fast(train_net: np.ndarray, penalty: np.ndarray) -> int:
    """select_best (tools/edge_evaluation.py:362-369) con la distancia al prior ya calculada."""
    return int(np.argmax(train_net - penalty))


def permutation_2f(pred_grid: np.ndarray, truth: np.ndarray, grid: np.ndarray, prior: Sequence[float],
                   masks: Dict[str, np.ndarray], n_perm: int, seed: int,
                   block: int = WF_BLOCK) -> Dict[str, Tuple[int, float]]:
    """
    Permutación del procedimiento entero de 2F: se mezcla la verdad de todos los
    análisis, se repite la selección de cada fold y se mide el neto fuera de muestra.
    masks: conjuntos de análisis donde medirlo (p. ej. Todos, Direccionales, Choppy).
    Devuelve, por conjunto, (neto fuera de muestra observado, p).
    """
    n = len(truth)
    folds = wf_folds(n, block)
    if not folds:
        return {k: (0, 1.0) for k in masks}
    P = pred_grid.astype(np.int16)
    bool_masks = {k: np.asarray(m, dtype=bool) for k, m in masks.items()}
    # select_best recalcula la distancia al prior en cada llamada, y acá se la llama
    # decenas de miles de veces: se calcula una sola vez, con su misma fórmula
    # (tests/test_p2_bank_v2.py comprueba que elige exactamente lo mismo).
    penalty = _prior_penalty(grid, prior)

    def oos_contributions(t: np.ndarray) -> np.ndarray:
        C = P * t.astype(np.int16)[:, None]
        run = C[:folds[0][0].stop].sum(axis=0, dtype=np.int64)
        oos = np.zeros(n, dtype=np.int64)
        for _, test in folds:
            idx = _select_best_fast(run, penalty)
            oos[test.start:test.stop] = C[test.start:test.stop, idx]
            run = run + C[test.start:test.stop].sum(axis=0, dtype=np.int64)
        return oos

    observed = oos_contributions(truth)
    obs = {k: int(observed[m].sum()) for k, m in bool_masks.items()}
    rng = np.random.default_rng(seed)
    exceed = {k: 0 for k in masks}
    for _ in range(n_perm):
        oos = oos_contributions(rng.permutation(truth))
        for k, m in bool_masks.items():
            if oos[m].sum() >= obs[k]:
                exceed[k] += 1
    return {k: (obs[k], (exceed[k] + 1) / (n_perm + 1)) for k in masks}


@dataclass
class KnownOutcomes2F:
    """Control del walk-forward de 2F usando solo resultados ya conocidos en cada ancla."""
    pred: np.ndarray          # predicción fuera de muestra (0 fuera de los folds de prueba)
    mask: np.ndarray          # análisis de prueba
    tests_with_unknown: int   # análisis de prueba que, en el walk-forward normal, usaban algún resultado aún no conocido
    unknown_uses: int         # cuántos resultados aún no conocidos usaba en total
    net: int                  # neto fuera de muestra
    p: Optional[float]        # permutación del procedimiento (None si n_perm = 0)


def run_2f_known_outcomes(pred_grid: np.ndarray, truth: np.ndarray, grid: np.ndarray, prior: Sequence[float],
                          anchors: Sequence[datetime], touch_times: Sequence[datetime], n_perm: int = 0,
                          seed: int = 0, block: int = WF_BLOCK) -> KnownOutcomes2F:
    """
    El walk-forward de run_2f elige los pesos con los análisis ANTERIORES por fecha de
    ancla. Pero el resultado de un análisis se conoce recién en su primer toque, que puede
    llegar después del ancla del análisis que se predice: esos resultados todavía no se
    sabían. Este control repite el procedimiento dejando fuera del entrenamiento de cada
    análisis de prueba los resultados con touch_time > su ancla. Con n_perm > 0 agrega el
    p de permutación del procedimiento entero, como permutation_2f.
    """
    n = len(truth)
    folds = wf_folds(n, block)
    mask = np.zeros(n, dtype=bool)
    anchors64 = np.array([np.datetime64(pd.Timestamp(a)) for a in anchors])
    touches64 = np.array([np.datetime64(pd.Timestamp(t)) for t in touch_times])
    unknown: Dict[int, np.ndarray] = {}     # análisis de prueba -> índices de entrenamiento aún sin resultado
    for train, test in folds:
        mask[test.start:test.stop] = True
        for i in test:
            late = np.where(touches64[train.start:train.stop] > anchors64[i])[0]
            if late.size:
                unknown[i] = late
    P = pred_grid.astype(np.int16)
    penalty = _prior_penalty(grid, prior)

    def procedure(t: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        C = P * t.astype(np.int16)[:, None]
        oos = np.zeros(n, dtype=np.int64)
        chosen = np.zeros(n, dtype=int)
        run = C[:folds[0][0].stop].sum(axis=0, dtype=np.int64) if folds else None
        for _, test in folds:
            fold_idx = _select_best_fast(run, penalty)
            for i in test:
                idx = fold_idx if i not in unknown else _select_best_fast(
                    run - C[unknown[i]].sum(axis=0, dtype=np.int64), penalty)
                oos[i], chosen[i] = C[i, idx], idx
            run = run + C[test.start:test.stop].sum(axis=0, dtype=np.int64)
        return oos, chosen

    observed, chosen = procedure(truth)
    pred = np.where(mask, pred_grid[np.arange(n), chosen], 0).astype(int)
    net = int(observed.sum())
    p = None
    if n_perm and folds:
        rng = np.random.default_rng(seed)
        exceed = sum(int(procedure(rng.permutation(truth))[0].sum() >= net) for _ in range(n_perm))
        p = (exceed + 1) / (n_perm + 1)
    return KnownOutcomes2F(pred, mask, len(unknown), int(sum(v.size for v in unknown.values())), net, p)


# ---------------------------------------------------------------------------
# Momentos al azar (8.7)
# ---------------------------------------------------------------------------

def random_moments(provider: BankProvider, first_anchor: datetime, last_anchor: datetime, band: float,
                   models: Sequence[ModelSpec], seed: int, n: int = 400,
                   hours: Optional[Tuple[int, int]] = None, candidate_tf: str = "1H") -> pd.DataFrame:
    """
    Réplica de §8.7 del v1: momentos = apertura de una vela 1H + 30 min, sorteados sin
    reposición entre las velas que abren entre el primer y el último ancla (con `hours`,
    solo las que abren entre esas dos horas GT, incluidas). Niveles simétricos a ±band
    del precio de partida. Una fila por momento: estado S1, verdad y predicción de cada modelo.
    """
    frame = provider.frame(candidate_tf)
    cand = frame[(frame["time"] >= pd.Timestamp(first_anchor)) & (frame["time"] <= pd.Timestamp(last_anchor))]
    if hours is not None:
        cand = cand[cand["time"].dt.hour.between(hours[0], hours[1])]
    rng = np.random.default_rng(seed)
    chosen = rng.choice(len(cand), size=min(n, len(cand)), replace=False)
    needed = sorted({tf for m in models for tf in m.timeframes})
    recs = []
    for moment in sorted(cand["time"].iloc[chosen] + pd.Timedelta(minutes=30)):
        a = moment.to_pydatetime()
        p0, _ = starting_price(provider, a)
        if p0 is None:
            recs.append({"momento": moment, "estado": EXCL_NO_HISTORY})
            continue
        ft = first_touch_s1(provider, a, p0 + band, p0 - band)
        rec = {"momento": moment, "estado": ft.state, "verdad": ft.direction}
        if ft.state == S1_RESOLVED:
            snaps = snapshots_at(provider, a, needed)
            for m in models:
                rec[m.name] = predict_model(snaps, m).pred
        recs.append(rec)
    return pd.DataFrame(recs)


def two_proportions_p(k1: int, n1: int, k2: int, n2: int) -> float:
    """Dos proporciones, dos colas, aproximación normal (la misma de §8.7 del v1)."""
    if not n1 or not n2:
        return float("nan")
    pool = (k1 + k2) / (n1 + n2)
    if pool in (0.0, 1.0):
        return 1.0
    z = (k1 / n1 - k2 / n2) / math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2))
    return math.erfc(abs(z) / math.sqrt(2))


# ---------------------------------------------------------------------------
# Fuente del 2H (pre-registro §6), velas repetidas (§2) y parada por cobertura (§12)
# ---------------------------------------------------------------------------

BANK_TIMEFRAMES: Tuple[str, ...] = ("1W", "1D", "12H", "4H", "1H", "30M", "15M", "5M", "1M")
H2_NATIVE = "nativo (F2b)"
H2_RESAMPLED = "remuestreado desde 1H (F2c)"
F2D_THRESHOLD = 0.99
H2_STOP_THRESHOLD = 0.25


class StopCondition(RuntimeError):
    """Una de las paradas del pre-registro §12: no se sigue calculando."""


@dataclass
class H2Decision:
    source: str
    clock: Optional[ClockCalibration]          # F2b.1
    closed_at_first: Optional[int]             # F2b.2: velas 2H cerradas en el primer ancla que D mide
    first_d_anchor: Optional[datetime]
    gap_runs: List[Tuple[date, date, int]]     # F2b.3: tramos de días hábiles sin ninguna vela 2H
    f2d: Optional[Dict[str, float]]            # nativo contra remuestreado
    control_4h: Dict[str, float]               # 2H elegido agregado a 4H, contra el 4H del banco
    reasons: List[str]                         # por qué no se usó el nativo (vacío si se usó)


def decide_h2_source(provider: BankProvider, native: Optional[pd.DataFrame], resampled: pd.DataFrame,
                     entries: Sequence[Tuple[datetime, float]], first_d_anchor: Optional[datetime],
                     first_anchor: datetime, last_anchor: datetime, dst_rule: str) -> H2Decision:
    """
    F2b-F2d para un activo. `provider` ya tiene el banco sin velas repetidas y todavía no
    tiene 2H. Usa el nativo solo si valida el reloj, tiene 800 velas cerradas antes del
    primer análisis que D mide y no le falta más de 1 día hábil seguido. Levanta
    StopCondition si F2d o el control 2H -> 4H quedan por debajo del 99%.
    """
    reasons: List[str] = []
    clock = closed = f2d = None
    runs: List[Tuple[date, date, int]] = []
    if native is None or native.empty:
        reasons.append("no hay export nativo")
    else:
        probe = BankProvider(provider.base_dir, {"2H": native})
        clock = clock_check_tf(entries, probe, "2H")
        if clock.aligned is not True:
            reasons.append("F2b.1: el reloj del 2H nativo no valida")
        closed = probe.n_closed("2H", first_d_anchor) if first_d_anchor is not None else 0
        if closed < MIN_BARS_PER_TF:
            reasons.append(f"F2b.2: {closed} velas 2H cerradas antes del primer análisis que D mide (hacen falta {MIN_BARS_PER_TF})")
        runs = [r for r in missing_trading_day_runs(native, provider.frame("1H"), first_anchor, last_anchor) if r[2] >= 2]
        if runs:
            reasons.append(f"F2b.3: faltan {max(r[2] for r in runs)} días hábiles seguidos")
        f2d = compare_series(native, resampled)
        if min(f2d["pct_fronteras_alineadas"], f2d["pct_ohlc_identico"]) < F2D_THRESHOLD:
            raise StopCondition(f"F2d: el 2H nativo y el remuestreado coinciden menos del 99%: {f2d}")
    chosen = resampled if reasons else native
    control = compare_series(aggregate_server_aligned(chosen, 4, dst_rule), provider.frame("4H"))
    if min(control["pct_fronteras_alineadas"], control["pct_ohlc_identico"]) < F2D_THRESHOLD:
        raise StopCondition(f"Control 2H -> 4H por debajo del 99%: {control}")
    return H2Decision(H2_RESAMPLED if reasons else H2_NATIVE, clock, closed, first_d_anchor, runs, f2d, control, reasons)


def prepare_provider(provider: BankProvider, records: Sequence[AnalysisRecord],
                     entries: Sequence[Tuple[datetime, float]], native_2h: Optional[pd.DataFrame],
                     dst_rule: str, lead: timedelta = ANCHOR_LEAD) -> Tuple[Dict[str, Dict[str, int]], H2Decision]:
    """
    Deja listo un proveedor que ya tiene cargadas las velas del banco (en memoria, sin
    tocar ningún archivo):
      1. quita las velas repetidas de 4H, 12H, 1D y 1W (pre-registro v1.1);
      2. elige la fuente del 2H con F2b (nativo si cumple las tres condiciones; si no,
         remuestreado desde 1H) y lo agrega al proveedor.
    Devuelve (velas repetidas por TF, decisión del 2H).
    """
    dedup: Dict[str, Dict[str, int]] = {}
    for tf in DUPLICATE_PRONE_TIMEFRAMES:
        if not provider.has_timeframe(tf):
            continue
        before = len(provider.frame(tf))
        clean, stats = drop_relabeled_duplicates(provider.frame(tf), tf, dst_rule)
        provider.add_frame(tf, clean)
        dedup[tf] = {**stats, "antes": before, "después": len(clean)}

    in_scope = sorted((x for x in (scope_analysis(r, provider, lead) for r in records) if x.status == IN_SCOPE),
                      key=lambda x: x.anchor)
    if not in_scope:
        raise StopCondition("ningún análisis en alcance")
    d_measured = [x for x in in_scope if model_coverage(provider, MODEL_D, x.anchor)[0]]
    resampled = resample_1h_to_2h(provider.frame("1H"), dst_rule)[OHLC_COLUMNS]
    h2 = decide_h2_source(provider, native_2h, resampled, entries, d_measured[0].anchor if d_measured else None,
                          in_scope[0].anchor, in_scope[-1].anchor, dst_rule)
    provider.add_frame("2H", native_2h if h2.source == H2_NATIVE else resampled)
    return dedup, h2


def h2_loss_share(providers: Dict[str, BankProvider],
                  rows_by_account: Dict[str, Sequence[EvalRow]]) -> Tuple[int, int]:
    """Parada del pre-registro §12: (análisis que D mide sin 800 velas 2H cerradas, análisis que D mide)."""
    lost = measured = 0
    for name, provider in providers.items():
        for row in rows_by_account[name]:
            if not row.in_scope or not model_coverage(provider, MODEL_D, row.scoped.anchor)[0]:
                continue
            measured += 1
            lost += provider.n_closed("2H", row.scoped.anchor) < MIN_BARS_PER_TF
    return lost, measured
