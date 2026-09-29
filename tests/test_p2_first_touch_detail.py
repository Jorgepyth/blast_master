"""
T23 (spec 002): `first_touch_detail()` en `core/p2_ground_truth.py`. Misma lógica de toque que
`first_touch_direction`, pero dice además en qué vela fue el toque, qué nivel tocó y si la vela tocó los
dos niveles (RF-4, RF-4b). `first_touch_direction` y `tests/test_p2_ground_truth.py` no cambian (RF-4f).
Fixtures sintéticos, sin DB.
"""
import random
from datetime import datetime, timedelta

import pytest

from core.p2_ground_truth import (
    LEVEL_INVALIDATION,
    LEVEL_VALIDATION,
    FirstTouchDetail,
    OhlcBar,
    first_touch_detail,
    first_touch_direction,
    infer_thesis_direction,
)

ENTRY = 100.0
T0 = datetime(2026, 5, 20, 12, 0)
QUIET = (101.0, 99.0)  # (high, low) que no toca ninguno de los niveles de estos fixtures (90 y 110)


def _path(*high_low):
    """Velas de 1M consecutivas desde T0, cada una dada como (high, low)."""
    return [OhlcBar(time=T0 + timedelta(minutes=i), high=h, low=l) for i, (h, l) in enumerate(high_low)]


def test_fields_follow_the_plan_order():
    # plan §3.2, paso 5: (dirección, incompleto, índice, nivel, ambiguo)
    assert FirstTouchDetail._fields == ("direction", "incomplete", "bar_index", "level", "ambiguous")


# --- índice y nivel ------------------------------------------------------------------

@pytest.mark.parametrize("evp, si, touch, expected_direction, expected_level", [
    (110.0, 90.0, (110.5, 100.0), "long", LEVEL_VALIDATION),     # long, valida por arriba
    (110.0, 90.0, (101.0, 89.5), "short", LEVEL_INVALIDATION),   # long, invalida por abajo
    (90.0, 110.0, (101.0, 89.5), "short", LEVEL_VALIDATION),     # short, valida por abajo
    (90.0, 110.0, (110.5, 100.0), "long", LEVEL_INVALIDATION),   # short, invalida por arriba
])
def test_reports_the_bar_index_and_the_level_touched(evp, si, touch, expected_direction, expected_level):
    thesis = infer_thesis_direction(ENTRY, evp, si)
    path = _path(QUIET, QUIET, QUIET, touch, QUIET)

    detail = first_touch_detail(thesis, path, ENTRY, evp, si)

    assert detail == FirstTouchDetail(direction=expected_direction, incomplete=False, bar_index=3,
                                      level=expected_level, ambiguous=False)
    # Con el índice, el llamador saca la hora del toque: la apertura de esa vela (N19).
    assert path[detail.bar_index].time == T0 + timedelta(minutes=3)


def test_a_touch_on_the_first_bar_is_index_zero():
    detail = first_touch_detail("long", _path((110.5, 99.0), QUIET), ENTRY, 110.0, 90.0)
    assert (detail.bar_index, detail.level) == (0, LEVEL_VALIDATION)


def test_touching_exactly_at_the_level_counts():
    # high == nivel de arriba y low == nivel de abajo cuentan como toque (>= y <=), igual que first_touch_direction.
    up = first_touch_detail("long", _path(QUIET, (110.0, 100.0)), ENTRY, 110.0, 90.0)
    down = first_touch_detail("long", _path(QUIET, (100.0, 90.0)), ENTRY, 110.0, 90.0)
    assert (up.bar_index, up.level) == (1, LEVEL_VALIDATION)
    assert (down.bar_index, down.level) == (1, LEVEL_INVALIDATION)


def test_only_the_first_touch_counts():
    path = _path(QUIET, (95.0, 89.0), (111.0, 100.0))  # invalida en la vela 1; el toque de la 2 ya no importa
    detail = first_touch_detail("long", path, ENTRY, 110.0, 90.0)
    assert (detail.direction, detail.bar_index, detail.level) == ("short", 1, LEVEL_INVALIDATION)


# --- caso ambiguo (RF-4b) --------------------------------------------------------------

@pytest.mark.parametrize("evp, si, both", [
    (110.0, 90.0, (111.0, 89.0)),   # long
    (90.0, 110.0, (111.0, 89.0)),   # short
    (110.0, 90.0, (110.0, 90.0)),   # los dos toques justo en el nivel
])
def test_a_bar_that_touches_both_levels_is_ambiguous_and_keeps_its_index(evp, si, both):
    # El llamador necesita el índice para refinar esa vela con una TF más fina (plan §3.2, paso 2).
    thesis = infer_thesis_direction(ENTRY, evp, si)
    path = _path(QUIET, QUIET, both, (111.0, 100.0))  # el toque limpio de la vela 3 no se usa

    detail = first_touch_detail(thesis, path, ENTRY, evp, si)

    assert detail == FirstTouchDetail(direction=None, incomplete=True, bar_index=2, level=None, ambiguous=True)


# --- sin toque --------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    [],                    # camino vacío
    _path(QUIET, QUIET),   # ningún toque en el camino disponible
])
def test_without_a_touch_there_is_no_index_nor_level(path):
    detail = first_touch_detail("long", path, ENTRY, 110.0, 90.0)
    assert detail == FirstTouchDetail(direction=None, incomplete=True, bar_index=None, level=None, ambiguous=False)


def test_without_a_thesis_the_path_is_not_walked():
    # Niveles del mismo lado del precio (RF-4d): no hay tesis y no se recorre, aunque la vela toque los dos.
    thesis = infer_thesis_direction(ENTRY, 105.0, 108.0)
    assert thesis is None
    detail = first_touch_detail(thesis, _path((120.0, 80.0)), ENTRY, 105.0, 108.0)
    assert detail == FirstTouchDetail(direction=None, incomplete=True, bar_index=None, level=None, ambiguous=False)


# --- misma lógica que first_touch_direction (RF-4f) ---------------------------------------

def _touches(high_low, upper, lower):
    high, low = high_low
    return high >= upper, low <= lower


def test_always_agrees_with_first_touch_direction_on_random_paths():
    # Caminos al azar (semilla fija) con toques limpios, dobles, justo en el nivel, sin toque y sin tesis.
    rng = random.Random(20260929)
    seen = set()
    for _ in range(3000):
        evp, si = rng.choice([(110.0, 90.0), (90.0, 110.0), (105.0, 108.0)])
        thesis = infer_thesis_direction(ENTRY, evp, si)
        bars = [(rng.choice([101.0, 105.0, 110.0, 111.0, 115.0]), rng.choice([85.0, 89.0, 90.0, 95.0, 99.0]))
                for _ in range(rng.randint(0, 8))]
        path = _path(*bars)
        upper, lower = max(evp, si), min(evp, si)

        detail = first_touch_detail(thesis, path, ENTRY, evp, si)

        assert tuple(detail[:2]) == first_touch_direction(thesis, path, ENTRY, evp, si)
        if thesis is None:
            assert detail == FirstTouchDetail(None, True, None, None, False)
            seen.add("no_thesis")
            continue
        if detail.bar_index is None:
            assert not detail.ambiguous and detail.level is None and detail.incomplete
            assert not any(any(_touches(b, upper, lower)) for b in bars)
            seen.add("no_touch")
            continue
        # Ninguna vela anterior al índice tocó nada.
        assert not any(any(_touches(b, upper, lower)) for b in bars[:detail.bar_index])
        touched_upper, touched_lower = _touches(bars[detail.bar_index], upper, lower)
        if detail.ambiguous:
            assert touched_upper and touched_lower
            assert detail.direction is None and detail.incomplete and detail.level is None
            seen.add("ambiguous")
        else:
            assert touched_upper != touched_lower
            touched_level = upper if touched_upper else lower
            assert detail.level == (LEVEL_VALIDATION if touched_level == evp else LEVEL_INVALIDATION)
            assert (detail.direction == thesis) == (detail.level == LEVEL_VALIDATION)
            seen.add(detail.level)
    # El muestreo sí pasó por todos los casos.
    assert seen == {"no_thesis", "no_touch", "ambiguous", LEVEL_VALIDATION, LEVEL_INVALIDATION}
