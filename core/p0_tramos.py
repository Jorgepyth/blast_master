"""
core/p0_tramos.py — motor de tramos v0.2 de P0 por estructura (opción 2, `docs/p0-reglas-opcion2.md`).

Decide, por TF y con sus propias velas, la tendencia y el tramo A→B como los lee el operador:
- **Pivotes significativos (R5):** un techo cuenta si el precio después baja al menos k·ATR14 (y un piso, si sube k·ATR14):
  un zigzag. Se conoce en el cierre de la vela en que el giro llega a ese tamaño.
- **Quiebre (R2):** `closes` cierres seguidos más allá del pivote, cada uno por más de `margin`·ATR14 (el de la vela
  anterior). Una mecha, o menos cierres, es un barrido y no cambia nada.
- **Tendencia (R5, R8):** las referencias son el último techo y el último piso significativos todavía sin quebrar.
  Quebrar el techo deja la TF alcista; el piso, bajista. Si cambia la dirección y el giro ya estaba armado (A más alto que
  la B anterior al quebrar al alza, más bajo al quebrar a la baja), queda en tendencia; si fue en V, en rango hasta que se
  confirme un pivote significativo del lado contrario dentro del nuevo tramo.
- **A (R4):** el extremo opuesto más alejado entre el pivote quebrado (sin contar su vela) y la vela del quiebre.
- **B (R3):** el pivote fractal de 2 velas más extremo y ya confirmado desde A, con mecha; si todavía no hay ninguno, el
  extremo de las velas desde A.

Funciones puras, sin I/O. Solo velas cerradas: el punto de la línea de tiempo de la vela i vale desde su cierre.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Sequence

import numpy as np

from core.p0_structure import BOTTOM, TOP, Bars, Leg, Pivot, find_pivots

UP = 1
DOWN = -1
PHASE_TREND = "trend"
PHASE_RANGE = "range"


@dataclass(frozen=True)
class TramoParams:
    """Valores por defecto: los de la calibración del 2026-10-09 contra los 24 tramos del operador (borrador v0.3)."""
    k: float = 2.0         # tamaño mínimo de un pivote significativo, en ATR14 de la TF
    margin: float = 0.0    # margen de cada cierre del quiebre, en ATR14 de la TF
    closes: int = 2        # cierres seguidos más allá para quebrar
    n: int = 2             # velas de cada lado de un fractal (para B)


@dataclass(frozen=True)
class TramoPoint:
    """Desde `at` (el cierre de una vela), la TF vale esto."""
    at: datetime
    trend: int                     # UP, DOWN o 0 (todavía ningún quiebre)
    phase: str                     # PHASE_TREND o PHASE_RANGE
    leg: Optional[Leg]
    protected: Optional[float]     # el nivel cuyo quiebre cambiaría la dirección


def significant_pivots(bars: Bars, atr: Sequence[float], k: float) -> List[Pivot]:
    """Zigzag de k·ATR14: techos y pisos alternados, cada uno con `confirmed_at` = cierre de la vela que completó el giro."""
    h, lo = bars.high, bars.low
    out: List[Pivot] = []
    direction = 0
    hi_i = lo_i = 0
    for i in range(len(bars)):
        if direction >= 0 and h[i] >= h[hi_i]:
            hi_i = i
        if direction <= 0 and lo[i] <= lo[lo_i]:
            lo_i = i
        a = atr[i]
        if not np.isfinite(a):
            continue
        thr = k * a
        if direction >= 0 and hi_i < i and h[hi_i] - lo[i] >= thr:
            out.append(Pivot(TOP, hi_i, bars.times[hi_i], float(h[hi_i]), bars.close_time(i)))
            direction = DOWN
            lo_i = min(range(hi_i + 1, i + 1), key=lambda t: lo[t])
        elif direction <= 0 and lo_i < i and h[i] - lo[lo_i] >= thr:
            out.append(Pivot(BOTTOM, lo_i, bars.times[lo_i], float(lo[lo_i]), bars.close_time(i)))
            direction = UP
            hi_i = max(range(lo_i + 1, i + 1), key=lambda t: h[t])
    return out


def _extreme(bars: Bars, kind: int, first: int, last: int) -> int:
    seg = range(first, last + 1)
    if kind == TOP:
        return max(seg, key=lambda t: bars.high[t])
    return min(seg, key=lambda t: bars.low[t])


def _pivot_at(bars: Bars, kind: int, i: int, known_at: datetime) -> Pivot:
    price = bars.high[i] if kind == TOP else bars.low[i]
    return Pivot(kind, i, bars.times[i], float(price), known_at)


def tramo_timeline(bars: Bars, atr: Sequence[float], params: TramoParams = TramoParams(), tf: str = "") -> List[TramoPoint]:
    """La tendencia, la fase y el tramo de la TF a lo largo del tiempo: un punto en cada cierre en que cambian."""
    n = params.n
    sig_by_conf = {}
    for p in significant_pivots(bars, atr, params.k):
        k = bisect_right(bars.times, p.confirmed_at - bars.duration) - 1
        sig_by_conf.setdefault(k, []).append(p)
    frac_by_conf = {}
    for p in find_pivots(bars, n):
        frac_by_conf.setdefault(p.index + n, []).append(p)

    ref_top: Optional[Pivot] = None     # último techo significativo sin quebrar
    ref_bot: Optional[Pivot] = None
    streak_up = streak_down = 0
    trend, phase = 0, PHASE_TREND
    a: Optional[Pivot] = None
    tops: List[Pivot] = []              # fractales confirmados, para B
    bots: List[Pivot] = []
    points: List[TramoPoint] = []
    for i in range(len(bars)):
        at = bars.close_time(i)
        for p in frac_by_conf.get(i, []):
            (tops if p.kind == TOP else bots).append(p)
        for p in sig_by_conf.get(i, []):
            if p.kind == TOP:
                ref_top, streak_up = p, 0
                if trend == DOWN and phase == PHASE_RANGE and a is not None and p.index > a.index \
                        and p.price < a.price:
                    phase = PHASE_TREND   # techo más bajo dentro del tramo nuevo: el giro se armó
            else:
                ref_bot, streak_down = p, 0
                if trend == UP and phase == PHASE_RANGE and a is not None and p.index > a.index \
                        and p.price > a.price:
                    phase = PHASE_TREND
        prev_atr = atr[i - 1] if i > 0 else np.nan
        if np.isfinite(prev_atr):
            c = bars.close[i]
            streak_up = streak_up + 1 if ref_top is not None and c > ref_top.price + params.margin * prev_atr else 0
            streak_down = streak_down + 1 if ref_bot is not None and c < ref_bot.price - params.margin * prev_atr \
                else 0
            if streak_up >= params.closes:
                old_b = _leg_b(bars, a, trend, tops, bots, i) if trend == DOWN else None
                a = _pivot_at(bars, BOTTOM, _extreme(bars, BOTTOM, ref_top.index + 1, i), at)
                if trend == DOWN:
                    phase = PHASE_TREND if old_b is not None and a.price > old_b.price else PHASE_RANGE
                else:
                    phase = PHASE_TREND   # primer quiebre, o quiebre a favor: la estructura sigue
                trend, ref_top, streak_up = UP, None, 0
            elif streak_down >= params.closes:
                old_b = _leg_b(bars, a, trend, tops, bots, i) if trend == UP else None
                a = _pivot_at(bars, TOP, _extreme(bars, TOP, ref_bot.index + 1, i), at)
                if trend == UP:
                    phase = PHASE_TREND if old_b is not None and a.price < old_b.price else PHASE_RANGE
                else:
                    phase = PHASE_TREND
                trend, ref_bot, streak_down = DOWN, None, 0
        leg = None
        if trend != 0 and a is not None:
            b = _leg_b(bars, a, trend, tops, bots, i)
            if b is not None and b.index > a.index:
                leg = Leg(tf, a, b)
        protected = None
        if trend == UP and ref_bot is not None:
            protected = ref_bot.price
        elif trend == DOWN and ref_top is not None:
            protected = ref_top.price
        point = TramoPoint(at, trend, phase, leg, protected)
        if not points or (points[-1].trend, points[-1].phase, points[-1].leg, points[-1].protected) != \
                (point.trend, point.phase, point.leg, point.protected):
            points.append(point)
    return points


def _leg_b(bars: Bars, a: Optional[Pivot], trend: int, tops: Sequence[Pivot], bots: Sequence[Pivot],
           i: int) -> Optional[Pivot]:
    """B del tramo en la vela i: el fractal confirmado más extremo desde A o, si no hay, el extremo de las velas."""
    if a is None or trend == 0 or i <= a.index:
        return None
    if trend == UP:
        cands = [p for p in tops if p.index > a.index]
        if cands:
            return max(cands, key=lambda p: (p.price, -p.index))
        j = _extreme(bars, TOP, a.index + 1, i)
        return _pivot_at(bars, TOP, j, bars.close_time(j))
    cands = [p for p in bots if p.index > a.index]
    if cands:
        return min(cands, key=lambda p: (p.price, p.index))
    j = _extreme(bars, BOTTOM, a.index + 1, i)
    return _pivot_at(bars, BOTTOM, j, bars.close_time(j))


_BEFORE_ANY = TramoPoint(datetime.min, 0, PHASE_TREND, None, None)


def tramo_at(timeline: Sequence[TramoPoint], tau: datetime) -> TramoPoint:
    """El punto vigente en τ (el último con `at <= τ`)."""
    k = bisect_right([p.at for p in timeline], tau)
    return timeline[k - 1] if k else _BEFORE_ANY
