import pytest
from tools.database import (
    init_db,
    create_record,
    update_record_state,
    get_records_by_state,
    LifecycleState,
    EfficiencyDepartment,
    EfficiencyAudit,
    TacticalDepartment,
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
    
    # 1. Insert mock Efficiency payload
    efficiency_data = {
        "p0_direction": "Long", "p0_strength": "Strong",
        "p2_direction": "Long", "p2_strength": "Strong",
        "p3_direction": "Long", "p3_strength": "Strong",
        "Calc_edge": 3.0, "Market_Bias": "Bullish"
    }
    create_record(record_id, asset, efficiency_data, engine=test_engine)
    
    with Session(test_engine) as session:
        record = session.get(EfficiencyDepartment, record_id)
        assert record is not None
        assert record.asset == "BTC/USDT"
        assert record.calc_edge == 3.0
    
    # 2. Update with Tactical data and transition to OPEN
    tactical_data = {
        "p4_direction": "Long", "p4_strength": "Strong",
        "p1_direction": "Long", "p1_strength": "Strong",
        "p4_hierarchy": "Hard_Level (Daily,Weekly,Monthly)",
        "p1_timeframe": "15M", "p1_type": "1st_iteration",
        "nodes_l1": 2, "nodes_l2": 1,
        "tactical_classification": "Continuation_Pressure",
        "calc_edge": 4.0,
        "long_prob": 0.85, "short_prob": 0.15, "no_trade_prob": 0.0,
        "trade_status": "Trade_taken_good_execution"
    }
    update_record_state(record_id, LifecycleState.OPEN, tactical=tactical_data, engine=test_engine)
    
    with Session(test_engine) as session:
        record = session.get(EfficiencyDepartment, record_id)
        assert record.state == LifecycleState.OPEN.value
        assert record.tactical_department is not None
        assert record.tactical_department.calc_edge == 4.0
    
    # 3. Update with Audit data and transition to READY_FOR_NOTION
    audit_data = {"compliance": "Edge_valid"}
    update_record_state(record_id, LifecycleState.READY_FOR_NOTION, audit_tactical=audit_data, engine=test_engine)
    
    with Session(test_engine) as session:
        record = session.get(EfficiencyDepartment, record_id)
        assert record.state == LifecycleState.READY_FOR_NOTION.value
        assert record.tactical_audit is not None
        assert record.tactical_audit.compliance == "Edge_valid"

    # 4. Verify get_records_by_state formatting matches payload
    records = get_records_by_state(LifecycleState.READY_FOR_NOTION, engine=test_engine)
    assert len(records) == 1
    assert records[0]["payload"]["asset"] == "BTC/USDT"
    assert records[0]["payload"]["efficiency"]["Calc_edge"] == 3.0
    assert records[0]["payload"]["tactical"]["calc_edge"] == 4.0
    assert records[0]["payload"]["audit_tactical"]["compliance"] == "Edge_valid"
