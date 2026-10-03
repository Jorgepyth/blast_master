"""
T55 (spec 002): `tools/p2_model_feedback.py`, el registro prospectivo del P2 sistemático (RF-12, RF-12c, RF-12e, N38,
N40, N42; plan.md §2.5). Banco sintético en tmp_path con una tendencia alcista limpia en todas las TF (EMAs alineadas,
ADX 100, +DI arriba): el P2 de cualquier modelo da 1.0 y reescalado 2. DB de cuenta en tmp_path, que no se modifica.
"""
import dataclasses
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine

import config.auto_resolution as auto_cfg
import tools.p2_backtest as p2_backtest
from tests.test_wizard_safety_net import _seed_unified_for_efficiency
from tools.candle_bank import build_status_payload, write_bank_status
from tools.database import Base
from tools.p2_model_feedback import latest_lines, log_account, model_hash, model_recipe, read_log

N1_START = datetime(2026, 10, 1, 10, 0)
N2_START = datetime(2026, 10, 2, 9, 0)
H_TEST = p2_backtest.ModelSpec(name="H_TEST", label="H_TEST", timeframes=("1D", "4H", "1H"),
                               weights={"1D": 0.2, "4H": 0.3, "1H": 0.5}, ema_chain=(20, 200), use_di=False,
                               description="Solo para tests.")
D_ONLY = {"D": "2026-09-27"}


def _write_tf(bank_dir, tf, last_close, n=1000):
    """`n` velas de `tf` en tendencia alcista limpia; la última cierra en `last_close`."""
    dur = timedelta(minutes=p2_backtest.TIMEFRAME_MINUTES[tf])
    times = [last_close - dur * (n - i) for i in range(n)]
    close = pd.Series([1000.0 + 0.5 * i for i in range(n)])
    pd.DataFrame({"time": times, "open": close - 0.2, "high": close + 0.3, "low": close - 0.3, "close": close}).to_csv(
        bank_dir / f"{tf}.csv", index=False)


def _write_bank(root, symbol="XAUUSD", last_close=datetime(2026, 10, 3, 12, 0), clock="verified", sizes=None):
    bank_dir = root / symbol
    bank_dir.mkdir(parents=True, exist_ok=True)
    for tf in ("1W", "1D", "12H", "4H", "1H", "30M"):
        _write_tf(bank_dir, tf, last_close, (sizes or {}).get(tf, 1000))
    write_bank_status(str(bank_dir), build_status_payload(symbol, clock, "overlap", "unverified", "r1", "merged", {}))
    return bank_dir


def _write_db(path, analyses):
    """Una DB de cuenta con el esquema completo. `analyses`: (id, asset, analysis_start_time, is_backdated)."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    for trade_id, asset, start, backdated in analyses:
        _seed_unified_for_efficiency(engine, trade_id=trade_id, asset=asset)
    engine.dispose()
    with sqlite3.connect(path) as conn:
        for trade_id, asset, start, backdated in analyses:
            conn.execute("UPDATE unified_department SET analysis_start_time = ?, is_backdated = ? WHERE id = ?",
                         (start.isoformat(sep=" ") if start else None, int(backdated), trade_id))
            conn.execute("INSERT INTO analysis_layer (id, trade_id, department, layer_name, direction, strength, score) "
                         "VALUES (?, ?, 'EFFICIENCY', 'P2', 'Long', 'Strong', 2)", (f"l-{trade_id}", trade_id))
    return path


@pytest.fixture
def setup(tmp_path):
    bank_root = tmp_path / "bank"
    db = _write_db(tmp_path / "xau.db", [
        ("n1", "XAUUSDT.P", N1_START, False),
        ("n2", "XAUUSDT.P", N2_START, False),
        ("old", "XAUUSDT.P", None, False),                       # histórico, sin analysis_start_time (N40)
        ("bd", "XAUUSDT.P", datetime(2026, 10, 1, 8, 0), True),  # retroactivo o clon [2]
        ("pre", "XAUUSDT.P", datetime(2026, 9, 20, 9, 0), False),  # antes de la fecha de alta de D
    ])
    return db, bank_root, tmp_path / "p2_model_log.jsonl"


def _log(setup, **kwargs):
    db, bank_root, log = setup
    kwargs.setdefault("models", D_ONLY)
    return log_account(str(db), "000", str(bank_root), str(log), now=datetime(2026, 10, 3, 13, 0), **kwargs)


def _lines(log):
    return [json.loads(raw) for raw in Path(log).read_text().splitlines()]


def test_a_new_analysis_gets_one_line_with_the_operator_p2_the_recipe_and_the_detail(setup):
    _write_bank(setup[1])
    result = _log(setup, trade_ids=["n1"])
    assert result.warnings == []
    (line,) = _lines(setup[2])
    assert line["trade_id"] == "n1" and line["account"] == "000"
    assert (line["asset"], line["symbol"]) == ("XAUUSDT.P", "XAUUSD")
    assert (line["anchor"], line["logged_at"]) == ("2026-10-01T10:00:00", "2026-10-03T13:00:00")
    assert line["operator_p2"] == {"direction": "Long", "strength": "Strong", "score": 2}
    assert (line["model"], line["model_since"], line["model_hash"]) == ("D", "2026-09-27", "20315fe7bfe2")
    assert line["model_spec"] == {"timeframes": ["1W", "1D", "12H", "4H", "1H", "30M"],
                                  "weights": {"1W": 0.08, "1D": 0.12, "12H": 0.17, "4H": 0.22, "1H": 0.26,
                                              "30M": 0.15},
                                  "ema_chain": [20, 100, 200], "use_di": True}
    assert (line["status"], line["p2_rescaled"]) == ("ok", 2) and line["p2_raw"] == pytest.approx(1.0)
    assert list(line["by_tf"]) == ["1W", "1D", "12H", "4H", "1H", "30M"]
    one_w = line["by_tf"]["1W"]
    assert (one_w["bias"], one_w["weight"]) == (1, 0.08) and one_w["adx"] > 25
    assert one_w["price"] > one_w["ema20"] > one_w["ema100"] > one_w["ema200"]
    assert one_w["plus_di"] > one_w["minus_di"]
    assert result.written == [line]


def test_only_closed_candles_at_the_anchor_count(setup):
    bank_dir = _write_bank(setup[1])
    _log(setup, trade_ids=["n1"])
    thirty = pd.read_csv(bank_dir / "30M.csv", parse_dates=["time"])
    last_closed = thirty[thirty["time"] + pd.Timedelta(minutes=30) <= N1_START].iloc[-1]
    assert last_closed["time"] == pd.Timestamp(N1_START - timedelta(minutes=30))
    assert _lines(setup[2])[0]["by_tf"]["30M"]["price"] == last_closed["close"]


def test_the_hash_of_d_is_the_documented_one():
    assert model_hash(p2_backtest.MODEL_D) == "20315fe7bfe2"
    assert model_recipe(dataclasses.replace(p2_backtest.MODEL_D, label="x", description="y")) == model_recipe(
        p2_backtest.MODEL_D)


def test_no_second_line_for_the_same_analysis_and_model(setup):
    _write_bank(setup[1])
    _log(setup)
    first = Path(setup[2]).read_bytes()
    assert _log(setup).written == []
    assert Path(setup[2]).read_bytes() == first
    assert [line["trade_id"] for line in _lines(setup[2])] == ["n1", "n2"]


def test_backdated_clones_historical_and_earlier_analyses_are_never_logged_and_no_db_changes(setup):
    _write_bank(setup[1])
    before = Path(setup[0]).read_bytes()
    _log(setup)
    assert {line["trade_id"] for line in _lines(setup[2])} == {"n1", "n2"}
    assert Path(setup[0]).read_bytes() == before


def test_a_pending_line_is_replaced_once_the_bank_covers_the_anchor(setup):
    _write_bank(setup[1], last_close=datetime(2026, 9, 30, 12, 0))   # el banco no llega al ancla
    _log(setup, trade_ids=["n1"])
    assert [(line["status"], line["p2_raw"]) for line in _lines(setup[2])] == [("pending_candles", None)]
    assert _log(setup, trade_ids=["n1"]).written == []                # sigue pendiente: no se repite
    _write_bank(setup[1])                                            # llegó el export
    _log(setup, trade_ids=["n1"])
    lines = _lines(setup[2])
    assert len(lines) == 2 and lines[1]["status"] == "ok" and lines[1]["supersedes"] == 1
    assert latest_lines(read_log(str(setup[2])))[("n1", "D")].data["status"] == "ok"
    assert _log(setup, trade_ids=["n1"]).written == []


def test_an_unverified_clock_is_retried_too(setup):
    _write_bank(setup[1], clock="clock_unverified")
    _log(setup, trade_ids=["n1"])
    assert _lines(setup[2])[0]["status"] == "clock_unverified"
    _write_bank(setup[1])
    _log(setup, trade_ids=["n1"])
    assert [(line["status"], line.get("supersedes")) for line in _lines(setup[2])] == [
        ("clock_unverified", None), ("ok", 1)]


def test_not_enough_history_is_a_final_reason(setup):
    _write_bank(setup[1], sizes={"1W": 700})
    _log(setup, trade_ids=["n1"])
    assert [(line["status"], line["p2_raw"], line["by_tf"]) for line in _lines(setup[2])] == [
        ("insufficient_history:1W", None, {})]
    _write_bank(setup[1])
    assert _log(setup, trade_ids=["n1"]).written == []  # no se reintenta


@pytest.mark.parametrize("weeks, status", [(800, "ok"), (799, "insufficient_history:1W")])
def test_a_candle_closing_at_the_anchor_covers_it_and_800_closed_candles_are_enough(setup, weeks, status):
    _write_bank(setup[1], last_close=N1_START, sizes={"1W": weeks})  # todas cierran en el ancla o antes
    _log(setup, trade_ids=["n1"])
    assert _lines(setup[2])[0]["status"] == status


def test_an_asset_without_mt5_symbol_gets_its_reason(tmp_path):
    db = _write_db(tmp_path / "x.db", [("n1", "XAU/USD", N1_START, False)])
    log_account(str(db), "000", str(tmp_path / "bank"), str(tmp_path / "log.jsonl"), models=D_ONLY)
    assert [(line["symbol"], line["status"]) for line in _lines(tmp_path / "log.jsonl")] == [(None, "no_mt5_symbol")]


def test_two_models_each_get_their_line_and_h_only_from_its_date(setup, monkeypatch):
    monkeypatch.setattr(p2_backtest, "MODELS_BY_NAME", {**p2_backtest.MODELS_BY_NAME, "H_TEST": H_TEST})
    _write_bank(setup[1])
    _log(setup, models={"D": "2026-09-27", "H_TEST": "2026-10-02"})
    pairs = [(line["trade_id"], line["model"]) for line in _lines(setup[2])]
    assert pairs == [("n1", "D"), ("n2", "D"), ("n2", "H_TEST")]  # n1 empezó el 1 de octubre, antes del alta de H
    h_line = _lines(setup[2])[2]
    assert h_line["model_spec"]["timeframes"] == ["1D", "4H", "1H"] and h_line["model_hash"] == model_hash(H_TEST)
    assert list(h_line["by_tf"]) == ["1D", "4H", "1H"] and h_line["p2_rescaled"] == 2


def test_an_unknown_model_or_a_timeframe_the_bank_lacks_is_skipped_with_a_warning(setup, monkeypatch):
    two_h = dataclasses.replace(H_TEST, name="TWO_H", timeframes=("1D", "2H"), weights={"1D": 0.5, "2H": 0.5})
    monkeypatch.setattr(p2_backtest, "MODELS_BY_NAME", {**p2_backtest.MODELS_BY_NAME, "TWO_H": two_h})
    _write_bank(setup[1])
    result = _log(setup, models={"NOPE": "2026-09-27", "TWO_H": "2026-09-27", "D": "2026-09-27"})
    assert result.warnings == ["unknown_model:NOPE", "timeframe_not_in_bank:2H"]
    assert {line["model"] for line in _lines(setup[2])} == {"D"}


def test_a_changed_recipe_stops_that_model_with_a_warning(setup, monkeypatch):
    _write_bank(setup[1])
    _log(setup, trade_ids=["n1"])
    changed = dataclasses.replace(p2_backtest.MODEL_D, weights={**p2_backtest.MODEL_D.weights, "1W": 0.09,
                                                                "30M": 0.14})
    monkeypatch.setattr(p2_backtest, "MODELS_BY_NAME", {**p2_backtest.MODELS_BY_NAME, "D": changed})
    result = _log(setup)
    assert result.warnings == ["model_recipe_changed:D"] and result.written == []
    assert len(_lines(setup[2])) == 1


def test_the_symbol_filter_logs_only_that_symbol(setup, tmp_path):
    db = _write_db(tmp_path / "mixed.db", [("x1", "XAUUSDT.P", N1_START, False), ("b1", "BTCUSDT.P", N1_START, False)])
    _write_bank(setup[1])
    log_account(str(db), "000", str(setup[1]), str(setup[2]), symbol="XAUUSD", models=D_ONLY)
    assert [line["trade_id"] for line in _lines(setup[2])] == ["x1"]


def test_a_cut_last_line_does_not_glue_to_the_next_one(setup):
    _write_bank(setup[1])
    Path(setup[2]).write_text('{"trade_id": "zz", "model": "D", "model_hash": "20315fe7bfe2", "status": "ok"}\n'
                              '{"trade_id": "cut"')
    _log(setup, trade_ids=["n1"])
    raw = Path(setup[2]).read_text().splitlines()
    assert raw[1] == '{"trade_id": "cut"' and json.loads(raw[2])["trade_id"] == "n1"


def test_without_new_analyses_the_log_is_not_created(tmp_path):
    db = _write_db(tmp_path / "x.db", [("old", "XAUUSDT.P", None, False)])
    result = log_account(str(db), "000", str(tmp_path / "bank"), str(tmp_path / "log.jsonl"),
                         models={"NOPE": "2026-09-27"})
    assert result.warnings == ["unknown_model:NOPE"] and not (tmp_path / "log.jsonl").exists()


def test_a_database_without_the_new_column_has_no_new_analyses(tmp_path):
    with sqlite3.connect(tmp_path / "old.db") as conn:
        conn.execute("CREATE TABLE unified_department (id TEXT, asset TEXT, created_at TEXT, is_backdated INTEGER)")
        conn.execute("INSERT INTO unified_department VALUES ('a', 'XAUUSDT.P', '2026-10-01 10:00:00', 0)")
    assert log_account(str(tmp_path / "old.db"), "000", str(tmp_path), str(tmp_path / "log.jsonl"),
                       models=D_ONLY).written == []


def test_the_default_paths_come_from_the_config(setup, monkeypatch):
    db, bank_root, log = setup
    _write_bank(bank_root)
    monkeypatch.setattr(auto_cfg, "ACCOUNTS_DATA_DIR", str(log.parent))
    monkeypatch.setattr(auto_cfg, "CANDLE_BANK_DIR", str(bank_root))
    log_account(str(db), "000", trade_ids=["n1"])
    assert [line["model"] for line in _lines(log)] == list(auto_cfg.P2_LOG_MODELS)
