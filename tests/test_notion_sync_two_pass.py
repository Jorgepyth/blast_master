import datetime
import requests_mock
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import tools.database
from tools.database import Base, UnifiedDepartment, TacticalAudit, LifecycleState
from tools.notion_sync import sync_records

NOTION_URL = "https://api.notion.com/v1/pages"


def _make_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    tools.database.engine_default = engine
    return engine


@patch("tools.notion_sync._log_sync_error")
def test_sync_records_late_execution_only_creates_tactical_page_not_efficiency(mock_log_error):
    # A record already SYNCED (Efficiency page exists, first tactical row already
    # synced) that gains a second execution later must only get a Tactical page
    # for the NEW row -- the Efficiency page is never recreated or reposted.
    engine = _make_engine()

    with Session(engine) as session:
        record = UnifiedDepartment(
            id="trade-synced",
            state=LifecycleState.SYNCED.value,
            asset="BTC/USDT",
            market_bias="Bullish",
            calc_edge=0.40,
            edge_description="First execution already on Notion",
            p4_hierarchy="Psych Level",
            p1_timeframe="15M",
            p1_type="1st_iteration",
            nodes_l1=2,
            nodes_l2=4,
            tactical_classification="Continuation_Pressure",
            long_prob=0.70,
            short_prob=0.20,
            no_trade_prob=0.10,
            efficiency_page_id="eff-page-existing",
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow()
        )
        session.add(record)
        session.add(TacticalAudit(
            id="ta-already-synced",
            trade_id="trade-synced",
            order_filled=True,
            notion_page_id="tac-page-existing",
            created_at=datetime.datetime(2026, 8, 1, 12, 0, 0),
        ))
        session.add(TacticalAudit(
            id="ta-new-execution",
            trade_id="trade-synced",
            order_filled=True,
            notion_page_id=None,
            created_at=datetime.datetime(2026, 8, 20, 12, 0, 0),
        ))
        session.commit()

    with requests_mock.Mocker() as m:
        m.post(NOTION_URL, json={"id": "tac-page-new"}, status_code=200)
        sync_records()

        # Exactly one Notion page created -- the late tactical execution.
        assert m.call_count == 1
        request_body = m.request_history[0].json()
        assert request_body["properties"]["Efficiency_Relation"]["relation"][0]["id"] == "eff-page-existing"

    with Session(engine) as session:
        record = session.get(UnifiedDepartment, "trade-synced")
        ta_old = session.get(TacticalAudit, "ta-already-synced")
        ta_new = session.get(TacticalAudit, "ta-new-execution")

        assert record.state == LifecycleState.SYNCED.value  # untouched
        assert record.efficiency_page_id == "eff-page-existing"  # untouched, never recreated
        assert ta_old.notion_page_id == "tac-page-existing"  # untouched
        assert ta_new.notion_page_id == "tac-page-new"  # newly synced


@patch("tools.notion_sync._log_sync_error")
def test_sync_records_first_sync_creates_efficiency_and_tactical_pages(mock_log_error):
    engine = _make_engine()

    with Session(engine) as session:
        record = UnifiedDepartment(
            id="trade-first-sync",
            state=LifecycleState.READY_FOR_NOTION.value,
            asset="ETH/USDT",
            market_bias="Bearish",
            calc_edge=-0.30,
            edge_description="Brand new analysis, never synced",
            p4_hierarchy="Psych Level",
            p1_timeframe="15M",
            p1_type="1st_iteration",
            nodes_l1=2,
            nodes_l2=4,
            tactical_classification="Continuation_Pressure",
            long_prob=0.20,
            short_prob=0.70,
            no_trade_prob=0.10,
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow()
        )
        session.add(record)
        session.add(TacticalAudit(
            id="ta-first",
            trade_id="trade-first-sync",
            order_filled=True,
            notion_page_id=None,
            created_at=datetime.datetime.utcnow(),
        ))
        session.commit()

    with requests_mock.Mocker() as m:
        m.post(NOTION_URL, [
            {"json": {"id": "eff-page-new"}, "status_code": 200},
            {"json": {"id": "tac-page-new"}, "status_code": 200},
        ])
        sync_records()
        assert m.call_count == 2

    with Session(engine) as session:
        record = session.get(UnifiedDepartment, "trade-first-sync")
        ta = session.get(TacticalAudit, "ta-first")
        assert record.state == LifecycleState.SYNCED.value
        assert record.efficiency_page_id == "eff-page-new"
        assert ta.notion_page_id == "tac-page-new"
