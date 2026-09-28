"""
T9 (spec 002, RF-15, RF-4f): 5M y 1M en `tools.p2_backtest.TIMEFRAME_MINUTES`.

El banco de velas nuevo guarda hasta 1M (RF-15), y el resolvedor camina hacia
temporalidades cada vez más finas para refinar el primer toque (RF-4, RF-4f).
`CsvOHLCProvider.closed_bars()`/`.bar_containing()` ya funcionan con cualquier
entrada del dict -- este test prueba que 5M y 1M quedan cubiertas, con el mismo
patrón de fixture que `tests/test_p2_closed_bars.py`.
"""
from datetime import datetime, timedelta

import pandas as pd
import pytest

from tools.p2_backtest import CsvOHLCProvider, TIMEFRAME_MINUTES

DAY = datetime(2026, 9, 10)


def _write(tmp_path, tf, start, step, closes):
    times = [start + i * step for i in range(len(closes))]
    pd.DataFrame({"time": times, "open": closes, "high": [c + 1 for c in closes],
                  "low": [c - 1 for c in closes], "close": closes}).to_csv(tmp_path / f"{tf}.csv", index=False)


@pytest.fixture
def provider(tmp_path):
    # 5M: 00:00 .. 00:55 (12 velas). 1M: 00:00 .. 00:11 (12 velas).
    _write(tmp_path, "5M", DAY, timedelta(minutes=5), [300.0 + i for i in range(12)])
    _write(tmp_path, "1M", DAY, timedelta(minutes=1), [400.0 + i for i in range(12)])
    return CsvOHLCProvider(str(tmp_path))


def test_timeframe_minutes_has_5m_and_1m():
    assert TIMEFRAME_MINUTES["5M"] == 5
    assert TIMEFRAME_MINUTES["1M"] == 1


def test_closed_bars_5m_excludes_the_forming_bar(provider):
    # A las 00:07 la vela de 5M que abrió a las 00:05 sigue abierta: la última
    # cerrada es la de las 00:00 (índice 0, cierre 300.0).
    bars = provider.closed_bars("5M", DAY.replace(minute=7))
    assert len(bars) == 1
    assert bars.iloc[-1]["close"] == pytest.approx(300.0)


def test_closed_bars_5m_a_bar_closing_exactly_at_as_of_counts(provider):
    # A las 00:10 la vela de las 00:05 (open+5min=00:10) ya cerró, además de la de las 00:00.
    bars = provider.closed_bars("5M", DAY.replace(minute=10))
    assert len(bars) == 2
    assert bars.iloc[-1]["close"] == pytest.approx(301.0)


def test_closed_bars_1m_excludes_the_forming_bar(provider):
    bars = provider.closed_bars("1M", DAY.replace(minute=3, second=30))
    assert len(bars) == 3
    assert bars.iloc[-1]["close"] == pytest.approx(402.0)


def test_bar_containing_5m_returns_high_low_of_the_bar_in_progress(provider):
    # 00:07 cae dentro de la vela de 00:05-00:10 (índice 1: open=301, high=302, low=300).
    result = provider.bar_containing("5M", DAY.replace(minute=7))
    assert result == pytest.approx((302.0, 300.0))


def test_bar_containing_1m_returns_none_past_the_last_bar(provider):
    # La última vela de 1M abre a las 00:11 y cierra a las 00:12; a las 00:20 ya no hay vela.
    assert provider.bar_containing("1M", DAY.replace(minute=20)) is None


def test_bar_containing_5m_returns_none_before_the_first_bar(provider):
    assert provider.bar_containing("5M", DAY - timedelta(minutes=1)) is None
