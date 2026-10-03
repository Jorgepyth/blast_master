"""
T52 (spec 002): el export automático en segundo plano al guardar un unified analysis, con `AUTO_EXPORT` (RF-20, RF-20f,
N31). `subprocess.Popen` está reemplazado: ningún test lanza el exportador ni toca Windows. El único proceso real es
`candle_sync` sin exportador configurado y con todo en tmp_path, que termina en `exporter_not_configured`.
"""
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import pytest

import config.auto_resolution as auto_cfg
import tools.auto_export as auto_export
import tools.candle_sync as candle_sync
from tests.test_analysis_times import _run, _saved_and_finished, _selects, _xau_selects, clock, db  # noqa: F401
from tools.candle_bank import SYNC_RESULT_MERGED, SyncResult, bank_lock_held


class FakeProcess:
    """Un export que nunca termina mientras dura el test. Si alguien lo espera, el test falla."""

    def __init__(self, argv, **kwargs):
        self.argv, self.kwargs = argv, kwargs
        self.returncode = None
        self.polls = 0
        self.stdout = io.BytesIO()  # sin salida: con espera, el lector termina enseguida y el proceso sigue "vivo"

    def poll(self):
        self.polls += 1
        return self.returncode

    def wait(self, *args, **kwargs):
        raise AssertionError("the save waited for the export")

    communicate = wait


class FakePopen:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, argv, **kwargs):
        if self.error:
            raise self.error
        self.calls.append(FakeProcess(argv, **kwargs))
        return self.calls[-1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    data, bank_root, incoming = tmp_path / "data", tmp_path / "bank", tmp_path / "incoming"
    data.mkdir()
    bank_root.mkdir()
    monkeypatch.setattr(auto_cfg, "AUTO_EXPORT", True)
    monkeypatch.setattr(auto_cfg, "ACCOUNTS_DATA_DIR", str(data))
    monkeypatch.setattr(auto_cfg, "CANDLE_BANK_DIR", str(bank_root))
    monkeypatch.setattr(auto_cfg, "MT5_INCOMING_DIR", str(incoming))
    monkeypatch.setattr(auto_export, "_RUNNING", [])
    popen = FakePopen()
    monkeypatch.setattr(auto_export, "Popen", popen)
    return popen, data, bank_root


def _lock(bank_root, symbol, pid=None):
    """El candado de un export en curso, con el formato de `acquire_bank_lock`."""
    bank_dir = bank_root / symbol
    bank_dir.mkdir(exist_ok=True)
    (bank_dir / ".lock").write_text(json.dumps({"pid": pid or os.getpid(),
                                                "started_at": datetime.now(timezone.utc).isoformat()}))


# --- Al guardar ---------------------------------------------------------------------


def test_saving_an_analysis_launches_one_background_export_and_does_not_wait(env, db, clock, capsys):
    popen, data, _ = env
    record = _run(db, _xau_selects(), texts=("4568", "4584", "4520"))
    out = capsys.readouterr().out
    _saved_and_finished(out)
    assert record.asset == "XAUUSDT.P"
    assert len(popen.calls) == 1
    process = popen.calls[0]
    log = str(data / "candle_export.log")
    assert process.argv == [sys.executable, "-m", "tools.candle_sync", "--symbol", "XAUUSD", "--log", log]
    assert process.kwargs["start_new_session"] is True  # sigue vivo aunque se cierre el CLI
    assert process.kwargs["stdout"] is subprocess.DEVNULL and process.kwargs["stdin"] is subprocess.DEVNULL
    assert process.kwargs["stderr"].name == log and process.kwargs["stderr"].closed
    assert process.polls == 0  # nadie lo esperó: el wizard siguió con el export todavía corriendo
    assert "Candle export started in the background: XAUUSD" in out


def test_with_auto_export_off_saving_launches_nothing_and_reads_nothing(env, db, clock, capsys, monkeypatch):
    popen, _, _ = env
    monkeypatch.setattr(auto_cfg, "AUTO_EXPORT", False)

    def not_called(*args, **kwargs):
        raise AssertionError("symbols looked up with AUTO_EXPORT off")

    monkeypatch.setattr(auto_export, "symbols_for_assets", not_called)
    _run(db, _xau_selects(), texts=("4568", "4584", "4520"))
    out = capsys.readouterr().out
    _saved_and_finished(out)
    assert popen.calls == [] and "Candle export" not in out


def test_an_export_already_running_is_not_launched_again(env, db, clock, capsys):
    popen, _, bank_root = env
    _lock(bank_root, "XAUUSD")
    _run(db, _xau_selects(), texts=("4568", "4584", "4520"))
    out = capsys.readouterr().out
    _saved_and_finished(out)
    assert popen.calls == [] and "Candle export already running: XAUUSD" in out


def test_a_launch_that_fails_never_blocks_the_save(env, db, clock, capsys, monkeypatch):
    monkeypatch.setattr(auto_export, "Popen", FakePopen(error=FileNotFoundError("python not found")))
    record = _run(db, _xau_selects(), texts=("4568", "4584", "4520"))
    out = capsys.readouterr().out
    _saved_and_finished(out)
    assert float(record.mark_price) == 4568
    assert "Candle export skipped: sync_not_launched: python not found" in out


def test_an_unexpected_error_in_the_trigger_never_blocks_the_save(env, db, clock, capsys, monkeypatch):
    def broken(symbols):
        raise RuntimeError("bank unreadable")

    monkeypatch.setattr(auto_export, "start_export", broken)
    record = _run(db, _xau_selects(), texts=("4568", "4584", "4520"))
    out = capsys.readouterr().out
    _saved_and_finished(out)
    assert float(record.mark_price) == 4568
    assert "Candle export skipped: RuntimeError: bank unreadable" in out


def test_an_asset_without_mt5_symbol_launches_nothing(env, db, clock, capsys):
    popen, _, _ = env
    _run(db, _selects(), texts=("4568", "4584", "4520"))  # asset "XAU/USD"
    _saved_and_finished(capsys.readouterr().out)
    assert popen.calls == []


# --- start_export --------------------------------------------------------------------


def test_start_export_does_nothing_with_auto_export_off(env, monkeypatch):
    popen, _, _ = env
    monkeypatch.setattr(auto_cfg, "AUTO_EXPORT", False)
    assert auto_export.start_export(["XAUUSD"]) is None and popen.calls == []


def test_only_the_symbols_without_an_export_running_are_launched_in_one_process(env):
    popen, _, bank_root = env
    _lock(bank_root, "XAUUSD")
    launch = auto_export.start_export(["XAUUSD", "BTCUSD", None, "BTCUSD", "USTEC"])
    assert (launch.launched, launch.in_progress) == (["BTCUSD", "USTEC"], ["XAUUSD"])
    assert len(popen.calls) == 1
    assert popen.calls[0].argv[3:7] == ["--symbol", "BTCUSD", "--symbol", "USTEC"]


def test_a_finished_or_dead_export_does_not_count_as_running(env):
    popen, _, bank_root = env
    _lock(bank_root, "XAUUSD", pid=2 ** 22 + 12345)  # un PID que no existe
    assert auto_export.start_export(["XAUUSD"]).launched == ["XAUUSD"]
    (bank_root / "BTCUSD").mkdir()
    (bank_root / "BTCUSD" / ".lock").write_text("not json")
    assert auto_export.start_export(["BTCUSD"]).launched == ["BTCUSD"]


def test_finished_processes_are_reaped_on_the_next_launch(env):
    popen, _, _ = env
    auto_export.start_export(["XAUUSD"])
    popen.calls[0].returncode = 0
    auto_export.start_export(["BTCUSD"])
    assert auto_export._RUNNING == [popen.calls[1]]


def test_without_a_data_dir_the_log_is_skipped_but_the_export_still_launches(env, monkeypatch, tmp_path):
    popen, _, _ = env
    monkeypatch.setattr(auto_cfg, "ACCOUNTS_DATA_DIR", str(tmp_path / "missing"))
    auto_export.start_export(["XAUUSD"])
    assert popen.calls[0].kwargs["stderr"] is subprocess.DEVNULL
    assert not (tmp_path / "missing").exists()


def test_the_background_process_uses_the_same_paths_as_the_cli(env):
    popen, data, bank_root = env
    auto_export.start_export(["XAUUSD"])
    child_env = popen.calls[0].kwargs["env"]
    assert child_env["PYTHONPATH"].split(os.pathsep)[0] == auto_export.ROOT_DIR
    assert (child_env["ACCOUNTS_DATA_DIR"], child_env["CANDLE_BANK_DIR"]) == (str(data), str(bank_root))
    assert popen.calls[0].kwargs["cwd"] == auto_export.ROOT_DIR


def test_symbols_for_assets_maps_and_skips_unknown_assets():
    assert auto_export.symbols_for_assets(["XAUUSDT.P", "XAU/USD", None, "US100", "XAUUSDT.P"]) == ["XAUUSD", "USTEC"]


def test_bank_lock_held_is_read_only(tmp_path):
    bank_dir = tmp_path / "XAUUSD"
    assert bank_lock_held(str(bank_dir)) is False and not bank_dir.exists()
    _lock(tmp_path, "XAUUSD")
    before = (bank_dir / ".lock").read_bytes()
    assert bank_lock_held(str(bank_dir)) is True
    assert (bank_dir / ".lock").read_bytes() == before


# --- candle_sync con varios símbolos y log -------------------------------------------


def _fake_sync(results, order):
    def sync(symbol, **kwargs):
        order.append(symbol)
        return results[symbol]
    return sync


def test_candle_sync_exports_several_symbols_one_after_the_other(monkeypatch, tmp_path, capsys):
    order = []
    monkeypatch.setattr(candle_sync, "sync_symbol", _fake_sync({
        "XAUUSD": SyncResult("XAUUSD", SYNC_RESULT_MERGED, verified_by="overlap", bars_added={"1M": 3}),
        "BTCUSD": SyncResult("BTCUSD", auto_cfg.REASON_EXPORT_FAILED, error="exporter_exit_1: MT5\nnot running"),
        "USTEC": SyncResult("USTEC", auto_cfg.REASON_CLOCK_UNVERIFIED, error="9 references")}, order))
    log = tmp_path / "candle_export.log"
    code = candle_sync.main(["--symbol", "XAUUSD", "--symbol", "BTCUSD", "--symbol", "USTEC", "--log", str(log)])
    assert order == ["XAUUSD", "BTCUSD", "USTEC"] and code == 6  # el peor de 0, 6 y 2
    out = capsys.readouterr().out.splitlines()
    assert out == ["Candle export merged for XAUUSD (clock verified by overlap; 1M: +3)",
                   "Candle export skipped: exporter_exit_1: MT5 not running",
                   "Candle export not merged for USTEC: clock_unverified (9 references)"]
    logged = log.read_text().splitlines()
    assert [line[20:] for line in logged] == out
    assert datetime.strptime(logged[0][:19], "%Y-%m-%d %H:%M:%S")


def test_candle_sync_survives_a_closed_output_and_an_unwritable_log(monkeypatch, tmp_path):
    monkeypatch.setattr(candle_sync, "sync_symbol", _fake_sync(
        {"XAUUSD": SyncResult("XAUUSD", SYNC_RESULT_MERGED, verified_by="overlap")}, []))

    def broken_pipe(*args, **kwargs):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr("builtins.print", broken_pipe)
    assert candle_sync.main(["--symbol", "XAUUSD", "--log", str(tmp_path / "no" / "such" / "dir.log")]) == 0


def test_the_real_background_process_runs_and_logs(tmp_path, monkeypatch):
    """`python -m tools.candle_sync` de verdad, sin exportador configurado: el comando y las rutas funcionan desde un
    proceso aparte. Con todo en tmp_path y sin Windows, nunca puede lanzar `powershell.exe`."""
    data, bank_root = tmp_path / "data", tmp_path / "bank"
    data.mkdir()
    monkeypatch.setattr(auto_cfg, "AUTO_EXPORT", True)
    monkeypatch.setattr(auto_cfg, "ACCOUNTS_DATA_DIR", str(data))
    monkeypatch.setattr(auto_cfg, "CANDLE_BANK_DIR", str(bank_root))
    monkeypatch.setattr(auto_cfg, "MT5_INCOMING_DIR", str(tmp_path / "incoming"))
    monkeypatch.setattr(auto_export, "_RUNNING", [])
    monkeypatch.setenv("WINDOWS_PYTHON", "")
    monkeypatch.setenv("EXPORTER_WIN_PATH", "")
    launch = auto_export.start_export(["XAUUSD"])
    assert launch.process.wait(timeout=60) == 6
    assert "Candle export skipped: exporter_not_configured" in (data / "candle_export.log").read_text()
    status = json.loads((bank_root / "XAUUSD" / "status.json").read_text())
    assert status["last_export"]["result"] == auto_cfg.REASON_EXPORT_FAILED
    assert not (bank_root / "XAUUSD" / ".lock").exists()
