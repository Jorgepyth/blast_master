"""
Tests sintéticos del motor de estructura de P0 sistemático (pre-registro v1.1 §6, D3–D5; RF-1 a RF-3; INV-3, INV-4).

Cada serie se arma vela por vela con (máximo, mínimo, cierre) en velas de 1 hora. Las velas de relleno no forman
fractales por sí solas, así que los pivotes de cada test son los que el test declara.
"""
from datetime import datetime, timedelta

import pytest

from core.p0_structure import (
    BEARISH,
    BOTTOM,
    BULLISH,
    INSUFFICIENT,
    RANGE,
    TOP,
    Bars,
    find_pivots,
    state_at,
    structure_timeline,
)

T0 = datetime(2026, 1, 5, 0, 0)
H = timedelta(hours=1)


def make_bars(rows, start=T0, step=H):
    """rows: lista de (high, low, close)."""
    times = [start + i * step for i in range(len(rows))]
    return Bars.from_lists(times, [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], step)


def close_of(i, start=T0, step=H):
    return start + (i + 1) * step


# Una estructura alcista limpia: techo 2 (13), piso 4 (9.5), techo 7 (15, HH), piso 9 (11, HL).
BULL_ROWS = [
    (10.0, 9.0, 9.5),    # 0
    (11.0, 10.0, 10.5),  # 1
    (13.0, 12.0, 12.5),  # 2  techo
    (12.0, 11.0, 11.5),  # 3
    (11.0, 9.5, 10.0),   # 4  piso
    (12.0, 10.0, 11.5),  # 5
    (14.0, 12.0, 13.5),  # 6
    (15.0, 13.0, 14.0),  # 7  techo HH
    (14.0, 12.5, 13.0),  # 8
    (13.0, 11.0, 12.0),  # 9  piso HL
    (13.5, 12.0, 13.0),  # 10
    (14.5, 12.5, 14.0),  # 11
]


def test_pivots_of_the_bull_series():
    bars = make_bars(BULL_ROWS)
    piv = [(p.kind, p.index, p.price) for p in find_pivots(bars, n=2)]
    assert piv == [(TOP, 2, 13.0), (BOTTOM, 4, 9.5), (TOP, 7, 15.0), (BOTTOM, 9, 11.0)]


def test_pivot_is_known_only_at_close_of_t_plus_n():
    """INV-4 / R8: el pivote de t vale desde el cierre de t+n, nunca antes."""
    bars = make_bars(BULL_ROWS)
    top = find_pivots(bars, n=2)[0]
    assert top.index == 2
    assert top.confirmed_at == close_of(4)
    # Con las velas cerradas hasta el cierre de la vela 3, el techo de la vela 2 no existe.
    assert all(p.index != 2 for p in find_pivots(bars.closed(close_of(3)), n=2))
    assert any(p.index == 2 for p in find_pivots(bars.closed(close_of(4)), n=2))


def test_fractal_tie_allowed_only_on_the_old_side():
    rows = [
        (10.0, 9.0, 9.5),
        (12.0, 11.0, 11.5),
        (12.0, 11.0, 11.5),  # 2: iguala al 1 (lado viejo): puede ser techo
        (11.0, 10.0, 10.5),
        (10.5, 9.8, 10.0),
        (13.0, 12.0, 12.5),
        (13.0, 12.0, 12.5),  # 6: iguala al 5 (lado viejo) pero el 7 también iguala: no es techo
        (13.0, 11.0, 12.0),
        (11.0, 10.0, 10.5),
    ]
    tops = [p.index for p in find_pivots(make_bars(rows), n=2) if p.kind == TOP]
    assert 2 in tops        # H2 >= H1 (empate del lado viejo) y H2 > H3, H4
    assert 1 not in tops    # H1 > H2 falla: el empate del lado nuevo no vale
    assert 6 not in tops and 5 not in tops


def test_bullish_state_and_leg():
    bars = make_bars(BULL_ROWS)
    tl = structure_timeline(bars, n=2, tf="1H")
    pt = state_at(tl, close_of(11))
    assert pt.state == BULLISH
    # D5: alcista → B = último techo (7), A = último piso antes de B (4).
    assert (pt.leg.b.index, pt.leg.a.index) == (7, 4)
    assert pt.leg.up
    # Antes del cierre de la vela 11 (confirmación del piso 9) no hay dos pisos: historia insuficiente.
    assert state_at(tl, close_of(10)).state == INSUFFICIENT


def test_insufficient_before_any_point():
    tl = structure_timeline(make_bars(BULL_ROWS), n=2, tf="1H")
    assert state_at(tl, T0).state == INSUFFICIENT
    assert state_at(tl, T0).leg is None


def _mirror(rows, pivot=20.0):
    """Espejo de precios: convierte una serie alcista en bajista."""
    return [(pivot - lo, pivot - hi, pivot - c) for hi, lo, c in rows]


def test_bearish_state_and_leg():
    bars = make_bars(_mirror(BULL_ROWS))
    pt = state_at(structure_timeline(bars, n=2, tf="1H"), close_of(11))
    assert pt.state == BEARISH
    # Bajista → B = último piso (7), A = último techo antes de B (4).
    assert (pt.leg.b.index, pt.leg.a.index) == (7, 4)
    assert not pt.leg.up


def test_range_when_labels_disagree_and_its_leg():
    # Techo 7 más bajo que el 2 (LH) y piso 9 más alto que el 4 (HL): rango.
    rows = list(BULL_ROWS)
    rows[6] = (12.0, 11.0, 11.5)
    rows[7] = (12.5, 11.8, 12.0)  # techo LH (12.5 < 13)
    rows[8] = (12.2, 11.5, 11.8)
    rows[9] = (12.0, 11.0, 11.5)  # piso HL (11 > 9.5)
    rows[10] = (12.3, 11.4, 12.0)
    rows[11] = (12.4, 11.6, 12.2)
    bars = make_bars(rows)
    piv = [(p.kind, p.index) for p in find_pivots(bars, n=2)]
    assert piv == [(TOP, 2), (BOTTOM, 4), (TOP, 7), (BOTTOM, 9)]
    pt = state_at(structure_timeline(bars, n=2, tf="1H"), close_of(11))
    assert pt.state == RANGE
    # Rango → B = último pivote (piso 9), A = pivote opuesto anterior (techo 7).
    assert (pt.leg.b.index, pt.leg.a.index) == (9, 7)


def test_two_tops_in_a_row_keep_the_higher_and_ties_keep_the_older():
    from core.p0_structure import alternating_sequence

    p = find_pivots(make_bars(BULL_ROWS), n=2)
    t2, b4, t7 = p[0], p[1], p[2]
    higher = t7.__class__(TOP, 8, t7.time + H, 16.0, t7.confirmed_at + H)
    equal = t7.__class__(TOP, 8, t7.time + H, 15.0, t7.confirmed_at + H)
    seq = alternating_sequence([t2, b4, t7, higher])
    assert [(e.pivot.index, e.pivot.price) for e in seq] == [(2, 13.0), (4, 9.5), (8, 16.0)]
    seq = alternating_sequence([t2, b4, t7, equal])
    assert [(e.pivot.index, e.pivot.price) for e in seq] == [(2, 13.0), (4, 9.5), (7, 15.0)]
    # Etiquetas: el techo que reemplaza se compara con el techo anterior de la secuencia (2): HH.
    assert alternating_sequence([t2, b4, t7, higher])[-1].label == "HH"


def test_exact_tie_inherits_the_previous_label():
    from core.p0_structure import alternating_sequence

    p = find_pivots(make_bars(BULL_ROWS), n=2)
    t2, b4, t7, b9 = p
    t_eq = t7.__class__(TOP, 12, t7.time + 5 * H, 15.0, t7.confirmed_at + 5 * H)
    seq = alternating_sequence([t2, b4, t7, b9, t_eq])
    assert seq[-1].pivot.index == 12 and seq[-1].label == "HH"  # empata con el techo 7 (HH) y hereda HH


# Alcista y después una vela que CIERRA debajo del último HL (11): invalidación.
INVALIDATION_ROWS = BULL_ROWS + [
    (15.5, 13.0, 14.0),  # 12: techo HH (15.5 contra 15), confirmado al cierre de la 14
    (13.0, 10.5, 11.2),  # 13: mecha debajo de 11, cierre encima (11.2): no invalida por cierre
    (12.0, 10.0, 10.8),  # 14: cierra debajo de 11 → invalida
    (11.5, 10.2, 11.0),  # 15
    (11.8, 10.6, 11.4),  # 16
]


def test_invalidation_by_close_not_by_wick():
    bars = make_bars(INVALIDATION_ROWS)
    tl = structure_timeline(bars, n=2, tf="1H")
    assert state_at(tl, close_of(13)).state == BULLISH   # la mecha de la vela 13 no invalida
    assert state_at(tl, close_of(14)).state == RANGE     # el cierre de la 14 sí
    # Al cierre de la 15 las etiquetas siguen diciendo HH (techo 12) + HL (piso 9), pero sin un techo y un piso nuevos
    # después de la invalidación el estado sigue en rango.
    assert state_at(tl, close_of(15)).state == RANGE


def test_invalidation_by_wick_is_the_sensitivity():
    bars = make_bars(INVALIDATION_ROWS)
    tl = structure_timeline(bars, n=2, tf="1H", wick_invalidation=True)
    assert state_at(tl, close_of(12)).state == BULLISH
    assert state_at(tl, close_of(13)).state == RANGE


def test_after_invalidation_range_until_a_new_top_and_bottom_are_confirmed():
    rows = INVALIDATION_ROWS + [
        (12.5, 11.0, 12.0),  # 17
        (13.5, 12.0, 13.0),  # 18
        (16.0, 13.0, 15.0),  # 19 techo nuevo (HH contra 15)
        (15.0, 13.5, 14.0),  # 20
        (14.5, 12.8, 13.5),  # 21
        (14.0, 12.5, 13.8),  # 22 piso nuevo (12.5 contra el piso 14 = 10.0: HL)
        (15.0, 13.0, 14.5),  # 23
        (16.5, 13.5, 16.0),  # 24
        (17.0, 14.0, 16.5),  # 25
    ]
    bars = make_bars(rows)
    tl = structure_timeline(bars, n=2, tf="1H")
    piv = [(p.kind, p.index) for p in find_pivots(bars, n=2)]
    assert (TOP, 19) in piv and (BOTTOM, 22) in piv
    # Techo 19 confirmado al cierre de la 21: sigue en rango (falta el piso nuevo).
    assert state_at(tl, close_of(21)).state == RANGE
    # Piso 22 confirmado al cierre de la 24: HH + HL → alcista otra vez.
    assert state_at(tl, close_of(24)).state == BULLISH


def test_timeline_is_identical_with_and_without_future_bars():
    """INV-3 / T2 en el motor de estructura: el estado en τ no cambia si se agregan velas posteriores a τ."""
    rows = INVALIDATION_ROWS
    full = make_bars(rows)
    for i in range(len(rows)):
        tau = close_of(i)
        a = state_at(structure_timeline(full, n=2, tf="1H"), tau)
        b = state_at(structure_timeline(full.closed(tau), n=2, tf="1H"), tau)
        assert (a.state, a.leg) == (b.state, b.leg), i


@pytest.mark.parametrize("n", [3, 5])
def test_other_fractal_sizes_confirm_later(n):
    bars = make_bars(BULL_ROWS + [(14.0, 13.0, 13.5)] * 4)
    for p in find_pivots(bars, n=n):
        assert p.confirmed_at == close_of(p.index + n)
