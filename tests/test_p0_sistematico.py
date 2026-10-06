"""
Tests de tools/p0_sistematico.py: recetas (INV-5), puntaje (D8), P0 del modelo, lectura en τ sin look-ahead (T2,
INV-3), verdad y métricas (D12, D15), etiquetas (RF-7), estadística (RF-8), partición (INV-6), foto (E3), solo lectura
(INV-1), exclusiones (INV-7) y decisión (D18).
"""
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from core.candle_resolution import OUTCOME_CONFIRMED, OUTCOME_INVALIDATED, OUTCOME_OPEN, Candle
from core.outcome_metrics import s1, s4_strict
from core.p0_fib import REJECTION, RUPTURE
from core.p0_structure import BULLISH, RANGE
from tools import p0_sistematico as ps
from tools.p2_backtest import open_readonly_session

T0 = datetime(2026, 6, 1, 0, 0)
H = timedelta(hours=1)


# ---------------------------------------------------------------- recetas (INV-5)

def test_model_recipes_match_d9():
    expected = {
        "A": ({"1D": 0.25, "12H": 0.25, "4H": 0.25, "1H": 0.25}, False, False),
        "B": ({"1D": 0.25, "12H": 0.25, "4H": 0.25, "1H": 0.25}, True, False),
        "C": ({"1D": 0.40, "12H": 0.30, "4H": 0.20, "1H": 0.10}, True, False),
        "D": ({"1D": 0.20, "12H": 0.30, "4H": 0.30, "1H": 0.20}, True, False),
        "E": ({"1D": 0.10, "12H": 0.20, "4H": 0.30, "1H": 0.40}, True, False),
        "F": ({"1D": 0.30, "12H": 0.30, "4H": 0.20, "1H": 0.20}, True, True),
        "G": ({"1D": 0.20, "12H": 0.20, "4H": 0.30, "1H": 0.30}, True, False),
        "H": ({"1W": 0.20, "1D": 0.20, "12H": 0.20, "4H": 0.20, "1H": 0.20}, True, False),
    }
    for name, (weights, fib, hier) in expected.items():
        spec = ps.SPECS_BY_NAME[name]
        assert spec.weights == weights and spec.uses_fib is fib and spec.hierarchical is hier, name
        assert spec.levels == "p0" and spec.s_max == (2 if fib else 1)
    assert ps.P0_MODEL_A is ps.SPECS_BY_NAME["A"] and ps.P0_MODEL_H is ps.SPECS_BY_NAME["H"]


def test_twin_recipes_match_d10():
    for name in "BCDEFGH":
        twin, base = ps.SPECS_BY_NAME[name + "p"], ps.SPECS_BY_NAME[name]
        assert twin.levels == "placebo"
        assert (twin.weights, twin.timeframes, twin.uses_fib, twin.hierarchical) == (
            base.weights, base.timeframes, base.uses_fib, base.hierarchical)
    assert "Ap" not in ps.SPECS_BY_NAME
    assert ps.SELECTION_NAMES == ("A", "B", "C", "D", "E", "F", "G")


# ---------------------------------------------------------------- puntaje (D8) y x0

def _reading(situation, state=BULLISH):
    return ps.TfReading("1H", state, None, situation)


@pytest.mark.parametrize("situation, a_score, fib_score", [
    (ps.SIT_RETR_REJECTION, 1, 2),
    (ps.SIT_RETR_RUPTURE, 1, 0),
    (ps.SIT_ZERO_REJECTION, 1, 0),
    (ps.SIT_ZERO_RUPTURE, 1, 2),
    (ps.SIT_NO_EVENT, 1, 1),
])
def test_tf_score_table_d8(situation, a_score, fib_score):
    assert ps.tf_score(_reading(situation), uses_fib=False) == a_score
    assert ps.tf_score(_reading(situation), uses_fib=True) == fib_score
    bear = _reading(situation, state="bearish")
    assert ps.tf_score(bear, uses_fib=False) == -a_score
    assert ps.tf_score(bear, uses_fib=True) == -fib_score


def test_range_and_insufficient_score_zero():
    for sit, state in ((ps.SIT_RANGE, RANGE), (ps.SIT_INSUFFICIENT, "insufficient_history")):
        assert ps.tf_score(_reading(sit, state), True) == 0
        assert ps.tf_score(_reading(sit, state), False) == 0


def test_x0_rounding_half_away_from_zero_is_exact():
    a, b, c = ps.P0_MODEL_A, ps.P0_MODEL_B, ps.P0_MODEL_C
    assert ps.model_x0(a, {"1D": 1, "12H": 1, "4H": 1, "1H": 1}) == 2
    assert ps.model_x0(a, {"1D": 1, "12H": -1, "4H": 1, "1H": 0}) == 1      # 2·0.25 = 0.5 → 1
    assert ps.model_x0(a, {"1D": -1, "12H": 1, "4H": -1, "1H": 0}) == -1    # −0.5 → −1
    assert ps.model_x0(a, {"1D": 1, "12H": -1, "4H": 1, "1H": -1}) == 0
    assert ps.model_x0(b, {"1D": 2, "12H": 0, "4H": 0, "1H": 0}) == 1       # S = 0.5, 2S/2 = 0.5 → 1
    assert ps.model_x0(b, {"1D": 2, "12H": 2, "4H": 2, "1H": 2}) == 2
    # C: 0.4·1 + 0.3·(−1) + 0.2·1 + 0.1·(−2) = 0.1 → 2·0.1/2 = 0.1 → 0; con aritmética binaria saldría 0.0999…
    assert ps.model_x0(c, {"1D": 1, "12H": -1, "4H": 1, "1H": -2}) == 0
    # C: 0.4·2 + 0.3·(−1) + 0 + 0 = 0.5 → 0.5 → 1 (empate exacto, lejos de cero)
    assert ps.model_x0(c, {"1D": 2, "12H": -1, "4H": 0, "1H": 0}) == 1


def test_hierarchical_rule_of_f():
    f = ps.P0_MODEL_F
    assert ps.model_x0(f, {"1D": 1, "12H": -1, "4H": 2, "1H": 2}) == 0       # s_1D + s_12H = 0: no opina
    # Arriba alcista (1 + 1); 4H bajista no cuenta; 1H alcista cuenta: S = 0.3 + 0.3 + 0 + 0.2·2 = 1.0 → x0 = 1
    assert ps.model_x0(f, {"1D": 1, "12H": 1, "4H": -2, "1H": 2}) == 1
    assert ps.model_x0(f, {"1D": 2, "12H": 2, "4H": 2, "1H": 2}) == 2


# ---------------------------------------------------------------- lectura en τ y T2 en un mercado sintético

def _synthetic_candles():
    """
    1H: techos 2 (12), 6 (13.5, HH) y 11 (20, HH); pisos 4 (9.2) y 8 (10, HL). Desde el cierre de la 13 el tramo
    alcista es 8 → 11 (A 10, B 20); la bajada entra a la zona de 0.382 (16.18 ± 0.25) en la vela 14 y la 15 rechaza
    (16.18 + 1.0 = 17.18), sin formar ningún techo intermedio.
    """
    rows = [
        (10.0, 9.0, 9.5), (11.0, 9.8, 10.5), (12.0, 11.0, 11.5), (11.5, 10.0, 10.2), (11.0, 9.2, 9.6),
        (12.5, 10.0, 12.0), (13.5, 11.5, 13.0), (13.0, 11.0, 11.5), (12.0, 10.0, 10.5), (13.0, 10.5, 12.8),
        (15.0, 12.5, 14.8), (20.0, 14.5, 19.5),                       # 11: techo 20, confirmado al cierre de la 13
        (19.8, 18.0, 18.5), (18.6, 16.5, 17.0),                       # 13: cierre de referencia 17.0
        (17.2, 16.0, 16.2), (17.3, 16.1, 17.2),                       # 14: entra; 15: rechazo
    ]
    return {"1H": [Candle(T0 + i * H, (h + lo) / 2, h, lo, c) for i, (h, lo, c) in enumerate(rows)]}


def _market(candles):
    return ps.Market("SYN", candles)


def test_read_tf_before_and_after_the_rejection():
    market = _market(_synthetic_candles())
    tau_entry = T0 + 15 * H     # cierre de la vela 14: entró, resultado no conocido
    tau_after = T0 + 16 * H     # cierre de la vela 15: rechazo conocido
    r0 = ps.read_tf(market, "1H", T0 + 12 * H, "p0")
    assert r0.state == BULLISH and (r0.leg.a.index, r0.leg.b.index) == (4, 6)
    r1 = ps.read_tf(market, "1H", tau_entry, "p0")
    assert r1.state == BULLISH and (r1.leg.a.index, r1.leg.b.index) == (8, 11)
    assert r1.situation == ps.SIT_NO_EVENT and r1.last_event.outcome == "pending"
    r2 = ps.read_tf(market, "1H", tau_after, "p0")
    assert r2.situation == ps.SIT_RETR_REJECTION and r2.last_event.r == 0.382
    assert ps.tf_score(r2, True) == 2


def test_predictions_identical_with_and_without_future_candles():
    """T2 / INV-3: la salida en τ no cambia si se quitan las velas posteriores a τ."""
    full = _synthetic_candles()
    market_full = _market(full)
    for k in range(4, 17):
        tau = T0 + k * H
        a = ps.predict_at(market_full, tau)
        b = ps.predict_at(_market(ps.candles_until(full, tau)), tau)
        assert {n: v["x0"] for n, v in a.items() if n != "_readings"} == \
               {n: v["x0"] for n, v in b.items() if n != "_readings"}, k
        assert {key: (r.state, r.leg, r.situation) for key, r in a["_readings"].items()} == \
               {key: (r.state, r.leg, r.situation) for key, r in b["_readings"].items()}, k


# ---------------------------------------------------------------- verdad y métricas (D12, D15)

def _item(tid, anchor, start=100.0, upper=110.0, lower=95.0, bias="Bullish"):
    return ps.AnalysisInput(tid, "XAUUSD", "000", anchor, anchor, None, False, bias, "Direccionales", start, upper,
                            lower, upper, lower, 1, "Mid", "")


def test_outcomes_for_reuse_outcome_metrics_and_match_truth_vectors():
    a = datetime(2026, 7, 1, 8, 0)
    items = [_item("u_fast", a), _item("d_fast", a), _item("u_late", a), _item("open", a), _item("amb", a)]
    truths = {
        "u_fast": ps.GeometricTruth("u_fast", OUTCOME_CONFIRMED, True, a + 5 * H, a + 5 * H),
        "d_fast": ps.GeometricTruth("d_fast", OUTCOME_INVALIDATED, False, a + 3 * H, a + 3 * H),
        "u_late": ps.GeometricTruth("u_late", OUTCOME_CONFIRMED, True, a + 60 * H, a + 60 * H),
        "open": ps.GeometricTruth("open", OUTCOME_OPEN, None, None, a + 3000 * H),
        "amb": ps.GeometricTruth("amb", "ambiguous", None, None, a + 2 * H),
    }
    tv = ps.truth_vectors(items, truths)
    assert list(tv["s1"]) == [1, -1, 1, 0, 0]
    assert list(tv["s4s_mask"]) == [True, True, True, True, False]
    assert list(tv["s4s"][tv["s4s_mask"]]) == [1, -1, 0, 0]
    long_all = {i.trade_id: 1 for i in items}
    wr = s4_strict(ps.outcomes_for(items, truths, long_all))
    m = tv["s4s_mask"]
    assert (wr.wins, wr.n) == (int((tv["s4s"][m] == 1).sum()), int(m.sum()))
    wr1 = s1(ps.outcomes_for(items, truths, long_all))
    assert (wr1.wins, wr1.n) == (2, 3)
    # Un short acierta lo que un long falla.
    short_all = {i.trade_id: -1 for i in items}
    assert s4_strict(ps.outcomes_for(items, truths, short_all)).wins == 1


def test_geometric_null_and_poisson_binomial():
    items = [_item("x", T0, start=100.0, upper=110.0, lower=95.0)]
    assert ps.geometric_null_p(items, [1])[0] == pytest.approx(5 / 15)
    assert ps.geometric_null_p(items, [-1])[0] == pytest.approx(10 / 15)
    assert np.isnan(ps.geometric_null_p(items, [0])[0])
    # Con p iguales es la binomial: P(X >= 2 | n = 3, p = 0.5) = 4/8.
    assert ps.poisson_binomial_sf(2, [0.5, 0.5, 0.5]) == pytest.approx(0.5)
    # P(X >= 1) con p = 0.2 y 0.5 = 1 − 0.8·0.5 = 0.6.
    assert ps.poisson_binomial_sf(1, [0.2, 0.5]) == pytest.approx(0.6)
    assert ps.poisson_binomial_sf(0, [0.2, 0.5]) == pytest.approx(1.0)


# ---------------------------------------------------------------- etiquetas (RF-7) y κ

def test_parse_thesis_labels_with_variants():
    thesis = ("1DT-BR: texto\n12HT-Bl == 1DT\n4T-BL: texto 0.382\n1HT-BR: == 4HT\n12T-BR repetida")
    labels, refs = ps.parse_thesis_labels(thesis)
    assert labels == {"1D": "BR", "12H": "BL", "4H": "BL", "1H": "BR"}
    assert refs == {"12H": "1D", "1H": "4H"}
    assert ps.parse_thesis_labels("sin etiquetas") == ({}, {})


def test_cohen_kappa_known_values():
    assert ps.cohen_kappa(["BL", "BR", "BL", "BR"], ["BL", "BR", "BL", "BR"]) == pytest.approx(1.0)
    # po = 0.5, pe = 0.5 → κ = 0
    assert ps.cohen_kappa(["BL", "BL", "BR", "BR"], ["BL", "BR", "BL", "BR"]) == pytest.approx(0.0)


# ---------------------------------------------------------------- IRLS y bootstrap (RF-8)

def test_irls_matches_the_closed_form_of_a_2x2_table():
    # Grupo 0: 30 rechazos de 100; grupo 1: 60 de 100. log OR = log((60/40)/(30/70)).
    x = np.column_stack([np.ones(200), np.r_[np.zeros(100), np.ones(100)]])
    y = np.r_[np.ones(30), np.zeros(70), np.ones(60), np.zeros(40)]
    beta, ok = ps.logistic_irls(x, y)
    assert ok
    assert beta[0] == pytest.approx(np.log(30 / 70), abs=1e-9)
    assert beta[1] == pytest.approx(np.log((60 / 40) / (30 / 70)), abs=1e-9)


def _events_frame(n_legs=60, p_p0=0.7, p_pl=0.5, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_legs):
        for r, is_p0 in ((0.382, True), (0.300, False), (0.618, True), (0.559, False)):
            p = p_p0 if is_p0 else p_pl
            rows.append({"symbol": "XAUUSD", "tf": "1H" if g % 2 else "4H", "leg": ("1H", g), "r": r,
                         "is_p0": is_p0, "extreme": False, "state": "trend" if g % 3 else "range",
                         "entry_time": T0 + g * H, "outcome": REJECTION if rng.random() < p else RUPTURE,
                         "cluster": f"c{g}"})
    return pd.DataFrame(rows)


def test_logit_test_is_deterministic_and_detects_a_planted_effect():
    df = _events_frame(n_legs=200, p_p0=0.75, p_pl=0.45)
    r1 = ps.logit_test(df, n_boot=300, min_arm=10)
    r2 = ps.logit_test(df, n_boot=300, min_arm=10)
    assert r1 == r2                                    # INV-2: misma semilla, misma salida
    assert r1.odds_ratio > 1 and r1.ci95[0] > 1 and r1.p_one_sided < 0.05
    assert r1.n_clusters == 200 and not r1.insufficient
    assert ps.logit_test(df, n_boot=50).insufficient   # 400 por brazo < 480


def test_design_matrix_drops_constant_columns():
    df = _events_frame(n_legs=10)
    df["tf"] = "1H"
    _, names = ps.design_matrix(df)
    assert not any(name.startswith("tf_") for name in names)  # una sola TF: es la referencia


# ---------------------------------------------------------------- partición (INV-6) y deduplicación

def test_confirmatory_is_locked_without_the_flag():
    df = _events_frame(n_legs=10)
    with pytest.raises(ps.ConfirmatoryLocked):
        ps.split_events(df, "confirmatory")
    exp = ps.split_events(df, "exploratory")
    conf = ps.split_events(df, "confirmatory", allow_confirmatory=True)
    assert len(exp) + len(conf) == len(df)
    for tf in ("1H", "4H"):
        e, c = exp[exp.tf == tf], conf[conf.tf == tf]
        assert e.entry_time.max() < c.entry_time.min()
        n = int((df.tf == tf).sum())
        assert len(e) <= int(np.floor(0.6 * n))


def test_dedupe_keeps_the_coarser_tf():
    base = {"symbol": "XAUUSD", "up": True, "a": 100.0, "b": 200.0, "r": 0.382, "is_p0": True, "extreme": False,
            "state": "trend", "outcome": REJECTION, "entry_time": T0}
    rows = [
        dict(base, tf="4H", leg=("4H", 1), a_time=T0, b_time=T0 + 40 * H),
        dict(base, tf="1H", leg=("1H", 1), a_time=T0 + 2 * H, b_time=T0 + 41 * H),   # dentro de las velas de 4H
        dict(base, tf="1H", leg=("1H", 2), a_time=T0 + 5 * H, b_time=T0 + 41 * H),   # A fuera de la vela de 4H
    ]
    out = ps.dedupe_legs(pd.DataFrame(rows))
    assert sorted(out.tf) == ["1H", "4H"]
    assert ("1H", 1) not in set(out.leg)


# ---------------------------------------------------------------- foto (E3) y solo lectura (INV-1)

def test_verify_snapshot_detects_a_change(tmp_path):
    (tmp_path / "XAUUSD").mkdir()
    f = tmp_path / "XAUUSD" / "1H.csv"
    f.write_text("time,open,high,low,close\n2026-01-01 00:00:00,1,2,0,1\n")
    manifest = tmp_path / "m.json"
    manifest.write_text('{"files": {"XAUUSD/1H.csv": {"sha256": "%s"}}}' % ps.sha256_file(str(f)))
    assert ps.verify_snapshot(str(tmp_path), str(manifest)) == {"XAUUSD/1H.csv": ps.sha256_file(str(f))}
    f.write_text("time,open,high,low,close\n2026-01-01 00:00:00,1,2,0,1.5\n")
    with pytest.raises(ps.SnapshotChanged):
        ps.verify_snapshot(str(tmp_path), str(manifest))


def _tiny_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE unified_department (id TEXT, calc_edge NUMERIC)")
    con.execute("CREATE TABLE analysis_layer (trade_id TEXT, layer_name TEXT, direction TEXT, strength TEXT, "
                "thesis TEXT)")
    con.execute("INSERT INTO unified_department VALUES ('t1', 0.3)")
    con.execute("INSERT INTO analysis_layer VALUES ('t1', 'P0', 'Long', 'Mid', '1DT-BL')")
    con.commit()
    con.close()


def test_readers_are_read_only(tmp_path):
    db = str(tmp_path / "acc.db")
    _tiny_db(db)
    before = ps.sha256_file(db)
    assert ps.read_calc_edge(db) == {"t1": 0.3}
    assert ps.read_p0_layers(db) == {"t1": ("Long", "Mid", "1DT-BL")}
    assert ps.sha256_file(db) == before
    session = open_readonly_session(db)
    try:
        with pytest.raises(OperationalError):
            session.execute(text("INSERT INTO unified_department VALUES ('t2', 0.1)"))
    finally:
        session.close()


# ---------------------------------------------------------------- decisión (D18)

def _inputs(**kw):
    base = dict(t0_ok=True, t2_ok=True, fib_positive=False, fib_null=False, btc_same_sign=False, twin_pairs=40,
                twin_b=0, twin_c=0, t5_passing=0, t4_passing=0, t6_fails=False)
    base.update(kw)
    return ps.DecisionInputs(**base)


def test_d18_first_matching_row():
    assert ps.decide_d18(_inputs(t2_ok=False))[0] == ps.DECISION_STOP
    assert ps.decide_d18(_inputs(fib_positive=True, btc_same_sign=True, twin_b=3, twin_c=1))[0] == \
        ps.DECISION_CONTINUE
    assert ps.decide_d18(_inputs(t4_passing=1))[0] == ps.DECISION_MODIFY
    assert ps.decide_d18(_inputs(fib_null=True, t6_fails=True))[0] == ps.DECISION_ABANDON_P0
    assert ps.decide_d18(_inputs(fib_null=True))[0] == ps.DECISION_ABANDON_FIB
    # ganador ≤ gemelo con pares suficientes (b = c = 0) también abandona Fibonacci
    assert ps.decide_d18(_inputs())[0] == ps.DECISION_ABANDON_FIB
    decision, why = ps.decide_d18(_inputs(twin_pairs=5))
    assert decision == ps.DECISION_INSUFFICIENT and "T3a/T3b" in why
    assert ps.decide_d18(_inputs(fib_positive=True, btc_same_sign=False, twin_b=3, twin_c=1))[0] == \
        ps.DECISION_INSUFFICIENT


def test_label_t8():
    assert ps.label_t8(10, 5, 0, 0.01, (0.1, 0.5)) == ps.LABEL_NO_CONCLUYENTE
    assert ps.label_t8(30, 9, 1, 0.02, (0.05, 0.4), p_r13=0.01, involves_winner=True) == ps.LABEL_SUPERA
    assert ps.label_t8(30, 9, 1, 0.02, (0.05, 0.4), p_r13=0.2, involves_winner=True) == ps.LABEL_NO_DISTINGUIBLE
    assert ps.label_t8(30, 1, 9, 0.02, (-0.4, -0.05)) == ps.LABEL_INFERIOR
    assert ps.label_t8(30, 3, 2, 0.9, (-0.1, 0.15)) == ps.LABEL_EQUIVALENTE


# ---------------------------------------------------------------- marcas de T0 y E4

def test_dst_window_and_anomalous_gaps():
    assert ps.touches_dst_window(datetime(2026, 3, 20), datetime(2026, 3, 21))
    assert ps.touches_dst_window(datetime(2026, 3, 6), datetime(2026, 3, 9))
    assert not ps.touches_dst_window(datetime(2026, 7, 1), datetime(2026, 7, 3))
    # Lunes 2026-06-29: falta la vela de las 00:00 (anómalo); martes 2026-06-30: falta la de las 15:00 (pausa del oro).
    times = [datetime(2026, 6, 28, 23)] + [datetime(2026, 6, 29, 1) + k * H for k in range(40)]
    times = [t for t in times if t != datetime(2026, 6, 30, 15)]
    bars = ps.Bars.from_lists(times, [1.0] * len(times), [0.0] * len(times), [0.5] * len(times), H)
    assert ps.anomalous_gaps(bars, "XAUUSD") == [(datetime(2026, 6, 29, 0), datetime(2026, 6, 29, 1))]
    assert len(ps.anomalous_gaps(bars, "BTCUSD")) == 2
