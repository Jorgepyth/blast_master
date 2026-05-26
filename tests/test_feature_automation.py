import pytest
import datetime
from tools.database import (
    init_db,
    update_record_state,
    LifecycleState,
    UnifiedDepartment,
    EfficiencyAudit,
    TacticalAudit
)
from sqlalchemy import select
from sqlalchemy.orm import Session
from cli.schemas.audit_tactical import TacticalAudit as PydanticTacticalAudit

@pytest.fixture
def test_engine():
    return init_db("sqlite:///:memory:")

def test_dynamic_exposure_math():
    # Long Trade Decision Test
    audit_long = PydanticTacticalAudit(
        tactical_id="trade-1",
        compliance="Edge_valid",
        entry_price=100.0,
        stop_loss=90.0,
        size=10.0,
        take_profit=120.0,
        cost=0.0
    )
    assert audit_long.trade_decision == "Long"
    assert audit_long.notional_size == 1000.0
    assert audit_long.capital_at_risk == 100.0

    # Short Trade Decision Test
    audit_short = PydanticTacticalAudit(
        tactical_id="trade-2",
        compliance="Edge_valid",
        entry_price=100.0,
        stop_loss=110.0,
        size=10.0,
        take_profit=80.0,
        cost=0.0
    )
    assert audit_short.trade_decision == "Short"
    assert audit_short.notional_size == 1000.0
    assert audit_short.capital_at_risk == 100.0

def test_backdated_and_exposure_db_persistence(test_engine):
    record_id = "test-backdated-123"
    backdated_ts = datetime.datetime(2025, 12, 25, 10, 30)

    # 1. Insert record with backdated timestamp
    with Session(test_engine) as session:
        new_record = UnifiedDepartment(
            id=record_id,
            state=LifecycleState.PENDING_AUDITS.value,
            asset="BTC/USDT",
            market_bias="Bullish",
            calc_edge=1.5,
            p4_hierarchy="Hard_Level (Daily,Weekly,Monthly)",
            p1_timeframe="15M",
            p1_type="1st_iteration",
            nodes_l1=2,
            nodes_l2=1,
            tactical_classification="Continuation_Pressure",
            long_prob=0.85,
            short_prob=0.15,
            no_trade_prob=0.0,
            created_at=backdated_ts,
            updated_at=backdated_ts
        )
        session.add(new_record)
        
        new_ea = EfficiencyAudit(
            id=record_id, 
            bias_a="Bullish",
            created_at=backdated_ts,
            updated_at=backdated_ts
        )
        session.add(new_ea)
        session.commit()

    with Session(test_engine) as session:
        record = session.get(UnifiedDepartment, record_id)
        assert record.created_at == backdated_ts
        assert record.efficiency_audit.created_at == backdated_ts

    # 2. Complete tactical audit with exposure calculations and verify persistence
    audit_tact_payload = {
        "compliance": "Edge_valid",
        "entry_price": 50000.0,
        "stop_loss": 49000.0,
        "size": 0.5,
        "take_profit": 55000.0,
        "could_hit_tp": "yes",
        "mae": 1.0,
        "mfe": 2.0
    }
    
    # Calculate using Pydantic model
    pyd_ta = PydanticTacticalAudit(
        tactical_id=record_id,
        cost=0.0,
        **audit_tact_payload
    )
    
    # Merge pydantic calculated fields into dict payload
    payload_to_save = pyd_ta.model_dump()
    
    update_record_state(
        record_id, 
        LifecycleState.READY_FOR_NOTION, 
        append_payload={"audit_tactical": payload_to_save}, 
        engine=test_engine
    )

    with Session(test_engine) as session:
        record = session.get(UnifiedDepartment, record_id)
        assert record.state == LifecycleState.READY_FOR_NOTION.value
        assert record.tactical_audit is not None
        assert record.tactical_audit.notional_size == 25000.0
        assert record.tactical_audit.capital_at_risk == 500.0
        assert record.tactical_audit.mae_adverse == 1.0
        assert record.tactical_audit.mfe_favorable == 2.0
