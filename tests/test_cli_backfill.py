"""
T51 (spec 002): el subcomando `backfill [--apply] [--account ID ...]` (RF-11, RF-11e, RF-19; plan.md §4). Sin
`--apply` muestra la vista previa y el historial, y no escribe nada. Con `--apply` pregunta cada conflicto uno por uno
(y/n, o q para no aceptar ninguno más), pasa por las puertas de T50 y escribe. Todo en tmp_path con `CliRunner`.
"""
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

import config.auto_resolution as auto_cfg
import tools.database
from tests.test_auto_backfill import _account
from tests.test_backfill_apply import OLD_AUDIT, TRADE
from tools.database import init_db


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setattr(tools.database, "engine_default", init_db("sqlite:///:memory:"))
    data = tmp_path / "data"
    data.mkdir()
    db, bank = _account(data, efficiency=OLD_AUDIT, tactical=[TRADE])
    backup = data / "backups" / (datetime.now() - timedelta(minutes=5)).strftime("%Y%m%d_%H%M%S")
    backup.mkdir(parents=True)
    shutil.copy(db, backup / "xau.db")
    monkeypatch.setattr(auto_cfg, "ACCOUNTS_DATA_DIR", str(data))
    monkeypatch.setattr(auto_cfg, "CANDLE_BANK_DIR", bank)
    monkeypatch.setattr(auto_cfg, "REAL_ACCOUNTS", {"000": "xau.db"})
    return Path(db)


def _invoke(*args, answers=""):
    from cli.main import cli
    return CliRunner().invoke(cli, ["backfill", *args], input=answers, env={"COLUMNS": "250"})


def _history(db):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT kind, table_name, field FROM backfill_history ORDER BY id").fetchall()


def test_the_dry_run_shows_the_plan_and_writes_nothing(account):
    before = account.read_bytes()
    result = _invoke()
    assert result.exit_code == 0, result.output
    assert "(000, HEAD, dry-run)" in result.output
    assert "| ! a1 efficiency_audit.structural_resolution:" in result.output
    assert "| > a1 efficiency_audit.audit_registration_time: — → " in result.output
    assert "Dry run: nothing was written" in result.output
    assert account.read_bytes() == before


def test_apply_asks_each_conflict_and_records_the_accepted_one(account):
    # Conflictos, en orden: structural_resolution (sí), structural_mfe (no), mfe_favorable táctico (no). Después APPLY.
    result = _invoke("--apply", answers="y\nn\nn\nAPPLY\n")
    assert result.exit_code == 0, result.output
    history = _history(account)
    assert ("accepted_conflict", "efficiency_audit", "structural_resolution") in history
    assert not any(field in ("structural_mfe", "mfe_favorable") for _, _, field in history)
    assert ("legacy_move", "efficiency_audit", "audit_registration_time") in history
    assert "1 conflict accepted" in result.output and "changes written" in result.output


def test_q_stops_asking_and_keeps_the_remaining_conflicts(account):
    result = _invoke("--apply", answers="q\nAPPLY\n")
    assert result.exit_code == 0, result.output
    assert not any(kind == "accepted_conflict" for kind, _, _ in _history(account))


def test_not_typing_apply_cancels_with_5(account):
    before = account.read_bytes()
    result = _invoke("--apply", answers="n\nn\nn\napply\n")
    assert result.exit_code == 5 and "Cancelled" in result.output
    assert account.read_bytes() == before


def test_without_a_recent_backup_it_stops_with_4(account):
    shutil.rmtree(account.parent / "backups")
    before = account.read_bytes()
    result = _invoke("--apply", answers="n\nn\nn\nAPPLY\n")
    assert result.exit_code == 4 and "No backup of xau.db" in result.output
    assert account.read_bytes() == before


def test_the_history_shows_up_in_the_next_preview(account):
    _invoke("--apply", answers="y\nn\nn\nAPPLY\n")
    result = _invoke()
    assert result.exit_code == 0
    run_lines = [line for line in result.output.splitlines() if line.startswith("* ")]
    assert "(000, HEAD, dry-run)" in run_lines[0] and run_lines[1].split(") ")[0].endswith("(000")
    assert "| ! a1 efficiency_audit.structural_resolution: Confirmed + expansión significativa → Confirmed pero " \
           "mínima [candles]" in result.output
    history = result.output.split(run_lines[1], 1)[1]
    assert history.index("structural_resolution") < history.index("tactical_audit.mae_adverse")  # en el orden aplicado


def test_an_unknown_account_exits_1(account):
    result = _invoke("--account", "009")
    assert result.exit_code == 1 and "Unknown account: 009" in result.output
