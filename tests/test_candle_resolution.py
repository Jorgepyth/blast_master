"""
T24 (spec 002): `core/candle_resolution.py`, parte 1. Cobertura del banco por TF de la escalera y los códigos
`no_history` y `pending_candles` (RF-4c, RF-4g, N28; plan.md §3.1, casos límite). Velas sintéticas, sin DB ni disco.
"""
from datetime import datetime, timedelta

import pytest

from config.auto_resolution import REASON_NO_HISTORY, REASON_PENDING_CANDLES
from core.candle_resolution import (
    LADDER,
    LADDER_MINUTES,
    BankCoverage,
    TimeframeCoverage,
    anchor_missing_reason,
    bank_coverage,
    untouched_missing_reason,
)
from core.p2_ground_truth import OhlcBar, first_touch_detail

T0 = datetime(2026, 6, 1, 0, 0)


def _bars(timeframe, start, n, high=101.0, low=99.0):
    step = timedelta(minutes=LADDER_MINUTES[timeframe])
    return [OhlcBar(time=start + i * step, high=high, low=low) for i in range(n)]


# --- escalera ----------------------------------------------------------------------

def test_the_ladder_goes_from_the_finest_to_the_coarsest_timeframe():
    assert LADDER == ("1M", "5M", "15M", "30M", "1H")  # plan.md §3.1, paso 1


def test_ladder_durations_agree_with_the_backtest_table():
    from tools.p2_backtest import TIMEFRAME_MINUTES
    assert LADDER_MINUTES == {tf: TIMEFRAME_MINUTES[tf] for tf in LADDER}


# --- cobertura por TF -----------------------------------------------------------------

def test_coverage_of_a_timeframe_runs_from_its_first_open_to_the_close_of_its_last_candle():
    coverage = bank_coverage({"15M": _bars("15M", T0, 4)})
    assert coverage.by_timeframe == {"15M": TimeframeCoverage("15M", start=T0, end=T0 + timedelta(hours=1))}


def test_coverage_ignores_timeframes_outside_the_ladder_and_empty_ones():
    coverage = bank_coverage({
        "4H": _bars("1H", T0, 3),  # 4H no está en la escalera: el resolvedor no la usa
        "5M": [],
        "1H": _bars("1H", T0, 2),
    })
    assert list(coverage.by_timeframe) == ["1H"]


def test_coverage_does_not_depend_on_the_order_of_the_candles():
    bars = _bars("1M", T0, 10)
    assert bank_coverage({"1M": list(reversed(bars))}) == bank_coverage({"1M": bars})


def test_the_bank_starts_at_its_oldest_candle_and_ends_at_its_newest_close_across_timeframes():
    coverage = bank_coverage({
        "1M": _bars("1M", T0 + timedelta(days=30), 60),   # el 1M empieza más tarde (poco historial en MT5)
        "1H": _bars("1H", T0, 24 * 31),                     # el 1H viene de antes y llega más lejos
    })
    assert coverage.start == T0
    assert coverage.end == T0 + timedelta(days=31)
    assert BankCoverage({}).start is None and BankCoverage({}).end is None


def test_covering_lists_the_timeframes_that_cover_an_instant_from_the_finest():
    coverage = bank_coverage({
        "1H": _bars("1H", T0, 48),
        "1M": _bars("1M", T0 + timedelta(hours=10), 120),  # cubre de las 10:00 a las 12:00
        "15M": _bars("15M", T0, 96),
    })
    assert coverage.covering(T0 + timedelta(hours=11)) == ["1M", "15M", "1H"]
    assert coverage.covering(T0 + timedelta(hours=10)) == ["1M", "15M", "1H"]  # la apertura cuenta
    assert coverage.covering(T0 + timedelta(hours=12)) == ["15M", "1H"]          # el cierre del último 1M no
    assert coverage.covering(T0 + timedelta(hours=30)) == ["1H"]
    assert coverage.covering(T0 + timedelta(hours=48)) == []


# --- no_history y pending_candles en el ancla (RF-4g, RF-4c) ---------------------------------

def test_an_anchor_before_the_oldest_candle_in_every_timeframe_is_no_history():
    coverage = bank_coverage({"1M": _bars("1M", T0, 60), "1H": _bars("1H", T0, 24)})
    assert anchor_missing_reason(T0 - timedelta(minutes=1), coverage) == REASON_NO_HISTORY


def test_an_anchor_before_the_1m_history_but_inside_the_1h_one_is_not_no_history():
    coverage = bank_coverage({
        "1M": _bars("1M", T0 + timedelta(days=30), 60),
        "1H": _bars("1H", T0, 24 * 31),
    })
    assert anchor_missing_reason(T0 + timedelta(days=1), coverage) is None
    assert anchor_missing_reason(T0, coverage) is None  # justo en la apertura de la vela más vieja


def test_an_anchor_the_bank_has_not_reached_yet_is_pending_candles():
    # Un análisis de hoy, con el banco exportado por última vez ayer: se resuelve con el próximo export.
    coverage = bank_coverage({"1H": _bars("1H", T0, 24)})
    assert anchor_missing_reason(T0 + timedelta(hours=24), coverage) == REASON_PENDING_CANDLES  # justo al final
    assert anchor_missing_reason(T0 + timedelta(days=5), coverage) == REASON_PENDING_CANDLES


def test_a_bank_with_no_candles_in_the_ladder_is_pending_candles():
    # Temporal: el próximo export trae las velas (el reloj sin verificar se chequea antes, RF-4e).
    assert anchor_missing_reason(T0, bank_coverage({})) == REASON_PENDING_CANDLES
    assert anchor_missing_reason(T0, bank_coverage({"1D": _bars("1H", T0, 3)})) == REASON_PENDING_CANDLES


# --- pending_candles sin toque (RF-4c) -----------------------------------------------------------

def test_without_a_touch_the_result_is_pending_until_the_path_reaches_the_end_of_the_horizon():
    horizon_end = T0 + timedelta(days=90)
    assert untouched_missing_reason(T0 + timedelta(days=3), horizon_end=None) == REASON_PENDING_CANDLES
    assert untouched_missing_reason(T0 + timedelta(days=3), horizon_end=horizon_end) == REASON_PENDING_CANDLES
    assert untouched_missing_reason(horizon_end, horizon_end=horizon_end) is None             # horizonte cumplido
    assert untouched_missing_reason(horizon_end + timedelta(hours=1), horizon_end=horizon_end) is None


def test_a_bank_that_ends_before_the_touch_is_pending_candles():
    """El caso del "Hecho cuando" de T24: el precio toca la validación el día 5, pero el banco llega solo hasta el
    día 3. Recorriendo lo que hay no aparece ningún toque, y el banco todavía no llega al fin del horizonte:
    `pending_candles`, no `open` (baseline H12)."""
    market = _bars("1H", T0, 24 * 7)
    touch_at = 24 * 5
    market[touch_at] = OhlcBar(time=market[touch_at].time, high=111.0, low=100.0)
    bank = {"1H": market[:24 * 3]}

    coverage = bank_coverage(bank)
    assert anchor_missing_reason(T0, coverage) is None
    detail = first_touch_detail("long", bank["1H"], 100.0, 110.0, 90.0)
    assert detail.bar_index is None  # en el banco no hay toque

    assert untouched_missing_reason(coverage.end, horizon_end=None) == REASON_PENDING_CANDLES
    # Con todo el mercado, el mismo recorrido sí encuentra el toque del día 5.
    assert first_touch_detail("long", market, 100.0, 110.0, 90.0).bar_index == touch_at


@pytest.mark.parametrize("timeframe", LADDER)
def test_every_ladder_timeframe_has_a_duration(timeframe):
    assert LADDER_MINUTES[timeframe] > 0
