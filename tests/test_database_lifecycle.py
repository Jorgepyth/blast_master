import pytest
import datetime
from tools.database import (
    init_db,
    update_record_state,
    get_records_by_state,
    LifecycleState,
    UnifiedDepartment,
    EfficiencyAudit,
    TacticalAudit
)
from sqlalchemy import select
from sqlalchemy.orm import Session

@pytest.fixture
def test_engine():
    return init_db("sqlite:///:memory:")

def test_database_lifecycle(test_engine):
    record_id = "test-uuid-123"
    asset = "BTC/USDT"
    
    # 1. Insert initial Unified Analysis record
    with Session(test_engine) as session:
        new_record = UnifiedDepartment(
            id=record_id,
            state=LifecycleState.ANALYSIS.value,
            asset=asset,
            market_bias="Bullish",
            calc_edge=3.0,
            edge_description="Initial edge desc",
            p4_hierarchy="Hard_Level (Daily,Weekly,Monthly)",
            p1_timeframe="15M",
            p1_type="1st_iteration",
            nodes_l1=2,
            nodes_l2=1,
            tactical_classification="Continuation_Pressure",
            long_prob=0.85,
            short_prob=0.15,
            no_trade_prob=0.0
        )
        session.add(new_record)
        session.commit()
    
    with Session(test_engine) as session:
        record = session.get(UnifiedDepartment, record_id)
        assert record is not None
        assert record.asset == "BTC/USDT"
        assert record.calc_edge == 3.0
    
    # 2. Transition to PENDING_AUDITS (tactical audit pending)
    update_record_state(record_id, LifecycleState.PENDING_AUDITS, engine=test_engine)

    with Session(test_engine) as session:
        record = session.get(UnifiedDepartment, record_id)
        assert record.state == LifecycleState.PENDING_AUDITS.value

    # 3. Update with Efficiency Audit data and remain in PENDING_AUDITS
    audit_eff_data = {
        "bias_a": "BOS",
        "resolution_type": "Confirmed (A equal to B)",
        "real_bias_b": "BOS",
        "structural_resolution": "Confirmed + expansión significativa",
        "failure_reason": "N/A",
        "specific_bias_compliance": "Valid",
        "false_regime_rate": "True Positive"
    }
    update_record_state(record_id, LifecycleState.PENDING_AUDITS, append_payload={
        "audit_efficiency": audit_eff_data
    }, engine=test_engine)
    
    with Session(test_engine) as session:
        record = session.get(UnifiedDepartment, record_id)
        assert record.state == LifecycleState.PENDING_AUDITS.value
        assert record.efficiency_audit is not None
        assert record.efficiency_audit.bias_a == "BOS"
        
    # 4. Update with Tactical Audit data and transition to READY_FOR_NOTION
    audit_tact_data = {
        "order_filled": True,
        "could_hit_tp": "yes"
    }
    update_record_state(record_id, LifecycleState.READY_FOR_NOTION, append_payload={
        "audit_tactical": audit_tact_data
    }, engine=test_engine)

    with Session(test_engine) as session:
        record = session.get(UnifiedDepartment, record_id)
        assert record.state == LifecycleState.READY_FOR_NOTION.value
        assert len(record.tactical_audits) == 1
        assert record.tactical_audits[0].order_filled is True
        assert record.tactical_audits[0].could_hit_tp == "yes"

    # 5. Verify get_records_by_state formatting matches payload
    records = get_records_by_state(LifecycleState.READY_FOR_NOTION, engine=test_engine)
    assert len(records) == 1
    assert records[0]["payload"]["asset"] == "BTC/USDT"
    assert records[0]["payload"]["efficiency"]["Calc_edge"] == 3.0
    assert records[0]["payload"]["tactical"]["calc_edge"] == 3.0
    assert records[0]["payload"]["audit_tactical"]["order_filled"] is True


def test_update_record_state_ignores_stray_trade_status_key(test_engine):
    # Regression guard: the special-case that wrote a top-level "trade_status"
    # key directly to UnifiedDepartment.trade_status was removed (ago-2026,
    # order_filled replaces it) — a stray key must be silently ignored, not
    # resurrect the old write path.
    record_id = "test-stray-trade-status"
    with Session(test_engine) as session:
        session.add(UnifiedDepartment(
            id=record_id,
            state=LifecycleState.ANALYSIS.value,
            asset="XAU/USD",
            market_bias="Bullish",
            calc_edge=2.0,
            p4_hierarchy="Hard_Level (Daily,Weekly,Monthly)",
            p1_timeframe="15M",
            p1_type="1st_iteration",
            nodes_l1=2,
            nodes_l2=1,
            tactical_classification="Continuation_Pressure",
            long_prob=0.80,
            short_prob=0.20,
            no_trade_prob=0.0
        ))
        session.commit()

    ok = update_record_state(record_id, LifecycleState.PENDING_AUDITS, append_payload={
        "trade_status": "Trade_taken_good_execution"
    }, engine=test_engine)
    assert ok is True

    with Session(test_engine) as session:
        record = session.get(UnifiedDepartment, record_id)
        assert record.state == LifecycleState.PENDING_AUDITS.value
        assert not hasattr(record, "trade_status")


def test_r_multiple_and_captured_mfe_persist_via_update_record_state(test_engine):
    # Regression test for the gap found in the blast_master / notion_api_analysis
    # cross-analysis: r_multiple and captured_mfe were computed by the Pydantic
    # model but silently dropped by update_record_state()'s valid_keys filter
    # because they weren't mapped ORM columns on TacticalAudit.
    record_id = "test-r-multiple-persist"
    with Session(test_engine) as session:
        session.add(UnifiedDepartment(
            id=record_id,
            state=LifecycleState.ANALYSIS.value,
            asset="XAU/USD",
            market_bias="Bullish",
            calc_edge=2.0,
            p4_hierarchy="Hard_Level (Daily,Weekly,Monthly)",
            p1_timeframe="15M",
            p1_type="1st_iteration",
            nodes_l1=2,
            nodes_l2=1,
            tactical_classification="Continuation_Pressure",
            long_prob=0.80,
            short_prob=0.20,
            no_trade_prob=0.0
        ))
        session.commit()

    update_record_state(record_id, LifecycleState.READY_FOR_NOTION, append_payload={
        "audit_tactical": {
            "order_filled": True,
            "r_multiple": 2.0,
            "captured_mfe": 1.0,
        }
    }, engine=test_engine)

    with Session(test_engine) as session:
        record = session.get(UnifiedDepartment, record_id)
        assert len(record.tactical_audits) == 1
        assert record.tactical_audits[0].r_multiple == 2.0
        assert record.tactical_audits[0].captured_mfe == 1.0


def test_structural_mae_and_structural_mfe_persist_via_update_record_state(test_engine):
    # Regression test mirroring test_r_multiple_and_captured_mfe_persist_via_update_record_state:
    # confirma que structural_mae/structural_mfe (agregados a EfficiencyAudit)
    # no queden silenciosamente descartados por el filtro valid_keys de
    # update_record_state() — mismo gap de Fase 0, ahora en efficiency_audit.
    record_id = "test-structural-mae-mfe-persist"
    with Session(test_engine) as session:
        session.add(UnifiedDepartment(
            id=record_id,
            state=LifecycleState.ANALYSIS.value,
            asset="XAU/USD",
            market_bias="Bullish",
            calc_edge=2.0,
            p4_hierarchy="Hard_Level (Daily,Weekly,Monthly)",
            p1_timeframe="15M",
            p1_type="1st_iteration",
            nodes_l1=2,
            nodes_l2=1,
            tactical_classification="Continuation_Pressure",
            long_prob=0.80,
            short_prob=0.20,
            no_trade_prob=0.0
        ))
        session.commit()

    update_record_state(record_id, LifecycleState.READY_FOR_NOTION, append_payload={
        "audit_efficiency": {
            "bias_a": "BOS",
            "real_bias_b": "BOS",
            "resolution_type": "Confirmed (A equal to B)",
            "structural_mae": 3950.0,
            "structural_mfe": 4550.0,
        }
    }, engine=test_engine)

    with Session(test_engine) as session:
        record = session.get(UnifiedDepartment, record_id)
        assert record.efficiency_audit is not None
        assert float(record.efficiency_audit.structural_mae) == 3950.0
        assert float(record.efficiency_audit.structural_mfe) == 4550.0


def test_update_record_state_supports_multiple_tactical_audits(test_engine):
    # Core of the 1:1 -> 1:many change: a second call with tactical_audit_id=None
    # must insert a new row instead of overwriting the first (scaling into the
    # same setup, or retrying it on a later day); a call that DOES pass an
    # existing tactical_audit_id must edit that row in place, not add a third.
    record_id = "test-multi-tactical"
    with Session(test_engine) as session:
        session.add(UnifiedDepartment(
            id=record_id,
            state=LifecycleState.PENDING_AUDITS.value,
            asset="XAU/USD",
            market_bias="Bullish",
            calc_edge=2.0,
            p4_hierarchy="Hard_Level (Daily,Weekly,Monthly)",
            p1_timeframe="15M",
            p1_type="1st_iteration",
            nodes_l1=2,
            nodes_l2=1,
            tactical_classification="Continuation_Pressure",
            long_prob=0.80,
            short_prob=0.20,
            no_trade_prob=0.0
        ))
        session.commit()

    ok1 = update_record_state(record_id, LifecycleState.READY_FOR_NOTION, append_payload={
        "audit_tactical": {"order_filled": True, "r_multiple": 1.5}
    }, engine=test_engine)
    assert ok1 is True

    ok2 = update_record_state(record_id, LifecycleState.READY_FOR_NOTION, append_payload={
        "audit_tactical": {"order_filled": True, "r_multiple": -1.0}
    }, engine=test_engine)
    assert ok2 is True

    with Session(test_engine) as session:
        record = session.get(UnifiedDepartment, record_id)
        assert len(record.tactical_audits) == 2
        r_multiples = sorted(float(ta.r_multiple) for ta in record.tactical_audits)
        assert r_multiples == [-1.0, 1.5]
        first_row_id = record.tactical_audits[0].id

    ok3 = update_record_state(record_id, LifecycleState.READY_FOR_NOTION, append_payload={
        "audit_tactical": {"order_filled": True, "r_multiple": 3.0}
    }, tactical_audit_id=first_row_id, engine=test_engine)
    assert ok3 is True

    with Session(test_engine) as session:
        record = session.get(UnifiedDepartment, record_id)
        assert len(record.tactical_audits) == 2
        edited = next(ta for ta in record.tactical_audits if ta.id == first_row_id)
        assert float(edited.r_multiple) == 3.0

