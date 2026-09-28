"""
T11 (spec 002, RF-1, RF-1b): tools/candle_bank.py, parte 1 -- lectura y
escritura atómica de un `{TF}.csv`, y la fusión por `time` sin borrar ni
alterar velas existentes. Nada de candado, verificación de reloj, filtro de
estación ni `status.json` acá -- eso es T12 a T16, sobre estas mismas
funciones.
"""
import os
from datetime import datetime, timedelta

import pandas as pd
import pytest

from tools.candle_bank import (
    bank_csv_path,
    merge_candle_frames,
    merge_timeframe_into_bank,
    read_candle_csv,
    write_candle_csv_atomic,
)

DAY = datetime(2026, 9, 10)


def _frame(times, closes):
    return pd.DataFrame({
        "time": times, "open": closes, "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes], "close": closes,
    })


def _write_csv(path, times, closes):
    _frame(times, closes).to_csv(path, index=False)


# --- bank_csv_path ----------------------------------------------------------

def test_bank_csv_path_format():
    assert bank_csv_path("/x/XAUUSD", "1H") == "/x/XAUUSD/1H.csv"


# --- read_candle_csv ----------------------------------------------------------

def test_read_candle_csv_missing_file_returns_empty_typed_frame(tmp_path):
    df = read_candle_csv(str(tmp_path / "nope.csv"))
    assert list(df.columns) == ["time", "open", "high", "low", "close"]
    assert len(df) == 0


def test_read_candle_csv_normalizes_column_case(tmp_path):
    # Mismo formato asumido que CsvOHLCProvider._load_tf (insensible a mayúsculas/espacios).
    p = tmp_path / "1H.csv"
    pd.DataFrame({"Time": [DAY], "Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5]}).to_csv(p, index=False)
    df = read_candle_csv(str(p))
    assert list(df.columns) == ["time", "open", "high", "low", "close"]
    assert df.iloc[0]["close"] == pytest.approx(1.5)


def test_read_candle_csv_missing_required_column_raises(tmp_path):
    p = tmp_path / "1H.csv"
    pd.DataFrame({"time": [DAY], "open": [1.0], "high": [2.0], "low": [0.5]}).to_csv(p, index=False)  # sin close
    with pytest.raises(ValueError, match="close"):
        read_candle_csv(str(p))


def test_read_candle_csv_sorts_even_if_the_file_is_out_of_order(tmp_path):
    p = tmp_path / "1H.csv"
    _write_csv(p, [DAY + timedelta(hours=2), DAY], [102.0, 100.0])
    df = read_candle_csv(str(p))
    assert list(df["close"]) == [100.0, 102.0]


# --- write_candle_csv_atomic --------------------------------------------------

def test_write_candle_csv_atomic_roundtrips(tmp_path):
    p = tmp_path / "1H.csv"
    df = _frame([DAY, DAY + timedelta(hours=1)], [100.0, 101.0])
    write_candle_csv_atomic(str(p), df)
    reread = read_candle_csv(str(p))
    assert len(reread) == 2
    assert reread.iloc[0]["close"] == pytest.approx(100.0)
    # No queda ningún archivo temporal.
    assert [f for f in os.listdir(tmp_path) if f.startswith(".tmp_candle_bank_")] == []


def test_write_candle_csv_atomic_creates_missing_directories(tmp_path):
    p = tmp_path / "XAUUSD" / "1H.csv"
    write_candle_csv_atomic(str(p), _frame([DAY], [100.0]))
    assert p.exists()


def test_write_candle_csv_atomic_failure_leaves_original_byte_for_byte_untouched(tmp_path, monkeypatch):
    p = tmp_path / "1H.csv"
    write_candle_csv_atomic(str(p), _frame([DAY], [100.0]))
    before = p.read_bytes()

    def _boom(self, *a, **kw):
        raise RuntimeError("disco lleno (simulado)")

    monkeypatch.setattr(pd.DataFrame, "to_csv", _boom)
    with pytest.raises(RuntimeError):
        write_candle_csv_atomic(str(p), _frame([DAY, DAY + timedelta(hours=1)], [100.0, 999.0]))

    assert p.read_bytes() == before
    assert [f for f in os.listdir(tmp_path) if f.startswith(".tmp_candle_bank_")] == []


# --- merge_candle_frames (RF-1, RF-1b) ---------------------------------------

def test_merge_no_previous_candle_disappears_or_changes():
    bank = _frame([DAY, DAY + timedelta(hours=1), DAY + timedelta(hours=2)], [100.0, 101.0, 102.0])
    incoming = _frame([DAY + timedelta(hours=3)], [103.0])
    merged = merge_candle_frames(bank, incoming)
    assert len(merged) == 4
    for _, row in bank.iterrows():
        match = merged[merged["time"] == row["time"]]
        assert len(match) == 1
        assert match.iloc[0]["close"] == pytest.approx(row["close"])
        assert match.iloc[0]["high"] == pytest.approx(row["high"])
        assert match.iloc[0]["low"] == pytest.approx(row["low"])


def test_merge_backward_coverage_never_shrinks():
    bank = _frame([DAY + timedelta(hours=5)], [105.0])
    incoming = _frame([DAY, DAY + timedelta(hours=5), DAY + timedelta(hours=6)], [999.0, 999.0, 106.0])
    merged = merge_candle_frames(bank, incoming)
    assert merged["time"].min() <= bank["time"].min()
    assert merged["time"].min() == DAY  # la vela más vieja del export sí entra


def test_merge_duplicate_time_keeps_the_bank_candle_not_the_incoming_one():
    bank = _frame([DAY], [100.0])
    incoming = _frame([DAY], [999.0])  # mismo time, precio distinto
    merged = merge_candle_frames(bank, incoming)
    assert len(merged) == 1
    assert merged.iloc[0]["close"] == pytest.approx(100.0)


def test_merge_result_is_sorted_by_time():
    bank = _frame([DAY + timedelta(hours=2)], [102.0])
    incoming = _frame([DAY, DAY + timedelta(hours=1)], [100.0, 101.0])
    merged = merge_candle_frames(bank, incoming)
    assert list(merged["time"]) == sorted(merged["time"])


def test_merge_empty_bank_keeps_all_incoming_candles(tmp_path):
    empty_bank = read_candle_csv(str(tmp_path / "does_not_exist.csv"))
    incoming = _frame([DAY, DAY + timedelta(hours=1)], [100.0, 101.0])
    merged = merge_candle_frames(empty_bank, incoming)
    assert len(merged) == 2
    assert list(merged["close"]) == [100.0, 101.0]


# --- merge_timeframe_into_bank (extremo a extremo, sin candado ni verificación) --

def test_merge_timeframe_into_bank_end_to_end(tmp_path):
    bank_dir = tmp_path / "bank" / "XAUUSD"
    bank_dir.mkdir(parents=True)
    _write_csv(bank_dir / "1H.csv", [DAY, DAY + timedelta(hours=1)], [100.0, 101.0])

    incoming_path = tmp_path / "incoming" / "1H.csv"
    incoming_path.parent.mkdir(parents=True)
    _write_csv(incoming_path, [DAY, DAY + timedelta(hours=1), DAY + timedelta(hours=2)], [999.0, 999.0, 102.0])

    merged = merge_timeframe_into_bank(str(bank_dir), "1H", str(incoming_path))
    assert len(merged) == 3
    assert merged.iloc[0]["close"] == pytest.approx(100.0)  # conservó la del banco
    assert merged.iloc[2]["close"] == pytest.approx(102.0)  # la vela nueva sí entró

    on_disk = read_candle_csv(bank_csv_path(str(bank_dir), "1H"))
    pd.testing.assert_frame_equal(on_disk, merged)


def test_merge_timeframe_into_bank_first_time_no_prior_bank_directory(tmp_path):
    bank_dir = tmp_path / "bank" / "XAUUSD"  # no existe todavía
    incoming_path = tmp_path / "incoming" / "1H.csv"
    incoming_path.parent.mkdir(parents=True)
    _write_csv(incoming_path, [DAY, DAY + timedelta(hours=1)], [100.0, 101.0])

    merged = merge_timeframe_into_bank(str(bank_dir), "1H", str(incoming_path))
    assert len(merged) == 2
    assert os.path.exists(bank_csv_path(str(bank_dir), "1H"))
