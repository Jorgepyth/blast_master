"""
T20 (spec 002, RF-20e, RF-20f, RF-1c): tools/candle_sync.py.

El exportador real (WSL -> powershell.exe -> Python de Windows -> MT5) no se
puede probar acá: lo cubre el spike (T22). Estos tests usan un exportador
FALSO, un script de Python en `tmp_path` que imita su contrato -- crea
`{incoming}/{SIMBOLO}/{run_id}/`, copia ahí los CSV de un directorio "payload",
e imprime `RUN_ID: ...` --, más modos que fallan: MT5 caído (código 1), colgado
(timeout) y sin RUN_ID.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import tools.candle_sync as candle_sync
from tools.candle_bank import read_bank_status, read_candle_csv
from tools.candle_sync import (
    ExporterRun,
    WINDOWS_CWD,
    build_export_command,
    derive_export_anchors,
    kill_windows_tree,
    main,
    powershell_argv,
    run_exporter,
    sync_symbol,
    wsl_to_windows_path,
)
from tools.database import Base, UnifiedDepartment

FAKE_EXPORTER = r'''
import os, shutil, sys, time
mode, incoming_root, symbol, payload_dir, marker = sys.argv[1:6]
with open(marker, "w") as f:
    f.write(str(os.getpid()))
if mode == "mt5_down":
    print("mt5.initialize() failed: (1, 'Terminal: Authorization failed')", file=sys.stderr)
    sys.exit(1)
if mode == "hang":
    time.sleep(60)
if mode == "hang_winpid":  # como el wrapper de PowerShell: primero su PID de Windows, y se cuelga
    sys.stdout.write("WINPID: 4242\r\n")
    sys.stdout.flush()
    time.sleep(60)
if mode == "bad_bytes":  # mensaje de Windows en otra codepage (bytes que no son UTF-8)
    sys.stderr.buffer.write(b"error de autorizaci\xf3n \xff\xfe\n")
    sys.stderr.flush()
    sys.exit(1)
run_id = "20261005T142011"
run_dir = os.path.join(incoming_root, symbol, run_id)
os.makedirs(run_dir)
for name in os.listdir(payload_dir):
    shutil.copy(os.path.join(payload_dir, name), run_dir)
if mode == "ok":
    print("WINPID: 4242")
    print("RUN_ID: " + run_id)
    print("RUN_DIR: " + run_dir)
elif mode == "ok_partial":  # el exportador salteó el 1M (RF-15) pero el resto salió bien
    print("SKIPPED_TF: 1M")
    print("RUN_ID: " + run_id)
else:  # "no_run_id"
    print("done")
'''

DAY = datetime(2026, 9, 10)


def _write_csv(path, hours, base=100.0):
    times = [DAY + timedelta(hours=h) for h in range(hours)]
    closes = [base + h for h in range(hours)]
    pd.DataFrame({"time": times, "open": closes, "high": [c + 1 for c in closes],
                  "low": [c - 1 for c in closes], "close": closes}).to_csv(path, index=False)


class Env:
    """Rutas de un banco, una carpeta de llegada y un exportador falso, todo en tmp_path."""

    def __init__(self, tmp_path):
        self.bank_root = tmp_path / "bank"
        self.incoming_root = tmp_path / "incoming"
        self.accounts_dir = tmp_path / "accounts"
        self.payload_dir = tmp_path / "payload"
        self.marker = tmp_path / "launched.pid"
        self.script = tmp_path / "fake_exporter.py"
        for d in (self.bank_root, self.incoming_root, self.accounts_dir, self.payload_dir):
            d.mkdir()
        self.script.write_text(FAKE_EXPORTER)
        self.bank_dir = self.bank_root / "XAUUSD"
        self.killed = []  # PID de Windows que se pidió matar (taskkill falso: nunca se toca Windows)

    def killer(self, windows_pid):
        self.killed.append(windows_pid)
        return None

    def command(self, mode="ok"):
        return [sys.executable, str(self.script), mode, str(self.incoming_root), "XAUUSD",
                str(self.payload_dir), str(self.marker)]

    def seed_bank(self, hours=12):
        self.bank_dir.mkdir(parents=True, exist_ok=True)
        _write_csv(self.bank_dir / "1H.csv", hours)

    def seed_payload(self, hours=15):
        _write_csv(self.payload_dir / "1H.csv", hours)  # las mismas primeras horas que el banco + nuevas

    def sync(self, mode="ok", **kw):
        kw.setdefault("export_command", self.command(mode))
        kw.setdefault("timeout_s", 30)
        kw.setdefault("windows_killer", self.killer)
        return sync_symbol(
            "XAUUSD", bank_root=str(self.bank_root), incoming_root=str(self.incoming_root),
            accounts_data_dir=str(self.accounts_dir), dst_rule="none",
            real_accounts={}, symbol_map={}, **kw)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


# --- wsl_to_windows_path / build_export_command ------------------------------

def test_wsl_to_windows_path_converts_a_drive_mount():
    assert wsl_to_windows_path("/mnt/c/Users/x/MT5Exports/_incoming") == "C:\\Users\\x\\MT5Exports\\_incoming"
    assert wsl_to_windows_path("/mnt/d") == "D:\\"


def test_wsl_to_windows_path_rejects_paths_outside_a_drive_mount():
    with pytest.raises(ValueError, match="/mnt/<drive>/"):
        wsl_to_windows_path("/home/user/incoming")


def test_build_export_command_from_configuration():
    command = build_export_command(
        "XAUUSD", "C:\\Py\\python.exe", "C:\\repo\\windows_export\\export_p2_ohlc.py", "/mnt/c/Users/x/_incoming",
        datetime(2026, 5, 18, 12, 15), datetime(2026, 9, 3, 18, 19), "us")

    assert command[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
    script = command[4]
    assert script.startswith("$env:PYTHONIOENCODING = 'utf-8'; Write-Output ('WINPID: ' + $PID); "
                             "& 'C:\\Py\\python.exe' 'C:\\repo\\windows_export\\export_p2_ohlc.py'")
    for expected in ("'--symbol' 'XAUUSD'", "'--out-dir' 'C:\\Users\\x\\_incoming'", "'--per-run-dir'",
                     "'--min-anchor' '2026-05-18 12:15:00'", "'--max-anchor' '2026-09-03 18:19:00'",
                     "'--dst-rule' 'us'"):
        assert expected in script
    assert "--timeframes" not in script
    assert script.endswith("; exit $LASTEXITCODE")  # conserva el código de salida del exportador


def test_build_export_command_passes_timeframes_when_asked_and_escapes_single_quotes():
    command = build_export_command(
        "XAUUSD", "C:\\Users\\O'Brien\\python.exe", "C:\\e.py", "/mnt/c/in",
        datetime(2026, 1, 1), datetime(2026, 1, 2), "none", timeframes=["1H", "5M"])
    assert "'C:\\Users\\O''Brien\\python.exe'" in command[4]
    assert "'--timeframes' '1H,5M'" in command[4]


# --- run_exporter --------------------------------------------------------------

def test_run_exporter_captures_output_and_run_id(env):
    env.seed_payload()
    run = run_exporter(env.command("ok"), timeout_s=30)
    assert (run.returncode, run.timed_out, run.launch_error) == (0, False, None)
    assert run.run_id == "20261005T142011"


def test_run_exporter_reports_a_command_that_cannot_be_launched():
    run = run_exporter(["/definitely/not/a/binary"], timeout_s=5)
    assert run.returncode is None
    assert "FileNotFoundError" in run.launch_error


def test_exporter_run_without_a_run_id_line_has_none():
    assert ExporterRun(returncode=0, stdout="done\n").run_id is None


# --- sync_symbol: éxito --------------------------------------------------------

def test_success_runs_the_exporter_merges_and_writes_status(env):
    env.seed_bank(12)
    env.seed_payload(15)

    result = env.sync("ok")

    assert result.result == "merged"
    assert result.verified_by == "overlap"
    assert result.run_id == "20261005T142011"
    assert result.bars_added == {"1H": 3}
    assert len(read_candle_csv(str(env.bank_dir / "1H.csv"))) == 15
    status = read_bank_status(str(env.bank_dir))
    assert (status["clock"], status["verified_by"]) == ("verified", "overlap")
    assert status["last_export"] == {"run_id": "20261005T142011", "result": "merged", "bars_added": {"1H": 3}}
    assert status["last_error"] is None
    assert not (env.bank_dir / ".lock").exists()  # el candado se soltó


# --- sync_symbol: RF-20e, export_failed ------------------------------------------

def test_timeout_is_export_failed_bank_untouched_and_the_exporter_is_killed(env):
    env.seed_bank(12)
    before = (env.bank_dir / "1H.csv").read_bytes()

    started = time.monotonic()
    result = env.sync("hang", timeout_s=1)
    elapsed = time.monotonic() - started

    assert elapsed < 20
    assert result.result == "export_failed"
    assert result.error.startswith("timeout")
    assert (env.bank_dir / "1H.csv").read_bytes() == before
    pid = int(env.marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # el proceso ya no existe
    status = read_bank_status(str(env.bank_dir))
    assert status["last_export"]["result"] == "export_failed"
    assert status["last_error"].startswith("timeout")
    assert not (env.bank_dir / ".lock").exists()


def test_mt5_unavailable_exit_code_1_is_export_failed_and_bank_untouched(env):
    env.seed_bank(12)
    before = (env.bank_dir / "1H.csv").read_bytes()

    result = env.sync("mt5_down")

    assert result.result == "export_failed"
    assert "exporter_exit_1" in result.error and "mt5.initialize() failed" in result.error
    assert (env.bank_dir / "1H.csv").read_bytes() == before
    assert os.listdir(env.incoming_root) == []  # ni carpeta de corrida


def test_a_failed_export_does_not_revoke_the_clock_verification_of_an_already_merged_bank(env):
    env.seed_bank(12)
    env.seed_payload(15)
    assert env.sync("ok").result == "merged"

    result = env.sync("mt5_down")

    assert result.result == "export_failed"
    assert read_bank_status(str(env.bank_dir))["clock"] == "verified"


def test_exporter_that_finishes_ok_without_a_run_id_is_export_failed(env):
    env.seed_payload()
    result = env.sync("no_run_id")
    assert result.result == "export_failed"
    assert result.error.startswith("no_run_dir")


def test_a_command_that_cannot_be_launched_is_export_failed(env):
    result = env.sync(export_command=["/definitely/not/a/binary"])
    assert result.result == "export_failed"
    assert result.error.startswith("exporter_not_launched")


def test_missing_configuration_is_export_failed_with_a_clear_reason(env):
    result = env.sync(export_command=None, windows_python="", exporter_win_path="")
    assert result.result == "export_failed"
    assert result.error.startswith("exporter_not_configured")
    assert "WINDOWS_PYTHON" in result.error


def test_clock_not_verified_is_reported_and_nothing_is_merged(env):
    # Banco vacío y sin referencias en las DBs: no hay cómo verificar el reloj.
    env.seed_payload(5)
    result = env.sync("ok")
    assert result.result == "clock_unverified"
    assert not (env.bank_dir / "1H.csv").exists()
    assert read_bank_status(str(env.bank_dir))["clock"] == "clock_unverified"


# --- sync_symbol: RF-20f, candado tomado ------------------------------------------

def test_lock_held_by_another_export_skips_without_launching_anything(env):
    env.seed_bank(12)
    before = (env.bank_dir / "1H.csv").read_bytes()
    (env.bank_dir / ".lock").write_text(json.dumps({
        "pid": os.getpid(),  # el propio proceso de test: garantizado vivo
        "started_at": datetime.now(timezone.utc).isoformat(),
    }))

    result = env.sync("ok")

    assert result.result == "locked"
    assert result.error.startswith("export_in_progress")
    assert not env.marker.exists()  # el exportador ni siquiera se lanzó
    assert (env.bank_dir / "1H.csv").read_bytes() == before
    assert read_bank_status(str(env.bank_dir)) is None  # y no se pisó el estado del otro export


# --- Comando armado desde la configuración, anclas y rutas -------------------------

def _account_db(path, rows):
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        for i, (created_at, asset) in enumerate(rows):
            session.add(UnifiedDepartment(
                id=f"a{i}", state="READY_FOR_NOTION", asset=asset, market_bias="Bullish", calc_edge=0.5,
                p4_hierarchy="x", p1_timeframe="15M", p1_type="x", nodes_l1=1, nodes_l2=1,
                tactical_classification="x", long_prob=0.5, short_prob=0.5, no_trade_prob=0.0,
                created_at=created_at))
        session.commit()
    engine.dispose()


def test_derive_export_anchors_uses_min_and_max_created_at_of_the_symbol_only(tmp_path):
    _account_db(tmp_path / "xau.db", [
        (datetime(2026, 5, 18, 12, 15), "XAUUSDT.P"), (datetime(2026, 9, 3, 18, 19), "XAUUSDT.P"),
        (datetime(2025, 1, 1), "BTCUSDT.P"),  # otro símbolo: no cuenta
    ])
    now = datetime(2026, 9, 28, 10, 0)

    anchors = derive_export_anchors("XAUUSD", str(tmp_path), now, {"000": "xau.db"}, {"XAUUSDT.P": "XAUUSD"})

    assert anchors == (datetime(2026, 5, 18, 12, 15), datetime(2026, 9, 3, 18, 19))


def test_derive_export_anchors_without_analyses_falls_back_to_now(tmp_path):
    now = datetime(2026, 9, 28, 10, 0)
    assert derive_export_anchors("XAUUSD", str(tmp_path), now, {"000": "missing.db"},
                                 {"XAUUSDT.P": "XAUUSD"}) == (now, now)


def test_sync_builds_the_powershell_command_from_configuration_and_the_database(tmp_path, monkeypatch):
    _account_db(tmp_path / "xau.db", [(datetime(2026, 5, 18, 12, 15), "XAUUSDT.P")])
    seen = {}

    def fake_run(command, timeout_s, **kw):
        seen["command"], seen["timeout_s"], seen["cwd"] = list(command), timeout_s, kw.get("cwd")
        return ExporterRun(returncode=1, stderr="boom")

    monkeypatch.setattr(candle_sync, "run_exporter", fake_run)  # nunca se lanza powershell.exe de verdad

    result = sync_symbol(
        "XAUUSD", bank_root=str(tmp_path / "bank"), incoming_root="/mnt/c/fake/_incoming",
        accounts_data_dir=str(tmp_path), dst_rule="us", timeout_s=42,
        windows_python="C:\\Py\\python.exe", exporter_win_path="C:\\e.py",
        real_accounts={"000": "xau.db"}, symbol_map={"XAUUSDT.P": "XAUUSD"},
        now_gt=datetime(2026, 9, 28, 10, 0))

    assert result.result == "export_failed" and "exporter_exit_1: boom" in result.error
    assert seen["timeout_s"] == 42
    assert seen["cwd"] == WINDOWS_CWD  # nunca desde la ruta UNC de WSL, que cmd.exe rechaza
    script = seen["command"][4]
    assert seen["command"][0] == "powershell.exe"
    assert "'--min-anchor' '2026-05-18 12:15:00'" in script
    assert "'--out-dir' 'C:\\fake\\_incoming'" in script and "'--dst-rule' 'us'" in script


def test_incoming_dir_outside_a_windows_drive_is_export_failed_without_launching(tmp_path, monkeypatch):
    monkeypatch.setattr(candle_sync, "run_exporter",
                        lambda *a, **k: pytest.fail("no debería lanzarse el exportador"))
    result = sync_symbol(
        "XAUUSD", bank_root=str(tmp_path / "bank"), incoming_root="/home/user/incoming",
        accounts_data_dir=str(tmp_path), dst_rule="none", timeout_s=5,
        windows_python="C:\\Py\\python.exe", exporter_win_path="C:\\e.py", real_accounts={}, symbol_map={})
    assert result.result == "export_failed"
    assert result.error.startswith("incoming_dir_not_on_windows_drive")


# --- Proceso: main y códigos de salida ----------------------------------------------

def _main(env, mode, *extra, capsys=None):
    code = main(["--symbol", "XAUUSD", *extra], export_command=env.command(mode),
                bank_root=str(env.bank_root), incoming_root=str(env.incoming_root),
                accounts_data_dir=str(env.accounts_dir), dst_rule="none", real_accounts={}, symbol_map={})
    return code, capsys.readouterr().out


def test_main_exit_0_when_merged(env, capsys):
    env.seed_bank(12)
    env.seed_payload(15)
    code, out = _main(env, "ok", capsys=capsys)
    assert code == 0
    assert "Candle export merged for XAUUSD" in out and "1H: +3" in out


def test_main_exit_2_when_the_clock_is_not_verified(env, capsys):
    env.seed_payload(5)
    code, out = _main(env, "ok", capsys=capsys)
    assert code == 2
    assert "not merged for XAUUSD: clock_unverified" in out


def test_main_exit_6_with_the_skipped_line_when_mt5_is_down(env, capsys):
    code, out = _main(env, "mt5_down", capsys=capsys)
    assert code == 6
    assert out.startswith("Candle export skipped: exporter_exit_1")


def test_main_wait_seconds_bounds_the_wait_for_the_exporter(env, capsys):
    code, out = _main(env, "hang", "--wait-seconds", "1", capsys=capsys)
    assert code == 6
    assert "Candle export skipped: timeout" in out


# --- T20b: matar el árbol de Windows al vencer el timeout, cwd y decodificación ---

def test_powershell_argv_prints_the_windows_pid_first_and_keeps_the_exit_code():
    argv = powershell_argv(["C:\\Windows\\System32\\PING.EXE", "-n", "3", "127.0.0.1"])
    assert argv[:4] == ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
    assert argv[4] == ("$env:PYTHONIOENCODING = 'utf-8'; Write-Output ('WINPID: ' + $PID); "
                       "& 'C:\\Windows\\System32\\PING.EXE' '-n' '3' '127.0.0.1'; exit $LASTEXITCODE")
    assert '"' not in argv[4]  # las comillas dobles se estropean camino a Windows


def test_exporter_run_parses_windows_pid_and_run_id_from_crlf_output():
    run = ExporterRun(returncode=0, stdout="WINPID: 6608\r\n\r\nRUN_ID: 20261005T142011\r\nRUN_DIR: C:\\x\r\n")
    assert run.windows_pid == 6608
    assert run.run_id == "20261005T142011"
    assert ExporterRun(returncode=0, stdout="nada\n").windows_pid is None


def test_timeout_kills_the_windows_process_tree_reported_by_the_exporter(env):
    env.seed_bank(12)

    result = env.sync("hang_winpid", timeout_s=1)

    assert env.killed == [4242]  # se pidió matar exactamente el árbol de ese PID de Windows
    assert result.result == "export_failed"
    assert "timeout" in result.error and "Windows process tree 4242 killed" in result.error
    with pytest.raises(ProcessLookupError):
        os.kill(int(env.marker.read_text()), 0)  # y el lado WSL también murió


def test_timeout_reports_when_the_windows_process_could_not_be_killed(env):
    result = env.sync("hang_winpid", timeout_s=1, windows_killer=lambda pid: "taskkill exited 128: not found")
    assert "could NOT kill Windows process 4242 (taskkill exited 128: not found)" in result.error


def test_timeout_without_a_winpid_line_does_not_call_the_windows_killer(env):
    result = env.sync("hang", timeout_s=1)
    assert env.killed == []
    assert result.error.startswith("timeout") and "Windows" not in result.error


def test_successful_export_never_calls_the_windows_killer(env):
    env.seed_bank(12)
    env.seed_payload(15)
    assert env.sync("ok").result == "merged"
    assert env.killed == []


def test_windows_error_output_with_non_utf8_bytes_is_still_a_clean_export_failed(env):
    result = env.sync("bad_bytes")
    assert result.result == "export_failed"
    assert result.error.startswith("exporter_exit_1")  # no "unexpected_error: UnicodeDecodeError"


def test_run_exporter_honors_the_working_directory(tmp_path):
    (tmp_path / "wd").mkdir()
    run = run_exporter([sys.executable, "-c", "import os; print(os.getcwd())"], 30, cwd=str(tmp_path / "wd"))
    assert run.stdout.strip() == str(tmp_path / "wd")


def _fake_taskkill(tmp_path, exit_code):
    script = tmp_path / "fake_taskkill.py"
    script.write_text(
        "import json, sys\n"
        f"open({str(tmp_path / 'taskkill_args.json')!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "print('ERROR: process not found', file=sys.stderr)\n"
        f"sys.exit({exit_code})\n")
    return [sys.executable, str(script)]


def test_kill_windows_tree_runs_taskkill_force_tree_on_the_pid(tmp_path):
    assert kill_windows_tree(4242, taskkill_command=_fake_taskkill(tmp_path, 0)) is None
    assert json.loads((tmp_path / "taskkill_args.json").read_text()) == ["/F", "/T", "/PID", "4242"]


def test_kill_windows_tree_reports_a_failing_taskkill(tmp_path):
    note = kill_windows_tree(4242, taskkill_command=_fake_taskkill(tmp_path, 128))
    assert note.startswith("taskkill exited 128") and "process not found" in note


def test_kill_windows_tree_reports_a_missing_taskkill_without_raising():
    assert kill_windows_tree(4242, taskkill_command=["/definitely/not/taskkill"]) == "/definitely/not/taskkill not found"


# --- Spike (2026-09-29): un export parcial (una TF salteada) se fusiona y se nota -----

def test_exporter_run_lists_the_timeframes_it_skipped():
    run = ExporterRun(returncode=0, stdout="SKIPPED_TF: 1M\r\nSKIPPED_TF: 5M\r\nRUN_ID: r1\r\n")
    assert run.skipped_timeframes == ["1M", "5M"]
    assert ExporterRun(returncode=0, stdout="RUN_ID: r1\n").skipped_timeframes == []


def test_a_partial_export_still_merges_and_says_which_timeframes_were_skipped(env):
    env.seed_bank(12)
    env.seed_payload(15)

    result = env.sync("ok_partial")

    assert result.result == "merged" and result.bars_added == {"1H": 3}
    assert result.error == "exporter skipped timeframes: 1M"
    assert read_bank_status(str(env.bank_dir))["last_error"] == "exporter skipped timeframes: 1M"
    assert candle_sync.format_result_line(result).endswith("-- exporter skipped timeframes: 1M")


def test_a_complete_export_has_no_skipped_note_in_the_summary_line(env):
    env.seed_bank(12)
    env.seed_payload(15)
    result = env.sync("ok")
    assert result.error is None
    assert "--" not in candle_sync.format_result_line(result)
