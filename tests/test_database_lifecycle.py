import pytest
from tools.database import (
    init_db,
    create_record,
    update_record_state,
    get_records_by_state,
    LifecycleState
)

@pytest.fixture
def test_engine():
    return init_db("sqlite:///:memory:")

def test_database_lifecycle(test_engine):
    record_id = "test-uuid-123"
    
    # 1. Insert mock Efficiency payload
    efficiency_payload = {"efficiency": {"calc_edge": 3.0}}
    create_record(record_id, efficiency_payload, engine=test_engine)
    
    records = get_records_by_state(LifecycleState.ANALYSIS, engine=test_engine)
    assert len(records) == 1
    assert records[0]["payload"]["efficiency"]["calc_edge"] == 3.0
    
    # 2. Update with Tactical data and transition to OPEN
    tactical_payload = {"tactical": {"calc_edge": 4.0}}
    update_record_state(record_id, LifecycleState.OPEN, tactical_payload, engine=test_engine)
    
    records_open = get_records_by_state(LifecycleState.OPEN, engine=test_engine)
    assert len(records_open) == 1
    assert records_open[0]["payload"]["efficiency"]["calc_edge"] == 3.0
    assert records_open[0]["payload"]["tactical"]["calc_edge"] == 4.0
    
    # 3. Update with Audit data and transition to READY_FOR_NOTION
    audit_payload = {"audit": {"compliance": "Valid"}}
    update_record_state(record_id, LifecycleState.READY_FOR_NOTION, audit_payload, engine=test_engine)
    
    records_ready = get_records_by_state(LifecycleState.READY_FOR_NOTION, engine=test_engine)
    assert len(records_ready) == 1
    assert records_ready[0]["payload"]["efficiency"]["calc_edge"] == 3.0
    assert records_ready[0]["payload"]["tactical"]["calc_edge"] == 4.0
    assert records_ready[0]["payload"]["audit"]["compliance"] == "Valid"
