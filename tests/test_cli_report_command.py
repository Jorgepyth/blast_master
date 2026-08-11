import datetime

from click.testing import CliRunner
from sqlalchemy.orm import Session

import tools.database
from tools.database import (
    AnalysisLayer,
    EfficiencyAudit,
    LifecycleState,
    TacticalAudit,
    UnifiedDepartment,
    init_db,
)


def _seed_one_closed_trade(engine):
    with Session(engine) as session:
        session.add(UnifiedDepartment(
            id="trade-1", state=LifecycleState.READY_FOR_NOTION.value, asset="XAUUSD",
            market_bias="Bullish", calc_edge=0.5, p4_hierarchy="Hard_Level (Daily,Weekly,Monthly)",
            p1_timeframe="15M", p1_type="1st_iteration", nodes_l1=2, nodes_l2=1,
            tactical_classification="Continuation_Pressure", long_prob=0.8, short_prob=0.2, no_trade_prob=0.0,
        ))
        session.add(EfficiencyAudit(
            id="trade-1", bias_a="BOS", resolution_type="Confirmed (A equal to B)",
            real_bias_b="BOS", specific_bias_compliance="Valid",
        ))
        session.add(TacticalAudit(
            id="trade-1", compliance="Edge_valid", r_multiple=1.5,
            entry_time=datetime.datetime(2026, 1, 1, 10, 0),
            exit_time=datetime.datetime(2026, 1, 1, 11, 0), pnl_and_cost=10.0,
        ))
        session.add(AnalysisLayer(
            trade_id="trade-1", department="EFFICIENCY", layer_name="P0",
            direction="Long", strength="Strong", score=2,
        ))
        session.commit()


def test_report_command_generates_both_files(tmp_path, monkeypatch):
    from cli.main import cli

    test_engine = init_db("sqlite:///:memory:")
    _seed_one_closed_trade(test_engine)
    monkeypatch.setattr(tools.database, "engine_default", test_engine)

    runner = CliRunner()
    result = runner.invoke(cli, ["report", "--output-dir", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "data_analysis_report.html").exists()
    assert (tmp_path / "data_analysis_llm_report.md").exists()


def test_report_command_html_only(tmp_path, monkeypatch):
    from cli.main import cli

    test_engine = init_db("sqlite:///:memory:")
    _seed_one_closed_trade(test_engine)
    monkeypatch.setattr(tools.database, "engine_default", test_engine)

    runner = CliRunner()
    result = runner.invoke(cli, ["report", "--output-dir", str(tmp_path), "--html-only"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "data_analysis_report.html").exists()
    assert not (tmp_path / "data_analysis_llm_report.md").exists()


def test_report_command_rejects_conflicting_flags(tmp_path):
    from cli.main import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["report", "--output-dir", str(tmp_path), "--html-only", "--llm-only"])

    assert result.exit_code == 0
    assert not (tmp_path / "data_analysis_report.html").exists()
    assert not (tmp_path / "data_analysis_llm_report.md").exists()
