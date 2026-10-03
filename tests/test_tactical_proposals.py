"""
T45, T46 (spec 002): las propuestas de las velas en el Tactical Audit, rama "orden llenada". MAE y MFE en R y
`Could hit TP?` llegan como default `(auto)`; sin propuesta, una línea con el motivo y los prompts de siempre. `session`
sigue saliendo de `entry_time`, como antes (RF-10c). Mismo manejo del wizard que la red de seguridad de T6.
"""
import datetime
from unittest.mock import MagicMock, patch

import pytest

import tools.auto_resolution as auto_resolution
from cli.schemas.audit_tactical import (
    ConfirmationStatus, ExitType, FollowedPlan, HTFTrendContext, MarketState, PrimaryEmotion, SetupType, TrendContext,
)
from core.candle_resolution import REASON_INVALID_TP, REASON_NO_INTERVAL, TpCheck, TradeExcursion
from tests.test_wizard_safety_net import (  # noqa: F401 -- fixtures
    _get_saved_tactical_row,
    _run_tactical_audit,
    _seed_unified_for_tactical,
    in_memory_db,
    isolated_cache_file,
)
from tools.auto_resolution import TacticalProposal

PROPOSAL = TacticalProposal("XAUUSD", None, TradeExcursion(0.6, 2.4, "1M"), TpCheck("yes", "1M"))


def _drive(engine, proposal, capsys, floats=(1900.0, 1950.0, 1.0, 2050.0, 5.0, 1980.0, 2.5, 6.0), could="yes"):
    """La rama llenada del Tactical, de punta a punta. `floats`: sl, entry, size, tp, cost, close, MAE, MFE."""
    requested = []

    def fake_propose(*args, **kwargs):
        requested.append(args)
        if isinstance(proposal, Exception):
            raise proposal
        return proposal

    trade_id, payload = _seed_unified_for_tactical(engine, asset="XAUUSDT.P")
    enum_answers = iter([HTFTrendContext.BULLISH, TrendContext.BULLISH, PrimaryEmotion.EQUANIMITY, MarketState.TREND,
                         SetupType.TREND_PULLBACK, ExitType.MANUAL, FollowedPlan.YES])
    text_answers = iter(["calm", "managed", "relieved", "lesson"])
    with patch.object(auto_resolution, "propose_tactical", side_effect=fake_propose), \
            patch("cli.main.render_pnl_box", return_value="accept"), \
            patch("cli.main.get_mandatory_time", return_value=datetime.time(10, 30)), \
            patch("cli.main.get_mandatory_datetime", return_value=datetime.datetime(2026, 1, 5, 12, 15)), \
            patch("cli.main.get_mandatory_int", return_value=3), \
            patch("cli.main.get_multi_enum_choice", return_value=[]), \
            patch("cli.main.get_enum_choice", side_effect=lambda *a, **k: next(enum_answers)), \
            patch("cli.main.handle_visual_lesson_assignment", return_value="nan"), \
            patch("cli.main.get_optional_text"), \
            patch("cli.main.get_mandatory_text", side_effect=lambda *a, **k: next(text_answers)), \
            patch("cli.main.get_mandatory_float", side_effect=list(floats)) as get_float, \
            patch("InquirerPy.inquirer.checkbox") as checkbox, patch("InquirerPy.inquirer.select") as select, \
            patch("builtins.input", return_value=""):
        # Orden real: el modo, el Confirmation Status, "Order Filled?" y después "Could hit TP?".
        select.return_value = MagicMock(**{"execute.side_effect": ["gates", ConfirmationStatus.S1_CLEAN, "yes", could,
                                                                    "save"]})
        checkbox.return_value = MagicMock(**{"execute.side_effect": [["g1", "g2", "g3", "g4", "g5", "g6", "g7"],
                                                                      ["c1", "c2"]]})
        _run_tactical_audit(trade_id, payload)
    could_call = next(c for c in select.call_args_list if c.kwargs["message"].startswith("Could hit TP?"))
    mae_call, mfe_call = [c for c in get_float.call_args_list if c.args and c.args[0].startswith(("MAE", "MFE"))]
    return capsys.readouterr().out, requested, could_call, mae_call, mfe_call, _get_saved_tactical_row(engine, trade_id)


def test_the_proposal_reaches_could_hit_tp_mae_and_mfe_as_auto_defaults(in_memory_db, capsys):  # noqa: F811
    out, requested, could_call, mae_call, mfe_call, row = _drive(in_memory_db, PROPOSAL, capsys)
    assert [args[:6] for args in requested] == [("XAUUSDT.P", datetime.datetime(2026, 1, 5, 10, 30),
                                                 datetime.datetime(2026, 1, 5, 12, 15), 1950.0, 1900.0, 2050.0)]
    assert could_call.kwargs["default"] == "yes" and could_call.kwargs["message"] == "Could hit TP? (auto: yes) >"
    assert (mae_call.kwargs["default"], mfe_call.kwargs["default"]) == (0.6, 2.4)
    assert "Candles: no proposal" not in out
    assert row.session == "New York"  # RF-10c: sale de entry_time, como siempre


def test_accepted_or_corrected_values_are_what_gets_saved(in_memory_db, capsys):  # noqa: F811
    *_, row = _drive(in_memory_db, PROPOSAL, capsys, floats=(1900.0, 1950.0, 1.0, 2050.0, 5.0, 1980.0, 0.6, 3.1),
                     could="no")
    assert (float(row.mae_adverse), float(row.mfe_favorable), row.could_hit_tp) == (0.6, 3.1, "no")


def test_without_a_proposal_each_field_says_why_and_has_no_default(in_memory_db, capsys):  # noqa: F811
    missing = TacticalProposal("XAUUSD", None, TradeExcursion(None, None, None, REASON_NO_INTERVAL),
                               TpCheck(None, None, REASON_INVALID_TP))
    out, _, could_call, mae_call, mfe_call, _ = _drive(in_memory_db, missing, capsys)
    assert "Candles: no proposal for Could hit TP? (invalid_tp)" in out
    assert "Candles: no proposal for MAE/MFE (no_interval)" in out
    assert "default" not in could_call.kwargs and could_call.kwargs["message"] == "Could hit TP? >"
    assert "default" not in mae_call.kwargs and "default" not in mfe_call.kwargs


@pytest.mark.parametrize("proposal, reason", [(TacticalProposal(None, "no_mt5_symbol"), "no_mt5_symbol"),
                                              (RuntimeError("bank unreadable"), "unavailable")])
def test_a_symbol_without_candles_or_a_failing_service_never_blocks(in_memory_db, capsys, proposal,  # noqa: F811
                                                                    reason):
    out, *_, row = _drive(in_memory_db, proposal, capsys)
    assert f"Candles: no proposal for Could hit TP? ({reason})" in out
    assert f"Candles: no proposal for MAE/MFE ({reason})" in out
    assert row.could_hit_tp == "yes" and float(row.mae_adverse) == 2.5


def test_propose_tactical_reads_the_bank_of_the_symbol(tmp_path):
    from tests.test_auto_resolution import T0, TOUCH, _write_bank
    _write_bank(tmp_path)  # 1M plano 99-101 hasta la 01:00, que toca 110.5; después, 104.5-105.5
    entry = T0 + datetime.timedelta(minutes=10)
    proposal = auto_resolution.propose_tactical("XAUUSDT.P", entry, T0 + datetime.timedelta(hours=2), 100.0, 95.0,
                                                110.0, str(tmp_path))
    assert proposal.excursion == TradeExcursion(0.2, 2.1, "1M") and proposal.tp == TpCheck("yes", "1M")
    short = auto_resolution.propose_tactical("XAUUSDT.P", entry, T0 + datetime.timedelta(hours=2), 100.0, 105.0, 90.0,
                                             str(tmp_path))  # entrada bajo el SL: Short, como en el schema
    assert short.excursion == TradeExcursion(2.1, 0.2, "1M") and short.tp == TpCheck("no", "1M")
    assert auto_resolution.propose_tactical("ETHUSDT.P", entry, None, 100.0, 95.0, 110.0, str(tmp_path)).reason == (
        "no_mt5_symbol")


def test_a_symbol_whose_clock_is_not_verified_gets_no_tactical_proposal(tmp_path):
    from tests.test_auto_resolution import T0, _write_bank
    _write_bank(tmp_path, clock="clock_unverified")
    proposal = auto_resolution.propose_tactical("XAUUSDT.P", T0 + datetime.timedelta(minutes=10),
                                                T0 + datetime.timedelta(hours=2), 100.0, 95.0, 110.0, str(tmp_path))
    assert (proposal.reason, proposal.excursion, proposal.tp) == ("clock_unverified", None, None)
