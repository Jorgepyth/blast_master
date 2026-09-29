"""
T21 (spec 002, RF-2c, RF-1): subcomandos `candles status`, `candles import-legacy`
y `candles export` de cli/main.py.

`CliRunner` con toda la configuración (`config.auto_resolution`) apuntando a
`tmp_path`: ningún test toca el banco real, `/mnt/c` ni MT5. Los comandos son
capas finas de presentación; la lógica ya la prueban tests/test_candle_bank.py y
tests/test_candle_sync.py. Acá se prueba la salida y los códigos de salida
(plan.md §4): 0 ok, 2 reloj sin verificar, 6 export fallido o saltado.
"""
import functools
import os
import sys
from datetime import datetime, timedelta

import pandas as pd
import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config.auto_resolution as auto_cfg
import tools.database
import tools.candle_sync as candle_sync
from tools.candle_bank import SyncResult, write_sync_status
from tools.database import Base, TacticalAudit, UnifiedDepartment, init_db

T0 = datetime(2026, 6, 1, 0, 0)
XAU_DB = "flight_account_001_xauusd.db"  # REAL_ACCOUNTS["000"]


def _csv(path, times, closes):
    pd.DataFrame({"time": times, "open": closes, "high": [c + 1 for c in closes],
                  "low": [c - 1 for c in closes], "close": closes}).to_csv(path, index=False)


def _write_15m(directory, lag_hours=0, bars=200):
    os.makedirs(directory, exist_ok=True)
    rows = [{"time": (T0 + timedelta(minutes=15 * i, hours=lag_hours)).strftime("%Y-%m-%d %H:%M:%S"),
             "open": 1000 + i, "high": 1000 + i + 0.9, "low": 1000 + i, "close": 1000 + i + 0.5}
            for i in range(bars)]
    pd.DataFrame(rows).to_csv(os.path.join(directory, "15M.csv"), index=False)


def _xau_account(accounts_dir, fills):
    engine = create_engine(f"sqlite:///{accounts_dir / XAU_DB}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        for i in range(fills):
            bar = 10 + i * 7
            session.add(UnifiedDepartment(
                id=f"t{i}", state="READY_FOR_NOTION", asset="XAUUSDT.P", market_bias="Bullish", calc_edge=0.5,
                p4_hierarchy="x", p1_timeframe="15M", p1_type="x", nodes_l1=1, nodes_l2=1,
                tactical_classification="x", long_prob=0.5, short_prob=0.5, no_trade_prob=0.0))
            session.add(TacticalAudit(trade_id=f"t{i}", order_filled=True,
                                      entry_time=T0 + timedelta(minutes=15 * bar + 5),
                                      entry_price=1000 + bar + 0.4))
        session.commit()
    engine.dispose()


class Env:
    def __init__(self, tmp_path):
        self.bank_root = tmp_path / "bank"
        self.legacy_root = tmp_path / "legacy"
        self.accounts_dir = tmp_path / "accounts"
        self.incoming_root = tmp_path / "incoming"
        for d in (self.bank_root, self.legacy_root, self.accounts_dir, self.incoming_root):
            d.mkdir()

    def invoke(self, *args):
        from cli.main import cli
        return CliRunner().invoke(cli, list(args), env={"COLUMNS": "220"})


@pytest.fixture
def env(tmp_path, monkeypatch):
    # `cli()` abre el engine de cuenta para CUALQUIER subcomando (cli/main.py): se lo fija en memoria.
    monkeypatch.setattr(tools.database, "engine_default", init_db("sqlite:///:memory:"))
    e = Env(tmp_path)
    monkeypatch.setattr(auto_cfg, "BROKER_DST_RULE", "none")  # los datos de fixture son de junio: sin depender de la fecha de hoy
    for name, path in (("CANDLE_BANK_DIR", e.bank_root), ("LEGACY_EXPORTS_DIR", e.legacy_root),
                       ("ACCOUNTS_DATA_DIR", e.accounts_dir), ("MT5_INCOMING_DIR", e.incoming_root)):
        monkeypatch.setattr(auto_cfg, name, str(path))
    return e


# --- candles --help ------------------------------------------------------------

def test_candles_group_lists_its_three_subcommands(env):
    result = env.invoke("candles", "--help")
    assert result.exit_code == 0
    for command in ("status", "import-legacy", "export"):
        assert command in result.output


# --- candles status -------------------------------------------------------------

def test_status_for_an_empty_bank_lists_every_symbol_as_never_exported(env):
    result = env.invoke("candles", "status")
    assert result.exit_code == 0
    for symbol in ("XAUUSD", "BTCUSD", "USTEC", "US500"):
        assert symbol in result.output
    assert result.output.count("never exported") == 4


def test_status_shows_clock_last_export_timeframes_and_error(env):
    bank_dir = env.bank_root / "XAUUSD"
    bank_dir.mkdir()
    _csv(bank_dir / "1M.csv", [T0], [1.0])
    _csv(bank_dir / "1H.csv", [T0], [1.0])
    write_sync_status(str(bank_dir), SyncResult("XAUUSD", "merged", verified_by="overlap", run_id="run-1",
                                                 bars_added={"1H": 3}))
    write_sync_status(str(bank_dir), SyncResult("XAUUSD", "export_failed", run_id="run-2", error="timeout"))

    result = env.invoke("candles", "status")

    assert result.exit_code == 0
    line = next(l for l in result.output.splitlines() if "XAUUSD" in l)
    for expected in ("verified", "overlap", "export_failed (run-2)", "1H 1M", "timeout"):
        assert expected in line
    assert result.output.count("never exported") == 3  # los otros tres símbolos


# --- candles import-legacy --------------------------------------------------------

def test_import_legacy_imports_a_verified_symbol_excludes_xau_5m_and_leaves_the_source_alone(env):
    _xau_account(env.accounts_dir, fills=12)
    legacy = env.legacy_root / "XAUUSD"
    _write_15m(legacy)
    _csv(legacy / "5M.csv", [T0], [1800.0])
    before = {f: (legacy / f).read_bytes() for f in os.listdir(legacy)}

    result = env.invoke("candles", "import-legacy")

    assert result.exit_code == 0
    assert "XAUUSD: imported 15M: 200 candles; excluded 5M (legacy_precontamination)" in result.output
    assert result.output.count("no legacy CSVs") == 3  # los otros tres símbolos
    assert (env.bank_root / "XAUUSD" / "15M.csv").exists()
    assert not (env.bank_root / "XAUUSD" / "5M.csv").exists()
    assert {f: (legacy / f).read_bytes() for f in os.listdir(legacy)} == before  # INV-6


def test_import_legacy_exit_2_when_the_clock_cannot_be_verified(env):
    _xau_account(env.accounts_dir, fills=3)  # menos de las 10 referencias
    _write_15m(env.legacy_root / "XAUUSD")

    result = env.invoke("candles", "import-legacy")

    assert result.exit_code == 2
    assert "XAUUSD: not imported, clock_unverified" in result.output
    assert not (env.bank_root / "XAUUSD" / "15M.csv").exists()  # ninguna vela entró al banco
    status_line = next(l for l in env.invoke("candles", "status").output.splitlines() if "XAUUSD" in l)
    assert "clock_unverified" in status_line  # pero `status` deja ver por qué


def test_import_legacy_exit_2_when_the_clock_is_misaligned(env):
    _xau_account(env.accounts_dir, fills=12)
    _write_15m(env.legacy_root / "XAUUSD", lag_hours=3)

    result = env.invoke("candles", "import-legacy")

    assert result.exit_code == 2
    assert "XAUUSD: not imported, clock_misaligned" in result.output


def test_import_legacy_exit_6_when_another_export_holds_the_symbol_lock(env):
    import json
    from datetime import timezone
    _xau_account(env.accounts_dir, fills=12)
    _write_15m(env.legacy_root / "XAUUSD")
    bank_dir = env.bank_root / "XAUUSD"
    bank_dir.mkdir()
    (bank_dir / ".lock").write_text(json.dumps({
        "pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat()}))

    result = env.invoke("candles", "import-legacy")

    assert result.exit_code == 6
    assert "XAUUSD: skipped, an export is already running" in result.output
    assert not (bank_dir / "15M.csv").exists()


# --- candles export --------------------------------------------------------------------

def _stub_sync(monkeypatch, result):
    calls = []

    def stub(symbol, timeout_s=None, **kw):
        calls.append((symbol, timeout_s))
        return result

    monkeypatch.setattr(candle_sync, "sync_symbol", stub)
    return calls


def test_export_merged_exits_0_and_prints_the_summary(env, monkeypatch):
    calls = _stub_sync(monkeypatch, SyncResult("XAUUSD", "merged", verified_by="overlap", bars_added={"1H": 3}))

    result = env.invoke("candles", "export", "--symbol", "XAUUSD")

    assert result.exit_code == 0
    assert "Candle export merged for XAUUSD (clock verified by overlap; 1H: +3)" in result.output
    assert calls == [("XAUUSD", None)]


def test_export_wait_option_is_the_exporter_timeout(env, monkeypatch):
    calls = _stub_sync(monkeypatch, SyncResult("XAUUSD", "merged", verified_by="overlap"))
    env.invoke("candles", "export", "--symbol", "XAUUSD", "--wait", "5")
    assert calls == [("XAUUSD", 5.0)]


def test_export_symbol_is_case_insensitive(env, monkeypatch):
    calls = _stub_sync(monkeypatch, SyncResult("XAUUSD", "merged", verified_by="overlap"))
    assert env.invoke("candles", "export", "--symbol", "xauusd").exit_code == 0
    assert calls == [("XAUUSD", None)]


def test_export_exits_2_when_the_clock_is_not_verified(env, monkeypatch):
    _stub_sync(monkeypatch, SyncResult("XAUUSD", "clock_unverified", error="9 reference prices in the exported range, need 10"))
    result = env.invoke("candles", "export", "--symbol", "XAUUSD")
    assert result.exit_code == 2
    assert "not merged for XAUUSD: clock_unverified" in result.output


def test_export_exits_6_with_the_skipped_line_when_the_export_fails(env, monkeypatch):
    _stub_sync(monkeypatch, SyncResult("XAUUSD", "export_failed", error="exporter_exit_1: mt5.initialize() failed"))
    result = env.invoke("candles", "export", "--symbol", "XAUUSD")
    assert result.exit_code == 6
    assert "Candle export skipped: exporter_exit_1: mt5.initialize() failed" in result.output


def test_export_exits_6_when_another_export_is_running(env, monkeypatch):
    _stub_sync(monkeypatch, SyncResult("XAUUSD", "locked", error="export_in_progress: ..."))
    result = env.invoke("candles", "export", "--symbol", "XAUUSD")
    assert result.exit_code == 6
    assert "Candle export skipped: export_in_progress" in result.output


def test_export_of_an_unknown_symbol_exits_6_without_running_anything(env, monkeypatch):
    calls = _stub_sync(monkeypatch, SyncResult("FOO", "merged"))
    result = env.invoke("candles", "export", "--symbol", "FOO")
    assert result.exit_code == 6
    assert "Candle export skipped: no_mt5_symbol" in result.output and "XAUUSD" in result.output
    assert calls == []


FAKE_EXPORTER = r'''
import os, shutil, sys
incoming_root, symbol, payload = sys.argv[1:4]
run_dir = os.path.join(incoming_root, symbol, "20261005T142011")
os.makedirs(run_dir)
for name in os.listdir(payload):
    shutil.copy(os.path.join(payload, name), run_dir)
print("RUN_ID: 20261005T142011")
'''


def test_export_end_to_end_through_the_real_sync_with_a_fake_exporter(env, monkeypatch, tmp_path):
    # Banco con 12 horas de 1H; el "export" trae las mismas + 3 nuevas: el reloj verifica por superposición.
    bank_dir = env.bank_root / "XAUUSD"
    bank_dir.mkdir()
    hours = lambda n: [T0 + timedelta(hours=h) for h in range(n)]
    _csv(bank_dir / "1H.csv", hours(12), [100.0 + h for h in range(12)])
    payload = tmp_path / "payload"
    payload.mkdir()
    _csv(payload / "1H.csv", hours(15), [100.0 + h for h in range(15)])
    script = tmp_path / "fake_exporter.py"
    script.write_text(FAKE_EXPORTER)
    monkeypatch.setattr(candle_sync, "sync_symbol", functools.partial(
        candle_sync.sync_symbol, export_command=[sys.executable, str(script), str(env.incoming_root), "XAUUSD", str(payload)],
        dst_rule="none", real_accounts={}, symbol_map={}, windows_killer=lambda pid: None))

    result = env.invoke("candles", "export", "--symbol", "XAUUSD")

    assert result.exit_code == 0, result.output
    assert "Candle export merged for XAUUSD (clock verified by overlap; 1H: +3)" in result.output
    assert len(pd.read_csv(bank_dir / "1H.csv")) == 15
    status = env.invoke("candles", "status")
    assert "verified" in next(l for l in status.output.splitlines() if "XAUUSD" in l)
