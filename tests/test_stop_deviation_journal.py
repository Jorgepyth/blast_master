"""
Stop Deviation Journaling (flow_pending_audits(), rama "camino principal",
cli/main.py) -- auditable, nunca bloqueante. Distinto de los gates de Tier D/F
(tests/test_tactical_tier_gate.py) y emocional (tests/test_emotional_gate.py):
esta feature no impide nunca guardar el registro. stop_slippage_r se calcula
siempre que unified_department.structural_invalidation no sea NULL; solo pide
una razón (StopDeviationReason, obligatoria) cuando stop_slippage_r > 0. La
nota (stop_deviation_note) es siempre opcional. Ver ARCHITECTURE.md (sin
sección dedicada -- fuera del alcance de esta feature, ver plan de la sesión).
"""
import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import tools.database
from tools.database import Base, UnifiedDepartment, LifecycleState
from tools.database import TacticalAudit as TacticalAuditORM
from cli.main import flow_pending_audits
from cli.schemas.audit_tactical import (
    HTFTrendContext, TrendContext, ConfirmationStatus, SkipReason,
    PrimaryEmotion, MarketState, SetupType, StopDeviationReason,
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


def _seed_unified(engine, trade_id="trade-stop-deviation", asset="XAUUSD", structural_invalidation=None):
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
            structural_invalidation=structural_invalidation,
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
@patch("cli.main.get_mandatory_int", return_value=3)
@patch("cli.main.get_multi_enum_choice", return_value=[])
@patch("cli.main.get_enum_choice")
@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_optional_text")
@patch("cli.main.get_mandatory_text")
@patch("cli.main.get_mandatory_float")
@patch("InquirerPy.inquirer.checkbox")
@patch("InquirerPy.inquirer.select")
def test_stop_wider_than_structural_no_prompt(
    mock_select, mock_checkbox, mock_float, mock_get_text, mock_get_optional_text, mock_visual,
    mock_get_enum_choice, mock_get_multi_enum_choice, mock_get_int, mock_get_time,
    mock_render_pnl_box, in_memory_db
):
    trade_id, payload = _seed_unified(in_memory_db, structural_invalidation=1900.0)

    mock_get_enum_choice.side_effect = [
        HTFTrendContext.BULLISH, TrendContext.BULLISH,   # htf_trend, ltf_trend
        PrimaryEmotion.EQUANIMITY, MarketState.TREND, SetupType.TREND_PULLBACK,
    ]
    mock_get_time.return_value = datetime.time(10, 30)
    # sl=1880 (más ancho que structural_invalidation=1900), entry_p=1950 -> stop_slippage_r = -0.4
    mock_float.side_effect = [1880.0, 1950.0, 1.0, 2000.0, 5.0]  # sl, entry_p, size, tp, cost
    mock_get_text.side_effect = ["Feeling calm.", "Missed by a few pips."]  # pre_trade_emotions, lesson_tact

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = [
        "gates",                          # ask_tac_mode
        ConfirmationStatus.S1_CLEAN,      # conf_status
        # -- ningún prompt de stop_deviation_reason: stop_slippage_r <= 0 --
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

    mock_get_optional_text.assert_not_called()

    row = _get_saved_row(in_memory_db, trade_id)
    assert float(row.stop_slippage_r) == pytest.approx(-0.4)
    assert row.stop_deviation_reason is None
    assert row.stop_deviation_note is None


@patch("cli.main.render_pnl_box", return_value="accept")
@patch("cli.main.get_mandatory_time")
@patch("cli.main.get_mandatory_int", return_value=3)
@patch("cli.main.get_multi_enum_choice", return_value=[])
@patch("cli.main.get_enum_choice")
@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_optional_text")
@patch("cli.main.get_mandatory_text")
@patch("cli.main.get_mandatory_float")
@patch("InquirerPy.inquirer.checkbox")
@patch("InquirerPy.inquirer.select")
def test_stop_narrower_than_structural_requires_reason_optional_note_persists(
    mock_select, mock_checkbox, mock_float, mock_get_text, mock_get_optional_text, mock_visual,
    mock_get_enum_choice, mock_get_multi_enum_choice, mock_get_int, mock_get_time,
    mock_render_pnl_box, in_memory_db
):
    trade_id, payload = _seed_unified(in_memory_db, structural_invalidation=1900.0)

    mock_get_enum_choice.side_effect = [
        HTFTrendContext.BULLISH, TrendContext.BULLISH,
        PrimaryEmotion.EQUANIMITY, MarketState.TREND, SetupType.TREND_PULLBACK,
    ]
    mock_get_time.return_value = datetime.time(10, 30)
    # sl=1920 (más angosto que structural_invalidation=1900), entry_p=1950 -> stop_slippage_r = 0.4
    mock_float.side_effect = [1920.0, 1950.0, 1.0, 2000.0, 5.0]
    mock_get_text.side_effect = ["Feeling calm.", "Missed by a few pips."]
    note_text = "Scaling risk down after two red days."
    mock_get_optional_text.return_value = note_text

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = [
        "gates",
        ConfirmationStatus.S1_CLEAN,
        StopDeviationReason.RISK_BUDGET_CONSTRAINT,  # ask_stop_deviation_reason (obligatorio)
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

    mock_get_optional_text.assert_called_once_with("Stop Deviation Note")

    row = _get_saved_row(in_memory_db, trade_id)
    assert float(row.stop_slippage_r) == pytest.approx(0.4)
    assert row.stop_deviation_reason == "risk_budget_constraint"
    assert row.stop_deviation_note == note_text


@patch("cli.main.render_pnl_box", return_value="accept")
@patch("cli.main.get_mandatory_time")
@patch("cli.main.get_mandatory_int", return_value=3)
@patch("cli.main.get_multi_enum_choice", return_value=[])
@patch("cli.main.get_enum_choice")
@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_optional_text")
@patch("cli.main.get_mandatory_text")
@patch("cli.main.get_mandatory_float")
@patch("InquirerPy.inquirer.checkbox")
@patch("InquirerPy.inquirer.select")
def test_null_structural_invalidation_never_blocks(
    mock_select, mock_checkbox, mock_float, mock_get_text, mock_get_optional_text, mock_visual,
    mock_get_enum_choice, mock_get_multi_enum_choice, mock_get_int, mock_get_time,
    mock_render_pnl_box, in_memory_db
):
    # structural_invalidation=None (default) -- sin dato base en Fase 1
    trade_id, payload = _seed_unified(in_memory_db)

    mock_get_enum_choice.side_effect = [
        HTFTrendContext.BULLISH, TrendContext.BULLISH,
        PrimaryEmotion.EQUANIMITY, MarketState.TREND, SetupType.TREND_PULLBACK,
    ]
    mock_get_time.return_value = datetime.time(10, 30)
    mock_float.side_effect = [1920.0, 1950.0, 1.0, 2000.0, 5.0]
    mock_get_text.side_effect = ["Feeling calm.", "Missed by a few pips."]

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = [
        "gates",
        ConfirmationStatus.S1_CLEAN,
        # -- ningún prompt de stop_deviation_reason: structural_invalidation es NULL --
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

    mock_get_optional_text.assert_not_called()

    row = _get_saved_row(in_memory_db, trade_id)
    assert row.stop_slippage_r is None
    assert row.stop_deviation_reason is None
    assert row.stop_deviation_note is None


@patch("cli.main.render_pnl_box", return_value="accept")
@patch("cli.main.get_mandatory_time")
@patch("cli.main.get_mandatory_int")
@patch("cli.main.get_multi_enum_choice", return_value=[])
@patch("cli.main.get_enum_choice")
@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_optional_text")
@patch("cli.main.get_mandatory_text")
@patch("cli.main.get_mandatory_float")
@patch("InquirerPy.inquirer.checkbox")
@patch("InquirerPy.inquirer.select")
def test_lowering_stop_via_edit_clears_deviation_requirement(
    mock_select, mock_checkbox, mock_float, mock_get_text, mock_get_optional_text, mock_visual,
    mock_get_enum_choice, mock_get_multi_enum_choice, mock_get_int, mock_get_time,
    mock_render_pnl_box, in_memory_db
):
    """Anti-loophole, mirroring test_emotional_gate.py's
    test_lowering_anxiety_via_edit_before_save_clears_override_requirement: si el
    usuario dispara el journaling (stop_slippage_r > 0, da razón) pero luego, desde
    el panel de Review, edita "Stop Loss" a un valor que deja de ser más angosto que
    structural_invalidation, el registro final NO debe llevar la razón dada -- se
    recalcula fresh en la siguiente vuelta del while True, sin código adicional."""
    mock_get_text.__name__ = "get_mandatory_text"
    mock_get_optional_text.__name__ = "get_optional_text"
    mock_visual.__name__ = "handle_visual_lesson_assignment"
    mock_get_enum_choice.__name__ = "get_enum_choice"
    mock_get_multi_enum_choice.__name__ = "get_multi_enum_choice"
    mock_float.__name__ = "get_mandatory_float"
    mock_get_int.__name__ = "get_mandatory_int"

    trade_id, payload = _seed_unified(in_memory_db, structural_invalidation=1900.0)

    mock_get_enum_choice.side_effect = [
        HTFTrendContext.BULLISH, TrendContext.BULLISH,
        PrimaryEmotion.EQUANIMITY, MarketState.TREND, SetupType.TREND_PULLBACK,
    ]
    mock_get_time.return_value = datetime.time(10, 30)
    mock_get_int.return_value = 3
    # 1st pass: sl=1920 (angosto -> stop_slippage_r=0.4, dispara). Then "Edit a Field"
    # -> "Stop Loss" calls get_mandatory_float directly (not via session.prompt) for
    # the new value: 1870.0 (más ancho -> stop_slippage_r deja de ser > 0).
    mock_float.side_effect = [1920.0, 1950.0, 1.0, 2000.0, 5.0, 1870.0]
    mock_get_text.side_effect = ["Feeling calm.", "Missed by a few pips."]
    mock_get_optional_text.return_value = "Overridden once, about to widen it via edit."

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = [
        "gates",                                        # ask_tac_mode
        ConfirmationStatus.S1_CLEAN,                     # conf_status (cached on 2nd pass)
        StopDeviationReason.RISK_BUDGET_CONSTRAINT,      # ask_stop_deviation_reason (1st pass)
        "no",                                             # ask_order_filled -> False (1st pass)
        SkipReason.PRECIO_NUNCA_LLEGO,                    # ask_skip_reason (1st pass)
        "edit",                                            # Review Action (1st pass) -> Edit a Field
        "sl",                                               # Select Field to Edit -> Stop Loss
        "save",                                             # Review Action (2nd pass) -> Confirm & Save
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
    assert row.stop_loss == 1870.0
    assert float(row.stop_slippage_r) == pytest.approx((1870.0 - 1900.0) / (1950.0 - 1900.0))
    assert row.stop_deviation_reason is None
    assert row.stop_deviation_note is None
