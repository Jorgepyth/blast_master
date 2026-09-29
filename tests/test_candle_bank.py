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
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import tools.candle_bank
from tools.candle_bank import (
    ALL_BANK_TIMEFRAMES,
    CandleBankLockedError,
    LEGACY_EXCLUSION_REASON,
    LEGACY_IMPORT_EXCLUSIONS,
    LEGACY_IMPORT_RUN_ID,
    OVERLAP_TIMEFRAMES,
    STATUS_JSON_FILENAME,
    SYNC_RESULT_MERGED,
    SyncResult,
    acquire_bank_lock,
    bank_csv_path,
    build_status_payload,
    collect_bank_status,
    filter_by_export_season,
    gather_reference_entries,
    import_legacy,
    import_legacy_with_status,
    merge_candle_frames,
    merge_incoming_run,
    merge_timeframe_into_bank,
    merge_timeframe_into_bank_checked,
    read_bank_status,
    read_candle_csv,
    status_json_path,
    verify_by_references,
    verify_overlap,
    write_bank_status,
    write_candle_csv_atomic,
    write_sync_status,
)
from tools.database import Base, UnifiedDepartment
from tools.database import TacticalAudit as TacticalAuditORM

DAY = datetime(2026, 9, 10)
EXPORT_MOMENT = datetime(2026, 9, 15, 12)  # un momento de verano fijo, para que los tests no dependan de la fecha de hoy


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


# --- filter_by_export_season (T15, RF-15c, N39) ------------------------------

def test_dst_active_us_rule_on_unambiguous_interior_dates():
    assert tools.candle_bank._dst_active_on(date(2026, 7, 15), "us") is True   # verano, sin ambigüedad
    assert tools.candle_bank._dst_active_on(date(2026, 1, 15), "us") is False  # invierno, sin ambigüedad


def test_dst_active_eu_rule_on_unambiguous_interior_dates():
    assert tools.candle_bank._dst_active_on(date(2026, 7, 15), "eu") is True
    assert tools.candle_bank._dst_active_on(date(2026, 1, 15), "eu") is False


def test_filter_by_export_season_keeps_same_season_1h_candles():
    df = _frame([datetime(2026, 7, 10, 6), datetime(2026, 7, 12, 6)], [100.0, 101.0])  # las dos en verano
    export_moment = datetime(2026, 7, 20, 12)  # también verano
    filtered = filter_by_export_season(df, "1H", export_moment, "us")
    assert len(filtered) == 2


def test_filter_by_export_season_drops_opposite_season_candles_on_fast_timeframes():
    df = _frame([datetime(2026, 1, 10, 6), datetime(2026, 7, 12, 6)], [100.0, 101.0])  # invierno, verano
    export_moment = datetime(2026, 7, 20, 12)  # verano
    for tf in OVERLAP_TIMEFRAMES:  # 1H, 30M, 15M, 5M, 1M
        filtered = filter_by_export_season(df, tf, export_moment, "us")
        assert len(filtered) == 1, tf
        assert filtered.iloc[0]["close"] == pytest.approx(101.0), tf


def test_filter_by_export_season_leaves_slow_timeframes_complete():
    df = _frame([datetime(2026, 1, 10, 6), datetime(2026, 7, 12, 6)], [100.0, 101.0])
    export_moment = datetime(2026, 7, 20, 12)
    for tf in ("4H", "12H", "1D", "1W"):
        filtered = filter_by_export_season(df, tf, export_moment, "us")
        assert len(filtered) == 2, tf


def test_filter_by_export_season_dst_rule_none_keeps_everything():
    df = _frame([datetime(2026, 1, 10, 6), datetime(2026, 7, 12, 6)], [100.0, 101.0])
    export_moment = datetime(2026, 7, 20, 12)
    filtered = filter_by_export_season(df, "1H", export_moment, "none")
    assert len(filtered) == 2


# --- status.json (T15, plan.md §2.3) -----------------------------------------

def test_build_status_payload_matches_plan_example_shape():
    payload = build_status_payload(
        symbol="XAUUSD", clock="verified", verified_by="overlap", dst_rule="unverified",
        run_id="20261005T142011", result="merged", bars_added={"1M": 412, "15M": 28},
    )
    assert payload == {
        "symbol": "XAUUSD", "clock": "verified", "verified_by": "overlap", "dst_rule": "unverified",
        "last_export": {"run_id": "20261005T142011", "result": "merged", "bars_added": {"1M": 412, "15M": 28}},
        "last_error": None,
    }


def test_status_json_path_format():
    assert status_json_path("/x/XAUUSD") == f"/x/XAUUSD/{STATUS_JSON_FILENAME}"


def test_read_bank_status_missing_file_returns_none(tmp_path):
    assert read_bank_status(str(tmp_path / "XAUUSD")) is None


def test_write_and_read_bank_status_roundtrips_and_leaves_no_temp_file(tmp_path):
    bank_dir = tmp_path / "XAUUSD"
    payload = build_status_payload("XAUUSD", "verified", "overlap", "unverified", "run-1", "merged", {"1H": 5})
    write_bank_status(str(bank_dir), payload)
    assert read_bank_status(str(bank_dir)) == payload
    assert [f for f in os.listdir(bank_dir) if f.startswith(".tmp_status_")] == []


def test_status_json_reflects_a_clock_misaligned_result(tmp_path):
    bank_dir = tmp_path / "XAUUSD"
    payload = build_status_payload("XAUUSD", "clock_misaligned", None, "unverified", "run-2",
                                    "clock_misaligned", {}, last_error=None)
    write_bank_status(str(bank_dir), payload)
    on_disk = read_bank_status(str(bank_dir))
    assert on_disk["clock"] == "clock_misaligned"
    assert on_disk["verified_by"] is None
    assert on_disk["last_export"]["bars_added"] == {}


# --- merge_timeframe_into_bank_checked (T15, RF-1, RF-1c) --------------------

def test_merge_checked_normal_case_adds_bars_like_the_unchecked_version(tmp_path):
    bank_dir = tmp_path / "XAUUSD"
    bank_dir.mkdir()
    _write_csv(bank_dir / "1H.csv", [DAY], [100.0])

    incoming_df = _frame([DAY + timedelta(hours=1)], [101.0])
    added = merge_timeframe_into_bank_checked(str(bank_dir), "1H", incoming_df)

    assert added == 1
    on_disk = read_candle_csv(bank_csv_path(str(bank_dir), "1H"))
    assert len(on_disk) == 2


def test_merge_checked_regression_raises_and_writes_nothing(tmp_path, monkeypatch):
    bank_dir = tmp_path / "XAUUSD"
    bank_dir.mkdir()
    _write_csv(bank_dir / "1H.csv", [DAY, DAY + timedelta(hours=1)], [100.0, 101.0])
    before = (bank_dir / "1H.csv").read_bytes()

    def _broken_merge(bank_df, incoming_df):
        return incoming_df  # "pierde" las velas del banco a propósito, para probar la red de seguridad

    monkeypatch.setattr(tools.candle_bank, "merge_candle_frames", _broken_merge)

    with pytest.raises(RuntimeError, match="perdió"):
        merge_timeframe_into_bank_checked(str(bank_dir), "1H", _frame([DAY + timedelta(hours=2)], [102.0]))

    assert (bank_dir / "1H.csv").read_bytes() == before


def test_merge_checked_write_failure_mid_fusion_leaves_bank_byte_for_byte_untouched(tmp_path, monkeypatch):
    bank_dir = tmp_path / "XAUUSD"
    bank_dir.mkdir()
    _write_csv(bank_dir / "1H.csv", [DAY], [100.0])
    before = (bank_dir / "1H.csv").read_bytes()

    def _boom(self, *a, **kw):
        raise RuntimeError("disco lleno (simulado)")

    monkeypatch.setattr(pd.DataFrame, "to_csv", _boom)
    with pytest.raises(RuntimeError, match="disco lleno"):
        merge_timeframe_into_bank_checked(str(bank_dir), "1H", _frame([DAY + timedelta(hours=1)], [101.0]))

    assert (bank_dir / "1H.csv").read_bytes() == before
    assert [f for f in os.listdir(bank_dir) if f.startswith(".tmp_candle_bank_")] == []


# --- import_legacy (T16, RF-2c) ----------------------------------------------

def test_import_legacy_exclusions_only_covers_xau_5m():
    assert LEGACY_IMPORT_EXCLUSIONS == frozenset({("XAUUSD", "5M")})


def test_import_legacy_aligned_symbol_imports_all_present_timeframes(tmp_path):
    _make_account_db(tmp_path / "btc.db", fills=_reference_fills(12), asset="BTCUSDT.P")
    legacy_dir = tmp_path / "legacy" / "BTCUSD"
    _write_reference_15m_csv(legacy_dir, lag_hours=0)  # 15M, alineado
    _write_csv(legacy_dir / "1H.csv", [T0, T0 + timedelta(hours=1)], [500.0, 501.0])

    bank_dir = tmp_path / "bank" / "BTCUSD"
    result = import_legacy(
        "BTCUSD", str(legacy_dir), str(bank_dir), str(tmp_path),
        real_accounts={"002": "btc.db"}, symbol_map={"BTCUSDT.P": "BTCUSD"}, min_entries=10,
        export_moment=EXPORT_MOMENT, dst_rule="none",
    )

    assert result.aligned is True
    assert set(result.imported) == {"15M", "1H"}
    assert result.excluded == {}
    on_disk = read_candle_csv(bank_csv_path(str(bank_dir), "1H"))
    assert len(on_disk) == 2


def test_import_legacy_excludes_xau_5m_even_when_symbol_verifies(tmp_path):
    _make_account_db(tmp_path / "xau.db", fills=_reference_fills(12), asset="XAUUSDT.P")
    legacy_dir = tmp_path / "legacy" / "XAUUSD"
    _write_reference_15m_csv(legacy_dir, lag_hours=0)
    _write_csv(legacy_dir / "5M.csv", [T0, T0 + timedelta(minutes=5)], [1800.0, 1801.0])  # el contaminado

    bank_dir = tmp_path / "bank" / "XAUUSD"
    result = import_legacy(
        "XAUUSD", str(legacy_dir), str(bank_dir), str(tmp_path),
        real_accounts={"000": "xau.db"}, symbol_map={"XAUUSDT.P": "XAUUSD"}, min_entries=10,
        export_moment=EXPORT_MOMENT, dst_rule="none",
    )

    assert result.aligned is True
    assert "15M" in result.imported
    assert result.excluded == {"5M": LEGACY_EXCLUSION_REASON}
    assert not os.path.exists(bank_csv_path(str(bank_dir), "5M"))


def test_import_legacy_5m_exclusion_is_scoped_to_xauusd_only(tmp_path):
    _make_account_db(tmp_path / "btc.db", fills=_reference_fills(12), asset="BTCUSDT.P")
    legacy_dir = tmp_path / "legacy" / "BTCUSD"
    _write_reference_15m_csv(legacy_dir, lag_hours=0)
    _write_csv(legacy_dir / "5M.csv", [T0], [30000.0])  # mismo nombre de TF, otro símbolo

    bank_dir = tmp_path / "bank" / "BTCUSD"
    result = import_legacy(
        "BTCUSD", str(legacy_dir), str(bank_dir), str(tmp_path),
        real_accounts={"002": "btc.db"}, symbol_map={"BTCUSDT.P": "BTCUSD"}, min_entries=10,
        export_moment=EXPORT_MOMENT, dst_rule="none",
    )
    assert "5M" in result.imported
    assert result.excluded == {}


def test_import_legacy_insufficient_references_excludes_everything_and_writes_nothing(tmp_path):
    _make_account_db(tmp_path / "xau.db", fills=_reference_fills(3), asset="XAUUSDT.P")  # < 10
    legacy_dir = tmp_path / "legacy" / "XAUUSD"
    _write_reference_15m_csv(legacy_dir, lag_hours=0)
    _write_csv(legacy_dir / "1H.csv", [T0], [1800.0])

    bank_dir = tmp_path / "bank" / "XAUUSD"
    result = import_legacy(
        "XAUUSD", str(legacy_dir), str(bank_dir), str(tmp_path),
        real_accounts={"000": "xau.db"}, symbol_map={"XAUUSDT.P": "XAUUSD"}, min_entries=10,
        export_moment=EXPORT_MOMENT, dst_rule="none",
    )
    assert result.imported == {}
    assert set(result.excluded) == {"15M", "1H"}
    assert all(v == "clock_unverified" for v in result.excluded.values())
    assert not os.path.exists(bank_dir)  # nada se escribió: el directorio del banco ni se creó


def test_import_legacy_misaligned_clock_excludes_everything_with_reason(tmp_path):
    _make_account_db(tmp_path / "xau.db", fills=_reference_fills(12), asset="XAUUSDT.P")
    legacy_dir = tmp_path / "legacy" / "XAUUSD"
    _write_reference_15m_csv(legacy_dir, lag_hours=3)  # corrido, el bug real del 2026-09-22

    bank_dir = tmp_path / "bank" / "XAUUSD"
    result = import_legacy(
        "XAUUSD", str(legacy_dir), str(bank_dir), str(tmp_path),
        real_accounts={"000": "xau.db"}, symbol_map={"XAUUSDT.P": "XAUUSD"}, min_entries=10,
        export_moment=EXPORT_MOMENT, dst_rule="none",
    )
    assert result.imported == {}
    assert result.excluded == {"15M": "clock_misaligned"}


def test_import_legacy_empty_source_directory_returns_none_aligned(tmp_path):
    result = import_legacy(
        "NOPE", str(tmp_path / "legacy" / "NOPE"), str(tmp_path / "bank" / "NOPE"), str(tmp_path),
        real_accounts={}, symbol_map={}, export_moment=EXPORT_MOMENT, dst_rule="none",
    )
    assert result.aligned is None
    assert result.imported == {}
    assert result.excluded == {}


def test_import_legacy_never_modifies_the_source_directory(tmp_path):
    _make_account_db(tmp_path / "xau.db", fills=_reference_fills(12), asset="XAUUSDT.P")
    legacy_dir = tmp_path / "legacy" / "XAUUSD"
    _write_reference_15m_csv(legacy_dir, lag_hours=0)
    before = (legacy_dir / "15M.csv").read_bytes()

    import_legacy(
        "XAUUSD", str(legacy_dir), str(tmp_path / "bank" / "XAUUSD"), str(tmp_path),
        real_accounts={"000": "xau.db"}, symbol_map={"XAUUSDT.P": "XAUUSD"}, min_entries=10,
        export_moment=EXPORT_MOMENT, dst_rule="none",
    )

    assert (legacy_dir / "15M.csv").read_bytes() == before


# --- merge_incoming_run / write_sync_status (T20, plan.md §3.8) --------------

def _hourly(start, n, base=100.0):
    return [start + timedelta(hours=h) for h in range(n)], [base + h for h in range(n)]


def _dirs(tmp_path):
    bank_dir, incoming_dir = tmp_path / "bank" / "XAUUSD", tmp_path / "incoming"
    bank_dir.mkdir(parents=True)
    incoming_dir.mkdir()
    return bank_dir, incoming_dir


def _merge(tmp_path, bank_dir, incoming_dir, **kw):
    kw.setdefault("export_moment", datetime(2026, 9, 15, 12))
    kw.setdefault("dst_rule", "none")
    kw.setdefault("real_accounts", {"000": "xau.db"})
    kw.setdefault("symbol_map", {"XAUUSDT.P": "XAUUSD"})
    return merge_incoming_run(str(bank_dir), str(incoming_dir), "XAUUSD", "run-1", str(tmp_path), **kw)


def test_all_bank_timeframes_are_the_nine_in_order():
    assert ALL_BANK_TIMEFRAMES == ("1W", "1D", "12H", "4H", "1H", "30M", "15M", "5M", "1M")


def test_merge_incoming_run_verified_by_overlap_merges_every_present_timeframe(tmp_path):
    bank_dir, incoming_dir = _dirs(tmp_path)
    times, closes = _hourly(DAY, 12)
    _write_csv(bank_dir / "1H.csv", times, closes)
    new_times, new_closes = _hourly(DAY, 15)  # las mismas 12 + 3 nuevas
    _write_csv(incoming_dir / "1H.csv", new_times, new_closes)
    _write_csv(incoming_dir / "1D.csv", [DAY, DAY + timedelta(days=1)], [5000.0, 5001.0])  # TF lenta, sin banco previo

    result = _merge(tmp_path, bank_dir, incoming_dir)

    assert result.result == SYNC_RESULT_MERGED
    assert result.verified_by == "overlap"
    assert result.run_id == "run-1"
    assert result.bars_added == {"1D": 2, "1H": 3}
    assert len(read_candle_csv(bank_csv_path(str(bank_dir), "1H"))) == 15
    assert len(read_candle_csv(bank_csv_path(str(bank_dir), "1D"))) == 2


def test_merge_incoming_run_overlap_mismatch_is_clock_misaligned_and_bank_untouched(tmp_path):
    bank_dir, incoming_dir = _dirs(tmp_path)
    times, closes = _hourly(DAY, 12)
    _write_csv(bank_dir / "1H.csv", times, closes)
    before = (bank_dir / "1H.csv").read_bytes()
    bad = list(closes)
    bad[4] = 9999.0
    _write_csv(incoming_dir / "1H.csv", times, bad)
    _write_csv(incoming_dir / "1D.csv", [DAY], [5000.0])

    result = _merge(tmp_path, bank_dir, incoming_dir)

    assert result.result == "clock_misaligned"
    assert "1H" in result.error
    assert (bank_dir / "1H.csv").read_bytes() == before
    assert not (bank_dir / "1D.csv").exists()  # nada se fusionó, tampoco la TF que no tenía problema


def test_merge_incoming_run_first_export_verified_by_references(tmp_path):
    _make_account_db(tmp_path / "xau.db", fills=_reference_fills(12), asset="XAUUSDT.P")
    bank_dir, incoming_dir = _dirs(tmp_path)
    _write_reference_15m_csv(incoming_dir, lag_hours=0)

    result = _merge(tmp_path, bank_dir, incoming_dir, export_moment=datetime(2026, 6, 5, 12), dst_rule="us")

    assert result.result == SYNC_RESULT_MERGED
    assert result.verified_by == "references"
    assert result.bars_added["15M"] == 200


def test_merge_incoming_run_too_few_references_is_clock_unverified_and_writes_nothing(tmp_path):
    _make_account_db(tmp_path / "xau.db", fills=_reference_fills(9), asset="XAUUSDT.P")
    bank_dir, incoming_dir = _dirs(tmp_path)
    _write_reference_15m_csv(incoming_dir, lag_hours=0)

    result = _merge(tmp_path, bank_dir, incoming_dir, export_moment=datetime(2026, 6, 5, 12), dst_rule="us")

    assert result.result == "clock_unverified"
    assert "9 reference prices" in result.error
    assert os.listdir(bank_dir) == []


def test_merge_incoming_run_shifted_clock_is_clock_misaligned(tmp_path):
    _make_account_db(tmp_path / "xau.db", fills=_reference_fills(12), asset="XAUUSDT.P")
    bank_dir, incoming_dir = _dirs(tmp_path)
    _write_reference_15m_csv(incoming_dir, lag_hours=3)

    result = _merge(tmp_path, bank_dir, incoming_dir, export_moment=datetime(2026, 6, 5, 12), dst_rule="us")

    assert result.result == "clock_misaligned"
    assert "+3h" in result.error
    assert os.listdir(bank_dir) == []


def test_merge_incoming_run_applies_the_season_filter_only_to_fast_timeframes(tmp_path):
    bank_dir, incoming_dir = _dirs(tmp_path)
    july, july_closes = _hourly(datetime(2026, 7, 15), 10)
    _write_csv(bank_dir / "1H.csv", july, july_closes)
    january = datetime(2026, 1, 15, 10)
    _write_csv(incoming_dir / "1H.csv", [january] + july, [777.0] + july_closes)  # 10 verifican; enero es de otra estación
    _write_csv(incoming_dir / "1D.csv", [january, datetime(2026, 7, 15)], [1.0, 2.0])

    result = _merge(tmp_path, bank_dir, incoming_dir, export_moment=datetime(2026, 9, 15, 12), dst_rule="us")

    assert result.result == SYNC_RESULT_MERGED
    assert result.bars_added["1H"] == 0  # la vela de enero se filtró
    assert january not in set(read_candle_csv(bank_csv_path(str(bank_dir), "1H"))["time"])
    assert result.bars_added["1D"] == 2  # las lentas entran completas (N39)


def test_merge_incoming_run_empty_incoming_dir_is_export_failed(tmp_path):
    bank_dir, incoming_dir = _dirs(tmp_path)
    result = _merge(tmp_path, bank_dir, incoming_dir)
    assert result.result == "export_failed"
    assert "empty_export" in result.error


def test_merge_incoming_run_failure_midway_keeps_finished_timeframes_and_leaves_the_rest_untouched(
        tmp_path, monkeypatch):
    bank_dir, incoming_dir = _dirs(tmp_path)
    times, closes = _hourly(DAY, 12)
    _write_csv(bank_dir / "1H.csv", times, closes)
    before_1h = (bank_dir / "1H.csv").read_bytes()
    new_times, new_closes = _hourly(DAY, 15)
    _write_csv(incoming_dir / "1H.csv", new_times, new_closes)
    _write_csv(incoming_dir / "1D.csv", [DAY], [5000.0])

    real = tools.candle_bank.merge_timeframe_into_bank_checked

    def flaky(bank, tf, df):
        if tf == "1H":
            raise RuntimeError("disco lleno (simulado)")
        return real(bank, tf, df)

    monkeypatch.setattr(tools.candle_bank, "merge_timeframe_into_bank_checked", flaky)
    result = _merge(tmp_path, bank_dir, incoming_dir)

    assert result.result == "export_failed"
    assert "merge failed on 1H" in result.error and "disco lleno" in result.error
    assert result.bars_added == {"1D": 1}  # 1D (más lenta, va antes) ya se había fusionado
    assert (bank_dir / "1H.csv").read_bytes() == before_1h


def _sync(result, **kw):
    kw.setdefault("symbol", "XAUUSD")
    return SyncResult(result=result, **kw)


def test_write_sync_status_merged_marks_the_clock_verified(tmp_path):
    write_sync_status(str(tmp_path), _sync(SYNC_RESULT_MERGED, verified_by="overlap", run_id="r1",
                                           bars_added={"1H": 3}))
    status = read_bank_status(str(tmp_path))
    assert (status["clock"], status["verified_by"]) == ("verified", "overlap")
    assert status["last_export"] == {"run_id": "r1", "result": "merged", "bars_added": {"1H": 3}}
    assert status["last_error"] is None


def test_write_sync_status_a_failed_verification_does_not_revoke_an_already_verified_bank(tmp_path):
    write_sync_status(str(tmp_path), _sync(SYNC_RESULT_MERGED, verified_by="references", run_id="r1"))
    write_sync_status(str(tmp_path), _sync("clock_misaligned", run_id="r2", error="shifted +3h"))
    status = read_bank_status(str(tmp_path))
    assert (status["clock"], status["verified_by"]) == ("verified", "references")  # el banco no cambió
    assert status["last_export"]["result"] == "clock_misaligned"
    assert status["last_error"] == "shifted +3h"


def test_write_sync_status_first_export_that_does_not_verify_records_the_reason(tmp_path):
    write_sync_status(str(tmp_path), _sync("clock_misaligned", error="shifted +3h"))
    assert read_bank_status(str(tmp_path))["clock"] == "clock_misaligned"


def test_write_sync_status_export_failed_says_nothing_about_the_clock(tmp_path):
    write_sync_status(str(tmp_path), _sync("export_failed", error="timeout"))
    assert read_bank_status(str(tmp_path))["clock"] == "clock_unverified"  # nunca se verificó
    write_sync_status(str(tmp_path), _sync("clock_misaligned", error="x"))
    write_sync_status(str(tmp_path), _sync("export_failed", error="timeout"))
    assert read_bank_status(str(tmp_path))["clock"] == "clock_misaligned"  # conserva lo que había


# --- collect_bank_status / import_legacy_with_status (T21) -------------------

def test_collect_bank_status_symbol_never_exported_has_no_clock(tmp_path):
    (row,) = collect_bank_status(str(tmp_path), ["XAUUSD"])
    assert (row.symbol, row.clock, row.timeframes, row.last_result) == ("XAUUSD", None, [], None)


def test_collect_bank_status_reads_status_json_and_lists_timeframes_in_bank_order(tmp_path):
    bank_dir = tmp_path / "XAUUSD"
    bank_dir.mkdir()
    for tf in ("1M", "1H", "1W"):  # a propósito desordenadas
        _write_csv(bank_dir / f"{tf}.csv", [DAY], [1.0])
    write_sync_status(str(bank_dir), SyncResult("XAUUSD", SYNC_RESULT_MERGED, verified_by="overlap",
                                                 run_id="r1", bars_added={"1H": 3}))
    write_sync_status(str(bank_dir), SyncResult("XAUUSD", "export_failed", run_id="r2", error="timeout"))

    (row,) = collect_bank_status(str(tmp_path), ["XAUUSD"])

    assert row.timeframes == ["1W", "1H", "1M"]  # el orden de ALL_BANK_TIMEFRAMES, no el alfabético
    assert (row.clock, row.verified_by) == ("verified", "overlap")
    assert (row.last_run_id, row.last_result, row.last_error) == ("r2", "export_failed", "timeout")


def test_collect_bank_status_unreadable_status_json_is_reported_not_raised(tmp_path):
    bank_dir = tmp_path / "XAUUSD"
    bank_dir.mkdir()
    (bank_dir / "status.json").write_text("{not json")
    (row,) = collect_bank_status(str(tmp_path), ["XAUUSD"])
    assert row.clock == "status.json unreadable"


def _legacy_env(tmp_path, fills, lag_hours=0, five_minute=False):
    _make_account_db(tmp_path / "xau.db", fills=_reference_fills(fills), asset="XAUUSDT.P")
    legacy_dir = tmp_path / "legacy" / "XAUUSD"
    _write_reference_15m_csv(legacy_dir, lag_hours=lag_hours)
    if five_minute:
        _write_csv(legacy_dir / "5M.csv", [T0], [1800.0])
    return legacy_dir, tmp_path / "bank" / "XAUUSD"


def _import_with_status(tmp_path, legacy_dir, bank_dir):
    return import_legacy_with_status(
        "XAUUSD", str(legacy_dir), str(bank_dir), str(tmp_path),
        real_accounts={"000": "xau.db"}, symbol_map={"XAUUSDT.P": "XAUUSD"},
        export_moment=EXPORT_MOMENT, dst_rule="none")


def test_import_legacy_with_status_verified_writes_status_and_imports(tmp_path):
    legacy_dir, bank_dir = _legacy_env(tmp_path, 12, five_minute=True)

    result = _import_with_status(tmp_path, legacy_dir, bank_dir)

    assert result.aligned is True and set(result.imported) == {"15M"}
    assert result.excluded == {"5M": LEGACY_EXCLUSION_REASON}
    status = read_bank_status(str(bank_dir))
    assert (status["clock"], status["verified_by"]) == ("verified", "references")
    assert status["last_export"] == {"run_id": LEGACY_IMPORT_RUN_ID, "result": "merged",
                                     "bars_added": {"15M": 200}}
    assert not (bank_dir / ".lock").exists()


def test_import_legacy_with_status_unverified_records_the_reason_and_imports_nothing(tmp_path):
    legacy_dir, bank_dir = _legacy_env(tmp_path, 3)

    result = _import_with_status(tmp_path, legacy_dir, bank_dir)

    assert result.imported == {} and result.excluded == {"15M": "clock_unverified"}
    status = read_bank_status(str(bank_dir))
    assert status["clock"] == "clock_unverified"
    assert "not enough reference prices" in status["last_error"]
    assert not (bank_dir / "15M.csv").exists()


def test_import_legacy_with_status_misaligned_clock_records_the_reason(tmp_path):
    legacy_dir, bank_dir = _legacy_env(tmp_path, 12, lag_hours=3)
    _import_with_status(tmp_path, legacy_dir, bank_dir)
    assert read_bank_status(str(bank_dir))["clock"] == "clock_misaligned"


def test_import_legacy_with_status_without_legacy_csvs_touches_nothing(tmp_path):
    result = import_legacy_with_status(
        "XAUUSD", str(tmp_path / "legacy" / "XAUUSD"), str(tmp_path / "bank" / "XAUUSD"), str(tmp_path),
        real_accounts={}, symbol_map={})
    assert result.aligned is None
    assert not (tmp_path / "bank").exists()  # ni el directorio del banco se creó


def test_import_legacy_with_status_with_the_lock_held_raises_and_leaves_the_bank_alone(tmp_path):
    legacy_dir, bank_dir = _legacy_env(tmp_path, 12)
    bank_dir.mkdir(parents=True)
    (bank_dir / ".lock").write_text(json.dumps({
        "pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}))

    with pytest.raises(CandleBankLockedError):
        _import_with_status(tmp_path, legacy_dir, bank_dir)

    assert not (bank_dir / "15M.csv").exists()
    assert read_bank_status(str(bank_dir)) is None


# --- import_legacy y la estación de horario (plan decisión T17; hallazgo real del 2026-09-29) ---

def _legacy_with_two_seasons(tmp_path):
    """Legacy que verifica el reloj (15M de junio, 12 referencias) y trae además 1H y 1D con una vela de enero
    (invierno) y una de julio (verano)."""
    legacy_dir, bank_dir = _legacy_env(tmp_path, 12)
    january, july = datetime(2026, 1, 15, 10), datetime(2026, 7, 15, 10)
    _write_csv(legacy_dir / "1H.csv", [january, july], [111.0, 222.0])
    _write_csv(legacy_dir / "1D.csv", [january, july], [333.0, 444.0])
    return legacy_dir, bank_dir, january, july


def test_import_legacy_drops_opposite_season_candles_on_fast_timeframes_only(tmp_path):
    legacy_dir, bank_dir, january, july = _legacy_with_two_seasons(tmp_path)

    result = import_legacy(
        "XAUUSD", str(legacy_dir), str(bank_dir), str(tmp_path),
        real_accounts={"000": "xau.db"}, symbol_map={"XAUUSDT.P": "XAUUSD"}, min_entries=10,
        export_moment=datetime(2026, 9, 15, 12), dst_rule="us",
    )

    assert result.aligned is True
    assert list(read_candle_csv(bank_csv_path(str(bank_dir), "1H"))["time"]) == [july]  # enero (invierno) no entra
    assert set(read_candle_csv(bank_csv_path(str(bank_dir), "1D"))["time"]) == {january, july}  # TF lenta: completa
    assert result.imported["1H"] == 1 and result.imported["1D"] == 2


def test_import_legacy_with_rule_none_imports_everything(tmp_path):
    legacy_dir, bank_dir, january, july = _legacy_with_two_seasons(tmp_path)

    import_legacy(
        "XAUUSD", str(legacy_dir), str(bank_dir), str(tmp_path),
        real_accounts={"000": "xau.db"}, symbol_map={"XAUUSDT.P": "XAUUSD"}, min_entries=10,
        export_moment=datetime(2026, 9, 15, 12), dst_rule="none",
    )

    assert set(read_candle_csv(bank_csv_path(str(bank_dir), "1H"))["time"]) == {january, july}


def test_import_legacy_with_status_takes_the_rule_from_the_configuration(tmp_path, monkeypatch):
    import config.auto_resolution as auto_cfg
    legacy_dir, bank_dir, january, july = _legacy_with_two_seasons(tmp_path)
    monkeypatch.setattr(auto_cfg, "BROKER_DST_RULE", "us")

    import_legacy_with_status(
        "XAUUSD", str(legacy_dir), str(bank_dir), str(tmp_path),
        real_accounts={"000": "xau.db"}, symbol_map={"XAUUSDT.P": "XAUUSD"},
        export_moment=datetime(2026, 9, 15, 12))  # sin dst_rule: sale de la configuración

    assert list(read_candle_csv(bank_csv_path(str(bank_dir), "1H"))["time"]) == [july]


def test_legacy_import_then_fresh_export_verifies_by_overlap_with_no_false_misalignment(tmp_path):
    """El caso real: un legacy con velas de invierno mal etiquetadas + un export nuevo con ellas bien
    etiquetadas. Con el filtro, el banco no arrastra las malas y la superposición (solo verano) verifica."""
    legacy_dir, bank_dir = _legacy_env(tmp_path, 12)
    winter_hours = [datetime(2026, 3, 5, 21) + timedelta(hours=h) for h in range(3)]      # mal etiquetadas en el legacy
    summer_hours = [datetime(2026, 7, 15, 0) + timedelta(hours=h) for h in range(12)]
    _write_csv(legacy_dir / "1H.csv", winter_hours + summer_hours, [100.0 + h for h in range(15)])
    import_legacy_with_status(
        "XAUUSD", str(legacy_dir), str(bank_dir), str(tmp_path),
        real_accounts={"000": "xau.db"}, symbol_map={"XAUUSDT.P": "XAUUSD"},
        export_moment=datetime(2026, 9, 15, 12), dst_rule="us")

    incoming = tmp_path / "incoming"
    incoming.mkdir()
    fixed_winter = [t - timedelta(hours=1) for t in winter_hours]  # el export nuevo las etiqueta bien
    _write_csv(incoming / "1H.csv", fixed_winter + summer_hours, [200.0 + h for h in range(3)] + [100.0 + h for h in range(3, 15)])

    result = merge_incoming_run(
        str(bank_dir), str(incoming), "XAUUSD", "r1", str(tmp_path),
        export_moment=datetime(2026, 9, 15, 12), dst_rule="us",
        real_accounts={"000": "xau.db"}, symbol_map={"XAUUSDT.P": "XAUUSD"})

    assert result.result == SYNC_RESULT_MERGED and result.verified_by == "overlap"
