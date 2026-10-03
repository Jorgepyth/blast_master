"""
Propuestas de las velas en el Efficiency Audit (spec 002):
- T41: al abrir el wizard se pide la propuesta una sola vez y, si no la hay, se muestra el motivo en una línea
  ("Still open according to candles", `pending_candles`, `clock_unverified`...). Nunca bloquea (RF-7f, RF-5b).

El wizard se maneja como en tests/test_wizard_safety_net.py (T5). La propuesta se reemplaza en el borde del servicio
(`tools.auto_resolution.propose_for_trade`), así que el test no necesita banco ni DB de archivo.
"""
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


def _drive(engine, proposal_or_error, capsys, answers=None, texts=("", "")):
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
