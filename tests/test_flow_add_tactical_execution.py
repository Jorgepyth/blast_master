import pytest
import datetime
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine

import tools.database
from tools.database import Base, UnifiedDepartment, LifecycleState
from sqlalchemy.orm import Session
from cli.main import flow_add_tactical_execution


@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    tools.database.engine_default = engine
    return engine


def _seed_synced_record(in_memory_db, record_id="trade-synced"):
    with Session(in_memory_db) as session:
        session.add(UnifiedDepartment(
            id=record_id,
            state=LifecycleState.SYNCED.value,
            asset="BTC/USDT",
            market_bias="Bullish",
            calc_edge=0.40,
            edge_description="Already synced to Notion once",
            p4_hierarchy="Psych Level",
            p1_timeframe="15M",
            p1_type="1st_iteration",
            nodes_l1=2,
            nodes_l2=4,
            tactical_classification="Continuation_Pressure",
            long_prob=0.70,
            short_prob=0.20,
            no_trade_prob=0.10,
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow()
        ))
        session.commit()


@patch("cli.main.flow_pending_audits")
@patch("cli.main.get_mandatory_text", return_value="1")
def test_flow_add_tactical_execution_targets_an_already_synced_record(mock_get_text, mock_pending_audits, in_memory_db):
    _seed_synced_record(in_memory_db)

    with patch("builtins.input", return_value=""):
        flow_add_tactical_execution()

    mock_pending_audits.assert_called_once()
    _, kwargs = mock_pending_audits.call_args
    assert kwargs["preselected_trade_id"] == "trade-synced"
    assert kwargs["preselected_choice"] == "tac"
    # Must never downgrade a record that already reached SYNCED/READY_FOR_NOTION/COMPLETED
    assert kwargs["state_rule"] == "preserve"
    assert kwargs["force_new_tactical"] is True
    assert kwargs["preselected_payload"]["asset"] == "BTC/USDT"


@patch("cli.main.flow_pending_audits")
@patch("cli.main.get_mandatory_text", return_value="c")
def test_flow_add_tactical_execution_cancel_does_not_touch_anything(mock_get_text, mock_pending_audits, in_memory_db):
    _seed_synced_record(in_memory_db)

    with patch("builtins.input", return_value=""):
        flow_add_tactical_execution()

    mock_pending_audits.assert_not_called()


@patch("cli.main.flow_pending_audits")
def test_flow_add_tactical_execution_no_records(mock_pending_audits, in_memory_db):
    with patch("builtins.input", return_value=""):
        flow_add_tactical_execution()

    mock_pending_audits.assert_not_called()
