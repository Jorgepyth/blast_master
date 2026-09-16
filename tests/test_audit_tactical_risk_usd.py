"""
Fase 2a -- el validator de cli/schemas/audit_tactical.py delega risk_usd /
notional_size_usd en tools/pnl_calculator (contract_size por instrumento).
NO VERIFICADO / desconocido / sin asset -> None + warn-log, nunca bloquea.
"""
import logging
from decimal import Decimal

import pytest

from cli.schemas.audit_tactical import TacticalAudit


def _mk(**over):
    base = dict(tactical_id="t-test", asset="XAUUSDT.P",
                entry_price=2742.92, stop_loss=2734.92, take_profit=2759.12, size=0.01)
    base.update(over)
    return TacticalAudit(**base)


def test_xauusdt_p_uses_contract_size_100():
    """Trade real (reporte MT5 fila 8): size 0.01, entry 2742.92, SL 2734.92,
    contract_size XAUUSD = 100 -> risk_usd == $8.00 EXACTO.

    Pre-fix (fórmula vieja `size * |ep - sl|`, sin contract_size) daba 0.08 -- se
    documenta como la regresión que este cambio corrige."""
    ta = _mk()
    assert ta.risk_usd == Decimal("8.00")            # 0.01 * 8.00 * 100
    assert ta.risk_usd != Decimal("0.08")            # el valor pre-fix
    assert ta.notional_size_usd == Decimal("2742.92")  # 2742.92 * 0.01 * 100


def test_size_as_decimal_input_still_exact():
    ta = _mk(size=Decimal("0.01"))
    assert ta.risk_usd == Decimal("8.00")


@pytest.mark.parametrize("asset", ["BTCUSDT.P", "US100"])
def test_unverified_symbol_nulls_risk_and_warns(asset, caplog):
    with caplog.at_level(logging.WARNING, logger="cli.schemas.audit_tactical"):
        ta = _mk(asset=asset, entry_price=100.0, stop_loss=99.0, take_profit=102.0,
                 closing_price=98.0)
    assert ta.risk_usd is None
    assert ta.notional_size_usd is None
    assert any(asset in r.message and "NO VERIFICADO" in r.message for r in caplog.records)
    # el resto del tactical_audit (campos NO monetarios) se calcula igual (no bloquea)
    assert ta.trade_decision == "Long"
    assert ta.r_multiple is not None
    # Fase 2f: notional_size / capital_at_risk / pnl / pnl_and_cost siguen el mismo
    # criterio que risk_usd -> None cuando el símbolo no está VERIFICADO (antes
    # notional_size / capital_at_risk / pnl_and_cost salían con un número sin contract_size)
    assert ta.notional_size is None
    assert ta.capital_at_risk is None
    assert ta.pnl is None
    assert ta.pnl_and_cost is None


def test_asset_none_nulls_risk_and_warns_no_crash(caplog):
    with caplog.at_level(logging.WARNING, logger="cli.schemas.audit_tactical"):
        ta = _mk(asset=None)
    assert ta.risk_usd is None
    assert ta.notional_size_usd is None
    assert any("asset" in r.message.lower() for r in caplog.records)
    assert ta.trade_decision is not None  # no crasheó, siguió calculando


def test_unknown_symbol_nulls_risk():
    ta = _mk(asset="EURUSD")
    assert ta.risk_usd is None
    assert ta.notional_size_usd is None


def test_notional_size_now_applies_contract_size():
    """Fase 2f: notional_size (sin _usd) YA aplica contract_size en el validator
    (antes era ep*size crudo = 27.4292 -- se documenta como la regresión que este
    cambio corrige)."""
    ta = _mk()
    assert ta.notional_size == Decimal("2742.92")     # 2742.92 * 0.01 * 100
    assert ta.notional_size != Decimal("27.4292")     # el valor pre-fix (sin *100)


# --------------------------------------------------------------------------- #
# Fase 2f -- notional_size / capital_at_risk / pnl / pnl_and_cost aplican
# contract_size en el validator (antes 100x mal para XAUUSDT.P).
# --------------------------------------------------------------------------- #
def test_2f_wizard_row_894cd7c9_values():
    """Réplica sintética del trade real 894cd7c9 (cuenta 001, XAUUSDT.P, size 0.01):
    entry 4433.919 / SL 4401.112 / TP 4500 / close 4401.112 (pegó el stop).
    contract_size XAUUSD = 100.

    Persistido pre-Fase-2f (validator sin contract_size):
        notional_size    = 44.33919
        capital_at_risk  = 0.32807
        pnl_and_cost     = -0.32807   (persistido real -1.18807: llevaba un cost != 0)
    Correcto (con contract_size):
        notional_size    = 4433.919
        capital_at_risk  = 32.807
        pnl_and_cost     = -32.807
    """
    ta = _mk(entry_price=4433.919, stop_loss=4401.112, take_profit=4500.0,
             closing_price=4401.112, size=0.01, cost=0.0)
    assert ta.trade_decision == "Long"
    assert ta.notional_size == Decimal("4433.919")
    assert ta.capital_at_risk == Decimal("32.807")
    assert ta.pnl == Decimal("-32.807")
    assert ta.pnl_and_cost == Decimal("-32.807")
    assert ta.risk_usd == Decimal("32.807")
    # valores pre-fix (regresión que se corrige)
    assert ta.notional_size != Decimal("44.33919")
    assert ta.capital_at_risk != Decimal("0.32807")


def test_2f_lot_other_than_0_01_scales_by_size_and_contract_size():
    """Guarda anti-regresión: TODOS los ejemplos de Fase 2a/2f usaron size=0.01,
    donde size·contract_size == 1 y enmascara si `* _szd` faltara. Con size=0.05
    el factor es 5 -> risk_usd 8.00 pasa a 40.00 (no se queda en 8.00)."""
    ta = _mk(size=Decimal("0.05"), closing_price=2750.0)
    assert ta.risk_usd == Decimal("40.00")            # abs(2742.92-2734.92) * 0.05 * 100
    assert ta.risk_usd != Decimal("8.00")             # el valor si `* _szd` faltara
    assert ta.notional_size == Decimal("13714.60")    # 2742.92 * 0.05 * 100
    assert ta.capital_at_risk == Decimal("40.00")     # 0.05 * 8 * 100
    assert ta.pnl == Decimal("35.40")                 # (2750-2742.92) * 0.05 * 100
    assert ta.pnl_and_cost == Decimal("35.40")


def test_2f_unverified_symbol_nulls_all_money_fields():
    """Símbolo NO VERIFICADO -> los 4 campos monetarios adicionales quedan None
    (no un número sin contract_size), mismo patrón ya probado para risk_usd."""
    ta = _mk(asset="BTCUSDT.P", entry_price=79000.0, stop_loss=78000.0,
             take_profit=81000.0, closing_price=77500.0, size=0.05)
    assert ta.notional_size is None
    assert ta.capital_at_risk is None
    assert ta.pnl is None
    assert ta.pnl_and_cost is None
    assert ta.risk_usd is None
    assert ta.notional_size_usd is None
