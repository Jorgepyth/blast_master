"""
P2_systematic — confirma que el loader real de CSV (CsvOHLCProvider /
load_ohlc_provider_from_csv) lee un directorio de fixtures con el formato
asumido (jupyter/p2_systematic_findings.md) y lo pasa correctamente a la
interfaz OHLCProvider ya existente. NO ejecuta el pipeline contra la DB
real ni contra un CSV real de MT5 -- solo fixtures sintéticos en
tests/fixtures/p2_ohlc/.
"""
import os
from datetime import datetime

import pytest

from tools.p2_backtest import (
    OHLC_DIR_ENV_VAR,
    TFIndicatorSnapshot,
    load_ohlc_provider_from_csv,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "p2_ohlc")
ANCHOR = datetime(2026, 1, 2, 0, 0, 0)  # coincide con una vela real del fixture 1H


def _provider():
    return load_ohlc_provider_from_csv(base_dir=FIXTURES_DIR)


def test_indicator_snapshot_reads_1h_fixture_with_point_in_time_filter():
    snapshot = _provider().get_indicator_snapshot("1H", ANCHOR)
    assert isinstance(snapshot, TFIndicatorSnapshot)
    # 10 velas estrictamente antes del anchor (14:00..23:00) -- la vela
    # exactamente en el anchor (00:00) NO cuenta como pasada.
    assert snapshot.bars_available == 10
    assert snapshot.price == pytest.approx(2004.5)
    assert isinstance(snapshot.ema20, float)
    assert isinstance(snapshot.ema200, float)
    assert isinstance(snapshot.adx14, float)
    assert not any(v != v for v in (snapshot.ema20, snapshot.ema200, snapshot.adx14))  # no NaN


def test_indicator_snapshot_reads_distinct_file_per_timeframe():
    snapshot = _provider().get_indicator_snapshot("1D", ANCHOR)
    assert isinstance(snapshot, TFIndicatorSnapshot)
    # Las 5 filas de 1D.csv son todas anteriores al anchor -> las 5 cuentan.
    assert snapshot.bars_available == 5
    assert snapshot.price == pytest.approx(1998.0)


def test_indicator_snapshot_returns_none_when_no_bars_before_anchor():
    very_early = datetime(2000, 1, 1)
    assert _provider().get_indicator_snapshot("1H", very_early) is None


def test_forward_path_excludes_anchor_bar_itself():
    path = _provider().get_forward_path(ANCHOR)
    # 5 velas estrictamente después del anchor (01:00..05:00); la vela del
    # propio anchor (00:00) queda excluida.
    assert len(path) == 5
    assert all(bar.time > ANCHOR for bar in path)
    assert path[0].time == datetime(2026, 1, 2, 1, 0, 0)


def test_forward_path_respects_max_bars_cap():
    path = _provider().get_forward_path(ANCHOR, max_bars=3)
    assert len(path) == 3
    assert [bar.time.hour for bar in path] == [1, 2, 3]


def test_forward_path_bar_fields_are_floats():
    path = _provider().get_forward_path(ANCHOR, max_bars=1)
    bar = path[0]
    assert isinstance(bar.high, float)
    assert isinstance(bar.low, float)


def test_missing_required_column_raises_value_error_naming_the_missing_column():
    with pytest.raises(ValueError, match="close"):
        _provider().get_indicator_snapshot("BAD", ANCHOR)


def test_missing_env_var_raises_runtime_error_without_base_dir(monkeypatch):
    monkeypatch.delenv(OHLC_DIR_ENV_VAR, raising=False)
    # Evita que load_dotenv() re-cargue un .env real de la máquina que
    # pudiera tener la variable seteada -- el test debe ser determinístico
    # sin importar el .env local del desarrollador.
    monkeypatch.setattr("tools.p2_backtest.load_dotenv", lambda **kwargs: None)
    with pytest.raises(RuntimeError, match=OHLC_DIR_ENV_VAR):
        load_ohlc_provider_from_csv()
