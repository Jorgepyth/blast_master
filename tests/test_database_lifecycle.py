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
    
    # 2. Update with trade execution status and transition to PENDING_AUDITS
    update_record_state(record_id, LifecycleState.PENDING_AUDITS, append_payload={
        "trade_status": "Trade_taken_good_execution"
    }, engine=test_engine)
    
    with Session(test_engine) as session:
        record = session.get(UnifiedDepartment, record_id)
        assert record.state == LifecycleState.PENDING_AUDITS.value
        assert record.trade_status == "Trade_taken_good_execution"
    
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
        "compliance": "Edge_valid",
        "could_hit_tp": "yes"
    }
    update_record_state(record_id, LifecycleState.READY_FOR_NOTION, append_payload={
        "audit_tactical": audit_tact_data
    }, engine=test_engine)
    
    with Session(test_engine) as session:
        record = session.get(UnifiedDepartment, record_id)
        assert record.state == LifecycleState.READY_FOR_NOTION.value
        assert record.tactical_audit is not None
        assert record.tactical_audit.compliance == "Edge_valid"
        assert record.tactical_audit.could_hit_tp == "yes"

    # 5. Verify get_records_by_state formatting matches payload
    records = get_records_by_state(LifecycleState.READY_FOR_NOTION, engine=test_engine)
    assert len(records) == 1
    assert records[0]["payload"]["asset"] == "BTC/USDT"
    assert records[0]["payload"]["efficiency"]["Calc_edge"] == 3.0
    assert records[0]["payload"]["tactical"]["calc_edge"] == 3.0
    assert records[0]["payload"]["audit_tactical"]["compliance"] == "Edge_valid"

