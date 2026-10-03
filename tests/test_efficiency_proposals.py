"""
Propuestas de las velas en el Efficiency Audit (spec 002):
- T41: al abrir el wizard se pide la propuesta una sola vez y, si no la hay, se muestra el motivo en una línea
  ("Still open according to candles", `pending_candles`, `clock_unverified`...). Nunca bloquea (RF-7f, RF-5b).

El wizard se maneja como en tests/test_wizard_safety_net.py (T5). La propuesta se reemplaza en el borde del servicio
(`tools.auto_resolution.propose_for_trade`), así que el test no necesita banco ni DB de archivo.
"""
import dataclasses
import datetime
from unittest.mock import MagicMock, patch

import pytest

import tools.auto_resolution as auto_resolution
from cli.schemas.audit_efficiency import FailureReason, ResolutionType, StructuralBias, StructuralResolution
from config.auto_resolution import REASON_CLOCK_UNVERIFIED, REASON_PENDING_CANDLES
from tests.test_wizard_safety_net import (  # noqa: F401 -- fixtures
    _get_saved_efficiency_row,
    _run_efficiency_audit,
    _seed_unified_for_efficiency,
    in_memory_db,
    isolated_cache_file,
)
from tools.auto_resolution import AutoProposal

ANCHOR = datetime.datetime(2026, 6, 1, 0, 10)


def _drive(engine, proposal_or_error, capsys, answers=None, texts=("", "", "")):
    """Recorre el wizard de punta a punta con respuestas fijas. Devuelve la salida, las llamadas a `get_enum_choice`,
    los pedidos al servicio, las llamadas a `inquirer.text` y el id del análisis."""
    requested = []

    def fake_propose(db_path, trade_id, bank_root):
        requested.append(trade_id)
        if isinstance(proposal_or_error, Exception):
            raise proposal_or_error
        return proposal_or_error

    answers = iter(answers or [StructuralBias.BOS, ResolutionType.CONFIRMED, StructuralResolution.CONFIRMED_MINIMAL,
                               FailureReason.NA])
    trade_id, payload = _seed_unified_for_efficiency(engine)
    with patch.object(auto_resolution, "propose_for_trade", side_effect=fake_propose), \
            patch("cli.main.get_enum_choice", side_effect=lambda *a, **k: next(answers)) as enum_choice, \
            patch("cli.main.get_optional_text", return_value="lesson"), \
            patch("InquirerPy.inquirer.text") as text, patch("InquirerPy.inquirer.select") as select, \
            patch("builtins.input", return_value=""):
        text.return_value = MagicMock(**{"execute.side_effect": list(texts)})
        select.return_value = MagicMock(**{"execute.side_effect": ["save"]})
        _run_efficiency_audit(trade_id, payload)
    return capsys.readouterr().out, enum_choice.call_args_list, requested, text.call_args_list, trade_id


@pytest.fixture
def wizard(in_memory_db, capsys):  # noqa: F811
    return lambda proposal, **kwargs: _drive(in_memory_db, proposal, capsys, **kwargs)


@pytest.mark.parametrize("reason, line", [
    ("open", "Still open according to candles"),
    (REASON_PENDING_CANDLES, "Candles: no proposal (pending_candles)"),
    (REASON_CLOCK_UNVERIFIED, "Candles: no proposal (clock_unverified)"),
])
def test_without_a_proposal_the_reason_is_one_line_and_the_prompts_have_no_default(wizard, reason, line):
    out, enum_calls, requested, _, trade_id = wizard(AutoProposal(trade_id="x", symbol="XAUUSD", anchor=ANCHOR,
                                                                   reason=reason))
    assert line in out
    assert requested == [trade_id]  # se pidió una sola vez
    assert all("default" not in call.kwargs for call in enum_calls)


def test_a_failing_proposal_never_blocks_the_audit(wizard, in_memory_db):  # noqa: F811
    out, _, _, _, trade_id = wizard(RuntimeError("bank unreadable"))
    assert "Candles: no proposal (unavailable)" in out
    assert _get_saved_efficiency_row(in_memory_db, trade_id).resolution_type == ResolutionType.CONFIRMED.value


def test_propose_for_trade_resolves_one_analysis_of_a_database_file(tmp_path):
    from tests.test_auto_resolution import A, TOUCH, _write_bank, _write_db
    (tmp_path / "bank").mkdir()
    _write_bank(tmp_path / "bank")
    _write_db(tmp_path / "account.db", [A])
    proposal = auto_resolution.propose_for_trade(str(tmp_path / "account.db"), "a1", str(tmp_path / "bank"))
    assert proposal.resolution_type == ResolutionType.CONFIRMED.value and proposal.resolution_time == TOUCH


# --- T42: defaults (auto) en Resolution Type, Structural Resolution, Failure Reason y Structural MAE/MFE -------------

TOUCH = datetime.datetime(2026, 6, 1, 1, 0)
PROPOSAL = AutoProposal(
    trade_id="x", symbol="XAUUSD", anchor=ANCHOR, reason=None,
    resolution_type=ResolutionType.CONFIRMED.value,
    structural_resolution=StructuralResolution.CONFIRMED_EXPANSION.value,
    failure_reason=FailureReason.NA.value, resolution_time=TOUCH, structural_mae=4300.5, structural_mfe=4369.62)


def test_each_proposed_value_reaches_its_prompt_as_an_auto_default(wizard):
    out, enum_calls, _, text_calls, _ = wizard(PROPOSAL, texts=("4300.5", "4369.62", ""))
    by_prompt = {call.args[0]: call.kwargs for call in enum_calls}
    assert "default" not in by_prompt["Real Bias B"]  # el sesgo real lo juzga el operador
    assert by_prompt["Resolution Type"]["default"] == ResolutionType.CONFIRMED.value
    assert by_prompt["Resolution Type"]["exclude"] == [ResolutionType.OPEN]
    assert by_prompt["Structural Resolution"]["default"] == StructuralResolution.CONFIRMED_EXPANSION.value
    assert by_prompt["Failure Reason"]["default"] == FailureReason.NA.value
    assert [call.kwargs["default"] for call in text_calls[:2]] == ["4300.5", "4369.62"]
    assert "(auto: 4300.5)" in text_calls[0].kwargs["message"] and "(auto: 4369.62)" in text_calls[1].kwargs["message"]
    assert "Candles: no proposal" not in out


def test_accepting_the_defaults_saves_the_proposed_values(in_memory_db, capsys):  # noqa: F811
    # Aceptar con Enter = cada prompt devuelve su default (InquirerPy está reemplazado).
    answers = [StructuralBias.BOS, ResolutionType.CONFIRMED, StructuralResolution.CONFIRMED_EXPANSION, FailureReason.NA]
    *_, trade_id = _drive(in_memory_db, PROPOSAL, capsys, answers=answers, texts=("4300.5", "4369.62", ""))
    row = _get_saved_efficiency_row(in_memory_db, trade_id)
    assert (row.resolution_type, row.structural_resolution, row.failure_reason) == (
        ResolutionType.CONFIRMED.value, StructuralResolution.CONFIRMED_EXPANSION.value, FailureReason.NA.value)
    assert (float(row.structural_mae), float(row.structural_mfe)) == (4300.5, 4369.62)


def test_correcting_a_default_saves_the_operator_value(in_memory_db, capsys):  # noqa: F811
    answers = [StructuralBias.CHOCH, ResolutionType.INVALIDATED, StructuralResolution.NA,
               FailureReason.LIQUIDITY_SWEEP]
    *_, trade_id = _drive(in_memory_db, PROPOSAL, capsys, answers=answers, texts=("4290", "", ""))
    row = _get_saved_efficiency_row(in_memory_db, trade_id)
    assert (row.resolution_type, row.failure_reason) == (ResolutionType.INVALIDATED.value,
                                                         FailureReason.LIQUIDITY_SWEEP.value)
    assert float(row.structural_mae) == 4290.0 and row.structural_mfe is None  # MFE vaciado a mano


def test_values_the_candles_do_not_propose_have_no_default(wizard):
    partial = AutoProposal(trade_id="x", symbol="XAUUSD", anchor=ANCHOR, reason=None,
                           resolution_type=ResolutionType.INVALIDATED.value,
                           structural_resolution=StructuralResolution.NA.value, failure_reason=None,
                           resolution_time=TOUCH, structural_mae=None, structural_mfe=4369.62)
    _, enum_calls, _, text_calls, _ = wizard(partial)
    by_prompt = {call.args[0]: call.kwargs for call in enum_calls}
    assert "default" not in by_prompt["Failure Reason"]  # RF-8b: sin Liquidity Sweep no se propone
    assert "default" not in text_calls[0].kwargs and text_calls[1].kwargs["default"] == "4369.62"



# --- T43: Resolution Time al final, audit_registration_time y resolution_time_source --------------------------------

SAVED = datetime.datetime(2026, 10, 3, 9, 30)


@pytest.fixture
def saved_clock(monkeypatch):
    import cli.main as cli_main
    monkeypatch.setattr(cli_main, "_now_gt", lambda: SAVED)


@pytest.mark.parametrize("typed, time, source", [
    ("2026-06-01 01:00", TOUCH, "candles"),                                     # Enter: acepta la propuesta
    ("2026-06-01 02:30", datetime.datetime(2026, 6, 1, 2, 30), "corrected"),    # la corrige
    ("", None, None),                                                           # la vacía: queda vacía
])
def test_resolution_time_with_a_proposal(in_memory_db, capsys, saved_clock, typed, time, source):  # noqa: F811
    out, _, _, text_calls, trade_id = _drive(in_memory_db, PROPOSAL, capsys, texts=("", "", typed))
    prompt = text_calls[2].kwargs
    assert prompt["default"] == "2026-06-01 01:00" and "(auto: 2026-06-01 01:00" in prompt["message"]
    row = _get_saved_efficiency_row(in_memory_db, trade_id)
    assert (row.resolution_time, row.resolution_time_source, row.audit_registration_time) == (time, source, SAVED)


@pytest.mark.parametrize("typed, time, source", [
    ("", None, "pending_candles"),                                              # vacía, por ese motivo
    ("2026-06-01 02:30", datetime.datetime(2026, 6, 1, 2, 30), "corrected"),    # la puso el operador
])
def test_resolution_time_without_a_proposal(in_memory_db, capsys, saved_clock, typed, time, source):  # noqa: F811
    pending = AutoProposal(trade_id="x", symbol="XAUUSD", anchor=ANCHOR, reason=REASON_PENDING_CANDLES)
    _, _, _, text_calls, trade_id = _drive(in_memory_db, pending, capsys, texts=("", "", typed))
    assert "default" not in text_calls[2].kwargs and "[Optional]" in text_calls[2].kwargs["message"]
    row = _get_saved_efficiency_row(in_memory_db, trade_id)
    assert (row.resolution_time, row.resolution_time_source, row.audit_registration_time) == (time, source, SAVED)


def test_an_unavailable_proposal_leaves_the_source_empty(in_memory_db, capsys, saved_clock):  # noqa: F811
    *_, trade_id = _drive(in_memory_db, RuntimeError("no bank"), capsys)
    row = _get_saved_efficiency_row(in_memory_db, trade_id)
    assert (row.resolution_time, row.resolution_time_source, row.audit_registration_time) == (None, None, SAVED)


def test_the_precision_comes_from_the_timeframe_of_the_touch(wizard):
    from core.candle_resolution import AnalysisResolution, FirstTouch
    touched = dataclasses.replace(PROPOSAL, resolution=AnalysisResolution(
        "confirmed", first_touch=FirstTouch("confirmed", touch_time=TOUCH, timeframe="15M")))
    _, _, _, text_calls, _ = wizard(touched)
    assert "(auto: 2026-06-01 01:00, ±15 min)" in text_calls[2].kwargs["message"]


@pytest.mark.parametrize("value, expected", [
    (TOUCH, TOUCH), ("2026-06-01 01:00", TOUCH), ("", None), (None, None),  # al retomar una pausa llega como texto
])
def test_a_resumed_resolution_time_is_read_back_as_a_datetime(value, expected):
    from cli.main import resolution_time_entry
    assert resolution_time_entry(value) == expected
