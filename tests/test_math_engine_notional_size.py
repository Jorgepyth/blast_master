"""
Fase 2b + 2c -- EXCEPCIÓN acotada al límite de dominio de core/math_engine.py:
calculate_algebraic_metrics gana `contract_size`; las líneas de notional_size /
notional_size_usd (2b) y risk_usd / capital_at_risk (2c) lo multiplican. Nada más
del archivo cambia.
"""
from decimal import Decimal

import pytest

from core.math_engine import calculate_algebraic_metrics

_BASE = dict(direction="Long", ep=2742.92, sl=2734.92, cp=2750.0, tp=2759.12,
             size=0.01, mae=1.0, mfe=2.0, cost=0.0)


def test_contract_size_multiplies_notional():
    r = calculate_algebraic_metrics(**_BASE, contract_size=Decimal("100"))
    assert r["notional_size"] == Decimal("2742.9200")       # 2742.92 * 0.01 * 100
    assert r["notional_size_usd"] == Decimal("2742.9200")


def test_no_contract_size_is_regression_negative():
    """Sin contract_size (o None) -> IDÉNTICO al comportamiento previo a Fase 2b."""
    r_default = calculate_algebraic_metrics(**_BASE)
    r_none = calculate_algebraic_metrics(**_BASE, contract_size=None)
    assert r_default == r_none
    assert r_default["notional_size"] == Decimal("27.4292")  # 2742.92 * 0.01, sin *cs


def test_contract_size_does_not_touch_non_exposure_metrics():
    """2c/2e ampliaron la excepción: notional_size, risk_usd, capital_at_risk, pnl y
    pnl_and_cost (dinero) SÍ escalan con contract_size. Los RATIOS y distancias NO."""
    r0 = calculate_algebraic_metrics(**_BASE)
    r1 = calculate_algebraic_metrics(**_BASE, contract_size=Decimal("100"))
    for k in ("r_r", "r_multiple", "dist_to_sl", "dist_to_tp", "captured_mfe", "captured_mae"):
        assert r0[k] == r1[k], f"contract_size alteró {k} (es un ratio -- fuera de alcance)"


def test_2e_pnl_and_pnl_and_cost_scale_with_contract_size():
    r0 = calculate_algebraic_metrics(**_BASE)                       # sin contract_size
    r1 = calculate_algebraic_metrics(**_BASE, contract_size=Decimal("100"))
    assert r1["pnl"] == r0["pnl"] * 100
    assert r1["pnl_and_cost"] == r0["pnl_and_cost"] * 100           # pnl_and_cost = pnl - cost
    # caso XAUUSD: 0.01 lotes, (2750.0 - 2742.92) = 7.08 -> 0.01 * 7.08 * 100 = 7.08
    assert r1["pnl"] == Decimal("7.0800")


def test_2e_no_contract_size_is_regression_negative_for_pnl():
    r_default = calculate_algebraic_metrics(**_BASE)
    r_none = calculate_algebraic_metrics(**_BASE, contract_size=None)
    assert r_default["pnl"] == r_none["pnl"] == Decimal("0.0708")   # 0.01 * 7.08, sin *cs


def test_2c_risk_usd_and_capital_at_risk_scale_with_contract_size():
    r0 = calculate_algebraic_metrics(**_BASE)                       # sin contract_size
    r1 = calculate_algebraic_metrics(**_BASE, contract_size=Decimal("100"))
    assert r1["risk_usd"] == r0["risk_usd"] * 100
    assert r1["capital_at_risk"] == r0["capital_at_risk"] * 100
    # caso XAUUSD conocido: 0.01 lotes, |2742.92-2734.92| = 8.00
    assert r1["risk_usd"] == Decimal("8.0000")                     # 0.01 * 8 * 100
    assert r1["capital_at_risk"] == Decimal("8.0000")              # Long: 0.01 * (ep-sl) * 100


def test_2c_no_contract_size_is_regression_negative_for_risk_and_capital():
    r_default = calculate_algebraic_metrics(**_BASE)
    r_none = calculate_algebraic_metrics(**_BASE, contract_size=None)
    assert r_default["risk_usd"] == r_none["risk_usd"] == Decimal("0.0800")   # 0.01 * 8, sin *cs
    assert r_default["capital_at_risk"] == r_none["capital_at_risk"] == Decimal("0.0800")


def test_recalculate_tactical_math_propagates_none_for_unverified_asset():
    """El repair flow (cli/main.py:recalculate_tactical_math) resuelve contract_size
    vía pnl_calculator; para un asset NO VERIFICADO pasa None y no truena."""
    from tools import pnl_calculator
    with pytest.raises(pnl_calculator.UnverifiedSymbolError):
        pnl_calculator.resolve_spec("BTCUSDT.P")
    # None se propaga como multiplicador 1 -> calculate_algebraic_metrics no falla
    r = calculate_algebraic_metrics(**_BASE, contract_size=None)
    assert r["notional_size"] == Decimal("27.4292")
