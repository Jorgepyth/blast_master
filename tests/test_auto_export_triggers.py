"""
T54 (spec 002): el export antes del reporte de comparación y del backfill, con espera acotada (RF-20c), y al abrir el
CLI con `start()`, de fondo (RF-20d). Los disparos están reemplazados; el test prueba que ocurren, con los símbolos de
las cuentas, antes del trabajo, y que no lo bloquean. Cuentas y banco de fixture en tmp_path.
"""
import sqlite3
from unittest.mock import patch

import pytest

import cli.main as cli_main
import config.auto_resolution as auto_cfg
import tools.auto_backfill
import tools.auto_export as auto_export
import tools.resolution_report
from tests.test_auto_export import FakeProcess
from tests.test_cli_backfill import _invoke as _invoke_backfill
from tests.test_cli_backfill import account  # noqa: F401 -- fixture
from tests.test_cli_resolution_report import _invoke as _invoke_report
from tests.test_cli_resolution_report import accounts  # noqa: F401 -- fixture
from tools.auto_export import ExportLaunch

SKIPPED = ("Candle export skipped: not finished in 30s (XAUUSD); continuing with the candles in the bank, the export "
           "keeps running in the background")


@pytest.fixture
def events(monkeypatch):
    """`start_export` y `wait_for_export` reemplazados: anotan qué se pidió y en qué orden."""
    log = []
    monkeypatch.setattr(auto_cfg, "AUTO_EXPORT", True)

    def start(symbols, wait=False):
        log.append(("start", list(symbols), wait))
        return ExportLaunch(FakeProcess([]), list(symbols), [])

    def wait_for(launch, wait_s, progress=None):
        log.append(("wait", wait_s))
        return [SKIPPED]

    monkeypatch.setattr(auto_export, "start_export", start)
    monkeypatch.setattr(auto_export, "wait_for_export", wait_for)
    return log


def _record(monkeypatch, module, name, log):
    real = getattr(module, name)

    def recorded(*args, **kwargs):
        log.append((name,))
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, recorded)


# --- account_symbols -----------------------------------------------------------------------


def _minimal_db(path, assets):
    """Solo `unified_department(id, asset)`: una cuenta sin ninguna columna nueva también se puede leer."""
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE unified_department (id TEXT PRIMARY KEY, asset TEXT)")
        conn.executemany("INSERT INTO unified_department VALUES (?, ?)",
                         [(str(i), asset) for i, asset in enumerate(assets)])


def test_account_symbols_are_the_mapped_assets_of_each_account_in_order(tmp_path):
    _minimal_db(tmp_path / "btc.db", ["BTCUSDT.P", "XAU/USD", None, "BTCUSDT.P"])
    _minimal_db(tmp_path / "xau.db", ["XAUUSDT.P", "BTCUSDT.P"])
    before = {name: (tmp_path / name).read_bytes() for name in ("btc.db", "xau.db")}
    symbols = auto_export.account_symbols({"002": "btc.db", "000": "xau.db", "003": "missing.db"}, str(tmp_path))
    assert symbols == ["BTCUSD", "XAUUSD"]
    assert {name: (tmp_path / name).read_bytes() for name in before} == before


def test_account_symbols_default_to_the_real_accounts(tmp_path, monkeypatch):
    _minimal_db(tmp_path / "us100.db", ["US100"])
    monkeypatch.setattr(auto_cfg, "ACCOUNTS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(auto_cfg, "REAL_ACCOUNTS", {"003": "us100.db"})
    assert auto_export.account_symbols() == ["USTEC"]


# --- Antes del reporte ---------------------------------------------------------------------


def test_the_report_exports_the_symbols_of_its_accounts_first_and_still_runs(accounts, events,  # noqa: F811
                                                                              monkeypatch):
    _record(monkeypatch, tools.resolution_report, "build_report", events)
    result = _invoke_report()
    assert result.exit_code == 0, result.output
    assert events == [("start", ["XAUUSD", "USTEC"], True), ("wait", 30), ("build_report",)]
    assert SKIPPED in result.output and "Report written to" in result.output


def test_the_report_of_one_account_exports_only_its_symbol(accounts, events):  # noqa: F811
    assert _invoke_report("--account", "003").exit_code == 0
    assert events[0] == ("start", ["USTEC"], True)


def test_a_report_that_cannot_run_exports_nothing(accounts, events):  # noqa: F811
    assert _invoke_report("--account", "009").exit_code == 1
    assert events == []


# --- Antes del backfill --------------------------------------------------------------------


def test_the_backfill_exports_first_and_still_shows_the_preview(account, events, monkeypatch):  # noqa: F811
    _record(monkeypatch, tools.auto_backfill, "build_plan", events)
    result = _invoke_backfill()
    assert result.exit_code == 0, result.output
    assert events == [("start", ["XAUUSD"], True), ("wait", 30), ("build_plan",)]
    assert SKIPPED in result.output and "Dry run: nothing was written" in result.output


def test_the_backfill_with_apply_also_exports_first(account, events):  # noqa: F811
    result = _invoke_backfill("--apply", answers="q\nAPPLY\n")
    assert result.exit_code == 0, result.output
    assert events[:2] == [("start", ["XAUUSD"], True), ("wait", 30)]


def _off(monkeypatch):
    monkeypatch.setattr(auto_cfg, "AUTO_EXPORT", False)

    def not_called(*args, **kwargs):
        raise AssertionError("account symbols read with AUTO_EXPORT off")

    monkeypatch.setattr(auto_export, "account_symbols", not_called)


def test_with_auto_export_off_the_report_reads_no_symbols(accounts, events, monkeypatch):  # noqa: F811
    _off(monkeypatch)
    result = _invoke_report()
    assert result.exit_code == 0 and events == [] and "Candle export" not in result.output


def test_with_auto_export_off_the_backfill_reads_no_symbols(account, events, monkeypatch):  # noqa: F811
    _off(monkeypatch)
    result = _invoke_backfill()
    assert result.exit_code == 0 and events == [] and "Candle export" not in result.output


# --- Al abrir el CLI -----------------------------------------------------------------------


class MenuOpened(Exception):
    """Corta `start()` donde empieza el menú, después del disparo."""


def test_opening_the_cli_launches_the_export_in_the_background_without_waiting(events, monkeypatch, tmp_path):
    monkeypatch.setattr(cli_main, "get_active_engine", lambda: None)
    monkeypatch.setattr(cli_main.state, "refresh_metrics", lambda engine: None)
    monkeypatch.setattr(auto_export, "account_symbols", lambda: ["XAUUSD", "US500", "BTCUSD", "USTEC"])
    with patch("cli.main.Live", side_effect=MenuOpened), pytest.raises(MenuOpened):
        cli_main.start.callback()
    assert events == [("start", ["XAUUSD", "US500", "BTCUSD", "USTEC"], False)]  # sin ("wait", ...)


def test_opening_the_cli_with_auto_export_off_launches_nothing(events, monkeypatch):
    monkeypatch.setattr(auto_cfg, "AUTO_EXPORT", False)
    monkeypatch.setattr(cli_main, "get_active_engine", lambda: None)
    monkeypatch.setattr(cli_main.state, "refresh_metrics", lambda engine: None)
    with patch("cli.main.Live", side_effect=MenuOpened), pytest.raises(MenuOpened):
        cli_main.start.callback()
    assert events == []


def test_a_failing_trigger_never_keeps_the_cli_from_opening(events, monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "get_active_engine", lambda: None)
    monkeypatch.setattr(cli_main.state, "refresh_metrics", lambda engine: None)

    def broken():
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(auto_export, "account_symbols", broken)
    with patch("cli.main.Live", side_effect=MenuOpened), pytest.raises(MenuOpened):
        cli_main.start.callback()
    assert "Candle export skipped: DatabaseError: file is not a database" in capsys.readouterr().out
