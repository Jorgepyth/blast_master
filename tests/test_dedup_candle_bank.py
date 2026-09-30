"""
T16c (spec 002, N44): `tools/dedup_candle_bank.py`, la limpieza única de las velas de 4H o más duplicadas por el
import legacy. Banco sintético en tmp_path.
"""
import os
from datetime import datetime

import pandas as pd
import pytest

import tools.dedup_candle_bank as dedup_tool
from tools.candle_bank import acquire_bank_lock, find_relabeled_duplicates, read_candle_csv
from tools.dedup_candle_bank import dedup_bank, main

STAMP = "20260930_120000"


def _write(path, rows):
    """rows: (hora GT, precio base). open=base, high=base+1, low=base-1, close=base+0.5."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pd.DataFrame({"time": [t for t, _ in rows], "open": [b for _, b in rows], "high": [b + 1 for _, b in rows],
                  "low": [b - 1 for _, b in rows], "close": [b + 0.5 for _, b in rows]}).to_csv(path, index=False)


@pytest.fixture
def bank(tmp_path):
    root = tmp_path / "candle_bank"
    _write(root / "XAUUSD" / "1D.csv", [
        (datetime(2021, 12, 26, 15), 1800.0), (datetime(2021, 12, 26, 16), 1800.0),   # par
        (datetime(2021, 12, 27, 15), 1810.0), (datetime(2021, 12, 27, 16), 1810.0),   # par
        (datetime(2021, 12, 28, 15), 1820.0), (datetime(2021, 12, 28, 16), 1825.0),   # a 1 h, pero otros precios
        (datetime(2022, 7, 4, 15), 1830.0),                                             # sola
    ])
    _write(root / "XAUUSD" / "4H.csv", [(datetime(2025, 11, 2, 15), 4000.0), (datetime(2025, 11, 2, 16), 4000.0)])
    _write(root / "XAUUSD" / "1H.csv", [(datetime(2026, 6, 1, 10), 3300.0), (datetime(2026, 6, 1, 11), 3300.0)])
    _write(root / "BTCUSD" / "1W.csv", [(datetime(2024, 11, 2, 15), 70000.0), (datetime(2024, 11, 2, 16), 70000.0)])
    (root / "US500").mkdir()  # sin CSV: nada que hacer
    return root


def _bytes(root):
    return {os.path.relpath(os.path.join(d, f), root): open(os.path.join(d, f), "rb").read()
            for d, _, files in os.walk(root) for f in files}


def test_by_default_it_only_reports_and_writes_nothing(bank, tmp_path):
    before = _bytes(bank)

    report = dedup_bank(str(bank), backup_root=str(tmp_path / "archives"), stamp=STAMP)

    assert not report.applied
    assert {(f.symbol, f.timeframe): len(f.pairs) for f in report.files} == {
        ("BTCUSD", "1W"): 1, ("XAUUSD", "1D"): 2, ("XAUUSD", "4H"): 1}
    assert report.total_pairs == 4
    assert _bytes(bank) == before
    assert not (tmp_path / "archives").exists()


def test_apply_backs_up_the_original_files_removes_only_the_earlier_copies_and_verifies(bank, tmp_path):
    before = _bytes(bank)

    report = dedup_bank(str(bank), apply=True, backup_root=str(tmp_path / "archives"), stamp=STAMP)

    assert report.applied and report.total_pairs == 4 and report.errors == {}
    backup = tmp_path / "archives" / f"candle_bank_pre_dedup_{STAMP}"
    assert report.backup_dir == str(backup)
    assert _bytes(backup) == {k: before[k] for k in ("XAUUSD/1D.csv", "XAUUSD/4H.csv", "BTCUSD/1W.csv")}

    daily = read_candle_csv(str(bank / "XAUUSD" / "1D.csv"))
    assert list(daily["time"]) == [pd.Timestamp(2021, 12, 26, 16), pd.Timestamp(2021, 12, 27, 16),
                                   pd.Timestamp(2021, 12, 28, 15), pd.Timestamp(2021, 12, 28, 16),
                                   pd.Timestamp(2022, 7, 4, 15)]
    assert list(read_candle_csv(str(bank / "XAUUSD" / "4H.csv"))["time"]) == [pd.Timestamp(2025, 11, 2, 16)]
    assert list(read_candle_csv(str(bank / "BTCUSD" / "1W.csv"))["time"]) == [pd.Timestamp(2024, 11, 2, 16)]
    assert _bytes(bank)["XAUUSD/1H.csv"] == before["XAUUSD/1H.csv"]  # 1H nunca se toca
    assert find_relabeled_duplicates(daily, "1D") == []
    assert {(f.symbol, f.timeframe): (f.rows_before, f.rows_after) for f in report.files} == {
        ("BTCUSD", "1W"): (2, 1), ("XAUUSD", "1D"): (7, 5), ("XAUUSD", "4H"): (2, 1)}


def test_a_second_run_finds_nothing_and_backs_up_nothing(bank, tmp_path):
    dedup_bank(str(bank), apply=True, backup_root=str(tmp_path / "archives"), stamp=STAMP)

    again = dedup_bank(str(bank), apply=True, backup_root=str(tmp_path / "archives"), stamp="20260930_130000")

    assert again.total_pairs == 0 and again.backup_dir is None
    assert os.listdir(tmp_path / "archives") == [f"candle_bank_pre_dedup_{STAMP}"]


def test_a_symbol_with_an_export_in_progress_is_skipped_untouched(bank, tmp_path):
    before = _bytes(bank)
    with acquire_bank_lock(str(bank / "XAUUSD")):
        report = dedup_bank(str(bank), apply=True, backup_root=str(tmp_path / "archives"), stamp=STAMP)

    assert set(report.skipped) == {"XAUUSD"} and "export_in_progress" in report.skipped["XAUUSD"]
    assert _bytes(bank)["XAUUSD/1D.csv"] == before["XAUUSD/1D.csv"]
    assert [(f.symbol, f.rows_after) for f in report.files] == [("BTCUSD", 1)]


def test_a_failed_verification_restores_the_original_file(bank, tmp_path, monkeypatch):
    before = _bytes(bank)
    real_write = dedup_tool.write_candle_csv_atomic

    def lossy_write(path, df):  # simula una escritura que pierde una vela que no era duplicada
        real_write(path, df.iloc[:-1] if path.endswith("1D.csv") else df)

    monkeypatch.setattr(dedup_tool, "write_candle_csv_atomic", lossy_write)
    report = dedup_bank(str(bank), apply=True, backup_root=str(tmp_path / "archives"), stamp=STAMP)

    assert set(report.errors) == {("XAUUSD", "1D")}
    assert _bytes(bank)["XAUUSD/1D.csv"] == before["XAUUSD/1D.csv"]  # restaurado desde el respaldo
    assert _bytes(bank)["BTCUSD/1W.csv"] != before["BTCUSD/1W.csv"]  # los demás sí se limpiaron


def test_main_dry_run_and_apply_print_one_line_per_file_and_a_summary(bank, tmp_path, capsys):
    args = ["--bank-dir", str(bank), "--backup-dir", str(tmp_path / "archives")]

    assert main(args) == 0
    out = capsys.readouterr().out
    assert "XAUUSD 1D: 2 relabeled duplicates (e.g. 2021-12-26 15:00 is 2021-12-26 16:00 labeled 1h early)" in out
    assert "Dry run: nothing was written" in out

    assert main(args + ["--apply"]) == 0
    out = capsys.readouterr().out
    assert "Removed 4 relabeled duplicates from 3 file(s)" in out and "Backup of the original files:" in out
    assert "Verified: no relabeled duplicates left" in out

    assert main(args) == 0
    assert "No relabeled duplicates found." in capsys.readouterr().out


def test_main_exits_1_when_a_symbol_was_skipped(bank, tmp_path, capsys):
    with acquire_bank_lock(str(bank / "BTCUSD")):
        code = main(["--bank-dir", str(bank), "--backup-dir", str(tmp_path / "archives"), "--apply"])
    assert code == 1
    assert "BTCUSD: skipped" in capsys.readouterr().out
