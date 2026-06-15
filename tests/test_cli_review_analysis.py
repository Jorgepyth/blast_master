import pytest
import datetime
from unittest.mock import MagicMock, patch
from cli.main import flow_review_analysis
import tools.database
from tools.database import Base, UnifiedDepartment, AnalysisLayer
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine

@patch("tools.database.engine_default")
@patch("InquirerPy.inquirer.select")
def test_flow_review_analysis_empty(mock_select, mock_engine_default, in_memory_db):
    mock_engine_default.return_value = in_memory_db
    
    # We override the actual engine_default module variable
    tools.database.engine_default = in_memory_db
    
    # Mock prompt to press Enter
    with patch("builtins.input", return_value=""):
        flow_review_analysis()
        
    # Since no records exist, it should print the "no analyses found" message and exit
    mock_select.assert_not_called()

@patch("tools.database.engine_default")
@patch("InquirerPy.inquirer.select")
def test_flow_review_analysis_with_records(mock_select, mock_engine_default, in_memory_db):
    tools.database.engine_default = in_memory_db
    
    # Populate dummy record
    with Session(in_memory_db) as session:
        record = UnifiedDepartment(
            id="test-uuid-1",
            asset="BTC/USDT",
            market_bias="Bullish",
            calc_edge=0.35,
            edge_description="My cool edge",
            p4_hierarchy="Psych Level",
            p1_timeframe="5M",
            p1_type="1st_iteration",
            nodes_l1=3,
            nodes_l2=5,
            tactical_classification="Continuation_Pressure",
            long_prob=0.75,
            short_prob=0.15,
            no_trade_prob=0.10,
            created_at=datetime.datetime.now(),
            updated_at=datetime.datetime.now()
        )
        session.add(record)
        
        # Add a couple layers
        session.add(AnalysisLayer(
            trade_id="test-uuid-1",
            department="EFFICIENCY",
            layer_name="P0",
            direction="Long",
            strength="Strong",
            thesis="P0 thesis"
        ))
        session.add(AnalysisLayer(
            trade_id="test-uuid-1",
            department="TACTICAL",
            layer_name="P4",
            direction="Long",
            strength="Strong",
            thesis="P4 thesis"
        ))
        
        session.commit()
        
    # Mock inquirer selects
    mock_prompt = MagicMock()
    mock_select.return_value = mock_prompt
    
    # First call returns the trade ID to inspect, second call returns "back" to exit detail view, third call returns "back" to exit loop
    mock_prompt.execute.side_effect = ["test-uuid-1", "back", "back"]
    
    with patch("builtins.input", return_value=""):
        flow_review_analysis()
        
    assert mock_select.call_count == 3

@patch("tools.database.engine_default")
@patch("InquirerPy.inquirer.select")
def test_flow_review_analysis_with_tactical_fields(mock_select, mock_engine_default, in_memory_db):
    import json
    from tools.database import TacticalAudit
    tools.database.engine_default = in_memory_db
    
    # Populate dummy record with full tactical audit
    with Session(in_memory_db) as session:
        record = UnifiedDepartment(
            id="test-uuid-2",
            asset="BTC/USDT",
            market_bias="Bullish",
            calc_edge=0.35,
            edge_description="My cool edge",
            p4_hierarchy="Psych Level",
            p1_timeframe="5M",
            p1_type="1st_iteration",
            nodes_l1=3,
            nodes_l2=5,
            tactical_classification="Continuation_Pressure",
            long_prob=0.75,
            short_prob=0.15,
            no_trade_prob=0.10,
            created_at=datetime.datetime.now(),
            updated_at=datetime.datetime.now()
        )
        session.add(record)
        
        session.add(TacticalAudit(
            id="test-uuid-2",
            compliance="Edge_valid",
            entry_price=100.0,
            closing_price=110.0,
            size=2.0,
            stop_loss=95.0,
            take_profit=120.0,
            mae_adverse=0.1,
            mfe_favorable=2.0,
            could_hit_tp="yes",
            risk_usd=10.0,
            r_r=4.0,
            pnl_and_cost=20.0,
            notional_size=200.0,
            capital_at_risk=10.0,
            trade_decision="Long",
            lesson_learned="nice win",
            pre_trade_emotions="excited",
            mid_trade_emotions="patient",
            post_trade_emotions="happy",
            confirmation_params=json.dumps(["5_15M_confirmation", "tier_1_setup"])
        ))
        session.commit()
        
    mock_prompt = MagicMock()
    mock_select.return_value = mock_prompt
    mock_prompt.execute.side_effect = ["test-uuid-2", "back", "back"]
    
    with patch("builtins.input", return_value=""):
        flow_review_analysis()
        
    assert mock_select.call_count == 3

