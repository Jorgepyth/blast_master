"""
tools/p0_sistematico.py — evaluación de P0 sistemático (fractales + Fibonacci): foto del banco, registro de modelos,
puntaje, ensamblaje por análisis con exclusiones, estudio de niveles y estadística sin dependencias nuevas.

Pre-registro: `jupyter/p0_sistematico/preregistro.md` v1.1. Protocolo: `jupyter/p0_sistematico/00_protocolo.md`.

Reglas que este módulo hace cumplir:
- Solo lectura (INV-1): DBs con `open_readonly_session` (`mode=ro`) y columnas explícitas; nunca `init_db()`; nada se
  escribe en `.data/`.
- Foto fija (R5, E3): las velas se leen de la copia en `.tmp/`, y cada archivo se verifica contra
  `foto_manifest.json` antes de usarlo.
- Sin look-ahead (INV-3): en τ solo existen las velas con `time + duración <= τ`.
- Confirmatorio cerrado (INV-6): las pruebas de niveles no devuelven el 40% confirmatorio sin `allow_confirmatory=True`.
- Toda exclusión lleva motivo (INV-7), con `ExclusionRecord` de `tools/p2_backtest.py`.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from bisect import bisect_right
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import text

from cli.main import determine_market_bias
from config.auto_resolution import RECOVERED_BACKDATED, S4_WINDOW_H, TOUCH_EXCEPTIONS
from core.candle_resolution import (
    LADDER,
    OUTCOME_CONFIRMED,
    OUTCOME_INVALIDATED,
    OUTCOME_OPEN,
    Candle,
    resolve_analysis,
    start_price,
)
from core.outcome_metrics import AnalysisOutcome, analysis_anchor, counted_outcomes
from core.p0_fib import (
    AMBIGUOUS,
    BARRIER_FRACTION,
    COARSE_TFS,
    EXTREME_P0,
    EXTREME_PLACEBO,
    OPEN,
    P0_LEVELS,
    PENDING,
    PLACEBO_LEVELS,
    PLACEBO_OF,
    REJECTION,
    RETRACEMENT_P0,
    RETRACEMENT_PLACEBO,
    RUPTURE,
    TF_MINUTES,
    ZERO_LEVEL,
    ZONE_DELTA,
    LevelEvent,
    Path,
    build_event_path,
    detect_events,
    ladder_path,
)
from core.p0_structure import (
    BEARISH,
    BULLISH,
    INSUFFICIENT,
    RANGE,
    TREND_STATES,
    Bars,
    Leg,
    leg_intervals,
    state_at,
    structure_timeline,
)
from core.p2_ground_truth import infer_thesis_direction
from tools.auto_resolution import AccountResolver, read_analysis_rows
from tools.p2_backtest import ExclusionRecord, clamp, open_readonly_session, round_half_away_from_zero

# ---------------------------------------------------------------------------
# Configuración del pre-registro (§15)
# ---------------------------------------------------------------------------

SEED = 20261006
N_PERM = 10_000
N_BOOT = 5_000
SPLIT_EXPLORATORY = 0.60
N_MIN_ARM = 480
N_MIN_PAIRS = 20
ALPHA = 0.05
FRACTAL_N = 2

SYMBOLS = {"XAUUSD": "flight_account_001_xauusd.db", "BTCUSD": "flight_account_002_btcusdtp.db"}
ACCOUNT_IDS = {"XAUUSD": "000", "BTCUSD": "002"}
STRUCTURE_TFS = ("1W", "1D", "12H", "4H", "1H")
LEVEL_STUDY_TFS = ("1D", "12H", "4H", "1H")
ALL_TFS = ("1W", "1D", "12H", "4H", "1H", "30M", "15M", "5M", "1M")

# Ventanas ambiguas de la regla horaria (T0).
DST_WINDOWS = ((datetime(2026, 3, 8), datetime(2026, 3, 30)), (datetime(2026, 10, 25), datetime(2026, 11, 2)))

# Huecos explicados del 1H (`00_protocolo.md` §5.3): feriados de EE.UU. y fin de año. Los demás son anómalos (E4).
EXPLAINED_GAP_DATES = frozenset({"2026-05-25", "2026-09-07", "2025-12-24", "2025-12-25", "2025-12-31", "2026-01-01",
                                 "2024-12-24", "2024-12-25", "2024-12-31", "2025-01-01"})

# Motivos de exclusión (INV-7).
REASON_BACKDATED = "backdated"
REASON_INDEPENDENT = "independent_evidence"
REASON_NO_HISTORY = "no_history"
REASON_NO_LEVELS = "no_levels"

# Situaciones de una TF en τ (D8).
SIT_INSUFFICIENT = "insufficient_history"
SIT_RANGE = "range"
SIT_NO_EVENT = "trend_no_event"
SIT_RETR_REJECTION = "trend_retracement_or_1_rejection"
SIT_RETR_RUPTURE = "trend_retracement_or_1_rupture"
SIT_ZERO_REJECTION = "trend_0_rejection"
SIT_ZERO_RUPTURE = "trend_0_rupture"


# ---------------------------------------------------------------------------
# Foto del banco (R5, E3)
# ---------------------------------------------------------------------------

class SnapshotChanged(RuntimeError):
    """E3: un archivo de la foto no coincide con el manifiesto."""


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_snapshot(foto_dir: str, manifest_path: str) -> Dict[str, str]:
    """Verifica cada archivo de la foto contra el manifiesto. Devuelve {ruta relativa: sha256}; si no, E3."""
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    found = {}
    for rel, entry in manifest["files"].items():
        digest = sha256_file(os.path.join(foto_dir, rel))
        if digest != entry["sha256"]:
            raise SnapshotChanged(f"E3: {rel} changed: {digest} != {entry['sha256']}")
        found[rel] = digest
    return found


def read_candles(path: str) -> List[Candle]:
    df = pd.read_csv(path, parse_dates=["time"]).sort_values("time")
    return [Candle(t.to_pydatetime(), float(o), float(h), float(lo), float(c))
            for t, o, h, lo, c in zip(df.time, df.open, df.high, df.low, df.close)]


def candles_until(candles: Mapping[str, Sequence[Candle]], tau: datetime) -> Dict[str, List[Candle]]:
    """Solo las velas cerradas en τ, en todas las TF (INV-3). Para el chequeo T2."""
    out = {}
    for tf, bars in candles.items():
        dur = timedelta(minutes=TF_MINUTES[tf])
        out[tf] = [bar for bar in bars if bar.time + dur <= tau]
    return out


def bars_of(candles: Sequence[Candle], tf: str) -> Bars:
    return Bars.from_lists([c.time for c in candles], [c.high for c in candles], [c.low for c in candles],
                           [c.close for c in candles], timedelta(minutes=TF_MINUTES[tf]))


@dataclass
class Market:
    """Velas de un símbolo y lo que se deriva de ellas sin ninguna verdad: series por TF y caminos de eventos."""
    symbol: str
    candles: Dict[str, List[Candle]]
    n: int = FRACTAL_N
    wick_invalidation: bool = False
    zone_mode: str = "delta"  # "delta" (δ·|B−A|) o "atr" (sensibilidad T11)
    # Distancia de la barrera de ruptura en fracción del tramo. 0.10 = la del pre-registro (igual que la de rechazo);
    # otro valor aleja solo la ruptura (exploratorio, 2026-10-08).
    rupture_fraction: float = BARRIER_FRACTION
    bars: Dict[str, Bars] = field(default_factory=dict)
    paths: Dict[str, Path] = field(default_factory=dict)
    timelines: Dict[str, list] = field(default_factory=dict)
    _atr_hw: Dict[str, Dict[tuple, float]] = field(default_factory=dict)

    def rupture_distance(self, leg: Leg) -> Optional[float]:
        """None con la regla del pre-registro (ruptura = rechazo = 0.10·|B−A|); si no, rupture_fraction·|B−A|."""
        return None if self.rupture_fraction == BARRIER_FRACTION else self.rupture_fraction * leg.size

    def half_width(self, tf: str, leg: Leg, levels: Sequence[float]) -> Optional[Dict[float, float]]:
        """Semiancho de zona por nivel: None con δ·|B−A|; con "atr", c·ATR14 al confirmarse B (T11). La constante c
        se calibra con todos los tramos de la TF en la foto: es una escala, no un resultado."""
        if self.zone_mode == "delta":
            return None
        if tf not in self._atr_hw:
            legs = [info["leg"] for info in leg_intervals(self.timelines[tf]).values()]
            self._atr_hw[tf], _ = atr_half_widths(self, tf, legs)
        hw = self._atr_hw[tf].get(leg.key, np.nan)
        return {r: hw for r in levels}

    def __post_init__(self):
        self.bars = {tf: bars_of(self.candles[tf], tf) for tf in STRUCTURE_TFS if self.candles.get(tf)}
        ladder = ladder_path(self.candles)
        self.paths = {tf: build_event_path(self.candles, tf, ladder=ladder) for tf in STRUCTURE_TFS
                      if self.candles.get(tf)}
        self.timelines = {tf: structure_timeline(b, n=self.n, tf=tf, wick_invalidation=self.wick_invalidation)
                          for tf, b in self.bars.items()}

    def close_at(self, tf: str, instant: datetime) -> float:
        """El cierre de la vela de la TF que cerró exactamente en `instant` (o la última cerrada antes)."""
        b = self.bars[tf]
        k = bisect_right(b.times, instant - b.duration) - 1
        return float(b.close[k])


def load_market(foto_dir: str, symbol: str, n: int = FRACTAL_N, wick_invalidation: bool = False,
                candles: Optional[Mapping[str, List[Candle]]] = None, zone_mode: str = "delta",
                rupture_fraction: float = BARRIER_FRACTION) -> Market:
    if candles is None:
        candles = {tf: read_candles(os.path.join(foto_dir, symbol, f"{tf}.csv")) for tf in ALL_TFS}
    return Market(symbol, dict(candles), n=n, wick_invalidation=wick_invalidation, zone_mode=zone_mode,
                  rupture_fraction=rupture_fraction)


# ---------------------------------------------------------------------------
# Registro de modelos (D9, D10; INV-5)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class P0ModelSpec:
    """Receta de un modelo de P0 sistemático. Mismos campos comunes que `ModelSpec` de P2, más los de P0."""
    name: str
    label: str
    timeframes: Tuple[str, ...]
    weights: Dict[str, float]
    uses_fib: bool
    hierarchical: bool
    levels: str  # "p0" o "placebo"
    description: str

    @property
    def s_max(self) -> int:
        return 2 if self.uses_fib else 1


_TF4 = ("1D", "12H", "4H", "1H")


def _spec(name, label, weights, uses_fib=True, hierarchical=False, levels="p0", description="",
          timeframes=_TF4) -> P0ModelSpec:
    return P0ModelSpec(name, label, timeframes, dict(zip(timeframes, weights)), uses_fib, hierarchical, levels,
                       description)


P0_MODEL_A = _spec("A", "A — solo estructura (control)", (0.25, 0.25, 0.25, 0.25), uses_fib=False,
                   description="¿La estructura sola predice?")
P0_MODEL_B = _spec("B", "B — estructura + Fib", (0.25, 0.25, 0.25, 0.25), description="B contra A = aporte de Fib")
P0_MODEL_C = _spec("C", "C — pico en 1D", (0.40, 0.30, 0.20, 0.10))
P0_MODEL_D = _spec("D", "D — pico en 12H y 4H", (0.20, 0.30, 0.30, 0.20))
P0_MODEL_E = _spec("E", "E — pico en 1H", (0.10, 0.20, 0.30, 0.40))
P0_MODEL_F = _spec("F", "F — jerárquico", (0.30, 0.30, 0.20, 0.20), hierarchical=True,
                   description="Lectura de arriba hacia abajo")
P0_MODEL_G = _spec("G", "G — pesos del operador", (0.20, 0.20, 0.30, 0.30))
P0_MODEL_H = _spec("H", "H — con 1W (experimental)", (0.20, 0.20, 0.20, 0.20, 0.20),
                   timeframes=("1W", "1D", "12H", "4H", "1H"), description="H contra B = aporte de 1W")

P0_MODELS: Tuple[P0ModelSpec, ...] = (P0_MODEL_A, P0_MODEL_B, P0_MODEL_C, P0_MODEL_D, P0_MODEL_E, P0_MODEL_F,
                                      P0_MODEL_G, P0_MODEL_H)


def _twin(spec: P0ModelSpec) -> P0ModelSpec:
    return replace(spec, name=spec.name + "p", label=spec.label + " (gemelo placebo)", levels="placebo")


P0_MODEL_Bp, P0_MODEL_Cp, P0_MODEL_Dp, P0_MODEL_Ep, P0_MODEL_Fp, P0_MODEL_Gp, P0_MODEL_Hp = (
    _twin(s) for s in P0_MODELS[1:])
P0_TWINS: Tuple[P0ModelSpec, ...] = (P0_MODEL_Bp, P0_MODEL_Cp, P0_MODEL_Dp, P0_MODEL_Ep, P0_MODEL_Fp, P0_MODEL_Gp,
                                     P0_MODEL_Hp)
ALL_SPECS: Tuple[P0ModelSpec, ...] = P0_MODELS + P0_TWINS
SPECS_BY_NAME = {s.name: s for s in ALL_SPECS}
SELECTION_NAMES = ("A", "B", "C", "D", "E", "F", "G")  # R13: H y los gemelos no participan

for _s in ALL_SPECS:
    assert set(_s.weights) == set(_s.timeframes), _s.name
    assert sum(Decimal(str(w)) for w in _s.weights.values()) == 1, _s.name


# ---------------------------------------------------------------------------
# Puntaje (D8) y P0 del modelo
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TfReading:
    """Lo que una TF dice en τ para un juego de niveles."""
    tf: str
    state: str
    leg: Optional[Leg]
    situation: str
    last_event: Optional[LevelEvent] = None

    @property
    def direction(self) -> int:
        return 1 if self.state == BULLISH else -1 if self.state == BEARISH else 0


def tf_events_at(market: Market, tf: str, leg: Leg, tau: Optional[datetime], levels: Sequence[float],
                 half_width: Optional[Mapping[float, float]] = None) -> Dict[float, LevelEvent]:
    """Los eventos de `leg` en todos los intervalos en que fue el vigente (hasta τ si se da), uno por nivel."""
    info = leg_intervals(market.timelines[tf], until=tau).get(leg.key)
    events: Dict[float, LevelEvent] = {}
    if info is None:
        return events
    for start, end, _state in info["intervals"]:
        remaining = [r for r in levels if r not in events]
        if not remaining:
            break
        found = detect_events(market.paths[tf], leg.a.price, leg.b.price, remaining, market.close_at(tf, start),
                              start_time=start, stop_time=end, tau=tau, ext_from_time=leg.b.confirmed_at,
                              half_width=half_width, rupture_barrier=market.rupture_distance(leg))
        events.update({r: ev for r, ev in found.items() if ev is not None})
    return events


def read_tf(market: Market, tf: str, tau: datetime, level_set: str) -> TfReading:
    """Estado, tramo y situación de D8 de una TF en τ, con los niveles de `level_set` ("p0" o "placebo")."""
    pt = state_at(market.timelines[tf], tau)
    if pt.state == INSUFFICIENT or pt.leg is None:
        return TfReading(tf, INSUFFICIENT, None, SIT_INSUFFICIENT)
    if pt.state == RANGE:
        return TfReading(tf, RANGE, pt.leg, SIT_RANGE)
    levels = P0_LEVELS if level_set == "p0" else PLACEBO_LEVELS
    events = tf_events_at(market, tf, pt.leg, tau, levels, half_width=market.half_width(tf, pt.leg, levels))
    return reading_from_events(tf, pt.state, pt.leg, events, level_set)


def reading_from_events(tf: str, state: str, leg: Optional[Leg], events: Mapping[float, LevelEvent],
                        level_set: str) -> TfReading:
    """
    D8: la situación de una TF a partir de su estado y de los eventos de su tramo conocidos en τ. La usan el sistema
    (`read_tf`) y el visor cuando el usuario elige otro tramo, así las dos lecturas siguen la misma regla.
    """
    if state == INSUFFICIENT or leg is None:
        return TfReading(tf, INSUFFICIENT, None, SIT_INSUFFICIENT)
    if state == RANGE:
        return TfReading(tf, RANGE, leg, SIT_RANGE)
    if not events:
        return TfReading(tf, state, leg, SIT_NO_EVENT)
    # Último por entrada. Si una misma vela entró a varias zonas, el último es el más avanzado en el sentido de su
    # llegada (el más bajo si llegó desde arriba, el más alto si llegó desde abajo).
    last = max(events.values(), key=lambda ev: (ev.entry_index, -ev.arrival * ev.price))
    if last.outcome not in (REJECTION, RUPTURE):
        return TfReading(tf, state, leg, SIT_NO_EVENT, last)
    zero = last.r == ZERO_LEVEL[level_set]
    if zero:
        situation = SIT_ZERO_REJECTION if last.outcome == REJECTION else SIT_ZERO_RUPTURE
    else:
        situation = SIT_RETR_REJECTION if last.outcome == REJECTION else SIT_RETR_RUPTURE
    return TfReading(tf, state, leg, situation, last)


def tf_score(reading: TfReading, uses_fib: bool) -> int:
    """D8: puntaje de una TF."""
    if reading.situation in (SIT_INSUFFICIENT, SIT_RANGE):
        return 0
    d = reading.direction
    if not uses_fib or reading.situation == SIT_NO_EVENT:
        return d
    return {SIT_RETR_REJECTION: 2 * d, SIT_RETR_RUPTURE: 0, SIT_ZERO_REJECTION: 0, SIT_ZERO_RUPTURE: 2 * d}[
        reading.situation]


def _sign(x) -> int:
    return (x > 0) - (x < 0)


def model_x0(spec: P0ModelSpec, scores: Mapping[str, int]) -> int:
    """D8/D9: `x0 = clamp(round_half_away_from_zero(2·S / S_max), −2, 2)`, con la regla jerárquica de F."""
    s = {tf: int(scores[tf]) for tf in spec.timeframes}
    if spec.hierarchical:
        top = s["1D"] + s["12H"]
        if top == 0:
            return 0
        for tf in ("4H", "1H"):
            if _sign(s[tf]) != _sign(top):
                s[tf] = 0
    total = sum(Decimal(str(spec.weights[tf])) * s[tf] for tf in spec.timeframes)
    return int(clamp(round_half_away_from_zero(Decimal(2) * total / spec.s_max), -2, 2))


def predict_at(market: Market, tau: datetime, specs: Sequence[P0ModelSpec] = ALL_SPECS,
               overrides: Optional[Mapping[Tuple[str, str], TfReading]] = None) -> Dict[str, dict]:
    """
    x0 de cada modelo en τ, con las lecturas por TF (para T7 y diagnóstico). `overrides` reemplaza la lectura de
    algunas (TF, juego de niveles): la usa el visor cuando el usuario elige su propio tramo A→B (opción 3).
    """
    readings = {}
    for level_set in ("p0", "placebo"):
        for tf in STRUCTURE_TFS:
            if overrides and (tf, level_set) in overrides:
                readings[(tf, level_set)] = overrides[(tf, level_set)]
            elif tf in market.timelines:
                readings[(tf, level_set)] = read_tf(market, tf, tau, level_set)
    out = {}
    for spec in specs:
        scores = {}
        for tf in spec.timeframes:
            reading = readings.get((tf, spec.levels))
            scores[tf] = 0 if reading is None else tf_score(reading, spec.uses_fib)
        out[spec.name] = {"x0": model_x0(spec, scores), "scores": scores}
    out["_readings"] = readings
    return out


# ---------------------------------------------------------------------------
# Análisis: carga, exclusiones (INV-7) y verdad (D12)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AnalysisInput:
    trade_id: str
    symbol: str
    account: str
    anchor: datetime
    created_at: datetime
    analysis_start_time: Optional[datetime]
    is_backdated: bool         # retroactivo NO recuperado (los recuperados cuentan como no retroactivos)
    bias: str                  # determine_market_bias(calc_edge)
    segment: str               # "Direccionales" o "Choppy"
    start_price: float
    upper: float
    lower: float
    evp: float
    si: float
    operator_dir: int          # +1 Long, −1 Short, 0 Neutral o vacío
    operator_strength: Optional[str]
    thesis: str


def read_p0_layers(db_path: str) -> Dict[str, Tuple[Optional[str], Optional[str], Optional[str]]]:
    session = open_readonly_session(db_path)
    try:
        rows = session.execute(text(
            "SELECT trade_id, direction, strength, thesis FROM analysis_layer WHERE layer_name = 'P0'")).all()
    finally:
        session.close()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def read_calc_edge(db_path: str) -> Dict[str, Optional[float]]:
    session = open_readonly_session(db_path)
    try:
        rows = session.execute(text("SELECT id, calc_edge FROM unified_department")).all()
    finally:
        session.close()
    return {r[0]: (None if r[1] is None else float(r[1])) for r in rows}


def load_analyses(db_path: str, symbol: str, candles: Mapping[str, Sequence[Candle]], cutoff: datetime,
                  anchor_override_min: Optional[int] = None
                  ) -> Tuple[List[AnalysisInput], List[AnalysisInput], List[ExclusionRecord], Dict[str, int]]:
    """
    Los análisis de una cuenta con los filtros del pre-registro §3, en orden. Devuelve (retrospectivo, evidencia
    independiente, exclusiones, conteos por paso). `anchor_override_min` es la sensibilidad de ancla de T11: los que no
    tienen `analysis_start_time` ni son retroactivos usan `created_at − m`.
    """
    rows = read_analysis_rows(db_path)
    edges = read_calc_edge(db_path)
    layers = read_p0_layers(db_path)
    ladder = {tf: candles[tf] for tf in LADDER if candles.get(tf)}
    retro, independent, exclusions = [], [], []
    counts = {"all": len(rows)}
    for row in rows:
        recovered = row.trade_id in RECOVERED_BACKDATED
        if row.is_backdated and not recovered:
            exclusions.append(ExclusionRecord(row.trade_id, REASON_BACKDATED))
            continue
        anchor = analysis_anchor(row.analysis_start_time, row.created_at, row.is_backdated)
        if anchor_override_min is not None and row.analysis_start_time is None and not row.is_backdated:
            anchor = row.created_at - timedelta(minutes=anchor_override_min)
        sp = start_price(anchor, ladder)
        if sp is None:
            exclusions.append(ExclusionRecord(row.trade_id, REASON_NO_HISTORY))
            continue
        evp, si = row.edge_validation_price, row.structural_invalidation
        if evp is None or si is None or infer_thesis_direction(sp.price, evp, si) is None:
            exclusions.append(ExclusionRecord(row.trade_id, REASON_NO_LEVELS))
            continue
        bias = determine_market_bias(edges[row.trade_id]) if edges.get(row.trade_id) is not None else None
        direction, strength, thesis = layers.get(row.trade_id, (None, None, None))
        item = AnalysisInput(
            row.trade_id, symbol, ACCOUNT_IDS.get(symbol, symbol), anchor, row.created_at, row.analysis_start_time,
            False, bias, "Choppy" if bias == "Choppy / Neutral" else "Direccionales", sp.price, max(evp, si),
            min(evp, si), evp, si, 1 if direction == "Long" else -1 if direction == "Short" else 0, strength,
            thesis or "")
        if anchor > cutoff:
            independent.append(item)
            exclusions.append(ExclusionRecord(row.trade_id, REASON_INDEPENDENT))
        else:
            retro.append(item)
    counts["backdated"] = sum(e.reason == REASON_BACKDATED for e in exclusions)
    counts["no_history"] = sum(e.reason == REASON_NO_HISTORY for e in exclusions)
    counts["no_levels"] = sum(e.reason == REASON_NO_LEVELS for e in exclusions)
    counts["independent_evidence"] = len(independent)
    counts["in_scope"] = len(retro)
    return retro, independent, exclusions, counts


@dataclass(frozen=True)
class GeometricTruth:
    """El primer toque en términos geométricos (D12): `upper_first` es None si no hubo un toque resuelto."""
    trade_id: str
    code: str                   # confirmed, invalidated, open, ambiguous, pending_candles, ...
    upper_first: Optional[bool]
    touch_time: Optional[datetime]
    path_end: Optional[datetime]
    overlap: bool = False       # etiqueta Overlap del resolvedor (N36): nunca excluye


def geometric_truths(db_path: str, foto_dir: str, account: str) -> Dict[str, GeometricTruth]:
    """La verdad de cada análisis de la cuenta, con el resolvedor de la spec 002 sobre la foto (`AccountResolver`)."""
    resolver = AccountResolver(db_path, foto_dir, account=account)
    out = {}
    for proposal in resolver.propose_all():
        res = proposal.resolution
        code = res.outcome if res else proposal.reason
        touch = res.first_touch if res else None
        upper = None
        if code in (OUTCOME_CONFIRMED, OUTCOME_INVALIDATED):
            upper = (res.thesis_direction == "long") == (code == OUTCOME_CONFIRMED)
        out[proposal.trade_id] = GeometricTruth(proposal.trade_id, code, upper,
                                                touch.touch_time if touch and upper is not None else None,
                                                touch.path_end if touch else None, proposal.overlap is not None)
    return out


def geometric_truth_at(item: AnalysisInput, ladder: Mapping[str, Sequence[Candle]]) -> GeometricTruth:
    """
    La verdad de un análisis con su `anchor` (que puede venir de la sensibilidad de ancla de T11), con el mismo
    resolvedor que `AccountResolver.propose`: `resolve_analysis` y la excepción de toque de `TOUCH_EXCEPTIONS`.
    """
    exception = TOUCH_EXCEPTIONS.get(item.trade_id)
    res = resolve_analysis(item.anchor, ladder, item.evp, item.si, touched_level=exception[0] if exception else None)
    touch = res.first_touch
    upper = None
    if res.outcome in (OUTCOME_CONFIRMED, OUTCOME_INVALIDATED):
        upper = (res.thesis_direction == "long") == (res.outcome == OUTCOME_CONFIRMED)
    return GeometricTruth(item.trade_id, res.outcome, upper, touch.touch_time if touch and upper is not None else None,
                          touch.path_end if touch else None)


def predictions_for(items: Sequence[AnalysisInput], market: Market, specs: Sequence[P0ModelSpec] = ALL_SPECS
                    ) -> Tuple[Dict[str, np.ndarray], List[dict]]:
    """x0 de cada modelo para cada análisis (en el orden de `items`) y las lecturas por TF de cada uno."""
    x0 = {spec.name: np.zeros(len(items), dtype=int) for spec in specs}
    readings = []
    for k, item in enumerate(items):
        out = predict_at(market, item.anchor, specs)
        for spec in specs:
            x0[spec.name][k] = out[spec.name]["x0"]
        readings.append(out["_readings"])
    return x0, readings


def outcomes_for(items: Sequence[AnalysisInput], truths: Mapping[str, GeometricTruth],
                 predictions: Mapping[str, int]) -> List[AnalysisOutcome]:
    """
    Un `AnalysisOutcome` por análisis para un predictor: `confirmed` si su dirección coincide con el primer toque,
    `invalidated` si no. Sin opinión (0) el análisis no entra. Sin toque, el código del resolvedor. Así S4 estricto, S1
    y S4 salen de `core/outcome_metrics.py` sin reimplementarlos.
    """
    out = []
    for item in items:
        pred = predictions.get(item.trade_id, 0)
        if pred == 0:
            continue
        truth = truths[item.trade_id]
        if truth.upper_first is None:
            code = truth.code
        else:
            code = OUTCOME_CONFIRMED if (pred > 0) == truth.upper_first else OUTCOME_INVALIDATED
        out.append(AnalysisOutcome(item.trade_id, item.account, item.anchor, item.is_backdated, item.bias, code,
                                   truth.touch_time, path_end=truth.path_end))
    return out


def truth_vectors(items: Sequence[AnalysisInput], truths: Mapping[str, GeometricTruth]) -> Dict[str, np.ndarray]:
    """
    Verdades numéricas por análisis, en el orden de `items`:
    - `s1`: +1/−1 si hubo toque resuelto; `s1_mask` dice cuáles;
    - `s4s`: S4 estricto, +1/−1 si el primer toque fue dentro de 48 h y 0 si es tardío; `s4s_mask` dice cuáles cuentan
      (los mismos que cuenta `counted_outcomes(strict=True)` para un predictor que siempre opina).
    """
    s1 = np.zeros(len(items), dtype=int)
    s4s = np.zeros(len(items), dtype=int)
    always_long = {item.trade_id: 1 for item in items}
    counted = {o.analysis_id for o in counted_outcomes(outcomes_for(items, truths, always_long), strict=True,
                                                       include_backdated=False)}
    for k, item in enumerate(items):
        t = truths[item.trade_id]
        if t.upper_first is not None:
            s1[k] = 1 if t.upper_first else -1
            hours = (t.touch_time - item.anchor).total_seconds() / 3600
            s4s[k] = s1[k] if hours <= S4_WINDOW_H else 0
    return {"s1": s1, "s1_mask": s1 != 0, "s4s": s4s,
            "s4s_mask": np.array([item.trade_id in counted for item in items], dtype=bool)}


def geometric_null_p(items: Sequence[AnalysisInput], predictions: Sequence[int]) -> np.ndarray:
    """D15: probabilidad de acertar por pura distancia, para la dirección del predictor (nan si no opina)."""
    out = np.full(len(items), np.nan)
    for k, (item, pred) in enumerate(zip(items, predictions)):
        if pred == 0:
            continue
        d_up, d_down = item.upper - item.start_price, item.start_price - item.lower
        out[k] = d_down / (d_up + d_down) if pred > 0 else d_up / (d_up + d_down)
    return out


def poisson_binomial_sf(k: int, ps: Sequence[float]) -> float:
    """P(X >= k) con X ~ Poisson-binomial(ps), exacta por convolución."""
    pmf = np.array([1.0])
    for p in ps:
        pmf = np.convolve(pmf, [1.0 - p, p])
    return float(min(1.0, pmf[k:].sum())) if k < len(pmf) else 0.0


# ---------------------------------------------------------------------------
# Etiquetas de las tesis (D20, RF-7) y κ (T7)
# ---------------------------------------------------------------------------

_TF_TOKEN = r"(1D|12H?|4H?|1H)"
_LABEL_RE = re.compile(r"(?<![\dA-Za-z])" + _TF_TOKEN + r"\s*T\s*-\s*(B[LR])", re.I)
_EQ_RE = re.compile(r"(?<![\dA-Za-z])" + _TF_TOKEN + r"\s*T\s*-\s*(B[LR])\s*:?\s*==\s*" + _TF_TOKEN + r"\s*T", re.I)


def _norm_tf(token: str) -> str:
    token = token.upper()
    return {"12": "12H", "4": "4H"}.get(token, token)


def parse_thesis_labels(thesis: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    """({TF: "BL"/"BR"}, {TF: TF referida por `==`}). Si una TF aparece dos veces, vale la primera."""
    labels: Dict[str, str] = {}
    for m in _LABEL_RE.finditer(thesis or ""):
        labels.setdefault(_norm_tf(m.group(1)), m.group(2).upper())
    refs: Dict[str, str] = {}
    for m in _EQ_RE.finditer(thesis or ""):
        refs.setdefault(_norm_tf(m.group(1)), _norm_tf(m.group(3)))
    return labels, refs


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> Optional[float]:
    n = len(a)
    if n == 0:
        return None
    cats = sorted(set(a) | set(b))
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((list(a).count(c) / n) * (list(b).count(c) / n) for c in cats)
    return None if pe == 1 else (po - pe) / (1 - pe)


# ---------------------------------------------------------------------------
# Estudio de niveles (T3–T5): eventos, deduplicación y partición (D17, INV-6)
# ---------------------------------------------------------------------------

def atr14(bars: Bars) -> np.ndarray:
    """ATR de Wilder (14) por vela, con velas cerradas: el valor en k usa solo velas hasta k."""
    h, lo, c = bars.high, bars.low, bars.close
    prev = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - lo, np.maximum(abs(h - prev), abs(lo - prev)))
    out = np.full(len(tr), np.nan)
    if len(tr) >= 14:
        out[13] = tr[:14].mean()
        for k in range(14, len(tr)):
            out[k] = (out[k - 1] * 13 + tr[k]) / 14
    return out


def atr_half_widths(market: Market, tf: str, legs: Sequence[Leg]) -> Tuple[Dict[tuple, float], float]:
    """T11, ancho por ATR14: semiancho = c·ATR14 al confirmarse B, con c tal que la mediana iguale a la de δ·|B−A|."""
    bars = market.bars[tf]
    atr = atr14(bars)
    at = {}
    for leg in legs:
        k = bisect_right(bars.times, leg.b.confirmed_at - bars.duration) - 1
        at[leg.key] = atr[k] if k >= 0 else np.nan
    valid = [leg for leg in legs if np.isfinite(at[leg.key])]
    c = float(np.median([ZONE_DELTA * leg.size for leg in valid]) / np.median([at[leg.key] for leg in valid]))
    return {key: c * value for key, value in at.items()}, c


def level_events(market: Market, tf: str) -> List[dict]:
    """
    Todos los eventos (P0 y placebo) de los tramos de una TF en toda la foto, uno por (tramo, nivel). El ancho de zona
    es el del mercado (`zone_mode`); con "atr", un tramo sin ATR14 todavía (menos de 14 velas) no genera eventos.
    """
    timeline = market.timelines[tf]
    legs = leg_intervals(timeline)
    levels = P0_LEVELS + PLACEBO_LEVELS
    rows = []
    for key, info in legs.items():
        leg: Leg = info["leg"]
        if leg.size <= 0:
            continue
        hw = market.half_width(tf, leg, levels)
        if hw is not None and not np.isfinite(hw[levels[0]]):
            continue
        found: Dict[float, Tuple[LevelEvent, str]] = {}
        for start, end, state in info["intervals"]:
            remaining = [r for r in levels if r not in found]
            if not remaining:
                break
            events = detect_events(market.paths[tf], leg.a.price, leg.b.price, remaining, market.close_at(tf, start),
                                   start_time=start, stop_time=end, ext_from_time=leg.b.confirmed_at, half_width=hw,
                                   rupture_barrier=market.rupture_distance(leg))
            for r, ev in events.items():
                if ev is not None:
                    found[r] = (ev, state)
        for r, (ev, state) in found.items():
            rows.append({
                "symbol": market.symbol, "tf": tf, "leg": key, "a": leg.a.price, "b": leg.b.price, "up": leg.up,
                "a_time": leg.a.time, "b_time": leg.b.time, "r": r, "is_p0": r in P0_LEVELS,
                "extreme": r in EXTREME_P0 + EXTREME_PLACEBO, "state": "trend" if state in TREND_STATES else "range",
                "entry_time": ev.entry_time, "entry_tf": ev.entry_tf, "arrival": ev.arrival, "outcome": ev.outcome,
                "resolved_at": ev.resolved_at, "coarse": ev.coarse,
            })
    return rows


_TF_SPAN = {tf: timedelta(minutes=TF_MINUTES[tf]) for tf in TF_MINUTES}


def dedupe_legs(events: pd.DataFrame) -> pd.DataFrame:
    """
    Un mismo tramo en varias TF cuenta una vez (pre-registro §10): misma orientación, mismos precios de A y B, y las
    velas de A y B de la TF más fina dentro de las de la más gruesa. Queda el de la TF más gruesa.
    """
    if events.empty:
        return events.assign(cluster=[])
    order = ["1W", "1D", "12H", "4H", "1H"]
    legs = events.drop_duplicates("leg")[["leg", "tf", "a", "b", "up", "a_time", "b_time"]]
    legs = legs.assign(rank=legs.tf.map(order.index)).sort_values("rank")
    kept: Dict[tuple, tuple] = {}
    by_prices: Dict[tuple, list] = {}
    for row in legs.itertuples(index=False):
        sig = (row.up, row.a, row.b)
        owner = None
        for other in by_prices.get(sig, []):
            span = _TF_SPAN[other.tf]
            if other.a_time <= row.a_time < other.a_time + span and other.b_time <= row.b_time < other.b_time + span:
                owner = other
                break
        if owner is None:
            by_prices.setdefault(sig, []).append(row)
            kept[row.leg] = row.leg
        else:
            kept[row.leg] = None
    out = events[events.leg.map(lambda k: kept.get(k) is not None)].copy()
    out["cluster"] = out.leg.map(lambda k: "|".join(map(str, k)))
    return out


class ConfirmatoryLocked(PermissionError):
    """INV-6: el confirmatorio no se calcula sin el flag de R10."""


def split_events(events: pd.DataFrame, part: str, allow_confirmatory: bool = False) -> pd.DataFrame:
    """
    D17: por activo y TF, eventos ordenados por entrada; el corte es la entrada del evento número floor(0.60·n) (base 0):
    antes, exploratorio; desde ahí, confirmatorio. `part="confirmatory"` exige `allow_confirmatory=True` (R10, INV-6).
    """
    if part not in ("exploratory", "confirmatory"):
        raise ValueError(part)
    if part == "confirmatory" and not allow_confirmatory:
        raise ConfirmatoryLocked("R10: the confirmatory 40% needs the user's written approval (allow_confirmatory)")
    pieces = []
    for _, group in events.groupby(["symbol", "tf"], sort=True):
        g = group.sort_values("entry_time", kind="mergesort")
        cut = g.entry_time.iloc[int(math.floor(SPLIT_EXPLORATORY * len(g)))] if len(g) else None
        mask = g.entry_time < cut if part == "exploratory" else g.entry_time >= cut
        pieces.append(g[mask])
    return pd.concat(pieces) if pieces else events.iloc[0:0]


# ---------------------------------------------------------------------------
# Regresión logística por IRLS y bootstrap por tramo (RF-8, R16)
# ---------------------------------------------------------------------------

def logistic_irls(x: np.ndarray, y: np.ndarray, max_iter: int = 100, tol: float = 1e-10) -> Tuple[np.ndarray, bool]:
    """Máxima verosimilitud de una logística por mínimos cuadrados reponderados. Devuelve (coeficientes, convergió)."""
    beta = np.zeros(x.shape[1])
    for _ in range(max_iter):
        eta = np.clip(x @ beta, -30, 30)
        p = 1.0 / (1.0 + np.exp(-eta))
        w = p * (1.0 - p)
        if np.any(w < 1e-12):
            return beta, False
        z = eta + (y - p) / w
        xtw = x.T * w
        try:
            new = np.linalg.solve(xtw @ x, xtw @ z)
        except np.linalg.LinAlgError:
            return beta, False
        if np.max(np.abs(new - beta)) < tol:
            return new, True
        beta = new
    return beta, False


TF_LEVELS = ("1D", "12H", "4H", "1H")  # la referencia es 1D, o la primera TF presente en el subconjunto


def design_matrix(df: pd.DataFrame, depth: bool = True, extreme: bool = False, interaction: bool = False,
                  state: bool = True) -> Tuple[np.ndarray, List[str]]:
    """
    Columnas: intercepto, profundidad r, extremo, nivel P0, TF (dummies; referencia 1D o, si el subconjunto no tiene
    1D, la primera TF presente, para que las dummies no sumen el intercepto), rango (ref. tendencia) y la interacción
    nivel P0 × rango. Se quitan las columnas constantes.
    """
    cols = {"intercept": np.ones(len(df))}
    if depth:
        cols["r"] = df.r.to_numpy(float)
    if extreme:
        cols["extreme"] = df.extreme.to_numpy(float)
    cols["is_p0"] = df.is_p0.to_numpy(float)
    present = [tf for tf in TF_LEVELS if (df.tf == tf).any()]
    for tf in present[1:]:
        cols[f"tf_{tf}"] = (df.tf == tf).to_numpy(float)
    if state:
        cols["range"] = (df.state == "range").to_numpy(float)
    if interaction:
        cols["is_p0_x_range"] = cols["is_p0"] * (df.state == "range").to_numpy(float)
    names = [k for k, v in cols.items() if k == "intercept" or np.ptp(v) > 0]
    return np.column_stack([cols[k] for k in names]), names


@dataclass(frozen=True)
class LogitResult:
    term: str
    n_events: int
    n_p0: int
    n_placebo: int
    n_clusters: int
    beta: Optional[float]
    odds_ratio: Optional[float]
    ci95: Tuple[float, float]
    p_one_sided: Optional[float]
    n_boot_ok: int
    insufficient: bool
    columns: Tuple[str, ...]


def logit_test(df: pd.DataFrame, term: str = "is_p0", n_boot: int = N_BOOT, seed: int = SEED,
               min_arm: int = N_MIN_ARM, **design) -> LogitResult:
    """OR de `term` con IC95 percentil y p unilateral (H1: OR > 1) de un bootstrap por tramo (pre-registro §10)."""
    data = df[df.outcome.isin([REJECTION, RUPTURE])]
    y = (data.outcome == REJECTION).to_numpy(float)
    n_p0, n_pl = int(data.is_p0.sum()), int((~data.is_p0).sum())
    clusters = data.cluster.to_numpy()
    if len(data) == 0:
        return LogitResult(term, 0, 0, 0, 0, None, None, (np.nan, np.nan), None, 0, True, ())
    x, names = design_matrix(data, **design)
    insufficient = min(n_p0, n_pl) < min_arm
    if term not in names:
        return LogitResult(term, len(data), n_p0, n_pl, len(set(clusters)), None, None, (np.nan, np.nan), None, 0,
                           insufficient, tuple(names))
    j = names.index(term)
    beta, ok = logistic_irls(x, y)
    uniq, inverse = np.unique(clusters, return_inverse=True)
    if n_boot == 0:  # solo la estimación puntual (sensibilidades de T11: signo y tamaño)
        return LogitResult(term, len(data), n_p0, n_pl, len(uniq), float(beta[j]) if ok else None,
                           float(np.exp(beta[j])) if ok else None, (np.nan, np.nan), None, 0, insufficient,
                           tuple(names))
    members = [np.flatnonzero(inverse == g) for g in range(len(uniq))]
    rng = np.random.default_rng(seed)
    boot = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(uniq), len(uniq))
        idx = np.concatenate([members[g] for g in pick])
        xb = x[idx]
        if np.ptp(xb[:, j]) == 0:
            continue
        b, conv = logistic_irls(xb, y[idx])
        if conv:
            boot.append(b[j])
    boot = np.asarray(boot)
    if not ok or len(boot) == 0:
        return LogitResult(term, len(data), n_p0, n_pl, len(uniq), None, None, (np.nan, np.nan), None, len(boot),
                           insufficient, tuple(names))
    lo, hi = np.percentile(boot, [2.5, 97.5])
    p = (1 + int(np.sum(boot <= 0))) / (len(boot) + 1)
    return LogitResult(term, len(data), n_p0, n_pl, len(uniq), float(beta[j]), float(np.exp(beta[j])),
                       (float(np.exp(lo)), float(np.exp(hi))), p, len(boot), insufficient, tuple(names))


def passes(result: LogitResult, p_holm: Optional[float]) -> bool:
    """Un test de niveles pasa con n suficiente, OR > 1, IC95 inferior > 1 y p Holm < 0.05 (§13)."""
    return (not result.insufficient and result.odds_ratio is not None and result.odds_ratio > 1
            and result.ci95[0] > 1 and p_holm is not None and p_holm < ALPHA)


# ---------------------------------------------------------------------------
# Etiquetas de T8 y decisión D18
# ---------------------------------------------------------------------------

LABEL_SUPERA = "supera"
LABEL_INFERIOR = "inferior"
LABEL_EQUIVALENTE = "equivalente"
LABEL_NO_DISTINGUIBLE = "no distinguible"
LABEL_NO_CONCLUYENTE = "no concluyente"


def label_t8(n: int, b: int, c: int, p_holm: float, ci: Tuple[float, float], p_r13: Optional[float] = None,
             involves_winner: bool = False) -> str:
    """Pre-registro §12: etiquetas de una comparación pareada con el p de McNemar ajustado por Holm (F3)."""
    if n < N_MIN_PAIRS:
        return LABEL_NO_CONCLUYENTE
    if b > c and p_holm < ALPHA and (not involves_winner or (p_r13 is not None and p_r13 < ALPHA)):
        return LABEL_SUPERA
    if b < c and p_holm < ALPHA:
        return LABEL_INFERIOR
    if ci[0] <= 0 <= ci[1]:
        return LABEL_EQUIVALENTE
    return LABEL_NO_DISTINGUIBLE


DECISION_STOP = "parada"
DECISION_CONTINUE = "Continuar"
DECISION_MODIFY = "Modificar"
DECISION_ABANDON_P0 = "Abandonar P0 sistemático"
DECISION_ABANDON_FIB = "Abandonar Fibonacci"
DECISION_INSUFFICIENT = "Insuficiente"


@dataclass(frozen=True)
class DecisionInputs:
    t0_ok: bool
    t2_ok: bool
    fib_positive: bool       # T3a o T3b pasa en el confirmatorio
    fib_null: bool           # T3a y T3b con n suficiente y ninguno pasa
    btc_same_sign: bool      # OR de BTC > 1 en cada prueba que pasó en XAU
    twin_pairs: int
    twin_b: int
    twin_c: int
    t5_passing: int          # cuántos segmentos de T5 pasan
    t4_passing: int          # cuántos niveles de T4 pasan
    t6_fails: bool
    winner_is_a: bool = False  # v1.2: A no tiene gemelo; "ganador ≤ su gemelo" se da por cumplido


def decide_d18(d: DecisionInputs) -> Tuple[str, str]:
    """§13 del pre-registro: la primera fila que se cumple es la decisión."""
    twin_win = not d.winner_is_a and d.twin_pairs >= N_MIN_PAIRS and d.twin_b > d.twin_c
    twin_lose = d.winner_is_a or (d.twin_pairs >= N_MIN_PAIRS and d.twin_b <= d.twin_c)
    if not (d.t0_ok and d.t2_ok):
        return DECISION_STOP, "T0 o T2 fallan"
    if d.fib_positive and d.btc_same_sign and twin_win:
        return DECISION_CONTINUE, "Fib positivo, BTC del mismo signo y ganador > su gemelo"
    if d.t5_passing == 1 or d.t4_passing == 1:
        return DECISION_MODIFY, f"efecto solo en un segmento (T5: {d.t5_passing}) o en un nivel (T4: {d.t4_passing})"
    if (d.fib_null or twin_lose) and d.t6_fails:
        return DECISION_ABANDON_P0, "Fib nulo o ganador ≤ su gemelo, y T6 falla"
    if d.fib_null or twin_lose:
        return DECISION_ABANDON_FIB, "Fib nulo o ganador ≤ su gemelo, con T6 que pasa"
    missing = []
    if not d.fib_positive and not d.fib_null:
        missing.append("T3a/T3b sin n suficiente en el confirmatorio")
    if d.fib_positive and not d.btc_same_sign:
        missing.append("BTC con signo opuesto")
    if d.twin_pairs < N_MIN_PAIRS and not d.winner_is_a:
        missing.append(f"ganador contra gemelo con {d.twin_pairs} pares (< {N_MIN_PAIRS})")
    return DECISION_INSUFFICIENT, "; ".join(missing) or "ninguna fila anterior se cumple"


# ---------------------------------------------------------------------------
# Marcas de T0 (regla horaria) y E4 (huecos anómalos)
# ---------------------------------------------------------------------------

def touches_dst_window(start: datetime, end: Optional[datetime]) -> bool:
    end = end or start
    return any(start < w_end and end >= w_start for w_start, w_end in DST_WINDOWS)


def anomalous_gaps(bars: Bars, symbol: str) -> List[Tuple[datetime, datetime]]:
    """Huecos del 1H que no son fin de semana, pausa diaria del oro ni feriados explicados (E4)."""
    out = []
    for k in range(1, len(bars)):
        prev_end = bars.times[k - 1] + bars.duration
        if bars.times[k] <= prev_end:
            continue
        a, b = prev_end, bars.times[k]
        days = pd.date_range(pd.Timestamp(a).normalize(), (pd.Timestamp(b) - pd.Timedelta(seconds=1)).normalize())
        if any(d.dayofweek == 5 for d in days):
            continue
        if symbol == "XAUUSD" and b - a == timedelta(hours=1) and a.hour == 15 and a.weekday() <= 3:
            continue
        if a.strftime("%Y-%m-%d") in EXPLAINED_GAP_DATES:
            continue
        out.append((a, b))
    return out


def crosses_any(start: datetime, end: Optional[datetime], gaps: Iterable[Tuple[datetime, datetime]]) -> bool:
    end = end or start
    return any(start <= g_start and g_start <= end for g_start, _ in gaps)
