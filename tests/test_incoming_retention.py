"""
T62a (spec 002, N47): retención de `_incoming`. Al final de cada export se conservan las 3 corridas más recientes del
símbolo y se borran las demás, pero solo carpetas de corrida (`YYYYmmddTHHMMSS`, con `-N` opcional) que contengan
únicamente CSV de temporalidades. Todo lo demás queda intacto. Todo en tmp_path: nunca `/mnt/c` (RF-16).
"""
import os

import pytest

import config.auto_resolution as auto_cfg
from tests.test_candle_sync import Env
from tools.candle_sync import format_result_line, prune_incoming_runs

CSVS = ("1M.csv", "15M.csv", "4H.csv")


def _run(root, symbol, name, files=CSVS):
    run_dir = root / symbol / name
    run_dir.mkdir(parents=True)
    for file_name in files:
        (run_dir / file_name).write_text("time,open,high,low,close\n")
    return run_dir


def _names(root, symbol):
    return sorted(os.listdir(root / symbol))


def test_the_default_is_to_keep_3_runs():
    assert auto_cfg.INCOMING_RUNS_KEEP == 3


def test_keeps_the_3_newest_runs_and_deletes_the_older_ones(tmp_path):
    names = ["20260929T153134", "20260929T153707", "20260930T190335", "20261001T080000", "20261002T090000"]
    for name in names:
        _run(tmp_path, "XAUUSD", name)
    result = prune_incoming_runs(str(tmp_path), "XAUUSD")
    assert result.removed == names[:2] and result.skipped == []
    assert _names(tmp_path, "XAUUSD") == names[2:]


def test_runs_with_a_retry_suffix_are_ordered_by_time_and_then_by_the_suffix(tmp_path):
    for name in ("20261001T080000", "20261001T080000-2", "20261001T080000-10"):
        _run(tmp_path, "XAUUSD", name)
    # -10 es posterior a -2 aunque como texto vaya antes: con keep=1 queda -10.
    assert prune_incoming_runs(str(tmp_path), "XAUUSD", keep=1).removed == ["20261001T080000", "20261001T080000-2"]
    assert _names(tmp_path, "XAUUSD") == ["20261001T080000-10"]


def test_anything_that_is_not_a_run_folder_is_never_touched(tmp_path, monkeypatch):
    for name in ("20260901T000000", "20260902T000000", "20260903T000000", "20260904T000000"):
        _run(tmp_path, "XAUUSD", name)
    symbol_dir = tmp_path / "XAUUSD"
    (symbol_dir / "notes").mkdir()
    (symbol_dir / "notes" / "1M.csv").write_text("x")
    (symbol_dir / "20260801T000000_old").mkdir()
    (symbol_dir / "20260801T000000").write_text("a file named like a run")
    (symbol_dir / "README.txt").write_text("x")
    outside = _run(tmp_path, "elsewhere", "20260701T000000")
    os.symlink(outside, symbol_dir / "20260702T000000")  # un enlace con nombre de corrida, a otra carpeta

    result = prune_incoming_runs(str(tmp_path), "XAUUSD")

    assert result.removed == ["20260901T000000"]
    assert _names(tmp_path, "XAUUSD") == ["20260702T000000", "20260801T000000", "20260801T000000_old",
                                          "20260902T000000", "20260903T000000", "20260904T000000", "README.txt",
                                          "notes"]
    assert sorted(os.listdir(outside)) == sorted(CSVS)  # el destino del enlace sigue entero


@pytest.mark.parametrize("extra", ["manifest.json", "1M.csv.tmp", "sub"])
def test_an_old_run_with_anything_else_inside_is_kept_and_reported(tmp_path, extra):
    old = _run(tmp_path, "XAUUSD", "20260901T000000")
    if extra == "sub":
        (old / "sub").mkdir()
    else:
        (old / extra).write_text("x")
    for name in ("20260902T000000", "20260903T000000", "20260904T000000"):
        _run(tmp_path, "XAUUSD", name)
    result = prune_incoming_runs(str(tmp_path), "XAUUSD")
    assert result.removed == [] and result.skipped == ["20260901T000000"]
    assert sorted(os.listdir(old)) == sorted(CSVS + (extra,))  # ni un archivo menos


def test_a_csv_that_is_a_link_also_keeps_the_run(tmp_path):
    target = tmp_path / "target.csv"
    target.write_text("keep me")
    old = _run(tmp_path, "XAUUSD", "20260901T000000", files=("1M.csv",))
    os.symlink(target, old / "15M.csv")
    for name in ("20260902T000000", "20260903T000000", "20260904T000000"):
        _run(tmp_path, "XAUUSD", name)
    assert prune_incoming_runs(str(tmp_path), "XAUUSD").skipped == ["20260901T000000"]
    assert target.read_text() == "keep me"


def test_other_symbols_and_the_protected_run_are_never_touched(tmp_path):
    for name in ("20260901T000000", "20260902T000000", "20260903T000000", "20260904T000000"):
        _run(tmp_path, "XAUUSD", name)
        _run(tmp_path, "BTCUSD", name)
    result = prune_incoming_runs(str(tmp_path), "XAUUSD", protect="20260901T000000")
    assert result.removed == [] and len(_names(tmp_path, "XAUUSD")) == 4
    assert len(_names(tmp_path, "BTCUSD")) == 4


def test_a_missing_symbol_folder_or_a_symbol_folder_that_is_a_link_is_left_alone(tmp_path):
    assert prune_incoming_runs(str(tmp_path), "XAUUSD").removed == []
    real = tmp_path / "real"
    for name in ("20260901T000000", "20260902T000000", "20260903T000000", "20260904T000000"):
        _run(real, "XAUUSD", name)
    (tmp_path / "incoming").mkdir()
    os.symlink(real / "XAUUSD", tmp_path / "incoming" / "XAUUSD")
    assert prune_incoming_runs(str(tmp_path / "incoming"), "XAUUSD").removed == []
    assert len(_names(real, "XAUUSD")) == 4


@pytest.mark.parametrize("keep", [0, -1])
def test_keeping_less_than_one_run_is_refused(tmp_path, keep):
    with pytest.raises(ValueError):
        prune_incoming_runs(str(tmp_path), "XAUUSD", keep=keep)


def test_each_export_prunes_its_symbol_and_says_so(tmp_path):
    env = Env(tmp_path)
    env.seed_bank()
    env.seed_payload()
    for name in ("20260929T153134", "20260930T190335", "20261001T080000"):
        _run(env.incoming_root, "XAUUSD", name, files=("1H.csv",))
    (env.incoming_root / "XAUUSD" / "notes").mkdir()

    result = env.sync()  # el exportador falso escribe la corrida 20261005T142011

    assert result.pruned_runs == ["20260929T153134"]
    assert _names(env.incoming_root, "XAUUSD") == ["20260930T190335", "20261001T080000", "20261005T142011", "notes"]
    assert "removed old export runs: 20260929T153134" in format_result_line(result)


def test_a_failing_prune_never_breaks_the_export(tmp_path, monkeypatch):
    import tools.candle_sync as candle_sync

    def broken(*args, **kwargs):
        raise PermissionError("drvfs said no")

    monkeypatch.setattr(candle_sync, "prune_incoming_runs", broken)
    env = Env(tmp_path)
    env.seed_bank()
    env.seed_payload()
    result = env.sync()
    assert result.result == "merged" and result.pruned_runs == []
    assert "could not remove old export runs" in format_result_line(result)
