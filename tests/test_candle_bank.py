"""
T11 (spec 002, RF-1, RF-1b): tools/candle_bank.py, parte 1 -- lectura y
escritura atómica de un `{TF}.csv`, y la fusión por `time` sin borrar ni
alterar velas existentes. Nada de candado, verificación de reloj, filtro de
estación ni `status.json` acá -- eso es T12 a T16, sobre estas mismas
funciones.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tools.candle_bank import (
    CandleBankLockedError,
    OVERLAP_TIMEFRAMES,
    acquire_bank_lock,
    bank_csv_path,
    gather_reference_entries,
    merge_candle_frames,
    merge_timeframe_into_bank,
    read_candle_csv,
    verify_by_references,
    verify_overlap,
    write_candle_csv_atomic,
)
from tools.database import Base, UnifiedDepartment
from tools.database import TacticalAudit as TacticalAuditORM

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


# --- acquire_bank_lock (T12, RF-1d) -- mismo patrón que tests/test_backup.py --

def test_lock_prevents_concurrent_run(tmp_path):
    lock_path = tmp_path / ".lock"
    lock_path.write_text(json.dumps({
        "pid": os.getpid(),  # el propio proceso de test -- garantizado vivo
        "started_at": datetime.now(timezone.utc).isoformat(),
    }))

    with pytest.raises(CandleBankLockedError):
        with acquire_bank_lock(str(tmp_path), stale_hours=2):
            pytest.fail("no debería entrar al cuerpo del with")

    # El candado tomado por "otro proceso" sigue ahí, sin tocar.
    assert json.loads(lock_path.read_text())["pid"] == os.getpid()


def test_stale_lock_dead_pid_is_recovered(tmp_path):
    # Subproceso real que ya terminó -- PID garantizado muerto (reaped).
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid = proc.pid
    proc.wait()

    lock_path = tmp_path / ".lock"
    lock_path.write_text(json.dumps({
        "pid": dead_pid,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }))

    with acquire_bank_lock(str(tmp_path), stale_hours=2):
        new_lock = json.loads(lock_path.read_text())
        assert new_lock["pid"] == os.getpid()


def test_stale_lock_old_ttl_is_recovered(tmp_path):
    old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    lock_path = tmp_path / ".lock"
    lock_path.write_text(json.dumps({
        "pid": os.getpid(),  # vivo, pero el candado es viejo
        "started_at": old_timestamp,
    }))

    with acquire_bank_lock(str(tmp_path), stale_hours=2):
        new_lock = json.loads(lock_path.read_text())
        assert new_lock["pid"] == os.getpid()


def test_corrupt_lock_file_is_treated_as_orphaned(tmp_path):
    lock_path = tmp_path / ".lock"
    lock_path.write_text("{not valid json")

    with acquire_bank_lock(str(tmp_path), stale_hours=2):
        new_lock = json.loads(lock_path.read_text())
        assert new_lock["pid"] == os.getpid()


def test_lock_released_on_normal_exit(tmp_path):
    with acquire_bank_lock(str(tmp_path)):
        pass
    assert not (tmp_path / ".lock").exists()


def test_lock_released_even_if_body_raises(tmp_path):
    with pytest.raises(ValueError, match="boom"):
        with acquire_bank_lock(str(tmp_path)):
            raise ValueError("boom")
    assert not (tmp_path / ".lock").exists()


# --- Candado + fusión combinados: RF-1d dice "deja el banco intacto" --------

def test_locked_bank_cancels_merge_and_leaves_bank_byte_for_byte_untouched(tmp_path):
    bank_dir = tmp_path / "bank" / "XAUUSD"
    bank_dir.mkdir(parents=True)
    _write_csv(bank_dir / "1H.csv", [DAY], [100.0])
    before = (bank_dir / "1H.csv").read_bytes()

    (bank_dir / ".lock").write_text(json.dumps({
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }))

    incoming_path = tmp_path / "incoming" / "1H.csv"
    incoming_path.parent.mkdir(parents=True)
    _write_csv(incoming_path, [DAY + timedelta(hours=1)], [101.0])

    with pytest.raises(CandleBankLockedError):
        with acquire_bank_lock(str(bank_dir)):
            merge_timeframe_into_bank(str(bank_dir), "1H", str(incoming_path))

    assert (bank_dir / "1H.csv").read_bytes() == before


# --- verify_overlap (T13, RF-2d, N32) ----------------------------------------

def test_overlap_timeframes_excludes_the_slow_ones_that_dont_need_clock_verification():
    # 4H, 12H, 1D y 1W quedan afuera (N39, RF-15c): el desfase de 1h es
    # despreciable para EMA/ADX en esas TF.
    assert OVERLAP_TIMEFRAMES == ("1H", "30M", "15M", "5M", "1M")


def test_verify_overlap_enough_matching_bars_verifies(tmp_path):
    bank_dir = tmp_path / "bank"
    incoming_dir = tmp_path / "incoming"
    bank_dir.mkdir()
    incoming_dir.mkdir()

    times = [DAY + timedelta(hours=h) for h in range(12)]
    closes = [100.0 + h for h in range(12)]
    _write_csv(bank_dir / "1H.csv", times, closes)
    _write_csv(incoming_dir / "1H.csv", times, closes)  # 12 velas superpuestas, iguales

    result = verify_overlap(str(bank_dir), str(incoming_dir))
    assert result.verified is True
    assert result.misaligned is False
    assert result.verified_timeframe == "1H"
    assert result.overlap_counts["1H"] == 12


def test_verify_overlap_one_differing_candle_gives_misaligned_and_no_verified_timeframe(tmp_path):
    bank_dir = tmp_path / "bank"
    incoming_dir = tmp_path / "incoming"
    bank_dir.mkdir()
    incoming_dir.mkdir()

    times = [DAY + timedelta(hours=h) for h in range(12)]
    closes = [100.0 + h for h in range(12)]
    _write_csv(bank_dir / "1H.csv", times, closes)
    bad_closes = list(closes)
    bad_closes[5] = 9999.0  # una sola vela distinta alcanza (RF-2d)
    _write_csv(incoming_dir / "1H.csv", times, bad_closes)

    result = verify_overlap(str(bank_dir), str(incoming_dir))
    assert result.misaligned is True
    assert result.verified is False
    assert result.verified_timeframe is None
    assert result.mismatched_timeframe == "1H"


def test_verify_overlap_fewer_than_min_bars_neither_verifies_nor_misaligns(tmp_path):
    bank_dir = tmp_path / "bank"
    incoming_dir = tmp_path / "incoming"
    bank_dir.mkdir()
    incoming_dir.mkdir()

    times = [DAY + timedelta(hours=h) for h in range(5)]  # 5 < OVERLAP_MIN_BARS (10)
    closes = [100.0 + h for h in range(5)]
    _write_csv(bank_dir / "1H.csv", times, closes)
    _write_csv(incoming_dir / "1H.csv", times, closes)

    result = verify_overlap(str(bank_dir), str(incoming_dir))
    assert result.verified is False
    assert result.misaligned is False
    assert result.overlap_counts["1H"] == 5


def test_verify_overlap_zero_overlap_on_every_timeframe_neither_verifies_nor_misaligns(tmp_path):
    # Típico del primer export de un símbolo: nada en común todavía. Le toca
    # a la verificación por referencias (T14).
    bank_dir = tmp_path / "bank"
    incoming_dir = tmp_path / "incoming"
    bank_dir.mkdir()
    incoming_dir.mkdir()

    _write_csv(bank_dir / "1H.csv", [DAY], [100.0])
    _write_csv(incoming_dir / "1H.csv", [DAY + timedelta(days=365)], [200.0])

    result = verify_overlap(str(bank_dir), str(incoming_dir))
    assert result.verified is False
    assert result.misaligned is False
    assert result.overlap_counts["1H"] == 0


def test_verify_overlap_tolerance_absorbs_tiny_float_formatting_differences(tmp_path):
    bank_dir = tmp_path / "bank"
    incoming_dir = tmp_path / "incoming"
    bank_dir.mkdir()
    incoming_dir.mkdir()

    times = [DAY + timedelta(hours=h) for h in range(10)]
    closes = [1000.0 + h for h in range(10)]
    _write_csv(bank_dir / "1H.csv", times, closes)
    tiny_diff = [c * (1 + 1e-10) for c in closes]  # dentro de la tolerancia de 1e-9
    _write_csv(incoming_dir / "1H.csv", times, tiny_diff)

    result = verify_overlap(str(bank_dir), str(incoming_dir))
    assert result.verified is True
    assert result.misaligned is False


def test_verify_overlap_difference_beyond_tolerance_misaligns(tmp_path):
    bank_dir = tmp_path / "bank"
    incoming_dir = tmp_path / "incoming"
    bank_dir.mkdir()
    incoming_dir.mkdir()

    times = [DAY + timedelta(hours=h) for h in range(10)]
    closes = [1000.0 + h for h in range(10)]
    _write_csv(bank_dir / "1H.csv", times, closes)
    off_by_more = [c * (1 + 1e-6) for c in closes]  # 1e-6, muy por encima de 1e-9
    _write_csv(incoming_dir / "1H.csv", times, off_by_more)

    result = verify_overlap(str(bank_dir), str(incoming_dir))
    assert result.misaligned is True


def test_verify_overlap_mismatch_in_a_later_timeframe_overrides_an_earlier_verified_one(tmp_path):
    bank_dir = tmp_path / "bank"
    incoming_dir = tmp_path / "incoming"
    bank_dir.mkdir()
    incoming_dir.mkdir()

    # 1H verifica (12 velas iguales)...
    hourly_times = [DAY + timedelta(hours=h) for h in range(12)]
    hourly_closes = [100.0 + h for h in range(12)]
    _write_csv(bank_dir / "1H.csv", hourly_times, hourly_closes)
    _write_csv(incoming_dir / "1H.csv", hourly_times, hourly_closes)

    # ...pero 30M (chequeada después, en OVERLAP_TIMEFRAMES) tiene una vela distinta.
    half_hour_times = [DAY + timedelta(minutes=30 * i) for i in range(4)]
    half_hour_closes = [200.0 + i for i in range(4)]
    _write_csv(bank_dir / "30M.csv", half_hour_times, half_hour_closes)
    bad_30m = list(half_hour_closes)
    bad_30m[1] = 9999.0
    _write_csv(incoming_dir / "30M.csv", half_hour_times, bad_30m)

    result = verify_overlap(str(bank_dir), str(incoming_dir))
    assert result.misaligned is True
    assert result.verified is False
    assert result.mismatched_timeframe == "30M"


def test_verify_overlap_is_read_only_never_writes_the_bank(tmp_path):
    """T13 solo verifica -- decidir si fusionar según el resultado es de otra
    tarea (T15). El banco no cambia ni un byte por llamar a verify_overlap()."""
    bank_dir = tmp_path / "bank"
    incoming_dir = tmp_path / "incoming"
    bank_dir.mkdir()
    incoming_dir.mkdir()
    _write_csv(bank_dir / "1H.csv", [DAY], [100.0])
    before = (bank_dir / "1H.csv").read_bytes()
    _write_csv(incoming_dir / "1H.csv", [DAY], [999.0])  # desalineado

    verify_overlap(str(bank_dir), str(incoming_dir))

    assert (bank_dir / "1H.csv").read_bytes() == before


# --- verify_by_references / gather_reference_entries (T14, RF-2, RF-2b, N29) --

T0 = datetime(2026, 6, 1, 0, 0)


def _make_account_db(path, fills=(), mark_prices=(), asset="XAUUSDT.P"):
    """
    DB de cuenta mínima, en un archivo real (no :memory:, porque
    open_readonly_session necesita un path). `fills`: lista de
    (entry_time, entry_price). `mark_prices`: lista de (created_at, mark_price).
    """
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        campos = dict(
            state="READY_FOR_NOTION", asset=asset, market_bias="Bullish", calc_edge=0.5,
            p4_hierarchy="x", p1_timeframe="15M", p1_type="x", nodes_l1=1, nodes_l2=1,
            tactical_classification="x", long_prob=0.5, short_prob=0.5, no_trade_prob=0.0,
        )
        for i, (t, price) in enumerate(fills):
            tid = f"fill-{i}"
            session.add(UnifiedDepartment(id=tid, **campos))
            session.add(TacticalAuditORM(trade_id=tid, entry_time=t, entry_price=price, order_filled=True))
        for i, (t, price) in enumerate(mark_prices):
            tid = f"mp-{i}"
            session.add(UnifiedDepartment(id=tid, created_at=t, mark_price=price, is_backdated=False, **campos))
        session.commit()
    engine.dispose()


def _write_reference_15m_csv(dir_path, lag_hours=0, start=T0, n_bars=200):
    """Mismo patrón que tests/test_p2_clock.py::_write_15m_csv: precio único
    por vela, así que un fill solo cae dentro de UNA."""
    rows = []
    for i in range(n_bars):
        real = start + timedelta(minutes=15 * i)
        rows.append({"time": (real + timedelta(hours=lag_hours)).strftime("%Y-%m-%d %H:%M:%S"),
                     "open": 1000 + i, "high": 1000 + i + 0.9, "low": 1000 + i, "close": 1000 + i + 0.5})
    os.makedirs(dir_path, exist_ok=True)
    pd.DataFrame(rows).to_csv(os.path.join(dir_path, "15M.csv"), index=False)


def _reference_fills(n, start=T0):
    """Fills cuyo precio es el de la vela real que contiene su entry_time."""
    result = []
    for i in range(n):
        bar = 10 + i * 7
        t = start + timedelta(minutes=15 * bar + 5)
        result.append((t, 1000 + bar + 0.4))
    return result


def test_gather_reference_entries_filters_by_asset_and_aggregates_accounts(tmp_path):
    _make_account_db(tmp_path / "xau.db", fills=_reference_fills(3), asset="XAUUSDT.P")
    _make_account_db(tmp_path / "btc.db", fills=_reference_fills(5), asset="BTCUSDT.P")  # otro símbolo, no cuenta

    entries = gather_reference_entries(
        "XAUUSD", str(tmp_path),
        real_accounts={"000": "xau.db", "002": "btc.db"},
        symbol_map={"XAUUSDT.P": "XAUUSD", "BTCUSDT.P": "BTCUSD"},
    )
    assert len(entries) == 3


def test_gather_reference_entries_includes_mark_price_excludes_backdated(tmp_path):
    _make_account_db(
        tmp_path / "xau.db",
        fills=_reference_fills(2),
        mark_prices=[(T0, 1500.0)],
        asset="XAUUSDT.P",
    )
    with_mp = gather_reference_entries(
        "XAUUSD", str(tmp_path), real_accounts={"000": "xau.db"},
        symbol_map={"XAUUSDT.P": "XAUUSD"}, include_mark_price=True,
    )
    without_mp = gather_reference_entries(
        "XAUUSD", str(tmp_path), real_accounts={"000": "xau.db"},
        symbol_map={"XAUUSDT.P": "XAUUSD"}, include_mark_price=False,
    )
    assert len(with_mp) == 3
    assert len(without_mp) == 2


def test_gather_reference_entries_skips_missing_db_file(tmp_path):
    entries = gather_reference_entries(
        "XAUUSD", str(tmp_path), real_accounts={"000": "does_not_exist.db"},
        symbol_map={"XAUUSDT.P": "XAUUSD"},
    )
    assert entries == []


def test_verify_by_references_twelve_in_range_and_aligned_verifies(tmp_path):
    _make_account_db(tmp_path / "xau.db", fills=_reference_fills(12), asset="XAUUSDT.P")
    _write_reference_15m_csv(tmp_path / "incoming", lag_hours=0)

    result = verify_by_references(
        "XAUUSD", str(tmp_path / "incoming"), T0, T0 + timedelta(days=2),
        accounts_data_dir=str(tmp_path), real_accounts={"000": "xau.db"},
        symbol_map={"XAUUSDT.P": "XAUUSD"}, min_references=10,
    )
    assert result.verified is True
    assert result.n_in_range == 12
    assert result.aligned is True
    assert result.best_offset == 0


def test_verify_by_references_nine_in_range_is_clock_unverified(tmp_path):
    _make_account_db(tmp_path / "xau.db", fills=_reference_fills(9), asset="XAUUSDT.P")
    _write_reference_15m_csv(tmp_path / "incoming", lag_hours=0)

    result = verify_by_references(
        "XAUUSD", str(tmp_path / "incoming"), T0, T0 + timedelta(days=2),
        accounts_data_dir=str(tmp_path), real_accounts={"000": "xau.db"},
        symbol_map={"XAUUSDT.P": "XAUUSD"}, min_references=10,
    )
    assert result.verified is False
    assert result.n_in_range == 9
    assert result.aligned is None  # no llegó a evaluarse: faltaron referencias
    assert result.best_offset is None


def test_verify_by_references_clock_shifted_3h_is_clock_misaligned(tmp_path):
    _make_account_db(tmp_path / "xau.db", fills=_reference_fills(12), asset="XAUUSDT.P")
    _write_reference_15m_csv(tmp_path / "incoming", lag_hours=3)  # el bug real del 2026-09-22

    result = verify_by_references(
        "XAUUSD", str(tmp_path / "incoming"), T0, T0 + timedelta(days=2),
        accounts_data_dir=str(tmp_path), real_accounts={"000": "xau.db"},
        symbol_map={"XAUUSDT.P": "XAUUSD"}, min_references=10,
    )
    assert result.verified is False
    assert result.aligned is False
    assert result.best_offset == 3


def test_verify_by_references_no_matching_timeframe_in_incoming_export(tmp_path):
    _make_account_db(tmp_path / "xau.db", fills=_reference_fills(12), asset="XAUUSDT.P")
    os.makedirs(tmp_path / "incoming", exist_ok=True)  # sin ningún CSV de CLOCK_TIMEFRAME_PREFERENCE

    result = verify_by_references(
        "XAUUSD", str(tmp_path / "incoming"), T0, T0 + timedelta(days=2),
        accounts_data_dir=str(tmp_path), real_accounts={"000": "xau.db"},
        symbol_map={"XAUUSDT.P": "XAUUSD"}, min_references=10,
    )
    assert result.verified is False
    assert result.timeframe is None
