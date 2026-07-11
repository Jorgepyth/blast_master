import pytest
import datetime
import json
from unittest.mock import MagicMock, patch
from cli.main import flow_repair_analysis_audits, get_active_engine
import tools.database
from tools.database import Base, UnifiedDepartment, AnalysisLayer, EfficiencyAudit, TacticalAudit
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    # Set the global defaults as well
    tools.database.engine_default = engine
    return engine

@patch("tools.database.engine_default")
@patch("InquirerPy.inquirer.select")
def test_flow_repair_analysis_audits_empty(mock_select, mock_engine_default, in_memory_db):
    mock_engine_default.return_value = in_memory_db
    tools.database.engine_default = in_memory_db
    
    # When no records exist, it prints a message and returns
    with patch("builtins.input", return_value=""):
        flow_repair_analysis_audits()
        
    mock_select.assert_not_called()

@patch("tools.database.engine_default")
@patch("InquirerPy.inquirer.select")
def test_flow_repair_analysis_audits_on_the_fly_initialization_and_save(mock_select, mock_engine_default, in_memory_db):
    mock_engine_default.return_value = in_memory_db
    tools.database.engine_default = in_memory_db
    
    # 1. Create a trade record that does NOT have efficiency or tactical audits (Incomplete)
    with Session(in_memory_db) as session:
        record = UnifiedDepartment(
            id="trade-1234",
            asset="ETH/USDT",
            market_bias="Bearish",
            calc_edge=0.25,
            edge_description="Original edge desc",
            p4_hierarchy="Psych Level",
            p1_timeframe="15M",
            p1_type="1st_iteration",
            nodes_l1=2,
            nodes_l2=4,
            tactical_classification="Continuation_Pressure",
            long_prob=0.25,
            short_prob=0.65,
            no_trade_prob=0.10,
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow()
        )
        session.add(record)
        
        # Add P0, P2, P3, P4, P1 layers
        for layer_name in ["P0", "P2", "P3", "P4", "P1"]:
            dept = "EFFICIENCY" if layer_name in ["P0", "P2", "P3"] else "TACTICAL"
            session.add(AnalysisLayer(
                trade_id="trade-1234",
                department=dept,
                layer_name=layer_name,
                direction="Short",
                strength="Mid",
                thesis="thesis text"
            ))
        session.commit()
        
    # Mock inquirer prompts to select the trade, edit Efficiency Audit, and then confirm & save
    # Let's mock a sequence of selections:
    # 1. Select trade: "trade-1234"
    # 2. Select Component to Inspect/Repair: "efficiency"
    # 3. Action inside efficiency: "save" (confirm & save default initialized staging dict)
    # 4. Select Component to Inspect/Repair: "tactical"
    # 5. Action inside tactical: "save" (confirm & save default initialized staging dict)
    # 6. Select Component to Inspect/Repair: "back"
    # 7. Select Trade to Inspect and Repair: "back"
    
    mock_prompt = MagicMock()
    mock_select.return_value = mock_prompt
    
    mock_prompt.execute.side_effect = [
        "trade-1234",  # Selected trade ID
        "efficiency",  # Selected Efficiency Audit component
        "save",        # Saved Efficiency Audit (trigger on-the-fly SQL insert)
        "tactical",    # Selected Tactical Audit component
        "save",        # Saved Tactical Audit (trigger on-the-fly SQL insert)
        "back",        # Back to main selection
        "back"         # Back to configuration menu
    ]
    
    with patch("builtins.input", return_value=""):
        flow_repair_analysis_audits()
        
    # Verify that the databases rows now exist!
    with Session(in_memory_db) as session:
        ea = session.get(EfficiencyAudit, "trade-1234")
        ta = session.get(TacticalAudit, "trade-1234")
        
        assert ea is not None
        assert ea.bias_a == "Bearish"
        assert ea.resolution_type is None  # Safe None classification fallback (Amendment 1)
        assert ea.real_bias_b is None      # Safe None classification fallback (Amendment 1)
        
        assert ta is not None
        assert ta.compliance == "nan"      # Saved as "nan" due to SQLite NOT NULL constraint
        assert ta.entry_price == 0.0      # Safe numerical 0.0 fallback (Amendment 1)
        assert ta.stop_loss == 0.0        # Safe numerical 0.0 fallback (Amendment 1)
        assert ta.lesson_learned == ""    # Safe text empty string fallback (Amendment 1)
        assert ta.trade_decision == "Short" # Resolved from active structural direction Short

@patch("tools.database.engine_default")
@patch("InquirerPy.inquirer.select")
def test_flow_repair_analysis_audits_tactical_math_and_parameterized_updates(mock_select, mock_engine_default, in_memory_db):
    mock_engine_default.return_value = in_memory_db
    tools.database.engine_default = in_memory_db
    
    # 1. Create a trade record with existing audits
    with Session(in_memory_db) as session:
        record = UnifiedDepartment(
            id="trade-5678",
            asset="BTC/USDT",
            market_bias="Bullish",
            calc_edge=0.45,
            edge_description="My long trade",
            p4_hierarchy="Psych Level",
            p1_timeframe="5M",
            p1_type="1st_iteration",
            nodes_l1=3,
            nodes_l2=5,
            tactical_classification="Continuation_Pressure",
            long_prob=0.80,
            short_prob=0.10,
            no_trade_prob=0.10,
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow()
        )
        session.add(record)
        
        session.add(EfficiencyAudit(
            id="trade-5678",
            bias_a="Bullish",
            resolution_type="Confirmed",
            real_bias_b="Bullish",
            structural_resolution="Confirmed pero minimal",
            failure_reason="N/A",
            specific_bias_compliance="Valid",
            false_regime_rate="True Positive",
            lesson_learned="no lesson"
        ))
        
        session.add(TacticalAudit(
            id="trade-5678",
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
            lesson_learned="nice win"
        ))
        session.commit()
        
    # We will simulate:
    # 1. Select trade: "trade-5678"
    # 2. Select Component: "tactical"
    # 3. Action inside tactical: "edit"
    # 4. Field to edit: "entry_price"
    # 5. Let's patch get_mandatory_float to return 105.0
    #    This should trigger recalculate_tactical_math:
    #    Pass 1: entry_price=105.0, stop_loss=95.0 -> Direction: Long
    #    Pass 2:
    #      notional_size = 105 * 2.0 = 210.0
    #      capital_at_risk = 2.0 * (105.0 - 95.0) = 20.0
    #      risk_usd = 20.0
    #      pnl = (110.0 - 105.0) * 2.0 = 10.0
    #      r_r = (120.0 - 105.0) / 10.0 = 1.5
    #      r_multiple = (110.0 - 105.0) / 10.0 = 0.5
    #      captured_mfe = 0.5 / 2.0 = 0.25
    # 6. Action inside tactical: "save"
    # 7. Component selection: "back"
    # 8. Trade selection: "back"
    
    mock_prompt = MagicMock()
    mock_select.return_value = mock_prompt
    
    mock_prompt.execute.side_effect = [
        "trade-5678",  # Selected trade ID
        "tactical",    # Selected Tactical Audit component
        "edit",        # Edit a field
        "entry_price", # Selected entry_price to edit
        "save",        # Confirm & Save Tactical Audit
        "back",        # Back to main selection
        "back"         # Back to configuration menu
    ]
    
    with patch("cli.main.get_mandatory_float", return_value=105.0), \
         patch("builtins.input", return_value=""):
        flow_repair_analysis_audits()
        
    # Verify that the mathematical values were recalculated and persisted perfectly!
    with Session(in_memory_db) as session:
        ta = session.get(TacticalAudit, "trade-5678")
        assert ta is not None
        assert ta.entry_price == 105.0
        assert ta.notional_size == 210.0
        assert ta.capital_at_risk == 20.0
        assert ta.risk_usd == 20.0
        assert ta.pnl_and_cost == 10.0
        assert ta.r_r == 1.5

@patch("tools.database.engine_default")
@patch("InquirerPy.inquirer.select")
def test_flow_repair_analysis_audits_unified_recalculation_and_singular_sql_save(mock_select, mock_engine_default, in_memory_db):
    mock_engine_default.return_value = in_memory_db
    tools.database.engine_default = in_memory_db
    
    # 1. Create a trade record
    with Session(in_memory_db) as session:
        record = UnifiedDepartment(
            id="trade-999",
            asset="ETH/USDT",
            market_bias="Bearish",
            calc_edge=-0.35,
            edge_description="Original Desc",
            p4_hierarchy="Psych Level",
            p1_timeframe="15M",
            p1_type="1st_iteration",
            nodes_l1=2,
            nodes_l2=4,
            tactical_classification="Continuation_Pressure",
            long_prob=0.10,
            short_prob=0.80,
            no_trade_prob=0.10,
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow()
        )
        session.add(record)
        
        # Add Short/Strong P-layers
        for layer_name in ["P0", "P2", "P3", "P4", "P1"]:
            dept = "EFFICIENCY" if layer_name in ["P0", "P2", "P3"] else "TACTICAL"
            session.add(AnalysisLayer(
                trade_id="trade-999",
                department=dept,
                layer_name=layer_name,
                direction="Short",
                strength="Strong",
                thesis="Short strong thesis"
            ))
        session.commit()
        
    mock_prompt = MagicMock()
    mock_select.return_value = mock_prompt
    
    mock_prompt.execute.side_effect = [
        "trade-999",   # Selected trade ID
        "unified",     # Select Unified Analysis component
        "edit",        # Edit a field
        "p0_dir",      # Select P0 Direction field to edit
        "save",        # Confirm & Save Repair
        "back",        # Back to main selection
        "back"         # Back to configuration menu
    ]
    
    from cli.schemas.efficiency import Direction
    
    with patch("cli.main.get_enum_choice") as mock_enum, \
         patch("builtins.input", return_value=""):
        
        # Mock P0 direction input to change to Long
        mock_enum_val = MagicMock()
        mock_enum_val.value = "Long"
        mock_enum.return_value = mock_enum_val
        
        flow_repair_analysis_audits()
        
    # Verify that the database updated singular analysis_layer successfully and recalculations persisted!
    with Session(in_memory_db) as session:
        ud = session.get(UnifiedDepartment, "trade-999")
        assert ud is not None
        assert float(ud.calc_edge) == pytest.approx(-0.4)
        assert ud.long_prob == pytest.approx(0.428, 0.01)
        
        p0_layer = session.execute(
            select(AnalysisLayer).where(AnalysisLayer.trade_id == "trade-999", AnalysisLayer.layer_name == "P0")
        ).scalar()
        assert p0_layer is not None
        assert p0_layer.direction == "Long"
        assert p0_layer.score == 2 # Long Strong = 1 * 2 = 2

