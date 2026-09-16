"""
Fase 1 -- tests de tools/pnl_calculator.py y del segmentador de secciones de
tools/derive_contract_multiplier.py.
"""
import os
import socket
from decimal import Decimal

import pytest

from tools import pnl_calculator
from tools.pnl_calculator import (
    UnknownSymbolError,
    UnverifiedSymbolError,
    leg_usd,
    quantify,
    resolve_spec,
)
from tools.derive_contract_multiplier import (
    compute_positions_multipliers,
    read_rows,
    segment_sections,
    summarize,
)

REAL_REPORT = os.path.join(
    os.path.dirname(__file__), "..", "tools", "migration_data", "ReportHistory-87050257.xlsx"
)


# --------------------------------------------------------------------------- #
# pnl_calculator -- calculo
# --------------------------------------------------------------------------- #
def test_golden_xauusd_via_alias():
    """Trade real del reporte MT5 (fila 8): volumen 0.01, entry 2742.92, SL 2734.92,
    TP 2759.12, contract_size XAUUSD = 100.

    Perdida teorica esperada: EXACTAMENTE $8.00  (= 0.01 * 8.00 * 100).
    Ganancia teorica esperada: EXACTAMENTE $16.20 (= 0.01 * 16.20 * 100).

    El resultado REAL ejecutado fue -$8.14: la diferencia de $0.14 es spread /
    slippage entre el precio de SL (2734.92) y el fill real (2734.78), que el
    calculo teorico no modela a proposito.
    """
    res = quantify("XAUUSDT.P", 2742.92, Decimal("0.01"), 2734.92, 2759.12)
    assert res["symbol"] == "XAUUSD"
    assert res["contract_size"] == Decimal("100")
    assert res["loss_usd"] == Decimal("8.00")
    assert res["gain_usd"] == Decimal("16.20")


def test_alias_and_canonical_agree():
    via_alias = quantify("XAUUSDT.P", 2742.92, Decimal("0.01"), 2734.92, 2759.12)
    canonical = quantify("XAUUSD", 2742.92, Decimal("0.01"), 2734.92, 2759.12)
    assert via_alias == canonical


def test_all_return_values_are_decimal():
    res = quantify("XAUUSDT.P", 2742.92, Decimal("0.01"), 2734.92, 2759.12)
    for key in ("contract_size", "loss_usd", "gain_usd", "rr"):
        assert isinstance(res[key], Decimal), f"{key} no es Decimal: {type(res[key])}"


def test_rr_is_none_when_no_loss():
    res = quantify("XAUUSD", 2742.92, Decimal("0.01"), 2742.92, 2759.12)
    assert res["loss_usd"] == 0
    assert res["rr"] is None


def test_leg_usd_no_float_noise():
    # size pasado como string -> nunca toca float
    assert leg_usd(2742.92, 2734.92, "0.01", 100) == Decimal("8.00")
    assert str(leg_usd("2742.92", "2734.92", "0.01", "100")) == "8.0000"


# --------------------------------------------------------------------------- #
# pnl_calculator -- rechazo de simbolos NO VERIFICADO / desconocidos
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("asset", ["BTCUSDT.P", "BTCUSD", "US100", "NAS100"])
def test_unverified_symbol_raises_and_never_returns_number(asset):
    with pytest.raises(UnverifiedSymbolError) as exc:
        quantify(asset, 78000, Decimal("0.01"), 77000, 80000)
    msg = str(exc.value)
    assert asset in msg
    assert "Specification" in msg


def test_unknown_symbol_raises():
    with pytest.raises(UnknownSymbolError) as exc:
        quantify("EURUSD", 1.10, Decimal("0.01"), 1.09, 1.12)
    assert "EURUSD" in str(exc.value)


def test_resolve_spec_alias():
    spec = resolve_spec("XAUUSDT.P")
    assert spec["symbol"] == "XAUUSD"
    assert spec["contract_size"] == Decimal("100")


def test_resolve_spec_none_asset():
    with pytest.raises(UnknownSymbolError):
        resolve_spec(None)


# --------------------------------------------------------------------------- #
# config/contract_specs.py -- import y regla dura del placeholder
# --------------------------------------------------------------------------- #
def test_contract_specs_imports_clean():
    import importlib

    import config.contract_specs as cs

    importlib.reload(cs)
    for symbol, spec in cs.CONTRACT_SPECS.items():
        size = spec.get("contract_size")
        assert size is None or isinstance(size, Decimal), (
            f"{symbol}.contract_size debe ser Decimal o None, es {type(size)}"
        )
    # aliases apuntan a claves reales
    for src, dst in cs.SYMBOL_ALIASES.items():
        assert dst in cs.CONTRACT_SPECS


# --------------------------------------------------------------------------- #
# pnl_calculator -- cero llamadas de red
# --------------------------------------------------------------------------- #
def test_no_network(monkeypatch):
    def _boom(*a, **k):  # pragma: no cover - solo se dispara si algo abre un socket
        raise AssertionError("pnl_calculator intento abrir un socket")

    monkeypatch.setattr(socket, "socket", _boom)
    res = quantify("XAUUSDT.P", 2742.92, Decimal("0.01"), 2734.92, 2759.12)
    assert res["loss_usd"] == Decimal("8.00")


# --------------------------------------------------------------------------- #
# derive_contract_multiplier -- segmentador de secciones
# --------------------------------------------------------------------------- #
def _row(**cols):
    return dict(cols)


def _synthetic_stacked_report():
    """4 secciones apiladas con anchos de columna distintos a proposito
    (Positions 13 cols, Orders ~10, Deals 14, Open Positions 13) para verificar
    que no hay contaminacion de columnas entre secciones."""
    rows = [
        _row(A="Trade History Report"),
        _row(A="Account:", D="123 (USD, X, real)"),
        # -- Positions --
        _row(A="Positions"),
        _row(A="Time", B="Position", C="Symbol", D="Type", E="Volume", F="Price",
             G="S / L", H="T / P", I="Time", J="Price", K="Commission", L="Swap", M="Profit"),
        _row(A="2025.01.27 18:02:12", B="1055560286", C="XAUUSD", D="buy", E="0.01",
             F="2742.92", G="2734.92", H="2759.12", I="2025.01.27 18:51:23", J="2734.78",
             K="0", L="0", M="-8.14"),
        _row(A="2025.02.10 10:00:00", B="1055560300", C="XAUUSD", D="sell", E="0.03",
             F="2851.29", G="2860", H="2840", I="2025.02.10 12:00:00", J="2849.79",
             K="0", L="0", M="4.5"),
        _row(),  # fila en blanco -> corta la seccion
        # -- Orders (header mas angosto) --
        _row(A="Orders"),
        _row(A="Open Time", B="Order", C="Symbol", D="Type", E="Volume", F="Price",
             G="S / L", H="T / P", I="Time", J="State"),
        _row(A="2025.01.22 14:36:19", B="1054885913", C="XAUUSD", D="buy limit",
             E="0.01 / 0", F="2751.21", G="2744.94", H="2763.86", I="2025.01.22 15:34:54",
             J="canceled"),
        # -- Deals (header mas ancho, 14 cols) --
        _row(A="Deals"),
        _row(A="Time", B="Deal", C="Symbol", D="Type", E="Direction", F="Volume",
             G="Price", H="Order", I="Commission", J="Fee", K="Swap", L="Profit",
             M="Balance", N="Comment"),
        _row(A="2025.01.21 19:41:00", B="1253547174", C="", D="balance", E="",
             I="0", J="0", K="0", L="250", M="250", N="Deposit"),
        _row(A="", B="", I="0", J="0", K="-1", L="416", M="297"),  # subtotal -> corta (B vacio)
        # -- Open Positions --
        _row(A="Open Positions"),
        _row(A="Time", B="Position", C="Symbol", D="Type", E="Volume", F="Price",
             G="S / L", H="T / P", I="Market Price", J="Swap", L="Profit", M="Comment"),
        _row(A="2026.09.02 03:53:14", B="1209617621", C="BTCUSD", D="sell", E="0.03",
             F="77162.04", G="79000", H="75000", I="78512.5", J="-0.97", L="-40.51"),
        _row(A="Balance:", D="297.93"),  # bloque de cuenta -> no es data (A no es titulo, B vacio)
    ]
    return rows


def test_segmenter_splits_all_four_sections_without_bleed():
    sections = segment_sections(_synthetic_stacked_report())
    assert set(sections) == {"Positions", "Orders", "Deals", "Open Positions"}

    pos = sections["Positions"]
    assert pos["header"]["M"] == "Profit"
    assert len(pos["rows"]) == 2
    assert all(r["C"] == "XAUUSD" for r in pos["rows"])
    # ninguna fila de Positions se colo de otra seccion
    assert all("balance" != r.get("D") for r in pos["rows"])

    orders = sections["Orders"]
    assert orders["header"]["J"] == "State"
    assert len(orders["rows"]) == 1
    assert orders["rows"][0]["D"] == "buy limit"

    deals = sections["Deals"]
    assert deals["header"]["E"] == "Direction"  # no "Volume" (esa es la col de Positions)
    assert len(deals["rows"]) == 1  # el subtotal con B vacio queda fuera

    op = sections["Open Positions"]
    assert len(op["rows"]) == 1
    assert op["rows"][0]["C"] == "BTCUSD"
    assert all(r["A"] != "Balance:" for r in op["rows"])


def test_positions_multiplier_buy_and_sell_sign():
    sections = segment_sections(_synthetic_stacked_report())
    mults = compute_positions_multipliers(sections["Positions"])
    assert list(mults) == ["XAUUSD"]
    # buy: -8.14 / (0.01 * (2734.78 - 2742.92)) = 100
    # sell: 4.5 / (0.03 * (2849.79 - 2851.29)) = -100 -> flip -> +100
    for m in mults["XAUUSD"]:
        assert abs(m - Decimal("100")) < Decimal("0.01")


# --------------------------------------------------------------------------- #
# derive_contract_multiplier -- contra el reporte MT5 real (si esta presente)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not os.path.exists(REAL_REPORT), reason="ReportHistory-87050257.xlsx no esta en tools/migration_data/"
)
def test_real_report_segments_and_verifies_xauusd():
    rows = read_rows(REAL_REPORT)
    sections = segment_sections(rows)
    assert set(sections) >= {"Positions", "Orders", "Deals", "Open Positions"}

    mults = compute_positions_multipliers(sections["Positions"])
    summary = summarize(mults, min_n=30, max_rel_std=Decimal("0.001"))

    assert summary["XAUUSD"]["confidence"] == "VERIFICADO"
    assert summary["XAUUSD"]["n"] >= 30
    assert abs(summary["XAUUSD"]["contract_size"] - Decimal("100")) < Decimal("0.001")

    # BTCUSD / NAS100: muestra insuficiente -> NO VERIFICADO, contract_size None
    for sym in ("BTCUSD", "NAS100"):
        if sym in summary:
            assert summary[sym]["confidence"] == "NO VERIFICADO"
            assert summary[sym]["contract_size"] is None
