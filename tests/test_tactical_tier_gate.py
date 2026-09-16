"""
Regresión para el gate duro de Tier D/F en el wizard de Tactical Audit
(flow_pending_audits(), rama "tac", cli/main.py). El tier_setup se calcula
en un único punto (justo antes de "BLOQUE 3: Datos de entrada") y, si
resuelve a D o F (o si el cálculo falla), el wizard debe detenerse antes de
pedir cualquier campo de entrada de orden (Stop Loss/Entry Price/Size/Take
Profit), persistiendo un registro parcial (order_filled=False) por el mismo
camino ya usado por la rama existente "abort_trade". Tier A/B/C debe seguir
sin fricción hasta el primer campo de entrada de orden.
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
    PrimaryEmotion, MarketState, SetupType,
)


@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    tools.database.engine_default = engine
    return engine


@pytest.fixture(autouse=True)
def isolated_cache_file(tmp_path, monkeypatch):
    # AuditSession persists resumable wizard state to a real on-disk JSON
    # cache (cli.main.CACHE_FILE). Point it at a throwaway path for every
    # test in this file so we never touch the real .data/paused_audits.json.
    import cli.main as cli_main
    monkeypatch.setattr(cli_main, "CACHE_FILE", str(tmp_path / "paused_audits.json"))


def _seed_unified(engine, trade_id="trade-tier-gate", asset="XAU/USD"):
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


@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_mandatory_text", return_value="Avoided a bad setup on purpose.")
@patch("cli.main.get_mandatory_float", side_effect=RuntimeError("BLOQUE_3_REACHED"))
@patch("InquirerPy.inquirer.checkbox")
@patch("InquirerPy.inquirer.select")
def test_tier_abc_reaches_order_entry_without_friction(
    mock_select, mock_checkbox, mock_float, mock_get_text, mock_visual, in_memory_db
):
    trade_id, payload = _seed_unified(in_memory_db)

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = [
        "gates",                          # ask_tac_mode
        HTFTrendContext.BULLISH,          # htf_trend
        TrendContext.BULLISH,             # ltf_trend
        # gates_failed_cnt == 0 -> no "gate_action" prompt
        ConfirmationStatus.S1_CLEAN,      # conf_status (S1-S6 only)
    ]

    mock_checkbox_prompt = MagicMock()
    mock_checkbox.return_value = mock_checkbox_prompt
    mock_checkbox_prompt.execute.side_effect = [
        ["g1", "g2", "g3", "g4", "g5", "g6", "g7"],  # ask_gates: all 7 pass
        ["c1", "c2"],                                  # ask_confirmations: 2 -> Tier C
    ]

    with pytest.raises(RuntimeError, match="BLOQUE_3_REACHED"):
        _run_flow_pending_audits(trade_id, payload)

    # Reached "Stop Loss" -- the first order-entry field -- with no extra gate
    # prompt in between for a clean Tier C setup.
    mock_float.assert_called_once()
    assert mock_float.call_args[0][0] == "Stop Loss"

    # Never persisted -- the sentinel exception aborted before Confirm & Save.
    with Session(in_memory_db) as session:
        rows = session.scalars(select(TacticalAuditORM)).all()
        assert rows == []


@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_mandatory_text", return_value="Forced entry avoided real damage after review.")
@patch("cli.main.get_mandatory_float")
@patch("InquirerPy.inquirer.checkbox")
@patch("InquirerPy.inquirer.select")
def test_tier_f_via_forced_entry_blocks_before_order_entry(
    mock_select, mock_checkbox, mock_float, mock_get_text, mock_visual, in_memory_db
):
    trade_id, payload = _seed_unified(in_memory_db)

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = [
        "gates",                              # ask_tac_mode
        HTFTrendContext.BULLISH,              # htf_trend
        TrendContext.BULLISH,                 # ltf_trend
        "Forzar Entrada (Revenge)",           # gate_action (1 gate failed)
        # gates_failed_cnt > 0 -> conf_status auto-forced to S7, no prompt
        "save",                               # Review Action on the blocked panel
    ]

    mock_checkbox_prompt = MagicMock()
    mock_checkbox.return_value = mock_checkbox_prompt
    mock_checkbox_prompt.execute.side_effect = [
        ["g1", "g2", "g3", "g4", "g5", "g6"],  # ask_gates: g7 missing -> gates_failed_cnt=1
    ]

    with patch("builtins.input", return_value=""):
        _run_flow_pending_audits(trade_id, payload)

    # The gate fired before any order-entry field was ever requested.
    mock_float.assert_not_called()

    with Session(in_memory_db) as session:
        rows = session.scalars(select(TacticalAuditORM).where(TacticalAuditORM.trade_id == trade_id)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.order_filled is False
        assert row.tier_setup == "F"
        assert row.skip_reason == SkipReason.INVALIDADA_ANTES_DE_LLENAR.value
        assert row.gates_failed == 1
        assert row.stop_loss == 0.0
        assert row.entry_price == 0.0

        record = session.get(UnifiedDepartment, trade_id)
        assert record.state == LifecycleState.PENDING_AUDITS.value


@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_mandatory_text", return_value="Calc failure -- treated as blocked.")
@patch("cli.main.get_mandatory_float")
@patch("InquirerPy.inquirer.checkbox")
@patch("InquirerPy.inquirer.select")
def test_tier_gate_fails_closed_on_calculation_error(
    mock_select, mock_checkbox, mock_float, mock_get_text, mock_visual, in_memory_db
):
    trade_id, payload = _seed_unified(in_memory_db)

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = [
        "gates",                          # ask_tac_mode
        HTFTrendContext.BULLISH,          # htf_trend
        TrendContext.BULLISH,             # ltf_trend
        ConfirmationStatus.S1_CLEAN,      # conf_status (clean path, gates_failed_cnt == 0)
        "save",                           # Review Action on the blocked (error) panel
    ]

    mock_checkbox_prompt = MagicMock()
    mock_checkbox.return_value = mock_checkbox_prompt
    mock_checkbox_prompt.execute.side_effect = [
        ["g1", "g2", "g3", "g4", "g5", "g6", "g7"],  # ask_gates: all 7 pass
        ["c1", "c2", "c3", "c4", "c5"],                # -> would compute Tier A
    ]

    # Force the tier-gate's own try/except to see an unexpected value where a
    # real A-F grade is expected -- simulates "tier_setup no pudo determinarse"
    # without touching the calc_edge/tier_setup formula itself.
    with patch("cli.main.TierSetup") as mock_tier_setup_enum:
        mock_tier_setup_enum.A = "NOT_A_REAL_TIER"
        mock_tier_setup_enum.B = object()
        mock_tier_setup_enum.C = object()
        mock_tier_setup_enum.D = object()
        mock_tier_setup_enum.F = object()

        with patch("builtins.input", return_value=""):
            _run_flow_pending_audits(trade_id, payload)

    # Never reached BLOQUE 3 -- fail-closed, exactly like a real D/F block.
    mock_float.assert_not_called()

    with Session(in_memory_db) as session:
        rows = session.scalars(select(TacticalAuditORM).where(TacticalAuditORM.trade_id == trade_id)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.order_filled is False
        assert row.tier_setup in (None, "nan")
        assert row.skip_reason == SkipReason.INVALIDADA_ANTES_DE_LLENAR.value


@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_mandatory_text", return_value="Lesson learned text.")
@patch("cli.main.get_mandatory_float")
@patch("InquirerPy.inquirer.checkbox")
@patch("InquirerPy.inquirer.select")
def test_tier_gate_edit_menu_offers_no_order_entry_fields(
    mock_select, mock_checkbox, mock_float, mock_get_text, mock_visual, in_memory_db
):
    # AuditSession.prompt() re-reads func.__name__ on a cache hit (the 2nd
    # pass through the loop after "back"), so the mocks need a __name__ --
    # a plain MagicMock doesn't have one.
    mock_get_text.__name__ = "get_mandatory_text"
    mock_visual.__name__ = "handle_visual_lesson_assignment"

    trade_id, payload = _seed_unified(in_memory_db)

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = [
        "gates",                              # ask_tac_mode
        HTFTrendContext.BULLISH,              # htf_trend
        TrendContext.BULLISH,                 # ltf_trend
        "Forzar Entrada (Revenge)",           # gate_action
        "edit",                               # Review Action -> Edit a Field
        "back",                               # Select Field to Edit -> back
        "save",                               # Review Action -> Confirm & Save
    ]

    mock_checkbox_prompt = MagicMock()
    mock_checkbox.return_value = mock_checkbox_prompt
    mock_checkbox_prompt.execute.side_effect = [
        ["g1", "g2", "g3", "g4", "g5", "g6"],
    ]

    with patch("builtins.input", return_value=""):
        _run_flow_pending_audits(trade_id, payload)

    mock_float.assert_not_called()

    edit_menu_calls = [
        c for c in mock_select.call_args_list
        if c.kwargs.get("message") == "Select Field to Edit >"
    ]
    assert len(edit_menu_calls) == 1
    offered_values = {choice.value for choice in edit_menu_calls[0].kwargs["choices"] if hasattr(choice, "value")}
    assert offered_values.isdisjoint({"sl", "entry_p", "size", "tp", "entry_time"})


@patch("cli.main.render_pnl_box", return_value="accept")
@patch("cli.main.get_mandatory_time")
@patch("cli.main.get_mandatory_int", return_value=3)
@patch("cli.main.get_multi_enum_choice", return_value=[])
@patch("cli.main.get_enum_choice")
@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_mandatory_text", return_value="Lesson learned via edit-menu loophole test.")
@patch("cli.main.get_mandatory_float")
@patch("InquirerPy.inquirer.checkbox")
@patch("InquirerPy.inquirer.select")
def test_tier_gate_refires_after_edit_menu_forces_s7(
    mock_select, mock_checkbox, mock_float, mock_get_text, mock_visual,
    mock_get_enum_choice, mock_get_multi_enum_choice, mock_get_int, mock_get_time,
    mock_render_pnl_box, in_memory_db
):
    """Un tier inicial limpio (C) avanza normalmente hasta el panel de review
    final y llega a pedir Stop Loss/Entry/Size/TP (BLOQUE 3) -- eso es
    correcto, tier C no bloquea. Pero si ahi el usuario usa "Edit a Field" ->
    "Confirmation Status" para forzar S7_REVENGE_FORCED (loophole: ese campo
    no excluye S7, a diferencia del prompt inicial de conf_status), el
    `while True:` externo (cli/main.py:3569) re-ejecuta BLOQUE 2 desde cero en
    la siguiente vuelta, recalcula tier_setup=F con el conf_status editado, y
    el gate D/F se dispara igual -- sin volver a pedir ningun campo de orden y
    sin persistir el intento original (order_filled=False, sl/entry_p/size/tp
    ya contestados en la 1a vuelta nunca llegan a guardarse)."""
    trade_id, payload = _seed_unified(in_memory_db, asset="XAUUSD")

    # get_enum_choice is patched wholesale, so it intercepts *every* call site
    # that goes through it -- not just the ones after BLOQUE 3. htf_trend/
    # ltf_trend (cli/main.py:3430-3431) call it too, same as p_emotion/
    # market_state/setup_t and the "Edit Confirmation Status" edit-menu entry.
    mock_get_enum_choice.side_effect = [
        HTFTrendContext.BULLISH,             # htf_trend
        TrendContext.BULLISH,                # ltf_trend
        PrimaryEmotion.EQUANIMITY,           # p_emotion
        MarketState.TREND,                   # market_state
        SetupType.TREND_PULLBACK,            # setup_t
        ConfirmationStatus.S7_REVENGE_FORCED,  # Edit a Field -> Confirmation Status
    ]
    mock_get_time.return_value = datetime.time(10, 30)
    mock_float.side_effect = [1900.0, 1950.0, 1.0, 2000.0, 5.0]  # sl, entry_p, size, tp, cost

    # conf_status (cli/main.py:3586) is a plain inquirer.select behind a
    # lambda, not get_enum_choice, so it's still driven by mock_select.
    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = [
        "gates",                          # ask_tac_mode
        ConfirmationStatus.S1_CLEAN,      # conf_status (1st pass -- clean, tier C)
        "no",                              # ask_order_filled -> False
        SkipReason.PRECIO_NUNCA_LLEGO,     # ask_skip_reason (1st pass -- gets overwritten)
        "edit",                            # Review Action on the 1st (unblocked, tier C) panel
        "conf_status",                     # Select Field to Edit -> Confirmation Status
        "save",                            # Review Action on the 2nd (blocked, tier F) panel
    ]

    mock_checkbox_prompt = MagicMock()
    mock_checkbox.return_value = mock_checkbox_prompt
    mock_checkbox_prompt.execute.side_effect = [
        ["g1", "g2", "g3", "g4", "g5", "g6", "g7"],  # ask_gates: all 7 pass
        ["c1", "c2"],                                  # ask_confirmations: 2 -> Tier C
    ]

    with patch("builtins.input", return_value=""):
        _run_flow_pending_audits(trade_id, payload)

    # BLOQUE 3 (sl/entry_p/size/tp) + cost were asked exactly once, on the 1st
    # pass -- the re-fired gate on the 2nd pass never asks for them again.
    assert mock_float.call_count == 5

    with Session(in_memory_db) as session:
        rows = session.scalars(select(TacticalAuditORM).where(TacticalAuditORM.trade_id == trade_id)).all()
        assert len(rows) == 1
        row = rows[0]
        # The 1st-pass attempt (tier C, order_filled=False, skip_reason=
        # PRECIO_NUNCA_LLEGO) was never saved -- only the re-fired gate's
        # blocked record is persisted, overwriting the original intent.
        assert row.order_filled is False
        assert row.tier_setup == "F"
        assert row.skip_reason == SkipReason.INVALIDADA_ANTES_DE_LLENAR.value
        assert row.stop_loss == 0.0
        assert row.entry_price == 0.0
