"""
T49 (spec 002): aplicar el plan del backfill en una transacción por cuenta, con una fila de `backfill_history` por
cambio, e idempotencia (RF-11b, RF-11c, RF-18). Las DBs son las de fixture de tests/test_auto_backfill.py, en tmp_path.
"""
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cli.schemas.audit_efficiency import FailureReason, ResolutionType, StructuralResolution
from tests.test_auto_backfill import SAVED_OLD, _account
from tests.test_auto_resolution import T0, TOUCH
from tools.auto_backfill import (
    KIND_ACCEPTED_CONFLICT,
    KIND_CONFLICT,
    KIND_FILL,
    KIND_LEGACY_MOVE,
    PlannedChange,
    apply_plan,
    plan_account,
)

RUN_AT = datetime(2026, 10, 3, 11, 0)
OLD_AUDIT = dict(resolution_type=ResolutionType.CONFIRMED.value, real_bias_b="BOS",
                 structural_resolution=StructuralResolution.CONFIRMED_EXPANSION.value,
                 failure_reason=FailureReason.NA.value, structural_mae=99.05, structural_mfe=112.0,
                 resolution_time=SAVED_OLD)
TRADE = dict(id="t1", order_filled=True, entry_time=T0 + timedelta(minutes=20), exit_time=T0 + timedelta(hours=2),
             entry_price=100.0, stop_loss=95.0, take_profit=110.0, mae_adverse=None, mfe_favorable=3.0,
             could_hit_tp="yes")


def _row(db, table, record_id, *columns):
    with sqlite3.connect(db) as conn:
        return conn.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE id = ?", (record_id,)).fetchone()


def _history(db):
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT run_id, run_at, kind, table_name, record_id, field, old_value, new_value, source "
                            "FROM backfill_history ORDER BY id").fetchall()


def test_apply_writes_fills_and_the_legacy_move_and_leaves_conflicts_alone(tmp_path):
    db, bank = _account(tmp_path, efficiency=OLD_AUDIT, tactical=[TRADE])
    plan = plan_account("000", db, bank)

    applied = apply_plan(db, plan, run_id="run-1", run_at=RUN_AT)

    assert {(c.field, c.kind) for c in applied} == {
        ("audit_registration_time", KIND_LEGACY_MOVE), ("resolution_time", KIND_FILL),
        ("resolution_time_source", KIND_FILL), ("mae_adverse", KIND_FILL)}
    assert _row(db, "efficiency_audit", "a1", "audit_registration_time", "resolution_time",
                "resolution_time_source", "structural_resolution", "structural_mfe") == (
        "2026-06-02 18:00:00.000000", "2026-06-01 01:00:00.000000", "candles",
        StructuralResolution.CONFIRMED_EXPANSION.value, 112)  # los conflictos no se tocan
    assert _row(db, "tactical_audit", "t1", "mae_adverse", "mfe_favorable") == (0.2, 3)


def test_history_has_one_row_per_applied_change(tmp_path):
    db, bank = _account(tmp_path, efficiency=OLD_AUDIT, tactical=[TRADE])
    applied = apply_plan(db, plan_account("000", db, bank), run_id="run-1", run_at=RUN_AT)
    history = _history(db)
    assert len(history) == len(applied) == 4
    assert ("run-1", "2026-10-03 11:00:00.000000", KIND_LEGACY_MOVE, "efficiency_audit", "a1",
            "audit_registration_time", None, "2026-06-02 18:00:00", "legacy") in history
    assert ("run-1", "2026-10-03 11:00:00.000000", KIND_FILL, "efficiency_audit", "a1", "resolution_time",
            "2026-06-02 18:00:00", "2026-06-01 01:00:00", "candles") in history
    assert ("run-1", "2026-10-03 11:00:00.000000", KIND_FILL, "tactical_audit", "t1", "mae_adverse", None, "0.2",
            "candles") in history


def test_running_the_backfill_twice_changes_nothing_the_second_time(tmp_path):
    db, bank = _account(tmp_path, efficiency=dict(resolution_type="Open"), tactical=[TRADE])
    apply_plan(db, plan_account("000", db, bank), run_id="run-1", run_at=RUN_AT)
    second = plan_account("000", db, bank)
    assert second.count(KIND_FILL) == second.count(KIND_LEGACY_MOVE) == 0
    before = Path(db).read_bytes()
    assert apply_plan(db, second, run_id="run-2", run_at=RUN_AT) == []
    assert Path(db).read_bytes() == before  # sin nada que aplicar ni se abre para escribir
    assert len({row[0] for row in _history(db)}) == 1  # la segunda corrida no dejó filas


def test_an_accepted_conflict_is_applied_and_recorded_as_such(tmp_path):
    db, bank = _account(tmp_path, efficiency=OLD_AUDIT, tactical=[TRADE])
    plan = plan_account("000", db, bank)
    conflict = next(c for c in plan.changes if c.field == "structural_resolution")
    assert conflict.kind == KIND_CONFLICT
    applied = apply_plan(db, plan, accepted={("efficiency_audit", "a1", "structural_resolution")}, run_id="run-1",
                         run_at=RUN_AT)
    assert ("structural_resolution", KIND_ACCEPTED_CONFLICT) in {(c.field, c.kind) for c in applied}
    assert _row(db, "efficiency_audit", "a1", "structural_resolution") == (StructuralResolution.CONFIRMED_MINIMAL.value,)
    assert ("efficiency_audit", "a1", "structural_resolution", StructuralResolution.CONFIRMED_EXPANSION.value,
            StructuralResolution.CONFIRMED_MINIMAL.value) in {(r[3], r[4], r[5], r[6], r[7]) for r in _history(db)
                                                              if r[2] == KIND_ACCEPTED_CONFLICT}
    assert next(c for c in plan_account("000", db, bank).changes if c.field == "structural_resolution").kind == (
        "unchanged")


def test_an_error_rolls_the_whole_account_back(tmp_path):
    db, bank = _account(tmp_path, efficiency=OLD_AUDIT, tactical=[TRADE])
    plan = plan_account("000", db, bank)
    plan.changes.append(PlannedChange("000", "tactical_audit", "t1", "a1", "mfe_favorable", KIND_FILL, None, 2.1,
                                      None))  # sin origen: el INSERT del historial falla, después de los UPDATE
    before = Path(db).read_bytes()
    with pytest.raises(Exception):
        apply_plan(db, plan, run_id="run-1", run_at=RUN_AT)
    assert _row(db, "efficiency_audit", "a1", "resolution_time") == ("2026-06-02 18:00:00.000000",)
    assert _history(db) == []


def test_only_whitelisted_fields_can_be_written(tmp_path):
    db, bank = _account(tmp_path, efficiency=OLD_AUDIT)
    bad = PlannedChange("000", "efficiency_audit", "a1", "a1", "real_bias_b", KIND_FILL, None, "CHOCH", "candles")
    plan = plan_account("000", db, bank)
    plan.changes.append(bad)
    with pytest.raises(ValueError, match="real_bias_b"):
        apply_plan(db, plan, run_id="run-1", run_at=RUN_AT)
    assert _history(db) == []


def test_the_code_never_updates_or_deletes_backfill_history():
    source = (Path(__file__).resolve().parent.parent / "tools" / "auto_backfill.py").read_text()
    assert not re.search(r"(UPDATE|DELETE\s+FROM)\s+backfill_history", source, flags=re.I)


def test_a_change_for_a_row_that_does_not_exist_rolls_everything_back(tmp_path):
    db, bank = _account(tmp_path, efficiency=OLD_AUDIT)
    plan = plan_account("000", db, bank)
    plan.changes.append(PlannedChange("000", "efficiency_audit", "missing", "missing", "resolution_type", KIND_FILL,
                                      None, ResolutionType.CONFIRMED.value, "candles"))
    with pytest.raises(ValueError, match="missing not found"):
        apply_plan(db, plan, run_id="run-1", run_at=RUN_AT)
    assert _row(db, "efficiency_audit", "a1", "resolution_time") == ("2026-06-02 18:00:00.000000",)
    assert _history(db) == []


def test_nothing_to_apply_never_opens_the_database_for_writing(tmp_path, monkeypatch):
    import tools.auto_backfill as auto_backfill
    db, bank = _account(tmp_path, efficiency=OLD_AUDIT)
    plan = plan_account("000", db, bank)
    plan.changes[:] = [c for c in plan.changes if c.kind == "unchanged"]

    def no_engine(*args, **kwargs):
        raise AssertionError("the database was opened")

    monkeypatch.setattr(auto_backfill, "create_engine", no_engine)
    assert apply_plan(db, plan) == []
