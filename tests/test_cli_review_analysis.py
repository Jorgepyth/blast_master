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
    
    # First call returns the trade ID to inspect, second call returns "back" to exit loop
    mock_prompt.execute.side_effect = ["test-uuid-1", "back"]
    
    with patch("builtins.input", return_value=""):
        flow_review_analysis()
        
    assert mock_select.call_count == 2
