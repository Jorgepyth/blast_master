"""
T50 (spec 002): las puertas del backfill antes de escribir (RF-11d, R9; plan.md §4): el ensayo sobre copias temporales
de todas las cuentas (código 3 si falla, sin tocar ninguna DB real), un backup local de las últimas 24 h de cada DB
(código 4) y la confirmación escribiendo `APPLY` (código 5). Con las tres, escribe y devuelve 0.
"""
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.test_auto_backfill import SAVED_OLD, _account
from tests.test_backfill_apply import OLD_AUDIT, TRADE
from tools.auto_backfill import (
    EXIT_APPLIED,
    EXIT_CANCELLED,
    EXIT_NO_BACKUP,
    EXIT_REHEARSAL_FAILED,
    KIND_FILL,
    PlannedChange,
    build_plan,
    recent_backup,
    run_apply,
)

NOW = datetime(2026, 10, 3, 12, 0)


@pytest.fixture
def setup(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db, bank = _account(data, efficiency=OLD_AUDIT, tactical=[TRADE])
    _account(data, efficiency=dict(resolution_type="Open"), name="btc.db")
    backups = tmp_path / "backups"
    run = backups / "20261003_090434"
    run.mkdir(parents=True)
    for name in ("xau.db", "btc.db"):
        shutil.copy(data / name, run / name)
    accounts = {"000": "xau.db", "002": "btc.db"}
    return data, bank, backups, accounts


def _snapshot(data):
    return {path.name: path.read_bytes() for path in sorted(Path(data).glob("*.db"))}


def _run(data, bank, backups, accounts, confirm="APPLY", plans=None):
    plans = plans if plans is not None else build_plan(str(data), bank, real_accounts=accounts)
    return run_apply(plans, str(data), bank, str(backups), confirm=lambda: confirm, now=NOW)


def test_with_the_three_gates_it_writes_every_account_and_returns_0(setup):
    data, bank, backups, accounts = setup
    code, applied = _run(data, bank, backups, accounts)
    assert code == EXIT_APPLIED and set(applied) == {"000", "002"} and all(applied.values())
    with sqlite3.connect(data / "xau.db") as conn:
        assert conn.execute("SELECT audit_registration_time FROM efficiency_audit").fetchone()[0].startswith(
            SAVED_OLD.strftime("%Y-%m-%d %H:%M"))


def test_a_failing_rehearsal_returns_3_and_touches_no_real_database(setup):
    data, bank, backups, accounts = setup
    plans = build_plan(str(data), bank, real_accounts=accounts)
    plans[1].changes.append(PlannedChange("002", "efficiency_audit", "missing", "missing", "resolution_type",
                                          KIND_FILL, None, "Confirmed (A equal to B)", "candles"))
    before = _snapshot(data)
    code, applied = _run(data, bank, backups, accounts, plans=plans)
    assert (code, applied) == (EXIT_REHEARSAL_FAILED, {})
    assert _snapshot(data) == before  # tampoco la primera cuenta, que sí ensayó bien (RF-11d)


@pytest.mark.parametrize("problem", ["no_backups", "too_old", "missing_db", "empty_file"])
def test_without_a_backup_of_the_last_24h_it_returns_4_and_writes_nothing(setup, problem):
    data, bank, backups, accounts = setup
    run = backups / "20261003_090434"
    if problem == "no_backups":
        shutil.rmtree(backups)
    elif problem == "too_old":
        run.rename(backups / "20261002_115959")              # 24 h y 1 s antes de NOW
    elif problem == "missing_db":
        (run / "btc.db").unlink()
    else:
        (run / "btc.db").write_bytes(b"")
    before = _snapshot(data)
    assert _run(data, bank, backups, accounts) == (EXIT_NO_BACKUP, {})
    assert _snapshot(data) == before


@pytest.mark.parametrize("typed", ["", "apply", "APPLY ", "yes"])
def test_anything_but_apply_cancels_with_5_and_writes_nothing(setup, typed):
    data, bank, backups, accounts = setup
    before = _snapshot(data)
    assert _run(data, bank, backups, accounts, confirm=typed) == (EXIT_CANCELLED, {})
    assert _snapshot(data) == before


def test_a_backup_exactly_24h_old_still_counts_and_the_newest_is_reported(setup):
    _, _, backups, _ = setup
    (backups / "20261002_120000").mkdir()
    shutil.copy(backups / "20261003_090434" / "xau.db", backups / "20261002_120000" / "xau.db")
    assert recent_backup("xau.db", str(backups), NOW).endswith("20261003_090434/xau.db")
    shutil.rmtree(backups / "20261003_090434")
    assert recent_backup("xau.db", str(backups), NOW).endswith("20261002_120000/xau.db")
    assert recent_backup("xau.db", str(backups), NOW + timedelta(seconds=1)) is None


def test_the_rehearsal_never_leaves_files_behind(setup, tmp_path):
    data, bank, backups, accounts = setup
    _run(data, bank, backups, accounts, confirm="no")
    assert sorted(path.name for path in data.iterdir()) == ["bank", "btc.db", "xau.db"]


def test_a_copy_that_fails_integrity_check_stops_the_backfill_with_3(setup, monkeypatch):
    import tools.auto_backfill as auto_backfill
    data, bank, backups, accounts = setup
    monkeypatch.setattr(auto_backfill, "integrity_check", lambda path: "*** in database main ***\nPage 3: corrupt")
    reported = []
    plans = build_plan(str(data), bank, real_accounts=accounts)
    before = _snapshot(data)
    result = run_apply(plans, str(data), bank, str(backups), confirm=lambda: "APPLY", now=NOW, report=reported.append)
    assert result == (EXIT_REHEARSAL_FAILED, {}) and _snapshot(data) == before
    assert "integrity_check" in reported[0]
