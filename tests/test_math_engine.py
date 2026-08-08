import pytest
from core.math_engine import calculate_algebraic_metrics


def test_long_winning_trade():
    # entry=100, sl=90 (risk dist=10), tp=120, close=110 (win)
    result = calculate_algebraic_metrics(
        direction="Long", ep=100.0, sl=90.0, cp=110.0, tp=120.0,
        size=2.0, mae=0.5, mfe=2.0,
    )
    assert float(result["r_r"]) == 2.0          # (120-100)/10
    assert float(result["r_multiple"]) == 1.0   # (110-100)/10
    assert float(result["captured_mfe"]) == 0.5  # r_multiple(1.0) / mfe(2.0)
    assert float(result["captured_mae"]) == 0.0  # winning trade -> no MAE capture
    assert float(result["pnl"]) == 20.0          # (110-100)*2


def test_short_winning_trade():
    # entry=100, sl=110 (risk dist=10), tp=80, close=90 (win, direction inverted)
    result = calculate_algebraic_metrics(
        direction="Short", ep=100.0, sl=110.0, cp=90.0, tp=80.0,
        size=1.0, mae=0.5, mfe=1.0,
    )
    assert float(result["r_r"]) == 2.0          # (100-80)/10
    assert float(result["r_multiple"]) == 1.0   # (100-90)/10
    assert float(result["captured_mfe"]) == 1.0  # r_multiple(1.0) / mfe(1.0)
    assert float(result["pnl"]) == 10.0          # (100-90)*1


def test_losing_trade_captures_mae_not_mfe():
    # Long trade that closes below entry -> r_multiple negative
    result = calculate_algebraic_metrics(
        direction="Long", ep=100.0, sl=90.0, cp=95.0, tp=120.0,
        size=1.0, mae=1.0, mfe=2.0,
    )
    assert float(result["r_multiple"]) == -0.5   # (95-100)/10
    # r_multiple < 0 -> captured_mfe forced to 0
    assert float(result["captured_mfe"]) == 0.0
    # r_multiple < 0 and mae > 0 -> captured_mae = abs(r_multiple)/mae
    assert float(result["captured_mae"]) == 0.5


def test_zero_mfe_forces_captured_mfe_zero_even_on_win():
    result = calculate_algebraic_metrics(
        direction="Long", ep=100.0, sl=90.0, cp=110.0, tp=120.0,
        size=1.0, mae=0.5, mfe=0.0,
    )
    assert float(result["r_multiple"]) == 1.0
    assert float(result["captured_mfe"]) == 0.0


@pytest.mark.parametrize("kwargs", [
    dict(direction="Long", ep=0.0, sl=90.0, cp=110.0, tp=120.0, size=1.0, mae=0.5, mfe=2.0),
    dict(direction="Long", ep=100.0, sl=0.0, cp=110.0, tp=120.0, size=1.0, mae=0.5, mfe=2.0),
    dict(direction="Long", ep=100.0, sl=100.0, cp=110.0, tp=120.0, size=1.0, mae=0.5, mfe=2.0),
    dict(direction="Long", ep=100.0, sl=90.0, cp=110.0, tp=120.0, size=0.0, mae=0.5, mfe=2.0),
    dict(direction="Long", ep=100.0, sl=90.0, cp=110.0, tp=120.0, size=-1.0, mae=0.5, mfe=2.0),
])
def test_invalid_inputs_raise_value_error(kwargs):
    with pytest.raises(ValueError):
        calculate_algebraic_metrics(**kwargs)
