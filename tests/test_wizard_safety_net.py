"""
Red de seguridad de los wizards, contra el código **sin modificar** (R10.1,
INV-1, spec 002). No agrega comportamiento nuevo: fija el orden actual de los
prompts y los valores que terminan persistidos en la DB, para que F7 no lo
rompa sin darse cuenta mientras arma las propuestas automáticas de horas,
MAE/MFE y `could_hit_tp` sobre estos mismos wizards.

- **Efficiency Audit** (`flow_pending_audits`, rama `"eff"`): baseline §2.2.
- **Tactical Audit**, rama llenada (`"tac"`): baseline §2.3. Ver T6.
- **`flow_new_analysis`**: baseline §2.1. Ver T7.
"""
import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import tools.database
from tools.database import Base, UnifiedDepartment, LifecycleState
from tools.database import EfficiencyAudit as EfficiencyAuditORM
from tools.database import TacticalAudit as TacticalAuditORM
from cli.main import flow_new_analysis, flow_pending_audits
from cli.schemas.audit_efficiency import (
    StructuralBias, ResolutionType, StructuralResolution, FailureReason,
)
from cli.schemas.audit_tactical import (
    HTFTrendContext, TrendContext, ConfirmationStatus, PrimaryEmotion,
    MarketState, SetupType, ExitType, FollowedPlan,
)
from cli.schemas.efficiency import Direction, Strength
from cli.schemas.tactical import Hierarchy, Timeframe, FractalType, TacticalClassification


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


def _seed_unified_for_efficiency(engine, trade_id="trade-eff-safety-net", asset="XAUUSD"):
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
    payload = {
        "asset": asset,
        "efficiency": {
            "Market_Bias": "Bullish",
            "Edge_Validation_Price": 1950.0,
            "Structural_Invalidation": 1900.0,
            "Mark_Price": 1930.0,
        },
        "audit_efficiency": {"bias_a": "BOS"},
        "edge_description": "Test edge for the safety net.",
    }
    return trade_id, payload


def _run_efficiency_audit(trade_id, payload):
    flow_pending_audits(
        preselected_trade_id=trade_id,
        preselected_payload=payload,
        preselected_choice="eff",
        state_rule="promote",
    )


def _get_saved_efficiency_row(engine, trade_id):
    with Session(engine) as session:
        row = session.get(EfficiencyAuditORM, trade_id)
        assert row is not None, "no se guardó ninguna fila de efficiency_audit"
        return row


@patch("cli.main.get_optional_text")
@patch("cli.main.get_enum_choice")
@patch("InquirerPy.inquirer.text")
@patch("InquirerPy.inquirer.select")
def test_efficiency_prompt_order_excludes_open_and_persists_values(
    mock_select, mock_text, mock_get_enum_choice, mock_get_optional_text, in_memory_db
):
    trade_id, payload = _seed_unified_for_efficiency(in_memory_db)

    mock_get_enum_choice.side_effect = [
        StructuralBias.BOS,                        # 1. real_bias_b
        ResolutionType.CONFIRMED,                   # 2. res_type
        StructuralResolution.CONFIRMED_MINIMAL,      # 3. struct_res
        FailureReason.NA,                            # 4. fail_reason
    ]
    mock_get_optional_text.return_value = "Held the level as expected."  # 5. lesson_eff

    mock_text_prompt = MagicMock()
    mock_text.return_value = mock_text_prompt
    mock_text_prompt.execute.side_effect = ["1948.5", "1962.0", ""]  # 6. MAE, 7. MFE, 8. Resolution Time (T43)

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = ["save"]  # Review Action -> Confirm & Save

    import cli.main as cli_main
    before = cli_main._now_gt().replace(microsecond=0)
    with patch("builtins.input", return_value=""):
        _run_efficiency_audit(trade_id, payload)
    after = cli_main._now_gt()

    # --- Orden de los 7 prompts (baseline §2.2, INV-1) ---
    enum_calls = mock_get_enum_choice.call_args_list
    assert len(enum_calls) == 4
    assert enum_calls[0].args[:2] == ("Real Bias B", StructuralBias)
    assert enum_calls[1].args[:2] == ("Resolution Type", ResolutionType)
    assert enum_calls[2].args[:2] == ("Structural Resolution", StructuralResolution)
    assert enum_calls[3].args[:2] == ("Failure Reason", FailureReason)

    # --- Punto 2: Resolution Type sin OPEN ---
    assert enum_calls[1].kwargs.get("exclude") == [ResolutionType.OPEN]
    # Los otros tres prompts de enum no excluyen nada.
    assert enum_calls[0].kwargs.get("exclude") is None
    assert enum_calls[2].kwargs.get("exclude") is None
    assert enum_calls[3].kwargs.get("exclude") is None

    # --- Puntos 5, 6 y 7, en orden: lesson_eff, luego MAE, luego MFE ---
    mock_get_optional_text.assert_called_once_with("Efficiency Lesson Learned")
    assert mock_text_prompt.execute.call_count == 3
    mae_call, mfe_call, resolution_time_call = mock_text.call_args_list
    assert "Structural MAE" in mae_call.kwargs["message"]
    assert "Structural MFE" in mfe_call.kwargs["message"]
    assert resolution_time_call.kwargs["message"].startswith("Resolution Time")  # el prompt nuevo va al final (RF-7c)

    # --- Valores guardados en efficiency_audit ---
    row = _get_saved_efficiency_row(in_memory_db, trade_id)
    assert row.bias_a == "BOS"
    assert row.real_bias_b == "BOS"
    assert row.resolution_type == "Confirmed (A equal to B)"
    assert row.structural_resolution == "Confirmed pero mínima"
    assert row.failure_reason == "N/A"
    assert row.lesson_learned == "Held the level as expected."
    assert float(row.structural_mae) == pytest.approx(1948.5)
    assert float(row.structural_mfe) == pytest.approx(1962.0)
    # Spec 002, T43 (N1, RF-7g, RF-14): la hora del guardado pasa a audit_registration_time; resolution_time es la
    # del primer toque, y acá quedó vacía (sin propuesta de las velas, el operador la dejó en blanco).
    assert row.resolution_time is None
    assert before <= row.audit_registration_time <= after


@patch("cli.main.get_optional_text")
@patch("cli.main.get_enum_choice")
@patch("InquirerPy.inquirer.text")
@patch("InquirerPy.inquirer.select")
def test_efficiency_invalid_mae_mfe_both_become_none(
    mock_select, mock_text, mock_get_enum_choice, mock_get_optional_text, in_memory_db
):
    """Baseline §2.2: si el MAE o el MFE no convierten a Decimal, los DOS
    quedan en None (no solo el que falló)."""
    trade_id, payload = _seed_unified_for_efficiency(in_memory_db, trade_id="trade-eff-invalid-mae")

    mock_get_enum_choice.side_effect = [
        StructuralBias.BOS, ResolutionType.CONFIRMED,
        StructuralResolution.CONFIRMED_MINIMAL, FailureReason.NA,
    ]
    mock_get_optional_text.return_value = None

    mock_text_prompt = MagicMock()
    mock_text.return_value = mock_text_prompt
    mock_text_prompt.execute.side_effect = ["not-a-number", "1962.0", ""]  # MAE inválido, MFE válido, sin hora

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = ["save"]

    with patch("builtins.input", return_value=""):
        _run_efficiency_audit(trade_id, payload)

    row = _get_saved_efficiency_row(in_memory_db, trade_id)
    assert row.structural_mae is None
    assert row.structural_mfe is None


# --- Tactical Audit, rama llenada (baseline §2.3, T6) ---------------------


def _seed_unified_for_tactical(engine, trade_id="trade-tac-safety-net", asset="XAUUSD",
                                created_at=datetime.datetime(2026, 1, 5, 9, 0)):
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
            structural_invalidation=None,  # sin Stop Deviation Journaling (fuera de T6)
            created_at=created_at,
            updated_at=created_at,
        ))
        session.commit()
    return trade_id, {"asset": asset, "efficiency": {"Market_Bias": "Bullish"}}


def _run_tactical_audit(trade_id, payload):
    flow_pending_audits(
        preselected_trade_id=trade_id,
        preselected_payload=payload,
        preselected_choice="tac",
        state_rule="promote",
        force_new_tactical=True,
    )


def _get_saved_tactical_row(engine, trade_id):
    with Session(engine) as session:
        rows = session.scalars(
            select(TacticalAuditORM).where(TacticalAuditORM.trade_id == trade_id)
        ).all()
        assert len(rows) == 1, f"se esperaba 1 fila táctica, hubo {len(rows)}"
        return rows[0]


@patch("cli.main.render_pnl_box", return_value="accept")
@patch("cli.main.get_mandatory_time")
@patch("cli.main.get_mandatory_datetime")
@patch("cli.main.get_mandatory_int", return_value=3)
@patch("cli.main.get_multi_enum_choice", return_value=[])
@patch("cli.main.get_enum_choice")
@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_optional_text")
@patch("cli.main.get_mandatory_text")
@patch("cli.main.get_mandatory_float")
@patch("InquirerPy.inquirer.checkbox")
@patch("InquirerPy.inquirer.select")
def test_tactical_filled_branch_prompt_order_and_persisted_values(
    mock_select, mock_checkbox, mock_float, mock_get_text, mock_get_optional_text, mock_visual,
    mock_get_enum_choice, mock_get_multi_enum_choice, mock_get_int, mock_get_datetime, mock_get_time,
    mock_render_pnl_box, in_memory_db
):
    """Rama 'llenada' del Tactical (order_filled -> yes), contra el código sin
    modificar: el orden de BLOQUE 5 (baseline §2.3, `mid_trade_emotions` hasta
    `visual_lesson_path`), y los valores guardados: `mae_adverse`,
    `mfe_favorable`, `could_hit_tp`, `exit_time` y `session` (auto-derivado de
    `entry_time`, nunca preguntado -- fuera de alcance de la spec, N/A)."""
    trade_id, payload = _seed_unified_for_tactical(in_memory_db)

    mock_get_enum_choice.side_effect = [
        HTFTrendContext.BULLISH, TrendContext.BULLISH,          # htf_trend, ltf_trend (BLOQUE 1)
        PrimaryEmotion.EQUANIMITY, MarketState.TREND, SetupType.TREND_PULLBACK,  # BLOQUE 4
        ExitType.MANUAL, FollowedPlan.YES,                       # BLOQUE 5
    ]
    mock_get_time.return_value = datetime.time(10, 30)  # Entry Time
    exit_time_val = datetime.datetime(2026, 1, 5, 12, 15)
    mock_get_datetime.return_value = exit_time_val
    # sl, entry_p, size, tp (BLOQUE 3); cost (BLOQUE 4); close_p, mae, mfe (BLOQUE 5)
    mock_float.side_effect = [1900.0, 1950.0, 1.0, 2050.0, 5.0, 1980.0, 2.5, 6.0]
    mock_get_text.side_effect = [
        "Feeling calm.",       # pre_trade_emotions (BLOQUE 4)
        "Managed the trade.",  # mid_trade_emotions (BLOQUE 5, 1º)
        "Relieved it worked.", # post_trade_emotions (BLOQUE 5, 2º)
        "Followed the plan.",  # lesson_tact (BLOQUE 5, último)
    ]

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = [
        "gates",                          # ask_tac_mode
        ConfirmationStatus.S1_CLEAN,      # conf_status
        "yes",                              # ask_could_hit_tp (BLOQUE 5, 5º)
        "yes",                              # ask_order_filled -> True
        "save",                             # Review Action -> Confirm & Save
    ]

    mock_checkbox_prompt = MagicMock()
    mock_checkbox.return_value = mock_checkbox_prompt
    mock_checkbox_prompt.execute.side_effect = [
        ["g1", "g2", "g3", "g4", "g5", "g6", "g7"],  # ask_gates: all pass -> gates_failed_cnt=0
        ["c1", "c2"],                                  # ask_confirmations: 2 -> Tier C (no bloquea)
    ]

    with patch("builtins.input", return_value=""):
        _run_tactical_audit(trade_id, payload)

    # --- Orden de BLOQUE 5, "orden llenada" (baseline §2.3, INV-1) ---
    text_calls = mock_get_text.call_args_list
    assert text_calls[1].args[0] == "Mid Trade Emotions"
    assert text_calls[2].args[0] == "Post Trade Emotions"
    assert text_calls[3].args[0] == "Tactical Lesson Learned"

    enum_calls = mock_get_enum_choice.call_args_list
    assert len(enum_calls) == 7
    assert enum_calls[5].args[:2] == ("Exit Type", ExitType)   # BLOQUE 5, posición 4
    assert enum_calls[6].args[:2] == ("Followed Plan", FollowedPlan)  # BLOQUE 5, posición 7

    # exit_time (get_mandatory_datetime) ocurre antes que exit_type (5º get_enum_choice
    # en general, 6º índice 0-based): no hay forma directa de comparar dos mocks
    # distintos por orden de llamada, así que se confirma indirectamente: el
    # `mock_get_datetime` se llamó exactamente una vez, con el prompt correcto.
    mock_get_datetime.assert_called_once_with("Exit Time")

    # --- Valores guardados: mae_adverse, mfe_favorable, could_hit_tp, exit_time, session ---
    row = _get_saved_tactical_row(in_memory_db, trade_id)
    assert float(row.mae_adverse) == pytest.approx(2.5)
    assert float(row.mfe_favorable) == pytest.approx(6.0)
    assert row.could_hit_tp == "yes"
    assert row.exit_time == exit_time_val
    # entry_time = 2026-01-05 10:30 GT; +6h = 16:30 UTC -> banda 16<=hr<21 -> New York
    # (Session, cli/schemas/audit_tactical.py:442-448; auto-derivado, nunca preguntado)
    assert row.session == "New York"
    assert row.order_filled is True
    assert row.mid_trade_emotions == "Managed the trade."
    assert row.post_trade_emotions == "Relieved it worked."
    assert row.exit_type == "manual"
    assert row.closing_price == pytest.approx(1980.0)
    assert row.followed_plan == "Yes"
    assert row.lesson_learned == "Followed the plan."


# --- flow_new_analysis (baseline §2.1, T7) ---------------------------------


def _drive_new_analysis_wizard(feed_now_answer="no"):
    """Secuencia completa de `inquirer.select` para llevar `flow_new_analysis()`
    del Paso 1 (asset) al Paso 3 (Confirm & Save) y al prompt posterior de
    "feed a Tactical Audit now?", en el mismo orden que pide `cli/main.py`
    (mismo patrón que `tests/test_flow_new_analysis.py::_drive_wizard_to_review`,
    copiado acá para que este archivo no dependa de otro módulo de test)."""
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
        feed_now_answer,                                # post-save "feed a Tactical Audit now?" prompt
    ]


@patch("cli.main.flow_pending_audits")
@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_mandatory_int", return_value=2)
@patch("cli.main.get_mandatory_text", return_value="some thesis text")
@patch("InquirerPy.inquirer.text")
@patch("InquirerPy.inquirer.select")
def test_new_analysis_saves_optional_prices_as_decimal(
    mock_select, mock_text, mock_get_text, mock_get_int, mock_visual, mock_pending_audits, in_memory_db
):
    """Baseline §2.1: Mark Price, Edge Validation Price y Structural Invalidation
    son texto libre opcional, y se guardan como Decimal cuando se completan."""
    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = _drive_new_analysis_wizard("no")

    mock_text_prompt = MagicMock()
    mock_text.return_value = mock_text_prompt
    mock_text_prompt.execute.side_effect = ["1930.25", "1950.50", "1900.00"]  # mark_price, evp, si, en ese orden

    with patch("builtins.input", return_value=""):
        flow_new_analysis()

    with Session(in_memory_db) as session:
        record = session.scalars(select(UnifiedDepartment)).one()
        assert float(record.mark_price) == pytest.approx(1930.25)
        assert float(record.edge_validation_price) == pytest.approx(1950.50)
        assert float(record.structural_invalidation) == pytest.approx(1900.00)
        assert record.is_backdated is False


@patch("cli.main.flow_pending_audits")
@patch("cli.main.handle_visual_lesson_assignment", return_value="nan")
@patch("cli.main.get_mandatory_int", return_value=2)
@patch("cli.main.get_mandatory_text", return_value="some thesis text")
@patch("InquirerPy.inquirer.text")
@patch("InquirerPy.inquirer.select")
def test_new_analysis_backdated_sets_created_at_and_is_backdated(
    mock_select, mock_text, mock_get_text, mock_get_int, mock_visual, mock_pending_audits, in_memory_db
):
    """Baseline §2.1: en un retroactivo, `created_at`/`updated_at` se sobrescriben
    con la hora tipeada, en `unified_department` **y** en `efficiency_audit`
    (cli/main.py:2917-2925); no existe `backdated_timestamp` como columna, la
    marca es `is_backdated`."""
    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = _drive_new_analysis_wizard("no")

    mock_text_prompt = MagicMock()
    mock_text.return_value = mock_text_prompt
    mock_text_prompt.execute.return_value = ""  # precios en blanco: no es el foco de este test

    backdated_ts = datetime.datetime(2026, 3, 1, 14, 0)

    with patch("builtins.input", return_value=""):
        flow_new_analysis(backdated_timestamp=backdated_ts)

    with Session(in_memory_db) as session:
        record = session.scalars(select(UnifiedDepartment)).one()
        assert record.is_backdated is True
        assert record.created_at == backdated_ts
        assert record.updated_at == backdated_ts

        eff_row = session.get(EfficiencyAuditORM, record.id)
        assert eff_row.created_at == backdated_ts
        assert eff_row.updated_at == backdated_ts
