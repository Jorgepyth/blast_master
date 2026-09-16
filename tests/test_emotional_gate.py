"""
Gate emocional (anxiety_level >= ANXIETY_GATE_THRESHOLD) en el wizard de Tactical
Audit (flow_pending_audits(), rama "camino principal", cli/main.py). Independiente
del gate de Tier D/F (tests/test_tactical_tier_gate.py): éste sí admite override,
con una justificación obligatoria persistida en
TacticalAudit.emotional_gate_override_reason. Ver ARCHITECTURE.md §15.
"""
import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import tools.database
from tools.database import Base, UnifiedDepartment, LifecycleState
from tools.database import TacticalAudit as TacticalAuditORM
from cli.main import flow_pending_audits, ANXIETY_GATE_THRESHOLD
from cli.schemas.audit_tactical import (
    HTFTrendContext, TrendContext, ConfirmationStatus, SkipReason,
    PrimaryEmotion, MarketState, SetupType, ExitType, FollowedPlan,
)


@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    tools.database.engine_default = engine
    return engine


@pytest.fixture(autouse=True)
def isolated_cache_file(tmp_path, monkeypatch):
    import cli.main as cli_main
    monkeypatch.setattr(cli_main, "CACHE_FILE", str(tmp_path / "paused_audits.json"))


def _seed_unified(engine, trade_id="trade-emotional-gate", asset="XAUUSD"):
    with Session(engine) as session:
        session.add(UnifiedDepartment(
            id=trade_id,
            state=LifecycleState.PENDING_AUDITS.value,
            asset=asset,
            market_bias="Bullish",
            calc_edge=0.30,
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
            updated_at=datetime.datetime.utcnow(),
        ))
        session.commit()
    return trade_id, {"asset": asset, "efficiency": {"Market_Bias": "Bullish"}}


def _run_flow_pending_audits(trade_id, payload):
    flow_pending_audits(
        preselected_trade_id=trade_id,
        preselected_payload=payload,
        preselected_choice="tac",
        state_rule="promote",
        force_new_tactical=True,
    )


def _get_saved_row(engine, trade_id):
    with Session(engine) as session:
        rows = session.scalars(
            select(TacticalAuditORM).where(TacticalAuditORM.trade_id == trade_id)
        ).all()
        assert len(rows) == 1
        return rows[0]


@patch("cli.main.render_pnl_box", return_value="accept")
@patch("cli.main.get_mandatory_time")
@patch("cli.main.get_mandatory_int")
@patch("cli.main.get_multi_enum_choice", return_value=[])
@patch("cli.main.get_enum_choice")
@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_mandatory_text")
@patch("cli.main.get_mandatory_float")
@patch("InquirerPy.inquirer.checkbox")
@patch("InquirerPy.inquirer.select")
def test_anxiety_below_threshold_no_override_asked(
    mock_select, mock_checkbox, mock_float, mock_get_text, mock_visual,
    mock_get_enum_choice, mock_get_multi_enum_choice, mock_get_int, mock_get_time,
    mock_render_pnl_box, in_memory_db
):
    trade_id, payload = _seed_unified(in_memory_db)

    mock_get_enum_choice.side_effect = [
        HTFTrendContext.BULLISH, TrendContext.BULLISH,   # htf_trend, ltf_trend
        PrimaryEmotion.EQUANIMITY, MarketState.TREND, SetupType.TREND_PULLBACK,
    ]
    mock_get_time.return_value = datetime.time(10, 30)
    mock_float.side_effect = [1900.0, 1950.0, 1.0, 2000.0, 5.0]  # sl, entry_p, size, tp, cost
    mock_get_int.side_effect = [3, 2, ANXIETY_GATE_THRESHOLD - 1]  # mental_clarity, impatience, anxiety
    mock_get_text.side_effect = ["Feeling calm.", "Missed by a few pips."]  # pre_trade_emotions, lesson_tact

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = [
        "gates",                          # ask_tac_mode
        ConfirmationStatus.S1_CLEAN,      # conf_status
        "no",                              # ask_order_filled -> False
        SkipReason.PRECIO_NUNCA_LLEGO,     # ask_skip_reason
        "save",                            # Review Action -> Confirm & Save
    ]

    mock_checkbox_prompt = MagicMock()
    mock_checkbox.return_value = mock_checkbox_prompt
    mock_checkbox_prompt.execute.side_effect = [
        ["g1", "g2", "g3", "g4", "g5", "g6", "g7"],  # ask_gates: all pass
        ["c1", "c2"],                                  # ask_confirmations: 2 -> Tier C
    ]

    with patch("builtins.input", return_value=""):
        _run_flow_pending_audits(trade_id, payload)

    # Only 2 get_mandatory_text calls -- no override justification was requested.
    assert mock_get_text.call_count == 2

    row = _get_saved_row(in_memory_db, trade_id)
    assert row.anxiety_level == ANXIETY_GATE_THRESHOLD - 1
    assert row.emotional_gate_override_reason is None
    assert row.tier_setup == "C"


@pytest.mark.parametrize("anxiety_value", [ANXIETY_GATE_THRESHOLD, 5])
@patch("cli.main.render_pnl_box", return_value="accept")
@patch("cli.main.get_mandatory_time")
@patch("cli.main.get_mandatory_int")
@patch("cli.main.get_multi_enum_choice", return_value=[])
@patch("cli.main.get_enum_choice")
@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_mandatory_text")
@patch("cli.main.get_mandatory_float")
@patch("InquirerPy.inquirer.checkbox")
@patch("InquirerPy.inquirer.select")
def test_anxiety_at_or_above_threshold_requires_and_persists_override(
    mock_select, mock_checkbox, mock_float, mock_get_text, mock_visual,
    mock_get_enum_choice, mock_get_multi_enum_choice, mock_get_int, mock_get_time,
    mock_render_pnl_box, anxiety_value, in_memory_db
):
    trade_id, payload = _seed_unified(in_memory_db, trade_id=f"trade-emotional-gate-{anxiety_value}")

    mock_get_enum_choice.side_effect = [
        HTFTrendContext.BULLISH, TrendContext.BULLISH,
        PrimaryEmotion.EQUANIMITY, MarketState.TREND, SetupType.TREND_PULLBACK,
    ]
    mock_get_time.return_value = datetime.time(10, 30)
    mock_float.side_effect = [1900.0, 1950.0, 1.0, 2000.0, 5.0]
    mock_get_int.side_effect = [3, 2, anxiety_value]
    override_text = f"Forced the review despite anxiety={anxiety_value} -- position was already scaling out."
    mock_get_text.side_effect = ["Feeling calm.", override_text, "Missed by a few pips."]

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = [
        "gates",
        ConfirmationStatus.S1_CLEAN,
        "no",
        SkipReason.PRECIO_NUNCA_LLEGO,
        "save",
    ]

    mock_checkbox_prompt = MagicMock()
    mock_checkbox.return_value = mock_checkbox_prompt
    mock_checkbox_prompt.execute.side_effect = [
        ["g1", "g2", "g3", "g4", "g5", "g6", "g7"],
        ["c1", "c2"],
    ]

    with patch("builtins.input", return_value=""):
        _run_flow_pending_audits(trade_id, payload)

    assert mock_get_text.call_count == 3
    assert mock_get_text.call_args_list[1].args[0] == "Emotional Gate Override — Justificación obligatoria"

    row = _get_saved_row(in_memory_db, trade_id)
    assert row.anxiety_level == anxiety_value
    assert row.emotional_gate_override_reason == override_text


@patch("cli.main.render_pnl_box", return_value="accept")
@patch("cli.main.get_mandatory_datetime")
@patch("cli.main.get_mandatory_time")
@patch("cli.main.get_mandatory_int")
@patch("cli.main.get_multi_enum_choice", return_value=[])
@patch("cli.main.get_enum_choice")
@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_mandatory_text")
@patch("cli.main.get_mandatory_float")
@patch("InquirerPy.inquirer.checkbox")
@patch("InquirerPy.inquirer.select")
def test_gate_also_applies_when_order_filled(
    mock_select, mock_checkbox, mock_float, mock_get_text, mock_visual,
    mock_get_enum_choice, mock_get_multi_enum_choice, mock_get_int, mock_get_time,
    mock_get_datetime, mock_render_pnl_box, in_memory_db
):
    trade_id, payload = _seed_unified(in_memory_db, trade_id="trade-emotional-gate-filled")

    mock_get_enum_choice.side_effect = [
        HTFTrendContext.BULLISH, TrendContext.BULLISH,
        PrimaryEmotion.EQUANIMITY, MarketState.TREND, SetupType.TREND_PULLBACK,
        ExitType.MANUAL, FollowedPlan.YES,
    ]
    mock_get_time.return_value = datetime.time(10, 30)
    mock_get_datetime.return_value = datetime.datetime(2026, 1, 1, 11, 0)
    # sl, entry_p, size, tp, cost, close_p, mae, mfe
    mock_float.side_effect = [1900.0, 1950.0, 1.0, 2000.0, 5.0, 1980.0, 2.0, 3.0]
    mock_get_int.side_effect = [3, 2, ANXIETY_GATE_THRESHOLD]
    override_text = "Filled anyway -- setup was too clean to skip despite the anxiety spike."
    mock_get_text.side_effect = [
        "Feeling calm.", override_text, "Mid trade fine.", "Post trade fine.",
        "Trade went as planned.",
    ]

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = [
        "gates",
        ConfirmationStatus.S1_CLEAN,
        "yes",   # ask_order_filled -> True
        "yes",   # ask_could_hit_tp -> yes
        "save",
    ]

    mock_checkbox_prompt = MagicMock()
    mock_checkbox.return_value = mock_checkbox_prompt
    mock_checkbox_prompt.execute.side_effect = [
        ["g1", "g2", "g3", "g4", "g5", "g6", "g7"],
        ["c1", "c2"],
    ]

    with patch("builtins.input", return_value=""):
        _run_flow_pending_audits(trade_id, payload)

    row = _get_saved_row(in_memory_db, trade_id)
    assert row.order_filled is True
    assert row.anxiety_level == ANXIETY_GATE_THRESHOLD
    assert row.emotional_gate_override_reason == override_text


@patch("cli.main.render_pnl_box", return_value="accept")
@patch("cli.main.get_mandatory_time")
@patch("cli.main.get_mandatory_int")
@patch("cli.main.get_multi_enum_choice", return_value=[])
@patch("cli.main.get_enum_choice")
@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_mandatory_text")
@patch("cli.main.get_mandatory_float")
@patch("InquirerPy.inquirer.checkbox")
@patch("InquirerPy.inquirer.select")
def test_lowering_anxiety_via_edit_before_save_clears_override_requirement(
    mock_select, mock_checkbox, mock_float, mock_get_text, mock_visual,
    mock_get_enum_choice, mock_get_multi_enum_choice, mock_get_int, mock_get_time,
    mock_render_pnl_box, in_memory_db
):
    """Anti-loophole, mirroring test_tactical_tier_gate.py's
    test_tier_gate_refires_after_edit_menu_forces_s7: el gate emocional se
    recalcula en cada vuelta del while True externo. Si el usuario dispara el
    gate (anxiety=4, se le pide y da una justificación) pero luego, desde el
    panel de Review, edita "Anxiety Level" a un valor bajo el umbral antes de
    guardar, el registro final NO debe llevar la justificación de override --
    se recalcula fresh en la siguiente vuelta, sin código adicional para
    cerrar el caso."""
    # Cache-hit path on the 2nd pass re-reads func.__name__ inside AuditSession.prompt
    # -- a plain MagicMock doesn't have one (same issue documented in
    # test_tactical_tier_gate.py::test_tier_gate_edit_menu_offers_no_order_entry_fields).
    mock_get_text.__name__ = "get_mandatory_text"
    mock_visual.__name__ = "handle_visual_lesson_assignment"
    mock_get_enum_choice.__name__ = "get_enum_choice"
    mock_get_multi_enum_choice.__name__ = "get_multi_enum_choice"
    mock_float.__name__ = "get_mandatory_float"
    mock_get_int.__name__ = "get_mandatory_int"

    trade_id, payload = _seed_unified(in_memory_db, trade_id="trade-emotional-gate-loophole")

    mock_get_enum_choice.side_effect = [
        HTFTrendContext.BULLISH, TrendContext.BULLISH,
        PrimaryEmotion.EQUANIMITY, MarketState.TREND, SetupType.TREND_PULLBACK,
    ]
    mock_get_time.return_value = datetime.time(10, 30)
    mock_float.side_effect = [1900.0, 1950.0, 1.0, 2000.0, 5.0]
    # 1st pass: mental_clarity, impatience, anxiety=4 (triggers override).
    # Then the "Edit a Field" -> "Anxiety Level" handler calls get_mandatory_int
    # directly (not via session.prompt) to obtain the new value: 2.
    mock_get_int.side_effect = [3, 2, ANXIETY_GATE_THRESHOLD, 2]
    mock_get_text.side_effect = [
        "Feeling calm.",                               # pre_trade_emotions
        "Overridden once, about to lower it via edit.",  # emotional gate override (1st pass)
        "Missed by a few pips.",                       # lesson_tact
    ]

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = [
        "gates",                          # ask_tac_mode
        ConfirmationStatus.S1_CLEAN,      # conf_status (only asked once -- cached on 2nd pass)
        "no",                              # ask_order_filled -> False (1st pass)
        SkipReason.PRECIO_NUNCA_LLEGO,     # ask_skip_reason (1st pass)
        "edit",                            # Review Action (1st pass) -> Edit a Field
        "anxiety",                         # Select Field to Edit -> Anxiety Level
        "save",                            # Review Action (2nd pass) -> Confirm & Save
    ]

    mock_checkbox_prompt = MagicMock()
    mock_checkbox.return_value = mock_checkbox_prompt
    mock_checkbox_prompt.execute.side_effect = [
        ["g1", "g2", "g3", "g4", "g5", "g6", "g7"],
        ["c1", "c2"],
    ]

    with patch("builtins.input", return_value=""):
        _run_flow_pending_audits(trade_id, payload)

    # Only one override justification was ever asked for (1st pass) -- the 2nd
    # pass doesn't re-trigger the gate, so get_mandatory_text isn't called again
    # for it.
    assert mock_get_text.call_count == 3

    row = _get_saved_row(in_memory_db, trade_id)
    assert row.anxiety_level == 2
    assert row.emotional_gate_override_reason is None
    assert row.tier_setup == "C"
