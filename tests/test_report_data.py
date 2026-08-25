import datetime

import pytest
from sqlalchemy.orm import Session

from tools.database import (
    AnalysisLayer,
    EfficiencyAudit,
    LifecycleState,
    TacticalAudit,
    UnifiedDepartment,
    init_db,
)
from tools.report_data import load_report_data


@pytest.fixture
def test_engine():
    return init_db("sqlite:///:memory:")


def _seed_one_closed_trade(engine, record_id="trade-1"):
    with Session(engine) as session:
        session.add(UnifiedDepartment(
            id=record_id,
            state=LifecycleState.READY_FOR_NOTION.value,
            asset="XAUUSD",
            market_bias="Bullish",
            calc_edge=0.5,
            p4_hierarchy="Hard_Level (Daily,Weekly,Monthly)",
            p1_timeframe="15M",
            p1_type="1st_iteration",
            nodes_l1=2,
            nodes_l2=1,
            tactical_classification="Continuation_Pressure",
            long_prob=0.8,
            short_prob=0.2,
            no_trade_prob=0.0,
        ))
        session.add(EfficiencyAudit(
            id=record_id,
            bias_a="BOS",
            resolution_type="Confirmed (A equal to B)",
            real_bias_b="BOS",
            specific_bias_compliance="Valid",
        ))
        session.add(TacticalAudit(
            trade_id=record_id,
            order_filled=True,
            r_multiple=1.5,
            entry_time=datetime.datetime(2026, 1, 1, 10, 0),
            exit_time=datetime.datetime(2026, 1, 1, 11, 0),
            pnl_and_cost=10.0,
        ))
        session.add(AnalysisLayer(trade_id=record_id, department="EFFICIENCY", layer_name="P0", direction="Long", strength="Strong", score=2))
        session.commit()


def test_load_report_data_shapes_all_dataframes(test_engine):
    _seed_one_closed_trade(test_engine)

    data = load_report_data(test_engine)

    assert len(data.unified_df) == 1
    assert len(data.tactical_df) == 1
    assert len(data.efficiency_df) == 1
    assert len(data.layer_df) == 1

    assert "p0_direction" in data.wide.columns
    assert data.wide.loc[0, "p0_direction"] == "Long"
    assert data.wide.loc[0, "p0_score"] == 2

    assert len(data.merged) == 1
    assert data.merged.loc[0, "calc_edge"] == 0.5
    assert data.merged.loc[0, "r_multiple"] == 1.5

    assert len(data.llm_df) == 1
    assert bool(data.llm_df.loc[0, "order_filled"]) is True
    assert data.llm_df.loc[0, "p0_direction"] == "Long"


def test_load_report_data_excludes_open_resolutions(test_engine):
    _seed_one_closed_trade(test_engine, record_id="closed-1")
    with Session(test_engine) as session:
        session.add(UnifiedDepartment(
            id="open-1", state=LifecycleState.ANALYSIS.value, asset="XAUUSD", market_bias="Bearish",
            calc_edge=-0.3, p4_hierarchy="Hard_Level (Daily,Weekly,Monthly)", p1_timeframe="15M",
            p1_type="1st_iteration", nodes_l1=1, nodes_l2=1, tactical_classification="Continuation_Pressure",
            long_prob=0.2, short_prob=0.8, no_trade_prob=0.0,
        ))
        session.add(EfficiencyAudit(id="open-1", bias_a="BOS", resolution_type="Open"))
        session.commit()

    data = load_report_data(test_engine)

    assert len(data.merged) == 1
    assert data.merged.loc[0, "id"] == "closed-1"


def test_load_report_data_handles_empty_db(test_engine):
    data = load_report_data(test_engine)
    assert data.merged.empty
    assert data.llm_df.empty
    assert list(data.wide.columns) == [
        "id",
        "p0_direction", "p1_direction", "p2_direction", "p3_direction", "p4_direction",
        "p0_strength", "p1_strength", "p2_strength", "p3_strength", "p4_strength",
        "p0_score", "p1_score", "p2_score", "p3_score", "p4_score",
    ]
