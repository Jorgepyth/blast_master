"""
Propuestas del Tactical Audit con las velas del banco (spec 002):
- T45: MAE y MFE en R de una orden llenada, con la TF más fina que cubre el trade (1M, si no 5M, si no 15M), contando
  las mechas de las velas que se solapan con [entrada, salida], redondeados a 2 decimales y con tope 10. Motivos:
  `no_interval`, `zero_r`, `pending_candles`, `no_history` (RF-9 a RF-9d, N8).
- T46: `Could hit TP?`: "yes" si el precio tocó el TP antes que el SL, desde la entrada y con tope `MAX_HORIZON`; "no"
  si tocó antes el SL o llegó al tope. Sin propuesta si los toca en la misma vela, si el TP está del lado contrario
  (`invalid_tp`) o si R es 0 (RF-10 a RF-10d, N12).

Escenario long: entrada 100 a las 00:10, SL 95 (R = 5), TP 110. Velas de 1M planas (máximo 101, mínimo 99).
"""
from datetime import datetime, timedelta

import pytest

from config.auto_resolution import REASON_AMBIGUOUS, REASON_NO_HISTORY, REASON_PENDING_CANDLES
from core.candle_resolution import (
    REASON_INVALID_TP,
    REASON_NO_INTERVAL,
    REASON_ZERO_R,
    Candle,
    TpCheck,
    TradeExcursion,
    could_hit_tp,
    trade_excursion,
)

T0 = datetime(2026, 6, 1, 0, 0)
ENTRY = T0 + timedelta(minutes=10)
EXIT = T0 + timedelta(minutes=60)


def _bars(timeframe_minutes, n, specials=None, start=T0):
    specials = specials or {}
    out = []
    for i in range(n):
        high, low = specials.get(i, (101.0, 99.0))
        out.append(Candle(start + timedelta(minutes=timeframe_minutes * i), 100.0, high, low, 100.0))
    return out


def _mirror(bars):
    return [Candle(c.time, 200 - c.open, 200 - c.low, 200 - c.high, 200 - c.close) for c in bars]


# --- T45: MAE / MFE en R ---------------------------------------------------------------------------------------------

def test_mae_and_mfe_in_r_with_the_wicks_of_the_candles_of_the_trade():
    bank = {"1M": _bars(1, 300, {20: (101.0, 97.0), 40: (112.0, 99.0), 70: (130.0, 80.0)})}  # el 70 es después
    assert trade_excursion("long", ENTRY, EXIT, 100.0, 95.0, bank) == TradeExcursion(0.6, 2.4, "1M")
    short = {"1M": _mirror(bank["1M"])}
    assert trade_excursion("short", ENTRY, EXIT, 100.0, 105.0, short) == TradeExcursion(0.6, 2.4, "1M")


def test_the_values_are_rounded_capped_at_10_and_never_negative():
    bank = {"1M": _bars(1, 300, {30: (160.0, 99.5), 31: (101.0, 99.3333)})}
    excursion = trade_excursion("long", ENTRY, EXIT, 98.0, 95.0, bank)   # R = 3; nunca bajó de la entrada (98)
    assert excursion == TradeExcursion(0.0, 10.0, "1M")
    assert trade_excursion("long", ENTRY, EXIT, 100.0, 97.0, bank).mae_r == 0.33  # (100 - 99) / 3, a 2 decimales


def test_the_candles_that_overlap_the_trade_include_the_entry_and_exit_ones_but_not_the_next():
    bank = {"1M": _bars(1, 300, {10: (101.0, 96.0), 59: (106.0, 99.0), 60: (150.0, 50.0)})}
    assert trade_excursion("long", ENTRY, EXIT, 100.0, 95.0, bank) == TradeExcursion(0.8, 1.2, "1M")


def test_without_1m_covering_the_trade_it_uses_5m_and_then_15m():
    one_minute_late = _bars(1, 300, start=T0 + timedelta(minutes=20))   # el 1M empieza después de la entrada
    five = _bars(5, 60, {4: (104.0, 99.0)})
    fifteen = _bars(15, 20, {1: (110.0, 99.0)})
    assert trade_excursion("long", ENTRY, EXIT, 100.0, 95.0, {"1M": one_minute_late, "5M": five}) == (
        TradeExcursion(0.2, 0.8, "5M"))
    assert trade_excursion("long", ENTRY, EXIT, 100.0, 95.0, {"1M": one_minute_late, "15M": fifteen}) == (
        TradeExcursion(0.2, 2.0, "15M"))


@pytest.mark.parametrize("exit_time, entry_price, stop_loss, reason", [
    (ENTRY, 100.0, 95.0, REASON_NO_INTERVAL),                         # salida igual a la entrada
    (ENTRY - timedelta(minutes=1), 100.0, 95.0, REASON_NO_INTERVAL),  # salida antes de la entrada
    (EXIT, 100.0, 100.0, REASON_ZERO_R),                              # entrada igual al SL
])
def test_no_proposal_without_an_interval_or_without_r(exit_time, entry_price, stop_loss, reason):
    bank = {"1M": _bars(1, 300)}
    assert trade_excursion("long", ENTRY, exit_time, entry_price, stop_loss, bank) == TradeExcursion(
        None, None, None, reason)


def test_a_trade_the_bank_does_not_cover_is_pending_or_without_history():
    short_bank = {"1M": _bars(1, 30)}                                    # termina a las 00:30
    assert trade_excursion("long", ENTRY, EXIT, 100.0, 95.0, short_bank).reason == REASON_PENDING_CANDLES
    late_bank = {"1M": _bars(1, 300, start=T0 + timedelta(hours=1))}     # empieza después de la entrada
    assert trade_excursion("long", ENTRY, EXIT, 100.0, 95.0, late_bank).reason == REASON_NO_HISTORY


# --- T46: Could hit TP? ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("specials, answer", [
    ({30: (110.5, 99.0), 50: (101.0, 94.0)}, "yes"),   # el TP antes que el SL
    ({30: (101.0, 94.0), 50: (110.5, 99.0)}, "no"),    # el SL antes que el TP
])
def test_could_hit_tp_is_whether_the_tp_came_before_the_sl(specials, answer):
    bank = {"1M": _bars(1, 300, specials)}
    assert could_hit_tp("long", ENTRY, 100.0, 95.0, 110.0, bank) == TpCheck(answer, "1M")
    short = {"1M": _mirror(bank["1M"])}
    assert could_hit_tp("short", ENTRY, 100.0, 105.0, 90.0, short) == TpCheck(answer, "1M")


def test_tp_and_sl_in_the_same_candle_gives_no_proposal():
    bank = {"1M": _bars(1, 300, {30: (110.5, 94.0)})}
    assert could_hit_tp("long", ENTRY, 100.0, 95.0, 110.0, bank) == TpCheck(None, "1M", REASON_AMBIGUOUS)


@pytest.mark.parametrize("direction, stop_loss, take_profit", [("long", 95.0, 99.0), ("long", 95.0, 100.0),
                                                                ("short", 105.0, 101.0)])
def test_a_tp_on_the_wrong_side_is_invalid_tp(direction, stop_loss, take_profit):
    bank = {"1M": _bars(1, 300)}
    assert could_hit_tp(direction, ENTRY, 100.0, stop_loss, take_profit, bank) == TpCheck(None, None,
                                                                                          REASON_INVALID_TP)


def test_zero_r_gives_no_could_hit_tp_either():
    assert could_hit_tp("long", ENTRY, 100.0, 100.0, 110.0, {"1M": _bars(1, 300)}).reason == REASON_ZERO_R


def test_reaching_the_horizon_without_tp_or_sl_is_no_and_running_out_of_candles_is_pending():
    flat = {"1M": _bars(1, 600), "1H": _bars(60, 10)}
    assert could_hit_tp("long", ENTRY, 100.0, 95.0, 110.0, flat, max_horizon=2) == TpCheck("no", None)
    assert could_hit_tp("long", ENTRY, 100.0, 95.0, 110.0, {"1M": _bars(1, 300)}).reason == REASON_PENDING_CANDLES
    late = {"1M": _bars(1, 300, start=T0 + timedelta(hours=1))}
    assert could_hit_tp("long", ENTRY, 100.0, 95.0, 110.0, late).reason == REASON_NO_HISTORY


def test_could_hit_tp_walks_only_1m_5m_and_15m():
    # N8: si el 1M se acaba, la vela de 1H que toca el TP no cuenta; falta velas finas, así que queda pendiente.
    bank = {"1M": _bars(1, 60), "1H": _bars(60, 10, {3: (110.5, 99.0)})}
    assert could_hit_tp("long", ENTRY, 100.0, 95.0, 110.0, bank).reason == REASON_PENDING_CANDLES
