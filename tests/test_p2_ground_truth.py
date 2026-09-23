"""
P2_systematic — tests del ground truth geométrico (Opción B). Fixtures
sintéticos únicamente, sin tocar ninguna DB real. Cubre los 5 casos mínimos
pedidos: long, short, anomalía (mismo lado), sin toque dentro del path
disponible, y determinismo.
"""
from datetime import datetime, timedelta

from core.p2_ground_truth import OhlcBar, first_touch_direction, infer_thesis_direction

_ENTRY_PRICE = 100.0
_T0 = datetime(2026, 5, 20, 12, 0, 0)


def _bar(offset_hours: int, high: float, low: float) -> OhlcBar:
    return OhlcBar(time=_T0 + timedelta(hours=offset_hours), high=high, low=low)


def test_long_thesis_validation_touched_first():
    edge_validation_price = 110.0  # arriba de entry -> thesis "long"
    structural_invalidation = 90.0
    thesis = infer_thesis_direction(_ENTRY_PRICE, edge_validation_price, structural_invalidation)
    assert thesis == "long"

    path = [
        _bar(1, high=102.0, low=99.0),
        _bar(2, high=105.0, low=98.0),
        _bar(3, high=111.0, low=104.0),  # toca validation (110) primero
        _bar(4, high=95.0, low=85.0),    # tocaría invalidation, pero ya se resolvió antes
    ]
    direction, incompleto = first_touch_direction(
        thesis, path, _ENTRY_PRICE, edge_validation_price, structural_invalidation
    )
    assert direction == "long"
    assert incompleto is False


def test_short_thesis_invalidation_touched_first_returns_opposite():
    edge_validation_price = 90.0   # abajo de entry -> thesis "short"
    structural_invalidation = 110.0  # arriba de entry
    thesis = infer_thesis_direction(_ENTRY_PRICE, edge_validation_price, structural_invalidation)
    assert thesis == "short"

    path = [
        _bar(1, high=103.0, low=97.0),
        _bar(2, high=108.0, low=96.0),
        _bar(3, high=111.0, low=95.0),  # toca invalidation (110) primero -> tesis invalidada
    ]
    direction, incompleto = first_touch_direction(
        thesis, path, _ENTRY_PRICE, edge_validation_price, structural_invalidation
    )
    assert direction == "long"  # opuesto a la tesis "short"
    assert incompleto is False


def test_anomaly_same_side_levels_returns_none_and_incomplete():
    # Ambos niveles arriba de entry_price -> anomalía de datos.
    edge_validation_price = 105.0
    structural_invalidation = 108.0
    thesis = infer_thesis_direction(_ENTRY_PRICE, edge_validation_price, structural_invalidation)
    assert thesis is None

    path = [_bar(1, high=120.0, low=90.0)]  # irrelevante, no debería ni recorrerse
    direction, incompleto = first_touch_direction(
        thesis, path, _ENTRY_PRICE, edge_validation_price, structural_invalidation
    )
    assert direction is None
    assert incompleto is True


def test_no_touch_within_available_path_is_incomplete():
    edge_validation_price = 110.0
    structural_invalidation = 90.0
    thesis = infer_thesis_direction(_ENTRY_PRICE, edge_validation_price, structural_invalidation)
    assert thesis == "long"

    # Simula el path ya recortado al tope de 2,160 velas (~90 días H1, ver
    # jupyter/p2_systematic_task_plan.md) sin que ninguno de los dos
    # niveles haya sido tocado.
    path = [_bar(h, high=105.0, low=95.0) for h in range(1, 50)]
    direction, incompleto = first_touch_direction(
        thesis, path, _ENTRY_PRICE, edge_validation_price, structural_invalidation
    )
    assert direction is None
    assert incompleto is True


def test_determinism_same_fixture_same_result_across_runs():
    edge_validation_price = 110.0
    structural_invalidation = 90.0
    path = [
        _bar(1, high=102.0, low=99.0),
        _bar(2, high=111.0, low=104.0),
    ]

    thesis_1 = infer_thesis_direction(_ENTRY_PRICE, edge_validation_price, structural_invalidation)
    result_1 = first_touch_direction(
        thesis_1, path, _ENTRY_PRICE, edge_validation_price, structural_invalidation
    )

    thesis_2 = infer_thesis_direction(_ENTRY_PRICE, edge_validation_price, structural_invalidation)
    result_2 = first_touch_direction(
        thesis_2, path, _ENTRY_PRICE, edge_validation_price, structural_invalidation
    )

    assert thesis_1 == thesis_2
    assert result_1 == result_2
