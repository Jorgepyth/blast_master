"""
T47 (spec 002): `tools/auto_backfill.py`, el plan del backfill (RF-11 a RF-11c; plan.md §3.11). Un análisis por DB de
fixture (así no hay Overlaps entre ellos), con el banco de tests/test_auto_resolution.py: 1M plano 99-101 hasta la
01:00, que toca 110.5, y después 104.5-105.5. El análisis empieza a las 00:10, el objetivo es 110 y la invalidación 90,
así que las velas proponen: Confirmed a las 01:00, "mínima", N/A, MAE 99 y MFE 110.5. Solo lectura.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, insert

from cli.schemas.audit_efficiency import FailureReason, ResolutionType, StructuralResolution
from tests.test_auto_resolution import T0, TOUCH, _write_bank, _write_db
from tools.auto_backfill import (
    KIND_CONFLICT,
    KIND_FILL,
    KIND_LEGACY_MOVE,
    KIND_UNCHANGED,
    build_plan,
    plan_account,
)
from tools.database import EfficiencyAudit, TacticalAudit

CONFIRMED, MINIMAL = ResolutionType.CONFIRMED.value, StructuralResolution.CONFIRMED_MINIMAL.value
SAVED_OLD = datetime(2026, 6, 2, 18, 0)   # la hora vieja del guardado del audit, en resolution_time


def _account(tmp_path, efficiency=None, tactical=(), clock="verified", name="xau.db"):
    bank_root = tmp_path / "bank"
    if not bank_root.exists():
        bank_root.mkdir()
        _write_bank(bank_root, clock=clock)
    db = tmp_path / name
    _write_db(db, [{"id": "a1", "created_at": T0 + timedelta(minutes=30)}], old_schema=False)
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        if efficiency is not None:
            conn.execute(insert(EfficiencyAudit.__table__).values(**dict(dict(id="a1", bias_a="BOS"), **efficiency)))
        for row in tactical:
            conn.execute(insert(TacticalAudit.__table__).values(**dict(dict(trade_id="a1"), **row)))
    engine.dispose()
    return str(db), str(bank_root)


def _by_field(plan):
    return {(c.table_name, c.field): c for c in plan.changes}


def test_a_never_done_audit_gets_every_objective_field_and_keeps_the_manual_ones(tmp_path):
    db, bank = _account(tmp_path, efficiency=dict(resolution_type="Open", real_bias_b=None))
    changes = _by_field(plan_account("000", db, bank))
    expected = {"resolution_type": ("Open", CONFIRMED), "structural_resolution": (None, MINIMAL),
                "failure_reason": (None, FailureReason.NA.value), "structural_mae": (None, 99.0),
                "structural_mfe": (None, 110.5), "resolution_time": (None, TOUCH),
                "resolution_time_source": (None, "candles")}
    for field, (old, new) in expected.items():
        change = changes[("efficiency_audit", field)]
        assert (change.kind, change.old_value, change.new_value, change.source) == (KIND_FILL, old, new, "candles")
    assert not {field for _, field in changes} & {"real_bias_b", "bias_a", "specific_bias_compliance",
                                                  "false_regime_rate", "lesson_learned"}  # INV-8


def test_an_old_audit_moves_its_save_time_and_compares_the_rest(tmp_path):
    db, bank = _account(tmp_path, efficiency=dict(
        resolution_type=CONFIRMED, real_bias_b="BOS", structural_resolution=StructuralResolution.CONFIRMED_EXPANSION.value,
        failure_reason=FailureReason.NA.value, structural_mae=99.05, structural_mfe=112.0, resolution_time=SAVED_OLD))
    changes = _by_field(plan_account("000", db, bank))
    move = changes[("efficiency_audit", "audit_registration_time")]
    assert (move.kind, move.old_value, move.new_value, move.source) == (KIND_LEGACY_MOVE, None, SAVED_OLD, "legacy")
    time = changes[("efficiency_audit", "resolution_time")]
    assert (time.kind, time.old_value, time.new_value) == (KIND_FILL, SAVED_OLD, TOUCH)  # N2: excepción autorizada
    assert changes[("efficiency_audit", "resolution_type")].kind == KIND_UNCHANGED
    assert changes[("efficiency_audit", "structural_mae")].kind == KIND_UNCHANGED      # 99.05 contra 99: dentro de 0.1%
    conflict = changes[("efficiency_audit", "structural_resolution")]
    assert (conflict.kind, conflict.new_value) == (KIND_CONFLICT, MINIMAL)
    assert changes[("efficiency_audit", "structural_mfe")].kind == KIND_CONFLICT       # 112 contra 110.5


def test_an_audit_already_registered_compares_its_resolution_time_like_any_field(tmp_path):
    db, bank = _account(tmp_path, efficiency=dict(
        resolution_type=CONFIRMED, real_bias_b="BOS", resolution_time=TOUCH + timedelta(seconds=40),
        audit_registration_time=SAVED_OLD, resolution_time_source="candles"))
    changes = _by_field(plan_account("000", db, bank))
    assert ("efficiency_audit", "audit_registration_time") not in changes
    assert changes[("efficiency_audit", "resolution_time")].kind == KIND_UNCHANGED  # mismo minuto
    db2, _ = _account(tmp_path, efficiency=dict(
        resolution_type=CONFIRMED, real_bias_b="BOS", resolution_time=TOUCH + timedelta(hours=2),
        audit_registration_time=SAVED_OLD), name="other.db")
    assert _by_field(plan_account("000", db2, bank))[("efficiency_audit", "resolution_time")].kind == KIND_CONFLICT


def test_without_candles_the_old_time_still_moves_and_resolution_time_is_left_empty_with_the_reason(tmp_path):
    db, bank = _account(tmp_path, clock="clock_unverified", efficiency=dict(
        resolution_type=CONFIRMED, real_bias_b="BOS", resolution_time=SAVED_OLD))
    changes = _by_field(plan_account("000", db, bank))
    assert changes[("efficiency_audit", "audit_registration_time")].kind == KIND_LEGACY_MOVE
    time = changes[("efficiency_audit", "resolution_time")]
    assert (time.kind, time.new_value, time.source) == (KIND_FILL, None, "clock_unverified")
    assert changes[("efficiency_audit", "resolution_time_source")].new_value == "clock_unverified"
    assert ("efficiency_audit", "resolution_type") not in changes  # sin propuesta, nada que comparar


def test_filled_orders_get_mae_mfe_and_could_hit_tp_and_the_others_are_skipped(tmp_path):
    trade = dict(order_filled=True, entry_time=T0 + timedelta(minutes=20), exit_time=T0 + timedelta(hours=2),
                 entry_price=100.0, stop_loss=95.0, take_profit=110.0)
    db, bank = _account(tmp_path, efficiency=dict(resolution_type="Open"), tactical=[
        dict(trade, id="t1", mae_adverse=None, mfe_favorable=3.0, could_hit_tp="yes"),
        dict(trade, id="t2", order_filled=False),                                   # no llenada: nada
        dict(trade, id="t3", exit_time=trade["entry_time"]),        # salida = entrada: sin MAE/MFE (RF-9b)
    ])
    tactical = [c for c in plan_account("000", db, bank).changes if c.table_name == "tactical_audit"]
    assert {c.record_id for c in tactical} == {"t1", "t3"}
    assert {c.field for c in tactical if c.record_id == "t3"} == {"could_hit_tp"}  # RF-10 no depende de la salida
    by_field = {c.field: c for c in tactical if c.record_id == "t1"}
    assert (by_field["mae_adverse"].kind, by_field["mae_adverse"].new_value) == (KIND_FILL, 0.2)
    assert (by_field["mfe_favorable"].kind, by_field["mfe_favorable"].new_value) == (KIND_CONFLICT, 2.1)
    assert by_field["could_hit_tp"].kind == KIND_UNCHANGED
    assert all(c.trade_id == "a1" for c in tactical)


def test_r_values_within_a_tenth_of_r_are_unchanged(tmp_path):
    trade = dict(order_filled=True, entry_time=T0 + timedelta(minutes=20), exit_time=T0 + timedelta(hours=2),
                 entry_price=100.0, stop_loss=95.0, take_profit=110.0)
    db, bank = _account(tmp_path, tactical=[dict(trade, id="t1", mae_adverse=0.25, mfe_favorable=2.21)])
    by_field = {c.field: c for c in plan_account("000", db, bank).changes if c.table_name == "tactical_audit"}
    assert by_field["mae_adverse"].kind == KIND_UNCHANGED and by_field["mfe_favorable"].kind == KIND_CONFLICT


def test_the_plan_covers_the_real_accounts_that_exist_and_never_writes(tmp_path):
    db, bank = _account(tmp_path, efficiency=dict(resolution_type="Open"))
    before = open(db, "rb").read()
    plans = build_plan(str(tmp_path), bank, real_accounts={"000": "xau.db", "009": "missing.db"})
    assert [(p.account, p.db_name) for p in plans] == [("000", "xau.db")]
    assert plans[0].count(KIND_FILL) == 7 and plans[0].count(KIND_CONFLICT) == 0
    assert open(db, "rb").read() == before


def test_an_open_with_a_real_bias_b_is_a_manual_value_and_becomes_a_conflict(tmp_path):
    db, bank = _account(tmp_path, efficiency=dict(resolution_type="Open", real_bias_b="BOS"))
    assert _by_field(plan_account("000", db, bank))[("efficiency_audit", "resolution_type")].kind == KIND_CONFLICT


def test_with_the_clock_not_verified_no_tactical_change_is_proposed(tmp_path):
    trade = dict(id="t1", order_filled=True, entry_time=T0 + timedelta(minutes=20), exit_time=T0 + timedelta(hours=2),
                 entry_price=100.0, stop_loss=95.0, take_profit=110.0)
    db, bank = _account(tmp_path, clock="clock_unverified", tactical=[trade])
    assert [c for c in plan_account("000", db, bank).changes if c.table_name == "tactical_audit"] == []


def test_a_time_with_a_source_is_never_taken_for_an_old_save_time(tmp_path):
    # Un audit nunca hecho que un backfill anterior ya llenó: hora del toque, origen `candles`, sin registro.
    db, bank = _account(tmp_path, efficiency=dict(resolution_type=CONFIRMED, real_bias_b=None, resolution_time=TOUCH,
                                                  resolution_time_source="candles"))
    changes = _by_field(plan_account("000", db, bank))
    assert ("efficiency_audit", "audit_registration_time") not in changes
    assert changes[("efficiency_audit", "resolution_time")].kind == KIND_UNCHANGED


def test_an_overlap_with_the_same_touch_as_the_manual_audit_is_not_a_conflict(tmp_path):
    """N52: con un análisis B iniciado antes del toque, el plan propone lo del toque, no "Overlap Invalidation"."""
    bank_root = tmp_path / "bank"
    bank_root.mkdir()
    _write_bank(bank_root)
    db = tmp_path / "xau.db"
    _write_db(db, [{"id": "a1", "created_at": T0 + timedelta(minutes=30)},
                   {"id": "b1", "created_at": T0 + timedelta(minutes=50)}], old_schema=False)  # b1: 00:30, antes del toque
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(insert(EfficiencyAudit.__table__).values(
            id="a1", bias_a="BOS", real_bias_b="BOS", resolution_type=CONFIRMED, structural_resolution=MINIMAL,
            failure_reason=FailureReason.NA.value))
    engine.dispose()
    changes = _by_field(plan_account("000", str(db), str(bank_root)))
    for name in ("resolution_type", "structural_resolution", "failure_reason"):
        assert changes[("efficiency_audit", name)].kind == "unchanged", name


# --- T49b: una hora vacía por un motivo temporal se vuelve a intentar ---------------------------------------------------

def _waiting(tmp_path, reason, clock="verified", name="xau.db"):
    """Un audit viejo al que un backfill anterior le movió la hora del guardado y dejó `resolution_time` vacía."""
    return _account(tmp_path, clock=clock, name=name, efficiency=dict(
        resolution_type=CONFIRMED, real_bias_b="BOS", resolution_time=None, audit_registration_time=SAVED_OLD,
        resolution_time_source=reason))


@pytest.mark.parametrize("reason", ["pending_candles", "clock_unverified", "clock_misaligned"])
def test_a_time_left_empty_for_a_temporary_reason_is_filled_once_the_candles_have_the_touch(tmp_path, reason):
    db, bank = _waiting(tmp_path, reason)
    changes = _by_field(plan_account("000", db, bank))
    time, source = changes[("efficiency_audit", "resolution_time")], changes[("efficiency_audit", "resolution_time_source")]
    assert (time.kind, time.old_value, time.new_value, time.source) == (KIND_FILL, None, TOUCH, "candles")
    assert (source.kind, source.old_value, source.new_value) == (KIND_FILL, reason, "candles")
    assert ("efficiency_audit", "audit_registration_time") not in changes  # la hora del guardado ya se movió


def test_a_temporary_reason_that_changes_is_updated_and_one_that_stays_is_left_alone(tmp_path):
    db, bank = _waiting(tmp_path, "pending_candles", clock="clock_unverified")
    changes = _by_field(plan_account("000", db, bank))
    assert ("efficiency_audit", "resolution_time") not in changes
    source = changes[("efficiency_audit", "resolution_time_source")]
    assert (source.old_value, source.new_value) == ("pending_candles", "clock_unverified")
    db2, _ = _waiting(tmp_path, "clock_unverified", clock="clock_unverified", name="same.db")
    changes = _by_field(plan_account("000", db2, bank))
    assert ("efficiency_audit", "resolution_time") not in changes
    assert ("efficiency_audit", "resolution_time_source") not in changes


@pytest.mark.parametrize("reason", ["no_levels", "no_history", "ambiguous", "open", "no_mt5_symbol", "corrected"])
def test_a_final_reason_or_a_time_the_operator_cleared_is_never_filled(tmp_path, reason):
    db, bank = _waiting(tmp_path, reason)
    changes = _by_field(plan_account("000", db, bank))
    assert ("efficiency_audit", "resolution_time") not in changes
    assert ("efficiency_audit", "resolution_time_source") not in changes


def test_an_account_whose_clock_is_verified_later_gets_its_times_in_a_second_run(tmp_path):
    """El caso de US100: la primera corrida mueve la hora del guardado; la segunda, con el reloj ya verificado, llena
    la hora del toque; la tercera no cambia nada."""
    import sqlite3
    from tools.auto_backfill import apply_plan
    from tools.candle_bank import build_status_payload, write_bank_status
    db, bank = _account(tmp_path, clock="clock_unverified", efficiency=dict(
        resolution_type=CONFIRMED, real_bias_b="BOS", resolution_time=SAVED_OLD))
    apply_plan(db, plan_account("000", db, bank), run_id="run-1")
    write_bank_status(f"{bank}/XAUUSD", build_status_payload("XAUUSD", "verified", "overlap", "unverified", "r2",
                                                             "merged", {}))
    apply_plan(db, plan_account("000", db, bank), run_id="run-2")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT resolution_time, resolution_time_source, audit_registration_time "
                            "FROM efficiency_audit").fetchone() == (
            "2026-06-01 01:00:00.000000", "candles", "2026-06-02 18:00:00.000000")
    third = plan_account("000", db, bank)
    assert third.count(KIND_FILL) == third.count(KIND_LEGACY_MOVE) == 0
