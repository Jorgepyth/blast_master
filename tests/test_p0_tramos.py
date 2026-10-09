"""Tests del motor de tramos v0.2 (core/p0_tramos.py) y de la combinación de la opción 2 (tools/p0_opcion2.py)."""
from datetime import datetime, timedelta

import numpy as np
import pytest

from core.candle_resolution import Candle
from core.p0_structure import BOTTOM, TOP, Bars
from core.p0_tramos import (
    DOWN,
    PHASE_RANGE,
    PHASE_TREND,
    UP,
    TramoParams,
    significant_pivots,
    tramo_at,
    tramo_timeline,
)
from tools import p0_opcion2 as o2
from tools import p0_sistematico as ps

T0 = datetime(2026, 6, 1)
H = timedelta(hours=1)
P = TramoParams(k=2.0, margin=0.5, closes=2)

# (máximo, mínimo, cierre). Con ATR = 1: piso en 0, techo 14 en 3, piso 11 en 6; en 8 una mecha pasa 14 (barrido); en 10
# y 11 dos cierres por encima de 14.5 (quiebre al alza, A = 11 en 6); techo 15.5 en 11, confirmado en 13 y 14.
BASE = [
    (10.5, 9.5, 10.0), (12.0, 10.5, 11.8), (13.0, 11.5, 12.8), (14.0, 12.5, 13.5), (13.6, 12.6, 13.0),
    (13.0, 11.8, 12.0), (12.5, 11.0, 11.4), (13.2, 11.6, 13.0), (14.6, 13.0, 14.3), (14.2, 13.4, 13.6),
    (15.0, 13.8, 14.8), (15.5, 14.6, 15.2), (15.2, 14.4, 14.6), (14.8, 13.6, 13.8), (14.0, 12.8, 13.0),
]
# Quiebre a la baja en V: rompe el piso 11 (de antes de B) sin techo más bajo → rango; después piso 9.5 y techo 12.4 más
# bajo que A → tendencia.
V_BREAK = [
    (13.0, 11.0, 11.2), (11.4, 10.0, 10.2), (10.5, 9.6, 9.8), (11.0, 9.5, 10.8), (12.0, 10.5, 11.8),
    (12.4, 11.2, 11.4), (11.6, 10.2, 10.4),
]
# Quiebre a la baja con el giro armado: piso 12.8 después de B, techo más bajo 14.9 y dos cierres debajo de 12.3.
LH_BREAK = [
    (14.0, 13.0, 13.8), (14.9, 13.6, 14.6), (14.6, 13.2, 13.4), (13.6, 12.6, 12.7), (12.8, 11.8, 12.0),
    (12.2, 11.2, 11.4),
]


def bars_of(rows):
    times = [T0 + i * H for i in range(len(rows))]
    h, lo, c = zip(*[(r[0], r[1], r[2]) for r in rows])
    return Bars.from_lists(times, h, lo, c, H)


def close_of(i):
    return T0 + (i + 1) * H


def test_small_wiggles_are_not_significant_pivots():
    rows = [(10.5, 9.5, 10), (11.5, 10.5, 11), (12.5, 11.5, 12), (12.2, 11.8, 12), (13.5, 12.5, 13),
            (15.0, 14.0, 14.5), (14.5, 13.5, 14), (13.5, 12.5, 13)]
    piv = significant_pivots(bars_of(rows), np.ones(len(rows)), 2.0)
    assert [(p.kind, p.index) for p in piv] == [(BOTTOM, 0), (TOP, 5)]   # el retroceso de 0.7 en la vela 3 no cuenta
    assert piv[1].confirmed_at == close_of(7)                            # 15 − 12.5 = 2.5 ≥ 2


def test_one_close_beyond_is_a_sweep_two_closes_with_margin_are_a_break():
    b = bars_of(BASE)
    tl = tramo_timeline(b, np.ones(len(BASE)), P, "1H")
    assert tramo_at(tl, close_of(9)).trend == 0     # la mecha de la vela 8 y su cierre en 14.3 < 14.5: barrido
    assert tramo_at(tl, close_of(10)).trend == 0    # un solo cierre más allá del margen
    pt = tramo_at(tl, close_of(11))                 # el segundo cierre: quiebre
    assert pt.trend == UP and pt.phase == PHASE_TREND
    assert (pt.leg.a.index, pt.leg.a.price) == (6, 11.0)   # el piso más bajo entre el techo quebrado y el quiebre
    assert pt.leg.b.price == 15.5


def test_b_is_the_confirmed_fractal():
    tl = tramo_timeline(bars_of(BASE), np.ones(len(BASE)), P, "1H")
    pt = tramo_at(tl, close_of(14))
    assert (pt.leg.b.index, pt.leg.b.price) == (11, 15.5) and pt.leg.b.confirmed_at == close_of(13)


def test_a_v_shaped_break_is_a_range_until_a_lower_high_forms():
    rows = BASE + V_BREAK
    tl = tramo_timeline(bars_of(rows), np.ones(len(rows)), P, "1H")
    pt = tramo_at(tl, close_of(17))
    assert pt.trend == DOWN and pt.phase == PHASE_RANGE   # rompió el piso 11, pero el techo es la B vieja (en V)
    assert pt.leg.a.price == 15.5
    pt = tramo_at(tl, close_of(21))
    assert pt.trend == DOWN and pt.phase == PHASE_TREND   # techo 12.4 < 15.5 confirmado: el giro se armó
    assert pt.leg.b.price == 9.5


def test_a_break_with_a_lower_high_already_formed_is_a_trend():
    rows = BASE + LH_BREAK
    tl = tramo_timeline(bars_of(rows), np.ones(len(rows)), P, "1H")
    pt = tramo_at(tl, close_of(20))
    assert pt.trend == DOWN and pt.phase == PHASE_TREND
    assert (pt.leg.a.index, pt.leg.a.price) == (16, 14.9)   # A = el techo más bajo, después del piso quebrado


def test_the_timeline_never_uses_unclosed_bars():
    rows = BASE + LH_BREAK
    full = tramo_timeline(bars_of(rows), np.ones(len(rows)), P, "1H")
    cut = tramo_timeline(bars_of(rows[:20]), np.ones(20), P, "1H")
    for i in range(20):
        assert tramo_at(full, close_of(i)) == tramo_at(cut, close_of(i))


@pytest.mark.parametrize("scores, expected", [
    ({"1H": -2, "4H": -1, "12H": 0, "1D": 0}, (-1, "Mid")),       # caso 1 de la relectura
    ({"1H": 0, "4H": 2, "12H": 0, "1D": 0}, (1, "Weak")),         # caso 2: 1H en rango, manda 4H
    ({"1H": -1, "4H": 2, "12H": 0, "1D": 0}, (0, None)),          # caso 3: 4H en contra, ninguna a favor
    ({"1H": -1, "4H": 1, "12H": 1, "1D": 1}, (0, None)),          # caso 5
    ({"1H": -1, "4H": 2, "12H": -2, "1D": -2}, (-1, "Mid")),      # caso 6: dos a favor, una en contra
    ({"1H": 1, "4H": 1, "12H": 2, "1D": 1}, (1, "Strong")),
    ({"1H": 0, "4H": 0, "12H": 0, "1D": 0}, (0, None)),
])
def test_combine_reads_bottom_up(scores, expected):
    assert o2.combine(scores) == expected


def test_opcion2_runs_on_a_market():
    rows = BASE + LH_BREAK + [(11.6, 10.8, 11.0)] * 3
    candles = {"1H": [Candle(T0 + i * H, (h + lo) / 2, h, lo, c) for i, (h, lo, c) in enumerate(rows)]}
    model = o2.Opcion2(ps.Market("SYN", candles), o2.Opcion2Params(P, expiry=10))
    out = model.predict_at(close_of(len(rows) - 1))
    assert set(out["scores"]) == {"1H"} and out["direction"] in (-1, 0, 1)
