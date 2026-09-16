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
            trade_id="test-uuid-2",
            order_filled=True,
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



# --------------------------------------------------------------------------- #
# Fase 2d -- show_unified_detail muestra risk_usd/notional_size/capital_at_risk
# YA PERSISTIDOS (no recalcula -> se eliminó la 4ta copia de la fórmula).
# --------------------------------------------------------------------------- #
def _seed_detail(engine, *, uid, asset, ta_kwargs):
    from tools.database import TacticalAudit
    with Session(engine) as s:
        s.add(UnifiedDepartment(
            id=uid, asset=asset, market_bias="Bullish", calc_edge=0.35,
            edge_description="x", p4_hierarchy="Psych Level", p1_timeframe="5M",
            p1_type="1st_iteration", nodes_l1=3, nodes_l2=5,
            tactical_classification="Continuation_Pressure",
            long_prob=0.75, short_prob=0.15, no_trade_prob=0.10,
            created_at=datetime.datetime.now(), updated_at=datetime.datetime.now()))
        s.add(TacticalAudit(trade_id=uid, order_filled=True, **ta_kwargs))
        s.commit()


def _render_detail(engine, uid, mock_select):
    import cli.main as m
    mock_select.return_value = MagicMock()
    mock_select.return_value.execute.side_effect = [uid, "back", "back"]
    with patch.object(m.console, "clear"), patch("builtins.input", return_value=""), \
         m.console.capture() as cap:
        flow_review_analysis()
    return cap.get()


@patch("tools.database.engine_default")
@patch("InquirerPy.inquirer.select")
def test_detail_panel_shows_persisted_risk_usd_not_recomputed(mock_select, _med, in_memory_db):
    tools.database.engine_default = in_memory_db
    # trade migrado por Track B: size real 0.01, risk_usd GUARDADO 26.80.
    # Un recálculo ciego a contract_size daría 0.01*|4644.27-4617.47| = 0.268.
    _seed_detail(in_memory_db, uid="mig-1", asset="XAUUSDT.P", ta_kwargs=dict(
        entry_price=4644.27, closing_price=4700.0, size=0.01, stop_loss=4617.47,
        take_profit=4700.0, mae_adverse=0.1, mfe_favorable=2.0, could_hit_tp="yes",
        risk_usd=26.80, notional_size=4644.27, capital_at_risk=26.80,
        r_r=2.08, r_multiple=1.92, captured_mfe=0.77, captured_mae=0.0,
        pnl_and_cost=51.39, trade_decision="Long",
        size_source="migrated_mt5_report_v1", size_match_confidence="AMBIGUO",
        size_migrated_at="2026-09-03 17:03:48"))
    out = _render_detail(in_memory_db, "mig-1", mock_select)

    assert "$26.80" in out                        # risk_usd GUARDADO
    assert "26.80" in out and "Capital @ Risk" in out
    assert "$0.27" not in out and "0.268" not in out  # NUNCA el recálculo sin contract_size
    assert "migrado de reporte MT5" in out        # línea de origen (size_source)
    assert "AMBIGUO" in out
    # Fase 2e: PnL en vivo escala por contract_size -> (4695.66-4644.27)*0.01*100 = 51.39
    assert "51.39" in out and "$0.51" not in out


@patch("tools.database.engine_default")
@patch("InquirerPy.inquirer.select")
def test_detail_panel_na_for_unverified_symbol_with_null_risk(mock_select, _med, in_memory_db):
    tools.database.engine_default = in_memory_db
    _seed_detail(in_memory_db, uid="nv-1", asset="BTCUSDT.P", ta_kwargs=dict(
        entry_price=79000.0, closing_price=78000.0, size=0.05, stop_loss=78000.0,
        take_profit=81000.0, mae_adverse=0.1, mfe_favorable=2.0, could_hit_tp="no",
        risk_usd=None, notional_size=None, capital_at_risk=None,
        r_r=2.0, r_multiple=-1.0, captured_mfe=0.0, captured_mae=1.0,
        pnl_and_cost=-50.0, trade_decision="Short"))
    out = _render_detail(in_memory_db, "nv-1", mock_select)

    assert "N/A (símbolo no verificado)" in out
    assert "$79000" not in out and "$3950" not in out   # nada de basura numérica para risk


@patch("tools.database.engine_default")
@patch("InquirerPy.inquirer.select")
def test_detail_query_does_not_crash_when_risk_usd_null(mock_select, _med, in_memory_db):
    tools.database.engine_default = in_memory_db
    # símbolo VERIFICADO pero fila nunca pasó por Fase 2a -> risk_usd NULL
    _seed_detail(in_memory_db, uid="pre-1", asset="XAUUSDT.P", ta_kwargs=dict(
        entry_price=4000.0, closing_price=4050.0, size=1.0, stop_loss=3950.0,
        take_profit=4200.0, mae_adverse=0.1, mfe_favorable=2.0, could_hit_tp="yes",
        risk_usd=None, notional_size=None, capital_at_risk=None,
        r_r=None, r_multiple=None, captured_mfe=None, captured_mae=None,
        pnl_and_cost=None, trade_decision=None))
    out = _render_detail(in_memory_db, "pre-1", mock_select)  # no debe lanzar
    assert "N/A (no calculado)" in out
    assert mock_select.call_count == 3
