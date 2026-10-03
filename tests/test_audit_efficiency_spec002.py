"""
T36 (spec 002): `cli/schemas/audit_efficiency.py` con `resolution_time` opcional, `audit_registration_time` y
`resolution_time_source` (RF-7g, RF-14, RF-14b, N1, N25), y su guardado vía `update_record_state`.
"""
from datetime import datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

import config.auto_resolution as auto_cfg
from cli.schemas.audit_efficiency import (
    RESOLUTION_TIME_SOURCES,
    EfficiencyAudit,
    FailureReason,
    ResolutionType,
    StructuralBias,
    StructuralResolution,
)
from tools.database import EfficiencyAudit as EfficiencyAuditRow
from tools.database import LifecycleState, UnifiedDepartment, init_db, update_record_state

TOUCH = datetime(2026, 6, 1, 9, 15)
SAVED = datetime(2026, 6, 4, 18, 2, 55)


def _audit(**fields):
    base = dict(efficiency_id="a1", bias_a=StructuralBias.BOS, resolution_type=ResolutionType.CONFIRMED,
                real_bias_b=StructuralBias.BOS, structural_resolution=StructuralResolution.CONFIRMED_MINIMAL,
                failure_reason=FailureReason.NA)
    base.update(fields)
    return EfficiencyAudit(**base)


def test_resolution_time_may_be_empty():
    audit = _audit()
    assert audit.resolution_time is None and audit.specific_bias_compliance == "Valid"
    assert _audit(resolution_time=None, resolution_time_source=auto_cfg.REASON_PENDING_CANDLES).resolution_time is None


def test_the_sources_are_exactly_the_ones_of_the_plan():
    assert RESOLUTION_TIME_SOURCES == ("candles", "corrected", "pending_candles", "no_history", "clock_unverified",
                                       "clock_misaligned", "ambiguous", "no_levels", "no_mt5_symbol", "open")
    reasons = {auto_cfg.REASON_PENDING_CANDLES, auto_cfg.REASON_NO_HISTORY, auto_cfg.REASON_CLOCK_UNVERIFIED,
               auto_cfg.REASON_CLOCK_MISALIGNED, auto_cfg.REASON_AMBIGUOUS, auto_cfg.REASON_NO_LEVELS,
               auto_cfg.REASON_NO_MT5_SYMBOL}
    assert reasons <= set(RESOLUTION_TIME_SOURCES)  # los mismos códigos que usa el resolvedor


@pytest.mark.parametrize("source", RESOLUTION_TIME_SOURCES)
def test_every_source_of_the_plan_is_accepted(source):
    resolution_time = TOUCH if source in ("candles", "corrected") else None
    assert _audit(resolution_time=resolution_time, resolution_time_source=source).resolution_time_source == source


def test_an_unknown_source_is_rejected():
    with pytest.raises(ValidationError, match="resolution_time_source"):
        _audit(resolution_time=TOUCH, resolution_time_source="manual")


@pytest.mark.parametrize("source", ["candles", "corrected"])
def test_candles_or_corrected_need_a_resolution_time(source):
    with pytest.raises(ValidationError, match="resolution_time"):
        _audit(resolution_time=None, resolution_time_source=source)


def test_saving_persists_the_three_columns_through_update_record_state(tmp_path):
    engine = init_db(f"sqlite:///{tmp_path / 'account.db'}")
    with sessionmaker(bind=engine)() as session:
        session.add(UnifiedDepartment(
            id="a1", state="PENDING_AUDITS", asset="XAUUSDT.P", market_bias="Bullish", calc_edge=0.5,
            p4_hierarchy="x", p1_timeframe="15M", p1_type="x", nodes_l1=1, nodes_l2=1, tactical_classification="x",
            long_prob=0.5, short_prob=0.5, no_trade_prob=0.0))
        session.commit()
    audit = _audit(resolution_time=TOUCH, audit_registration_time=SAVED, resolution_time_source="candles")

    assert update_record_state("a1", LifecycleState.READY_FOR_NOTION, {"audit_efficiency": audit.model_dump()},
                               engine=engine)

    with sessionmaker(bind=engine)() as session:
        row = session.get(EfficiencyAuditRow, "a1")
        assert (row.resolution_time, row.audit_registration_time, row.resolution_time_source) == (
            TOUCH, SAVED, "candles")
        assert row.specific_bias_compliance == "Valid"
    engine.dispose()


def test_saving_an_empty_resolution_time_keeps_the_reason(tmp_path):
    engine = init_db(f"sqlite:///{tmp_path / 'account.db'}")
    with sessionmaker(bind=engine)() as session:
        session.add(UnifiedDepartment(
            id="a1", state="PENDING_AUDITS", asset="XAUUSDT.P", market_bias="Bullish", calc_edge=0.5,
            p4_hierarchy="x", p1_timeframe="15M", p1_type="x", nodes_l1=1, nodes_l2=1, tactical_classification="x",
            long_prob=0.5, short_prob=0.5, no_trade_prob=0.0))
        session.commit()
    audit = _audit(audit_registration_time=SAVED, resolution_time_source="clock_unverified")
    assert update_record_state("a1", LifecycleState.READY_FOR_NOTION, {"audit_efficiency": audit.model_dump()},
                               engine=engine)
    with sessionmaker(bind=engine)() as session:
        row = session.get(EfficiencyAuditRow, "a1")
        assert (row.resolution_time, row.resolution_time_source) == (None, "clock_unverified")
    engine.dispose()
