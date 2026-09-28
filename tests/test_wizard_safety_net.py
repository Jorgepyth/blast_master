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
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import tools.database
from tools.database import Base, UnifiedDepartment, LifecycleState
from tools.database import EfficiencyAudit as EfficiencyAuditORM
from cli.main import flow_pending_audits
from cli.schemas.audit_efficiency import (
    StructuralBias, ResolutionType, StructuralResolution, FailureReason,
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
    mock_text_prompt.execute.side_effect = ["1948.5", "1962.0"]  # 6. MAE, 7. MFE

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = ["save"]  # Review Action -> Confirm & Save

    before = datetime.datetime.now()
    with patch("builtins.input", return_value=""):
        _run_efficiency_audit(trade_id, payload)
    after = datetime.datetime.now()

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
    assert mock_text_prompt.execute.call_count == 2
    mae_call, mfe_call = mock_text.call_args_list
    assert "Structural MAE" in mae_call.kwargs["message"]
    assert "Structural MFE" in mfe_call.kwargs["message"]

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
    # resolution_time es la hora del guardado del audit, nunca preguntada al
    # operador (baseline §2.2) -- INV-1 lo deja así; RF-7g/RF-14 de la spec 002
    # lo cambian recién cuando F7 llegue a esas tareas, no acá.
    assert row.resolution_time is not None
    assert before <= row.resolution_time <= after


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
    mock_text_prompt.execute.side_effect = ["not-a-number", "1962.0"]  # MAE inválido, MFE válido

    mock_select_prompt = MagicMock()
    mock_select.return_value = mock_select_prompt
    mock_select_prompt.execute.side_effect = ["save"]

    with patch("builtins.input", return_value=""):
        _run_efficiency_audit(trade_id, payload)

    row = _get_saved_efficiency_row(in_memory_db, trade_id)
    assert row.structural_mae is None
    assert row.structural_mfe is None
