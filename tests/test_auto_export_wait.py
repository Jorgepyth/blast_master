"""
T53 (spec 002): el export al abrir un Efficiency o Tactical Audit, con espera acotada y progreso (RF-20b, RF-20e). La
espera termina en `AUTO_EXPORT_WAIT_S` y el audit sigue con las velas del banco, con una línea "Candle export skipped".
El reloj de `wait_for_export` es falso; los procesos son falsos salvo el `candle_sync` real del final, sin exportador
configurado y con todo en tmp_path.
"""
import datetime
import json
import os
from unittest.mock import MagicMock, patch

import pytest

import config.auto_resolution as auto_cfg
import tools.auto_export as auto_export
import tools.auto_resolution as auto_resolution
from cli.schemas.audit_efficiency import FailureReason, ResolutionType, StructuralBias, StructuralResolution
from tests.test_auto_export import FakePopen, _lock
from tests.test_tactical_proposals import _drive as _drive_tactical
from tests.test_wizard_safety_net import (  # noqa: F401 -- fixtures
    _get_saved_efficiency_row,
    _run_efficiency_audit,
    _seed_unified_for_efficiency,
    in_memory_db,
    isolated_cache_file,
)
from tools.auto_export import ExportLaunch, wait_for_export
from tools.auto_resolution import AutoProposal, TacticalProposal
from tools.candle_bank import build_status_payload, write_bank_status

MERGED = "Candle export merged for XAUUSD (clock verified by overlap; 1M: +12)"


class Process:
    """Un proceso que termina con `returncode` cuando el reloj falso llega a `ends_at` (nunca, si es None)."""

    def __init__(self, clock, ends_at=None, returncode=0):
        self.clock, self.ends_at, self.code = clock, ends_at, returncode
        self.returncode = None
        self.killed = False

    def poll(self):
        if self.ends_at is not None and self.clock.now >= self.ends_at:
            self.returncode = self.code
        return self.returncode

    def kill(self):
        self.killed = True

    terminate = kill


class Clock:
    """Reloj falso: `sleep` avanza el tiempo y ejecuta lo que estaba previsto para ese momento."""

    def __init__(self):
        self.now = 0.0
        self.events = {}  # segundo -> función
        self.sleeps = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += seconds
        for at in sorted(list(self.events)):
            if at <= self.now:
                self.events.pop(at)()


@pytest.fixture
def bank(tmp_path, monkeypatch):
    root = tmp_path / "bank"
    root.mkdir()
    monkeypatch.setattr(auto_cfg, "CANDLE_BANK_DIR", str(root))
    monkeypatch.setattr(auto_cfg, "ACCOUNTS_DATA_DIR", str(tmp_path / "data"))
    return root


def _wait(launch, clock, wait_s=30, progress=None):
    return wait_for_export(launch, wait_s, progress=progress, clock=clock, sleep=clock.sleep)


# --- wait_for_export -----------------------------------------------------------------


def test_an_export_that_finishes_in_time_shows_its_line_and_stops_waiting(bank):
    clock = Clock()
    process = Process(clock, ends_at=4)
    launch = ExportLaunch(process, ["XAUUSD"], [])
    clock.events[4] = lambda: launch.lines.append(MERGED)
    seen = []
    assert _wait(launch, clock, progress=lambda pending, elapsed: seen.append((pending, elapsed))) == [MERGED]
    assert clock.now == 4 and seen[0] == (["XAUUSD"], 0) and seen[-1] == (["XAUUSD"], 3.5)


def test_the_wait_ends_at_auto_export_wait_s_and_the_export_keeps_running(bank):
    clock = Clock()
    process = Process(clock)
    lines = _wait(ExportLaunch(process, ["XAUUSD"], []), clock)
    assert clock.now == 30 and clock.sleeps == 60
    assert lines == ["Candle export skipped: not finished in 30s (XAUUSD); continuing with the candles in the bank, "
                     "the export keeps running in the background"]
    assert process.poll() is None and not process.killed


def test_symbols_are_reported_as_they_finish(bank):
    clock = Clock()
    launch = ExportLaunch(Process(clock), ["XAUUSD", "BTCUSD"], [])
    clock.events[16] = lambda: launch.lines.append(MERGED)
    lines = _wait(launch, clock)
    assert lines[0] == MERGED
    assert lines[1].startswith("Candle export skipped: not finished in 30s (BTCUSD);")


def test_an_export_started_earlier_is_waited_for_through_its_lock(bank):
    clock = Clock()
    _lock(bank, "XAUUSD")

    def other_export_finishes():
        write_bank_status(str(bank / "XAUUSD"), build_status_payload("XAUUSD", "verified", "overlap", "unverified", "r9",
                                                                     "merged", {"1M": 12}))
        os.remove(bank / "XAUUSD" / ".lock")

    clock.events[3] = other_export_finishes
    assert _wait(ExportLaunch(None, [], ["XAUUSD"]), clock) == [f"{MERGED} (started earlier)"]
    assert clock.now == 3


def test_an_export_started_earlier_that_failed_shows_its_reason(bank):
    clock = Clock()
    write_bank_status(str(bank / "XAUUSD"), build_status_payload("XAUUSD", "verified", "overlap", "unverified", None,
                                                                 "export_failed", {}, last_error="exporter_exit_1: down"))
    assert _wait(ExportLaunch(None, [], ["XAUUSD"]), clock) == [
        "Candle export skipped: exporter_exit_1: down (started earlier)"]


def test_an_export_started_earlier_that_does_not_finish_is_only_reported_as_skipped(bank):
    clock = Clock()
    _lock(bank, "XAUUSD")
    assert _wait(ExportLaunch(None, [], ["XAUUSD"]), clock) == [
        "Candle export skipped: not finished in 30s (XAUUSD); continuing with the candles in the bank, the export "
        "keeps running in the background"]


class Reader:
    """El hilo que lee la salida: sigue vivo hasta que el reloj llega a `until`."""

    def __init__(self, clock, until):
        self.clock, self.until = clock, until

    def is_alive(self):
        return self.clock.now < self.until


def test_the_wait_lasts_until_the_last_line_is_read_even_if_the_process_already_ended(bank):
    clock = Clock()
    launch = ExportLaunch(Process(clock, ends_at=1), ["XAUUSD"], [], reader=Reader(clock, until=2))
    clock.events[2] = lambda: launch.lines.append(MERGED)
    assert _wait(launch, clock) == [MERGED] and clock.now == 2


def test_ctrl_c_stops_the_wait_not_the_export(bank):
    clock = Clock()
    process = Process(clock)

    def ctrl_c():
        raise KeyboardInterrupt

    clock.events[2] = ctrl_c
    lines = _wait(ExportLaunch(process, ["XAUUSD"], []), clock)
    assert lines == ["Candle export skipped: wait cancelled with Ctrl+C (XAUUSD); continuing with the candles in the "
                     "bank, the export keeps running in the background"]
    assert not process.killed


def test_a_process_that_ends_without_a_line_says_so(bank, tmp_path):
    clock = Clock()
    lines = _wait(ExportLaunch(Process(clock, ends_at=1, returncode=1), ["XAUUSD"], []), clock)
    assert lines == [f"Candle export skipped: the export process ended without a result (exit 1; see "
                     f"{tmp_path / 'data' / 'candle_export.log'}) (XAUUSD)"]


def test_nothing_launched_or_a_failed_launch(bank):
    clock = Clock()
    assert _wait(None, clock) == []
    assert _wait(ExportLaunch(None, [], [], launch_error="sync_not_launched: boom"), clock) == [
        "Candle export skipped: sync_not_launched: boom"]
    assert clock.sleeps == 0


def test_start_export_with_wait_reads_the_output_of_the_real_process(bank, tmp_path, monkeypatch):
    """`candle_sync` de verdad (sin exportador configurado, todo en tmp_path), esperado con el reloj real."""
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(auto_cfg, "AUTO_EXPORT", True)
    monkeypatch.setattr(auto_cfg, "MT5_INCOMING_DIR", str(tmp_path / "incoming"))
    monkeypatch.setattr(auto_export, "_RUNNING", [])
    monkeypatch.setenv("WINDOWS_PYTHON", "")
    monkeypatch.setenv("EXPORTER_WIN_PATH", "")
    lines = wait_for_export(auto_export.start_export(["XAUUSD"], wait=True), 60)
    assert lines == ["Candle export skipped: exporter_not_configured: set WINDOWS_PYTHON and EXPORTER_WIN_PATH in .env"]


# --- Al abrir un audit -----------------------------------------------------------------


@pytest.fixture
def export_events(bank, monkeypatch):
    """`start_export` y `wait_for_export` reemplazados: anotan el orden de las llamadas."""
    events = []
    monkeypatch.setattr(auto_cfg, "AUTO_EXPORT", True)

    def start(symbols, wait=False):
        events.append(("start", symbols, wait))
        return ExportLaunch(None, symbols, [])

    def wait_for(launch, wait_s, progress=None):
        events.append(("wait", wait_s))
        return ["Candle export skipped: not finished in 30s (XAUUSD); continuing with the candles in the bank, the "
                "export keeps running in the background"]

    monkeypatch.setattr(auto_export, "start_export", start)
    monkeypatch.setattr(auto_export, "wait_for_export", wait_for)
    return events


def _drive_efficiency(engine, events):
    def propose(db_path, trade_id, bank_root):
        events.append(("propose",))
        return AutoProposal(trade_id=trade_id, symbol="XAUUSD", anchor=datetime.datetime(2026, 6, 1), reason="open")

    answers = iter([StructuralBias.BOS, ResolutionType.CONFIRMED, StructuralResolution.CONFIRMED_MINIMAL,
                    FailureReason.NA])
    trade_id, payload = _seed_unified_for_efficiency(engine, asset="XAUUSDT.P")
    with patch.object(auto_resolution, "propose_for_trade", side_effect=propose), \
            patch("cli.main.get_enum_choice", side_effect=lambda *a, **k: next(answers)), \
            patch("cli.main.get_optional_text", return_value="lesson"), \
            patch("InquirerPy.inquirer.text") as text, patch("InquirerPy.inquirer.select") as select, \
            patch("builtins.input", return_value=""):
        text.return_value = MagicMock(**{"execute.side_effect": ["", "", ""]})
        select.return_value = MagicMock(**{"execute.side_effect": ["save"]})
        _run_efficiency_audit(trade_id, payload)
    return trade_id


def test_opening_an_efficiency_audit_exports_and_waits_before_the_proposal(in_memory_db, export_events,  # noqa: F811
                                                                            capsys):
    trade_id = _drive_efficiency(in_memory_db, export_events)
    assert export_events == [("start", ["XAUUSD"], True), ("wait", 30), ("propose",)]
    out = capsys.readouterr().out
    assert "Candle export skipped: not finished in 30s (XAUUSD)" in out
    assert _get_saved_efficiency_row(in_memory_db, trade_id).resolution_type == ResolutionType.CONFIRMED.value


def test_opening_a_tactical_audit_exports_and_waits_before_the_proposals(in_memory_db, export_events,  # noqa: F811
                                                                          capsys, monkeypatch):
    def wait_for(launch, wait_s, progress=None):
        # Dentro del wizard `propose_tactical` es el mock de `_drive_tactical`: todavía no se pidió ninguna propuesta.
        export_events.append(("wait", wait_s, auto_resolution.propose_tactical.call_count))
        return ["Candle export skipped: not finished in 30s (XAUUSD); continuing with the candles in the bank"]

    monkeypatch.setattr(auto_export, "wait_for_export", wait_for)
    proposal = TacticalProposal(symbol="XAUUSD", reason="pending_candles", excursion=None, tp=None)
    out, requested, _, _, _, row = _drive_tactical(in_memory_db, proposal, capsys)
    assert export_events == [("start", ["XAUUSD"], True), ("wait", 30, 0)]
    assert requested  # las propuestas se pidieron después
    assert "Candle export skipped: not finished in 30s (XAUUSD)" in out
    assert row is not None


def test_with_auto_export_off_opening_an_audit_does_not_wait(in_memory_db, export_events, monkeypatch,  # noqa: F811
                                                             capsys):
    monkeypatch.setattr(auto_cfg, "AUTO_EXPORT", False)
    _drive_efficiency(in_memory_db, export_events)
    assert export_events == [("propose",)]
    assert "Candle export" not in capsys.readouterr().out


def test_the_real_wait_with_a_short_limit_lets_the_audit_continue(in_memory_db, bank, tmp_path,  # noqa: F811
                                                                  monkeypatch, capsys):
    """Sin reemplazar la espera: un export que nunca termina, `AUTO_EXPORT_WAIT_S` de 0.6 s, y el audit se guarda."""
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(auto_cfg, "AUTO_EXPORT", True)
    monkeypatch.setattr(auto_cfg, "AUTO_EXPORT_WAIT_S", 0.6)
    monkeypatch.setattr(auto_export, "_RUNNING", [])
    popen = FakePopen()
    monkeypatch.setattr(auto_export, "Popen", popen)
    events = []
    trade_id = _drive_efficiency(in_memory_db, events)
    out = capsys.readouterr().out
    assert "Candle export skipped: not finished in 0.6s (XAUUSD)" in out
    assert popen.calls[0].argv[3:5] == ["--symbol", "XAUUSD"] and events == [("propose",)]
    assert _get_saved_efficiency_row(in_memory_db, trade_id).resolution_type == ResolutionType.CONFIRMED.value


def test_an_unexpected_error_while_waiting_never_blocks_the_audit(in_memory_db, export_events,  # noqa: F811
                                                                 monkeypatch, capsys):
    def broken(launch, wait_s, progress=None):
        raise OSError("disk gone")

    monkeypatch.setattr(auto_export, "wait_for_export", broken)
    trade_id = _drive_efficiency(in_memory_db, export_events)
    assert "Candle export skipped: OSError: disk gone" in capsys.readouterr().out
    assert _get_saved_efficiency_row(in_memory_db, trade_id) is not None
