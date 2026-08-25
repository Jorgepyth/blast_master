import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import tools.database
from tools.database import Base, UnifiedDepartment
from cli.main import flow_new_analysis
from cli.schemas.efficiency import Direction, Strength
from cli.schemas.audit_efficiency import StructuralBias
from cli.schemas.tactical import Hierarchy, Timeframe, FractalType, TacticalClassification


@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    tools.database.engine_default = engine
    return engine


def _drive_wizard_to_review(feed_now_answer):
    """Builds the full sequence of `inquirer.select` answers needed to walk
    flow_new_analysis() from Step 1 (asset) through Step 3 (Confirm & Save)
    and into the new post-save prompt, in the exact order cli/main.py asks them."""
    return [
        "XAU/USD",                                     # prompt_asset
        Direction.LONG, Strength.STRONG,               # p0_dir, p0_str
        Direction.LONG, Strength.STRONG,               # p2_dir, p2_str
        Direction.LONG, Strength.STRONG,               # p3_dir, p3_str
        Direction.LONG, Strength.STRONG,               # p1_dir, p1_str
        Timeframe.M15, FractalType.FIRST_ITERATION,    # p1_tf, p1_type
        Direction.LONG, Strength.STRONG,               # p4_dir, p4_str
        Hierarchy.PSYCH_LEVEL,                         # p4_hier
        "1H",                                          # efficiency_timeframe
        StructuralBias.BOS,                            # bias_a
        TacticalClassification.CONTINUATION_PRESSURE,  # tact_class
        "save",                                        # Review Action -> Confirm & Save
        feed_now_answer,                                # NEW: post-save "feed a Tactical Audit now?" prompt
    ]


@patch("cli.main.flow_pending_audits")
@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_mandatory_int", return_value=2)
@patch("cli.main.get_mandatory_text", return_value="some thesis text")
@patch("InquirerPy.inquirer.text")
@patch("InquirerPy.inquirer.select")
def test_flow_new_analysis_accepts_post_save_prompt_and_opens_tactical_audit(
    mock_select, mock_text, mock_get_text, mock_get_int, mock_visual, mock_pending_audits, in_memory_db
):
    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = _drive_wizard_to_review("yes")

    mock_text_prompt = MagicMock()
    mock_text.return_value = mock_text_prompt
    mock_text_prompt.execute.return_value = ""  # mark_price_raw / evp_raw / si_raw all optional, blank

    with patch("builtins.input", return_value=""):
        flow_new_analysis()

    # The record was persisted before the prompt fired
    with Session(in_memory_db) as session:
        records = session.scalars(select(UnifiedDepartment)).all()
        assert len(records) == 1
        record = records[0]
        assert record.asset == "XAU/USD"

    # Accepting the prompt must hand off to flow_pending_audits() for THIS trade_id,
    # forcing a brand-new tactical row and keeping the normal promotion rule.
    mock_pending_audits.assert_called_once()
    _, kwargs = mock_pending_audits.call_args
    assert kwargs["preselected_trade_id"] == record.id
    assert kwargs["preselected_choice"] == "tac"
    assert kwargs["state_rule"] == "promote"
    assert kwargs["force_new_tactical"] is True
    assert kwargs["preselected_payload"]["asset"] == "XAU/USD"


@patch("cli.main.flow_pending_audits")
@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_mandatory_int", return_value=2)
@patch("cli.main.get_mandatory_text", return_value="some thesis text")
@patch("InquirerPy.inquirer.text")
@patch("InquirerPy.inquirer.select")
def test_flow_new_analysis_declines_post_save_prompt_and_returns_to_menu(
    mock_select, mock_text, mock_get_text, mock_get_int, mock_visual, mock_pending_audits, in_memory_db
):
    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = _drive_wizard_to_review("no")

    mock_text_prompt = MagicMock()
    mock_text.return_value = mock_text_prompt
    mock_text_prompt.execute.return_value = ""

    with patch("builtins.input", return_value=""):
        flow_new_analysis()

    # The record still gets persisted -- declining only skips the tactical shortcut
    with Session(in_memory_db) as session:
        records = session.scalars(select(UnifiedDepartment)).all()
        assert len(records) == 1

    # Declining must NOT jump into the tactical audit flow
    mock_pending_audits.assert_not_called()
