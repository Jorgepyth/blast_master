"""
tools/p2_vista.py: la vista P2 que abre Ctrl+J. Banco sintético en tendencia alcista limpia (los mismos ayudantes que el
registro del P2): el P2 de cualquier modelo da 2. La DB de cuenta y el banco están en tmp_path y no se modifican.
"""
import json
import re
import sqlite3
from datetime import datetime

import pytest

import tools.p2_backtest as p2_backtest
from tests.test_p2_model_feedback import D_ONLY, N1_START, _write_bank, _write_db
from tools import p2_vista as pv
from tools.p2_model_feedback import log_account, read_log, latest_lines

NOW = datetime(2026, 10, 3, 13, 0)


@pytest.fixture
def env(tmp_path):
    bank_root = tmp_path / "bank"
    _write_bank(bank_root)
    db = _write_db(tmp_path / "xau.db", [("n1-abc", "XAUUSDT.P", N1_START, False),
                                         ("n2-abc", "XAUUSDT.P", datetime(2026, 10, 2, 9, 0), False)])
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE unified_department SET edge_validation_price = 1600, structural_invalidation = 1400")
        conn.execute("UPDATE analysis_layer SET thesis = ? WHERE trade_id = 'n1-abc'",
                     ("Diario alcista </script><b>ojo</b>",))
    log = tmp_path / "p2_model_log.jsonl"
    log_account(str(db), "000", str(bank_root), str(log), models=D_ONLY, now=NOW, trade_ids=["n1-abc"])
    return {"db": str(db), "bank": str(bank_root), "log": str(log), "dir": tmp_path}


def _view(env, trade_id="n1-abc"):
    return pv.build_view(env["db"], trade_id, data_dir=str(env["dir"]), bank_root=env["bank"], log_path=env["log"],
                         models=["D", "D600"], now=NOW)


def test_the_view_has_the_operator_p2_both_models_the_charts_and_the_result(env):
    view = _view(env)
    assert view["analysis"]["id8"] == "n1-abc" and view["analysis"]["symbol"] == "XAUUSD"
    assert view["analysis"]["thesis_dir"] == 1
    assert view["operator"]["direction"] == "Long" and view["operator"]["value"] == 2
    d, d600 = view["models"]
    assert (d["name"], d["source"], d["status"], d["rescaled"]) == ("D", "registro", "ok", 2)
    assert (d600["name"], d600["source"], d600["status"], d600["rescaled"]) == ("D600", "calculado al abrir", "ok", 2)
    assert sum(r["contribution"] for r in d["rows"]) == pytest.approx(d["raw"])
    assert list(view["charts"]) == ["1W", "1D", "12H", "4H", "1H", "30M"]
    one_h = view["charts"]["1H"]
    assert one_h["n_before"] == pv.BARS_BEFORE and len(one_h["bars"]) == pv.BARS_BEFORE + pv.BARS_AFTER
    assert view["result"]["title"]


def test_the_chart_ema_at_the_anchor_is_the_one_the_model_used(env):
    view = _view(env)
    by_tf = latest_lines(read_log(env["log"]))[("n1-abc", "D")].data["by_tf"]
    for tf in ("1W", "1H", "30M"):
        chart, k = view["charts"][tf], view["charts"][tf]["n_before"] - 1
        assert chart["bars"][k][4] == pytest.approx(by_tf[tf]["price"])
        for n in (20, 100, 200):
            assert chart[f"ema{n}"][k] == pytest.approx(by_tf[tf][f"ema{n}"], rel=1e-6)


def test_model_rows_follow_the_score_rule():
    spec = p2_backtest.MODELS_BY_NAME["D"]
    by_tf = {
        "1W": {"price": 5, "ema20": 4, "ema100": 3, "ema200": 2, "adx": 30, "bias": 1, "plus_di": 20, "minus_di": 10},
        "1D": {"price": 5, "ema20": 4, "ema100": 3, "ema200": 2, "adx": 22, "bias": 1, "plus_di": 20, "minus_di": 10},
        "12H": {"price": 5, "ema20": 4, "ema100": 3, "ema200": 2, "adx": 19, "bias": 1, "plus_di": 20, "minus_di": 10},
        "4H": {"price": 3, "ema20": 4, "ema100": 2, "ema200": 1, "adx": 40, "bias": 0, "plus_di": 20, "minus_di": 10},
        "1H": {"price": 1, "ema20": 2, "ema100": 3, "ema200": 4, "adx": 40, "bias": 0, "plus_di": 30, "minus_di": 10},
        "30M": {"price": 1, "ema20": 2, "ema100": 3, "ema200": 4, "adx": 26, "bias": -1, "plus_di": 5, "minus_di": 30},
    }
    rows = {r["tf"]: r for r in pv.model_rows(by_tf, spec)}
    assert [rows[tf]["ema_bias"] for tf in spec.timeframes] == [1, 1, 1, 0, -1, -1]
    assert [rows[tf]["force"] for tf in spec.timeframes] == [1.0, 0.5, 0.0, 1.0, 1.0, 1.0]
    assert rows["1W"]["contribution"] == pytest.approx(0.08)
    assert rows["1D"]["contribution"] == pytest.approx(0.06)
    assert rows["12H"]["contribution"] == 0 and rows["1H"]["contribution"] == 0   # sin fuerza / el DI no confirma
    assert rows["30M"]["contribution"] == pytest.approx(-0.15)


def test_status_texts_say_why_a_model_has_no_value():
    d600 = p2_backtest.MODELS_BY_NAME["D600"]
    assert pv.status_text("insufficient_history:1W", d600, 550) == "pide 600 velas semanales; hay 550"
    assert pv.status_text("clock_unverified", symbol="USTEC") == "el reloj de las velas de USTEC todavía no está verificado"
    assert pv.status_text("timeframe_not_in_bank:30M") == "falta la temporalidad 30M en el banco"


def test_a_model_without_history_says_so(env):
    view = pv.build_view(env["db"], "n1-abc", data_dir=str(env["dir"]), bank_root=env["bank"], log_path=env["log"],
                         models=["D"], now=NOW)
    assert view["models"][0]["source"] == "registro"
    # Con un banco semanal corto, D (800) no opina y D600 (600) sí
    short = env["dir"] / "short"
    _write_bank(short, sizes={"1W": 700})
    view = pv.build_view(env["db"], "n2-abc", data_dir=str(env["dir"]), bank_root=str(short), log_path=env["log"],
                         models=["D", "D600"], now=NOW)
    d, d600 = view["models"]
    assert d["status"] == "insufficient_history:1W" and d["text"].startswith("pide 800 velas semanales; hay ")
    assert d600["status"] == "ok"


def test_the_html_embeds_the_view_safely(env):
    view = _view(env)
    page = pv.render_html(view)
    assert "</script><b>" not in page                       # el texto del operador no puede cerrar el script
    data = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1)
    assert json.loads(data)["operator"]["thesis"] == "Diario alcista </script><b>ojo</b>"
    assert "https://" not in page                            # sin nada externo: se abre sin internet


def _p0_line(trade_id="n1-abc", status="ok", model_hash=pv.P0_MODEL_HASH):
    return {"trade_id": trade_id, "model": pv.P0_MODEL, "model_hash": model_hash, "status": status, "direction": -1,
            "strength": "Mid", "by_tf": {
                "1H": {"score": -1, "state": "bearish", "situation": "trend_no_event", "leg": {"a": 1510.0, "b": 1490.0}},
                "4H": {"score": -2, "state": "bearish", "situation": "trend_0_rupture", "leg": {"a": 1520.0, "b": 1480.0}},
                "12H": {"score": 0, "state": "range", "situation": "range", "leg": None},
                "1D": {"score": 0, "state": "insufficient_history", "situation": "insufficient_history", "leg": None}}}


def test_the_systematic_p0_is_hidden_until_the_analysis_resolves(env):
    p0_log = env["dir"] / "p0_model_log.jsonl"
    p0_log.write_text(json.dumps(_p0_line()) + "\n", encoding="utf-8")
    early = datetime(2026, 10, 1, 12, 0)                       # 2 h después del ancla, sin toque: abierto
    view = pv.build_view(env["db"], "n1-abc", data_dir=str(env["dir"]), bank_root=env["bank"], log_path=env["log"],
                         p0_log_path=str(p0_log), models=["D"], now=early)
    assert view["result"]["resolved"] is False and view["result"]["hours_left"] == pytest.approx(46)
    assert view["p0"] == {"hidden": True, "hours_left": pytest.approx(46)}
    page = pv.render_html(view)
    data = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S).group(1)
    assert json.loads(data)["p0"] == {"hidden": True, "hours_left": pytest.approx(46)}   # el dato no viaja en el HTML
    assert "ruptura del nivel 0" not in page


def test_a_resolved_analysis_shows_the_systematic_p0_from_the_log(env):
    p0_log = env["dir"] / "p0_model_log.jsonl"
    p0_log.write_text(json.dumps(_p0_line()) + "\n", encoding="utf-8")
    view = pv.build_view(env["db"], "n1-abc", data_dir=str(env["dir"]), bank_root=env["bank"], log_path=env["log"],
                         p0_log_path=str(p0_log), models=["D"], now=NOW)   # 51 h después del ancla: resuelto
    assert view["result"]["resolved"] is True
    p0 = view["p0"]
    assert (p0["status"], p0["source"], p0["direction"], p0["strength"]) == ("ok", "registro", -1, "Mid")
    assert [r["tf"] for r in p0["rows"]] == ["1H", "4H", "12H", "1D"]
    assert p0["rows"][1] == {"tf": "4H", "state": "Bajista", "situation": "ruptura del nivel 0", "score": -2,
                             "leg": [1520.0, 1480.0]}
    assert view["operator_p0"]["direction"] is None            # este análisis no tiene capa P0 cargada


def test_a_line_with_another_recipe_is_not_used_and_the_model_is_computed(env):
    p0_log = env["dir"] / "p0_model_log.jsonl"
    p0_log.write_text(json.dumps(_p0_line(model_hash="otra")) + "\n", encoding="utf-8")
    view = pv.build_view(env["db"], "n1-abc", data_dir=str(env["dir"]), bank_root=env["bank"], log_path=env["log"],
                         p0_log_path=str(p0_log), models=["D"], now=NOW)
    # El banco de prueba no tiene 15M: calculado al abrir, el modelo dice por qué no puede
    assert view["p0"]["source"] == "calculado al abrir"
    assert view["p0"]["status"] == "timeframe_not_in_bank:15M"
    assert view["p0"]["text"] == "falta la temporalidad 15M en el banco"


def test_open_p2_view_writes_the_page_and_an_error_page_when_it_fails(env, tmp_path):
    out = tmp_path / "reports"
    path = pv.open_p2_view(env["db"], "n1-abc", out_dir=str(out), open_it=False, data_dir=str(env["dir"]),
                           bank_root=env["bank"], log_path=env["log"], now=NOW)
    assert path == str(out / "p2_n1-abc.html") and "Vista P2" in open(path, encoding="utf-8").read()
    bad = pv.open_p2_view(env["db"], "no-existe", out_dir=str(out), open_it=False)
    assert "No se pudo armar la vista P2" in open(bad, encoding="utf-8").read()


def test_find_analysis_by_unique_prefix(env):
    assert pv.find_analysis(env["db"], "n1").trade_id == "n1-abc"
    with pytest.raises(LookupError):
        pv.find_analysis(env["db"], "n")


def test_db_path_of_a_sqlite_connection(tmp_path):
    conn = sqlite3.connect(tmp_path / "x.db")
    try:
        assert pv.db_path_of(conn) == str(tmp_path / "x.db")
    finally:
        conn.close()
    assert pv.db_path_of(object()) is None


def test_the_view_does_not_write_to_the_db_or_the_bank(env):
    before = {p: p.stat().st_mtime_ns for p in env["dir"].rglob("*") if p.is_file()}
    _view(env)
    after = {p: p.stat().st_mtime_ns for p in env["dir"].rglob("*") if p.is_file()}
    assert before == after
