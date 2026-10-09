"""
Tests sintéticos del motor de niveles, zonas y eventos de P0 sistemático (pre-registro v1.1 §7, D1, D2, D6, D7; RF-4).
"""
from datetime import datetime, timedelta
from itertools import combinations

import pytest

from core.candle_resolution import Candle
from core.p0_fib import (
    AMBIGUOUS,
    BARRIER_FRACTION,
    OPEN,
    P0_LEVELS,
    PENDING,
    PLACEBO_LEVELS,
    PLACEBO_OF,
    REJECTION,
    RUPTURE,
    ZONE_DELTA,
    Path,
    build_event_path,
    detect_events,
    level_price,
    zone,
)

T0 = datetime(2026, 6, 1, 0, 0)
H = timedelta(hours=1)


def hourly_path(rows, start=T0):
    """rows: (high, low) por vela de 1H."""
    times = [start + i * H for i in range(len(rows))]
    return Path.from_bars(["1H"] * len(rows), times, [t + H for t in times], [r[0] for r in rows],
                          [r[1] for r in rows])


def test_level_prices_follow_d1_in_both_directions():
    # Tramo que sube: A = 100, B = 200. 0 = B, 1 = A.
    assert level_price(100.0, 200.0, 0.0) == 200.0
    assert level_price(100.0, 200.0, 1.0) == 100.0
    assert level_price(100.0, 200.0, 0.382) == pytest.approx(161.8)
    # Tramo que baja: A = 200, B = 100.
    assert level_price(200.0, 100.0, 0.382) == pytest.approx(138.2)
    assert level_price(200.0, 100.0, 0.0) == 100.0


def test_zone_width_is_delta_of_the_leg_and_no_zones_overlap():
    lo, hi = zone(100.0, 200.0, 0.382)
    assert (lo, hi) == pytest.approx((161.8 - 2.5, 161.8 + 2.5))
    assert ZONE_DELTA == 0.025 and BARRIER_FRACTION == 0.10
    levels = sorted(P0_LEVELS + PLACEBO_LEVELS)
    for r1, r2 in combinations(levels, 2):
        a, b = zone(100.0, 200.0, r1), zone(100.0, 200.0, r2)
        assert a[1] < b[0] or b[1] < a[0], (r1, r2)


def test_placebo_pairs_are_the_ordered_ones():
    assert PLACEBO_OF == {0.382: 0.300, 0.5: 0.441, 0.618: 0.559, 0.0: 0.10, 1.0: 0.90}
    assert set(PLACEBO_OF.values()) == set(PLACEBO_LEVELS)


def _one(path, r, ref_close, **kw):
    kw.setdefault("start_time", T0)
    return detect_events(path, a=100.0, b=200.0, levels=(r,), ref_close=ref_close, **kw)[r]


def test_entry_from_above_then_rejection():
    path = hourly_path([(199, 190), (190, 175), (176, 163), (172, 160)])
    ev = _one(path, 0.382, ref_close=195.0)
    assert ev.entry_index == 2 and ev.arrival == 1
    # Rechazo: se aleja D = 10 hacia arriba desde 161.8 → 171.8; la vela 3 lo toca.
    assert ev.outcome == REJECTION and ev.resolved_index == 3


def test_rupture_inside_the_entry_bar():
    path = hourly_path([(199, 190), (190, 175), (176, 150)])
    ev = _one(path, 0.382, ref_close=195.0)
    assert ev.outcome == RUPTURE and ev.resolved_index == 2


def test_entry_bar_extreme_on_the_arrival_side_does_not_count():
    # La vela de entrada abre arriba de la barrera de rechazo (171.8): ese máximo es anterior a la entrada.
    path = hourly_path([(199, 190), (180, 163), (165, 150)])
    ev = _one(path, 0.382, ref_close=195.0)
    assert ev.entry_index == 1
    assert ev.outcome == RUPTURE and ev.resolved_index == 2


def test_rupture_can_be_farther_than_rejection():
    # Tramo 100→200, nivel 0.382 = 161.8. La vela 2 entra desde arriba; la 3 baja a 150 (pasa 151.8, la ruptura a
    # 0.10, pero no 141.8, la ruptura a 0.20) y la 4 rebota a 172 (rechazo a 0.10: 171.8).
    path = hourly_path([(199, 190), (190, 175), (176, 163), (165, 150), (172, 160)])
    symmetric = _one(path, 0.382, ref_close=195.0)
    assert symmetric.outcome == RUPTURE and symmetric.resolved_index == 3
    asymmetric = _one(path, 0.382, ref_close=195.0, rupture_barrier=20.0)
    assert asymmetric.outcome == REJECTION and asymmetric.resolved_index == 4
    # Por defecto (sin rupture_barrier) es exactamente la regla del pre-registro.
    assert _one(path, 0.382, ref_close=195.0, rupture_barrier=None) == symmetric


def test_bar_touching_both_barriers_is_ambiguous():
    path = hourly_path([(199, 190), (176, 163), (175, 150)])
    ev = _one(path, 0.382, ref_close=195.0)
    assert ev.outcome == AMBIGUOUS


def test_open_and_pending():
    rows = [(199, 190), (176, 163)] + [(168, 158)] * 5
    path = hourly_path(rows)
    assert _one(path, 0.382, ref_close=195.0, horizon_hours=3).outcome == OPEN
    assert _one(path, 0.382, ref_close=195.0, horizon_hours=100).outcome == PENDING


def test_reference_inside_the_zone_arms_only_after_leaving_it():
    # El cierre de referencia (162) está dentro de la zona de 0.382: la vela 0 todavía toca la zona y no es entrada;
    # la 1 queda entera arriba (arma, llegada desde arriba) y la 2 entra.
    path = hourly_path([(165, 160), (175, 166), (170, 161), (172, 165)])
    ev = _one(path, 0.382, ref_close=162.0)
    assert ev.entry_index == 2 and ev.arrival == 1
    assert ev.outcome == REJECTION


def test_arrival_from_below():
    # Precio por debajo de la zona de 0.618 (138.2 ± 2.5) que sube hasta ella: llega desde abajo.
    path = hourly_path([(130, 125), (137, 129), (129, 120)])
    ev = _one(path, 0.618, ref_close=128.0)
    assert ev.entry_index == 1 and ev.arrival == -1
    # Desde abajo, el rechazo es alejarse D hacia abajo (138.2 − 10 = 128.2): la vela 2 lo toca.
    assert ev.outcome == REJECTION


def test_extended_leg_generates_no_new_events():
    # Tramo que sube (A 100, B 200). La vela 0 entra a la zona de 0 (B) desde abajo; la 1 supera B: extendido.
    path = hourly_path([(198, 195), (201, 196), (190, 175), (176, 163), (172, 160)])
    events = detect_events(path, a=100.0, b=200.0, levels=(0.0, 0.382), ref_close=196.0, start_time=T0)
    assert events[0.0] is not None and events[0.0].entry_index == 0 and events[0.0].arrival == -1
    assert events[0.382] is None


def test_entries_only_while_the_leg_is_current():
    path = hourly_path([(199, 190), (190, 175), (176, 163), (172, 160)])
    assert _one(path, 0.382, ref_close=195.0, stop_time=T0 + 2 * H) is None
    assert _one(path, 0.382, ref_close=195.0, stop_time=T0 + 3 * H).entry_index == 2


def test_tau_cuts_the_path():
    path = hourly_path([(199, 190), (190, 175), (176, 163), (172, 160)])
    # En τ = cierre de la vela 2: la entrada existe pero el resultado todavía no se conoce.
    ev = _one(path, 0.382, ref_close=195.0, tau=T0 + 3 * H)
    assert ev.entry_index == 2 and ev.outcome == PENDING
    # En τ = cierre de la vela 1: no hubo entrada todavía.
    assert _one(path, 0.382, ref_close=195.0, tau=T0 + 2 * H) is None


def test_horizon_counts_hours_from_the_entry_bar():
    rows = [(199, 190), (176, 163)] + [(168, 158)] * 3 + [(172, 160)]
    path = hourly_path(rows)
    # Entrada en la vela 1; la 5 rechaza. Con horizonte de 5 h (velas 1 a 5) se resuelve; con 4 h queda abierto.
    assert _one(path, 0.382, ref_close=195.0, horizon_hours=5).outcome == REJECTION
    assert _one(path, 0.382, ref_close=195.0, horizon_hours=4).outcome == OPEN


def _candles(start, n, step, high=10.0, low=9.0):
    return [Candle(start + i * step, 9.5, high, low, 9.5) for i in range(n)]


def test_event_path_falls_back_to_coarse_bars_before_the_ladder():
    four = timedelta(hours=4)
    start = datetime(2026, 3, 1, 0, 0)
    bars = {
        "4H": _candles(start, 30, four),                        # 2026-03-01 00:00 a 2026-03-06 00:00
        "1H": _candles(start + 12 * four, 40, H, high=11.0),     # desde 2026-03-03 00:00
    }
    path = build_event_path(bars, max_tf="4H")
    tfs = list(path.tf)
    assert tfs[:12] == ["4H"] * 12
    assert tfs[12:52] == ["1H"] * 40
    # Sin huecos ni superposición: cada vela abre donde cerró la anterior.
    for i in range(1, 52):
        assert path.time[i] == path.end[i - 1]
    # Cuando la escalera se termina, vuelve el 4H.
    assert tfs[52] == "4H" and path.time[52] == path.end[51]
    # Un tramo de 1H nunca usa velas de 4H.
    path_1h = build_event_path(bars, max_tf="1H")
    assert set(path_1h.tf) == {"1H"}


def test_coarse_flag_marks_events_resolved_on_coarse_bars():
    four = timedelta(hours=4)
    start = datetime(2026, 3, 1, 0, 0)
    coarse = [Candle(start, 0, 199, 190, 195), Candle(start + four, 0, 176, 163, 170),
              Candle(start + 2 * four, 0, 172, 165, 170)]
    fine = [Candle(start + 3 * four + i * H, 0, 172, 165, 170) for i in range(5)]
    path = build_event_path({"4H": coarse, "1H": fine}, max_tf="4H")
    ev = detect_events(path, a=100.0, b=200.0, levels=(0.382,), ref_close=195.0, start_time=start)[0.382]
    assert ev.entry_index == 1 and ev.outcome == REJECTION and ev.coarse
