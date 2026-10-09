"""
tools/p0_opcion2.py — P0 por estructura, opción 2 (`docs/p0-reglas-opcion2.md`), versión de prueba.

Une el motor de tramos v0.2 (`core/p0_tramos.py`) con la lectura de cada TF de la evaluación (D8, `reading_from_events`)
y la regla de combinación del operador (R10):
- **R9, lectura por TF:** los eventos de Fibonacci del tramo desde que existe (A y B conocidos) hasta τ, con la misma
  regla D8 de la evaluación. Un evento resuelto hace más de `expiry` velas de la TF deja de contar: la TF queda en rango.
- **R10, combinación:** manda la TF más chica con dirección (1H, después 4H, 12H y 1D). Si hay más TF mayores en contra
  que a favor, no opina. Fuerza: Weak si ninguna mayor confirma, Mid si alguna, Strong si todas.

No toca los modelos A–H de la evaluación ni su pre-registro. Solo lectura de velas.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Mapping, Optional, Tuple

import numpy as np

from core.p0_fib import P0_LEVELS, PLACEBO_LEVELS, REJECTION, RUPTURE, detect_events
from core.p0_structure import BEARISH, BULLISH, INSUFFICIENT, RANGE
from core.p0_tramos import PHASE_RANGE, UP, TramoParams, tramo_at, tramo_timeline
from tools import p0_sistematico as ps

ORDER = ("1H", "4H", "12H", "1D")   # de la más chica a la más grande


@dataclass(frozen=True)
class Opcion2Params:
    tramos: TramoParams = TramoParams()
    expiry: Optional[int] = 10        # velas de la TF; None = un evento nunca vence


# Modelos congelados (`docs/p0-reglas-opcion2.md` §9), con sus valores escritos acá y no tomados de los valores por
# defecto: cambiar un valor por defecto no cambia un modelo congelado. Un modelo nuevo es una entrada nueva; la receta de
# uno existente no se toca (el registro detecta el cambio por la huella).
MODELS_BY_NAME: Dict[str, Opcion2Params] = {
    "O2-v1.0": Opcion2Params(TramoParams(k=2.0, margin=0.0, closes=2, n=2), expiry=10),
}


def model_recipe(params: Opcion2Params) -> dict:
    """Lo que define el cálculo de un modelo de la opción 2."""
    t = params.tramos
    return {"k": t.k, "margin": t.margin, "closes": t.closes, "n": t.n, "expiry": params.expiry,
            "levels": "p0", "reading": "D8", "combine": "R10", "order": list(ORDER)}


class Opcion2:
    """Las líneas de tiempo del motor de tramos de un mercado, calculadas una vez por TF."""

    def __init__(self, market: "ps.Market", params: Opcion2Params = Opcion2Params()):
        self.market = market
        self.params = params
        self.timelines = {tf: tramo_timeline(bars, ps.atr14(bars), params.tramos, tf)
                          for tf, bars in market.bars.items() if tf in ORDER}

    def read_tf(self, tf: str, tau: datetime, level_set: str = "p0") -> "ps.TfReading":
        """Estado, tramo y situación D8 de una TF en τ, con el tramo del motor v0.2 y el vencimiento de eventos."""
        pt = tramo_at(self.timelines[tf], tau)
        leg = pt.leg
        if pt.trend == 0 or leg is None:
            return ps.TfReading(tf, INSUFFICIENT, None, ps.SIT_INSUFFICIENT)
        if pt.phase == PHASE_RANGE:
            return ps.TfReading(tf, RANGE, leg, ps.SIT_RANGE)
        state = BULLISH if pt.trend == UP else BEARISH
        levels = P0_LEVELS if level_set == "p0" else PLACEBO_LEVELS
        start = max(leg.a.confirmed_at, leg.b.confirmed_at)
        found = detect_events(self.market.paths[tf], leg.a.price, leg.b.price, levels,
                              self.market.close_at(tf, start), start_time=start, tau=tau,
                              ext_from_time=leg.b.confirmed_at, rupture_barrier=self.market.rupture_distance(leg))
        events = {r: ev for r, ev in found.items() if ev is not None}
        reading = ps.reading_from_events(tf, state, leg, events, level_set)
        last = reading.last_event
        if self.params.expiry is not None and last is not None and last.outcome in (REJECTION, RUPTURE) \
                and last.resolved_at is not None:
            age = (tau - last.resolved_at) / self.market.bars[tf].duration
            if age > self.params.expiry:
                return ps.TfReading(tf, RANGE, leg, ps.SIT_RANGE, last)
        return reading

    def predict_at(self, tau: datetime) -> Dict[str, object]:
        readings = {tf: self.read_tf(tf, tau) for tf in ORDER if tf in self.timelines}
        scores = {tf: ps.tf_score(r, True) for tf, r in readings.items()}
        direction, strength = combine(scores)
        return {"direction": direction, "strength": strength, "scores": scores, "readings": readings}


def combine(scores: Mapping[str, int]) -> Tuple[int, Optional[str]]:
    """R10: (dirección, fuerza) a partir del puntaje de cada TF. Dirección 0 = no opina."""
    st = {tf: int(np.sign(scores.get(tf, 0))) for tf in ORDER}
    for i, tf in enumerate(ORDER):
        d = st[tf]
        if d == 0:
            continue
        higher = ORDER[i + 1:]
        agree = sum(st[t] == d for t in higher)
        oppose = sum(st[t] == -d for t in higher)
        if oppose > agree:
            return 0, None
        if agree == 0:
            return d, "Weak"
        return d, "Strong" if agree == len(higher) else "Mid"
    return 0, None
