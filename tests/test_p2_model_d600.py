"""
D600 (2026-10-09): el modelo D de P2 con 600 velas semanales mínimas, para los índices (US500 tiene 739 semanas y D pide
800). Banco sintético y DB de cuenta en tmp_path, con los ayudantes de tests/test_p2_model_feedback.py.
"""
from datetime import datetime

import config.auto_resolution as auto_cfg
import tools.p2_backtest as p2_backtest
from tests.test_p2_model_feedback import N1_START, _lines, _write_bank, _write_db
from tools.p2_model_feedback import log_account, model_hash, model_recipe

D, D600 = p2_backtest.MODEL_D, p2_backtest.MODELS_BY_NAME["D600"]
BOTH = {"D": "2026-09-27", "D600": "2026-09-27"}


def _log(tmp_path, weeks):
    db = _write_db(tmp_path / "us500.db", [("n1", "XAUUSDT.P", N1_START, False)])
    _write_bank(tmp_path / "bank", sizes={"1W": weeks})
    log = tmp_path / "p2_model_log.jsonl"
    log_account(str(db), "001", str(tmp_path / "bank"), str(log), models=BOTH, now=datetime(2026, 10, 3, 13, 0))
    return {line["model"]: line for line in _lines(log)}


def test_d600_is_d_with_a_lower_weekly_minimum_only():
    assert (D600.timeframes, D600.weights, D600.ema_chain, D600.use_di) == (D.timeframes, D.weights, D.ema_chain,
                                                                            D.use_di)
    assert D600.min_bars_for("1W") == 600 and D600.min_bars_for("1H") == 800 and D.min_bars_for("1W") == 800
    assert D600 not in p2_backtest.MODELS            # no entra a las evaluaciones históricas
    assert auto_cfg.P2_LOG_MODELS["D600"] == "2026-10-09" and auto_cfg.P2_LOG_MODELS["D"] == "2026-09-27"


def test_with_739_weeks_d_cannot_score_and_d600_can(tmp_path):
    lines = _log(tmp_path, 739)
    assert lines["D"]["status"] == "insufficient_history:1W"
    assert lines["D600"]["status"] == "ok" and lines["D600"]["p2_rescaled"] is not None


def test_with_800_weeks_d600_gives_exactly_what_d_gives(tmp_path):
    lines = _log(tmp_path, 1000)
    assert lines["D"]["status"] == lines["D600"]["status"] == "ok"
    assert (lines["D"]["p2_raw"], lines["D"]["p2_rescaled"]) == (lines["D600"]["p2_raw"], lines["D600"]["p2_rescaled"])


def test_fewer_than_600_weeks_is_still_insufficient(tmp_path):
    assert _log(tmp_path, 599)["D600"]["status"] == "insufficient_history:1W"


def test_the_recipe_hash_of_d_does_not_change_and_d600_has_its_own():
    assert "min_bars" not in model_recipe(D)
    assert model_recipe(D600)["min_bars"] == {"1W": 600}
    assert model_hash(D) == "20315fe7bfe2"       # el de la spec 002 (T55)
    assert model_hash(D600) not in (model_hash(D), "")
