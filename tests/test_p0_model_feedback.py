"""
Registro prospectivo del P0 sistemático, opción 2 (tools/p0_model_feedback.py, docs/p0-reglas-opcion2.md §9): una línea
por análisis nuevo y modelo, solo con velas cerradas en el ancla, desde el catch-up después de cada export fusionado.
Banco sintético y DBs de cuenta en tmp_path.
"""
import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine

import tools.p0_model_feedback as p0_feedback
from tests.test_candle_sync import Env
from tests.test_wizard_safety_net import _seed_unified_for_efficiency
from tools import p0_opcion2 as o2
from tools import p0_sistematico as ps
from tools.candle_bank import build_status_payload, write_bank_status
from tools.database import Base

ANCHOR = datetime(2026, 10, 12, 10, 0)
MODELS = {"O2-v1.0": "2026-10-09"}
# Desde cuántos días antes del final se escribe cada TF (el banco real guarda menos 1M que 1D).
SPAN_DAYS = {"1W": 900, "1D": 400, "12H": 400, "4H": 120, "1H": 120, "30M": 20, "15M": 20, "5M": 10, "1M": 3}


def _price(t):
    """El mismo precio en función del tiempo para todas las TF: tendencia con ondas, así hay pivotes y quiebres."""
    h = (t - datetime(2025, 1, 1)).total_seconds() / 3600
    return 1000 + 0.02 * h + 30 * math.sin(h / 40) + 8 * math.sin(h / 7)


def _write_bank(root, end, symbol="XAUUSD", clock="verified"):
    """Velas de todas las TF hasta la que cierra en `end` (o antes). Dos bancos con distinto `end` comparten el pasado."""
    bank_dir = root / symbol
    bank_dir.mkdir(parents=True, exist_ok=True)
    for tf in ps.ALL_TFS:
        dur = timedelta(minutes=ps.TF_MINUTES[tf])
        start = datetime(2025, 1, 6) if tf == "1W" else ANCHOR - timedelta(days=SPAN_DAYS[tf])
        steps = int((end - start) / dur)
        times = [start + dur * i for i in range(steps)]
        rows = []
        for t in times:
            o, c = _price(t), _price(t + dur)
            rows.append((t, o, max(o, c) + 0.3, min(o, c) - 0.3, c))
        pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"]).to_csv(bank_dir / f"{tf}.csv",
                                                                                   index=False)
    write_bank_status(str(bank_dir), build_status_payload(symbol, clock, "overlap", "unverified", "r1", "merged", {}))
    return bank_dir


def _write_db(path, analyses):
    """`analyses`: (id, asset, analysis_start_time, is_backdated, p0 direction)."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    for trade_id, asset, *_ in analyses:
        _seed_unified_for_efficiency(engine, trade_id=trade_id, asset=asset)
    engine.dispose()
    with sqlite3.connect(path) as conn:
        for trade_id, asset, start, backdated, p0 in analyses:
            conn.execute("UPDATE unified_department SET analysis_start_time = ?, is_backdated = ? WHERE id = ?",
                         (start.isoformat(sep=" ") if start else None, int(backdated), trade_id))
            conn.execute("INSERT INTO analysis_layer (id, trade_id, department, layer_name, direction, strength, score) "
                         "VALUES (?, ?, 'EFFICIENCY', 'P0', ?, 'Mid', 1)", (f"l-{trade_id}", trade_id, p0))
    return path


XAU = [
    ("n1", "XAUUSDT.P", ANCHOR, False, "Long"),
    ("n2", "XAUUSDT.P", ANCHOR + timedelta(hours=5), False, "Short"),
    ("old", "XAUUSDT.P", None, False, "Long"),                        # histórico, sin analysis_start_time
    ("bd", "XAUUSDT.P", ANCHOR - timedelta(hours=2), True, "Long"),    # retroactivo o clon
    ("pre", "XAUUSDT.P", datetime(2026, 10, 8, 9, 0), False, "Long"),  # antes de la fecha de alta
]


@pytest.fixture
def setup(tmp_path):
    db = _write_db(tmp_path / "xau.db", XAU)
    return db, tmp_path / "bank", tmp_path / "p0_model_log.jsonl"


def _log(setup, **kwargs):
    db, bank_root, log = setup
    kwargs.setdefault("models", MODELS)
    return p0_feedback.log_account(str(db), "000", str(bank_root), str(log), now=datetime(2026, 10, 13, 9, 0),
                                   **kwargs)


def _lines(log):
    return [json.loads(raw) for raw in Path(log).read_text().splitlines()]


def test_a_new_analysis_gets_one_line_with_the_operator_p0_the_recipe_and_the_detail(setup):
    _write_bank(setup[1], ANCHOR + timedelta(days=2))
    result = _log(setup, trade_ids=["n1"])
    assert len(result.written) == 1 and result.warnings == []
    line = _lines(setup[2])[0]
    assert (line["trade_id"], line["account"], line["symbol"], line["model"]) == ("n1", "000", "XAUUSD", "O2-v1.0")
    assert line["anchor"] == ANCHOR.isoformat() and line["operator_p0"] == {"direction": "Long", "strength": "Mid"}
    assert line["status"] == "ok" and line["direction"] in (-1, 0, 1)
    assert line["model_hash"] == p0_feedback.model_hash(o2.MODELS_BY_NAME["O2-v1.0"])
    assert line["model_spec"]["k"] == 2.0 and line["model_spec"]["expiry"] == 10
    assert set(line["by_tf"]) == {"1H", "4H", "12H", "1D"}


def test_the_frozen_recipe_hash_does_not_change():
    # Si este número cambia, cambió la receta del modelo congelado: eso es una versión nueva (§9), no un arreglo.
    assert p0_feedback.model_hash(o2.MODELS_BY_NAME["O2-v1.0"]) == "0265dd400d07"


def test_only_candles_closed_at_the_anchor_count(tmp_path):
    """Con un banco que llega 1 hora después del ancla y con otro que llega 20 días después, la línea es la misma."""
    lines = []
    for name, extra in (("short", timedelta(hours=1)), ("long", timedelta(days=20))):
        db = _write_db(tmp_path / f"{name}.db", XAU[:1])
        bank_root = tmp_path / f"bank_{name}"
        _write_bank(bank_root, ANCHOR + extra)
        log = tmp_path / f"{name}.jsonl"
        p0_feedback.log_account(str(db), "000", str(bank_root), str(log), models=MODELS,
                                now=datetime(2026, 10, 13, 9, 0))
        lines.append(_lines(log)[0])
    assert lines[0]["status"] == "ok"
    assert {k: v for k, v in lines[0].items() if k != "logged_at"} == \
        {k: v for k, v in lines[1].items() if k != "logged_at"}
    # Y es lo que da el modelo sobre esas mismas velas.
    candles = {tf: ps.read_candles(str(tmp_path / "bank_long" / "XAUUSD" / f"{tf}.csv")) for tf in ps.ALL_TFS}
    out = o2.Opcion2(ps.Market("XAUUSD", ps.candles_until(candles, ANCHOR)), o2.MODELS_BY_NAME["O2-v1.0"]) \
        .predict_at(ANCHOR)
    assert lines[0]["direction"] == out["direction"]
    assert {tf: d["score"] for tf, d in lines[0]["by_tf"].items()} == out["scores"]


def test_nothing_is_written_until_the_bank_covers_the_anchor(setup):
    _write_bank(setup[1], ANCHOR - timedelta(hours=3))
    assert _log(setup).written == []
    assert not Path(setup[2]).exists()
    _write_bank(setup[1], ANCHOR + timedelta(hours=6))
    assert [line["trade_id"] for line in _log(setup).written] == ["n1", "n2"]


def test_old_backdated_and_earlier_analyses_are_never_logged_and_there_is_no_second_line(setup):
    _write_bank(setup[1], ANCHOR + timedelta(days=1))
    assert {line["trade_id"] for line in _log(setup).written} == {"n1", "n2"}
    assert _log(setup).written == []
    assert len(_lines(setup[2])) == 2


def test_an_unverified_clock_is_retried_and_then_replaced(setup):
    _write_bank(setup[1], ANCHOR + timedelta(days=1), clock="clock_unverified")
    first = _log(setup, trade_ids=["n1"], write_pending=True).written[0]
    assert first["status"] == "clock_unverified"
    _write_bank(setup[1], ANCHOR + timedelta(days=1))
    second = _log(setup, trade_ids=["n1"]).written[0]
    assert second["status"] == "ok" and second["supersedes"] == 1


def test_a_changed_recipe_or_an_unknown_model_is_skipped_with_a_warning(setup, monkeypatch):
    _write_bank(setup[1], ANCHOR + timedelta(days=1))
    _log(setup, trade_ids=["n1"])
    changed = dict(o2.MODELS_BY_NAME)
    changed["O2-v1.0"] = o2.Opcion2Params(o2.TramoParams(k=3.0), expiry=10)
    monkeypatch.setattr(o2, "MODELS_BY_NAME", changed)
    result = _log(setup, models={"O2-v1.0": "2026-10-09", "NOPE": "2026-10-09"})
    assert result.written == []
    assert result.warnings == ["model_recipe_changed:O2-v1.0", "unknown_model:NOPE"]


def test_the_catch_up_logs_only_the_analyses_of_the_merged_symbol(tmp_path):
    data, bank_root = tmp_path / "accounts", tmp_path / "bank"
    data.mkdir()
    _write_db(data / "xau.db", XAU[:2])
    _write_db(data / "btc.db", [("b1", "BTCUSDT.P", ANCHOR, False, "Short")])
    _write_bank(bank_root, ANCHOR + timedelta(days=1))
    _write_bank(bank_root, ANCHOR + timedelta(days=1), symbol="BTCUSD")
    accounts = {"000": "xau.db", "002": "btc.db", "003": "missing.db"}
    result = p0_feedback.catch_up("XAUUSD", str(data), str(bank_root), accounts, models=MODELS)
    assert sorted(line["trade_id"] for line in result.written) == ["n1", "n2"]
    assert p0_feedback.catch_up("XAUUSD", str(data), str(bank_root), accounts, models=MODELS).written == []
    assert [line["trade_id"] for line in
            p0_feedback.catch_up("BTCUSD", str(data), str(bank_root), accounts, models=MODELS).written] == ["b1"]
    assert len(_lines(data / "p0_model_log.jsonl")) == 3


def test_the_catch_up_runs_after_a_merge_and_never_changes_the_export(tmp_path, monkeypatch):
    calls = []

    def fake_catch_up(symbol, accounts_data_dir, bank_root, real_accounts, symbol_map):
        calls.append(symbol)
        return p0_feedback.LogResult(written=[{}], warnings=[])

    monkeypatch.setattr(p0_feedback, "catch_up", fake_catch_up)
    env = Env(tmp_path)
    env.seed_bank(12)
    env.seed_payload(15)
    result = env.sync("ok")
    assert result.result == "merged" and calls == ["XAUUSD"] and result.p0_logged == 1
    from tools.candle_sync import format_result_line
    assert format_result_line(result).endswith("; P0 model log: 1 new line")
    assert env.sync("mt5_down").result == "export_failed" and calls == ["XAUUSD"]  # sin fusión no hay catch-up


def test_a_failing_catch_up_keeps_the_merge(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("log unreadable")

    monkeypatch.setattr(p0_feedback, "catch_up", broken)
    env = Env(tmp_path)
    env.seed_bank(12)
    env.seed_payload(15)
    result = env.sync("ok")
    assert result.result == "merged" and result.bars_added == {"1H": 3}
    from tools.candle_sync import format_result_line
    assert "P0 model log failed (ValueError: log unreadable)" in format_result_line(result)
