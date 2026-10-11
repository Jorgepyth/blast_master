"""
tools/p0_prospectivo.py: la medición prospectiva del P0 sistemático (docs/p0-reglas-opcion2.md §9 y adendas), con datos
inventados. Fija las constantes del plan y las reglas de cada corte y de cada hipótesis.
"""
import json
from datetime import datetime, timedelta

import pytest

from core.candle_resolution import Candle
from tools import p0_prospectivo as pp

T0 = datetime(2026, 10, 12, 6, 0)


def case(k, o2=1, s4s=1, thesis=1, op=0, b=0, strength="Mid", layers=None, d600=None, s1=None, close=None,
         o2_status="ok"):
    """Un análisis de la población: precio de partida 100, objetivo 110 (arriba), invalidación 95."""
    return pp.Case(f"t{k:03d}" if isinstance(k, int) else f"t{k}", "XAUUSD", T0 + timedelta(hours=k), 100.0, 110.0, 95.0, thesis, s4s, s1, close,
                   o2_dir=o2, o2_strength=None if o2 is None else strength,
                   o2_source="log" if o2 is not None else "missing", o2_status=o2_status, b_dir=b, op_p0=op,
                   layers=layers or {}, d600=d600)


def test_constants_match_the_plan():
    assert pp.CUTS == ((63, 0.0015), (127, 0.0182), (190, 0.0436))
    assert (pp.N_DRAWS, pp.SEED) == (100_000, 20261009)
    assert pp.POPULATION_START == datetime(2026, 10, 9, 17, 0)
    assert (pp.H1_N, pp.H2_N, pp.H_LIMIT) == (66, 29, 0.50)
    assert pp.H3_HOLM == (0.025, 0.05)
    assert (pp.MODEL, pp.MODEL_HASH, pp.P2_MODEL, pp.B_MODEL) == ("O2-v1.0", "0265dd400d07", "D600", "B")
    assert set(pp.ACCOUNTS) == {"XAUUSD", "BTCUSD", "US500"}


def test_hit_counts_no_touch_as_a_loss_for_anyone():
    assert pp.hit(1, 1) and pp.hit(-1, -1)
    assert not pp.hit(1, -1) and not pp.hit(1, 0) and not pp.hit(-1, 0)
    assert not pp.hit(0, 1) and not pp.hit(1, None)


def test_r_multiple_long_and_short():
    win = case(1, s4s=1)
    assert pp.r_multiple(win, 1) == pytest.approx(10 / 5)       # objetivo 110, riesgo hasta 95
    assert pp.r_multiple(win, -1) == -1.0                       # el Short pierde: tocó arriba
    down = case(2, s4s=-1)
    assert pp.r_multiple(down, -1) == pytest.approx(5 / 10)     # el Short gana 5 arriesgando 10
    flat = case(3, s4s=0, close=103.0)
    assert pp.r_multiple(flat, 1) == pytest.approx(3 / 5)       # sin toque: se cierra a las 48 h
    assert pp.r_multiple(flat, -1) == pytest.approx(-3 / 10)
    assert pp.r_multiple(case(4, s4s=None), 1) is None
    assert pp.r_multiple(win, 0) is None


def test_permutation_test_is_reproducible_and_its_fair_chance_keeps_the_mix():
    truths = [1] * 30 + [-1] * 50 + [0] * 20
    dirs = [1] * 40 + [-1] * 60
    p1, fair1 = pp.permutation_test(dirs, truths, n_draws=20_000)
    p2, fair2 = pp.permutation_test(dirs, truths, n_draws=20_000)
    assert (p1, fair1) == (p2, fair2)
    expected = (40 * 30 / 100 + 60 * 50 / 100) / 100            # E[aciertos] / n, con 40 Long y 60 Short
    assert fair1 == pytest.approx(expected, abs=0.005)
    perfect = [1] * 30 + [-1] * 50 + [1] * 20                    # acierta todo lo que se puede acertar
    p_perfect, _ = pp.permutation_test(perfect, truths, n_draws=20_000)
    assert p_perfect < 0.001


def test_sign_test_and_holm():
    assert pp.sign_test_one_sided(15, 9) == pytest.approx(0.1537, abs=1e-4)
    assert pp.sign_test_one_sided(0, 0) == 1.0
    assert pp.holm_two(0.01, 0.04) == (True, True)
    assert pp.holm_two(0.04, 0.01) == (True, True)
    assert pp.holm_two(0.01, 0.06) == (True, False)
    assert pp.holm_two(0.03, 0.04) == (False, False)              # la más chica no pasa 0.025: ninguna pasa


def test_cut_verdict_rules():
    # "sirve" pide el p del corte y más neto que B, en cualquier corte
    assert pp.cut_verdict(0.001, 0.0015, 10, 3, 0.7, 0.44, last=False) == pp.VERDICT_SIRVE
    assert pp.cut_verdict(0.001, 0.0015, 3, 3, 0.7, 0.44, last=False) == pp.VERDICT_SIGUE
    assert pp.cut_verdict(0.01, 0.0015, 10, 3, 0.7, 0.44, last=False) == pp.VERDICT_SIGUE
    # "no sirve" e "insuficiente" solo en el último
    assert pp.cut_verdict(0.3, 0.0436, 1, 3, 0.48, 0.44, last=True) == pp.VERDICT_NO_SIRVE
    assert pp.cut_verdict(0.3, 0.0436, 1, 3, 0.52, 0.44, last=True) == pp.VERDICT_INSUFICIENTE
    assert pp.cut_verdict(0.3, 0.0436, 1, 3, 0.48, 0.44, last=False) == pp.VERDICT_SIGUE


def test_first_n_waits_for_counts_resolution_and_models_still_to_come():
    cases = [case(k) for k in range(5)]
    assert pp.first_n(cases, 6) == (None, "faltan 1")
    chosen, status = pp.first_n(cases, 3)
    assert status == "ok" and [c.trade_id for c in chosen] == ["t000", "t001", "t002"]
    cases[1].s4s = None
    assert pp.first_n(cases, 3) == (None, "sin resolver 1")
    late_model = case(1.5, o2=None, o2_status="pending_candles")     # entre el 1 y el 2: puede volverse opinión
    assert pp.first_n([case(k) for k in range(5)], 3, could_join=[late_model]) == (None, "modelo sin calcular 1")
    after = case(10, o2=None, o2_status="pending_candles")
    assert pp.first_n([case(k) for k in range(5)], 3, could_join=[after])[1] == "ok"


def test_opinions_skip_abstentions_and_missing_models_in_anchor_order():
    cases = [case(3, o2=-1), case(1, o2=0), case(2, o2=None, o2_status="insufficient_history"), case(0, o2=1)]
    assert [c.trade_id for c in pp.opinions(cases)] == ["t000", "t003"]
    assert pp.o2_waiting(cases) == []                                # un motivo definitivo no se espera


def test_evaluate_cut_numbers_and_comparison_with_b():
    # 63 opiniones: 40 aciertos (los primeros 40 Long que tocaron arriba), B opina en todos y acierta 30
    cases = []
    for k in range(63):
        truth = 1 if k < 40 else (-1 if k < 55 else 0)
        cases.append(case(k, o2=1, s4s=truth, b=1 if k < 30 or k >= 55 else -1, op=1))
    r = pp.evaluate_cut(cases, alpha=0.0015, last=False, n_draws=5_000)
    assert (r["n"], r["hits"], r["net"]) == (63, 40, 17)
    assert r["net_b"] == (30 + 15) - (63 - 45)                       # B: 30 Long arriba + 15 Short abajo
    assert r["fair_chance"] == pytest.approx(40 / 63)                # todos Long: el sorteo no cambia nada
    assert r["p"] == 1.0 and r["verdict"] == pp.VERDICT_SIGUE
    assert r["mcnemar_b"]["pairs"] == 63
    assert r["mcnemar_p0_operador"]["only_o2"] == 0


def test_h1_and_h2_selection_and_rule():
    cases = [case(0, o2=0, op=1), case(1, o2=1, op=1), case(2, o2=0, op=0), case(3, o2=None, op=1, o2_status="x"),
             case(4, o2=1, op=-1, thesis=1), case(5, o2=0, op=-1, thesis=-1)]
    assert [c.trade_id for c in pp.h1_cases(cases)] == ["t000", "t005"]
    assert [c.trade_id for c in pp.h2_cases(cases)] == ["t004"]
    few_hits = [case(k, o2=0, op=1, s4s=1 if k < 7 else -1) for k in range(29)]
    r = pp.evaluate_h(few_hits)                                      # 7 de 29: IC 95% hasta 42%
    assert r["verdict"] == "confirmada" and r["wilson"][1] == pytest.approx(0.42, abs=0.01)
    ten = [case(k, o2=0, op=1, s4s=1 if k < 10 else -1) for k in range(29)]
    assert pp.evaluate_h(ten)["verdict"].startswith("no confirmada")  # 10 de 29 = 34%, pero el IC llega a 53%


EXAMPLE_LAYERS = {"P0": ("Long", "Strong"), "P1": ("Long", "Mid"), "P2": ("Long", "Mid"), "P3": ("Short", "Weak"),
                  "P4": ("Long", "Weak")}


def test_edge_versions_follow_the_wizard_formula():
    # El ejemplo explicado al operador: edge real 0.50 (Bullish); con el P0 sistemático débil, 0.20 (Choppy); con el
    # P2 sistemático en +2, 0.275 (Bullish).
    c = case(0, o2=1, strength="Weak", layers=EXAMPLE_LAYERS, d600=2.0)
    assert pp.h3_directions(c) == {"real": 1, "h3a": 0, "h3b": 1}
    no_d600 = case(1, o2=1, strength="Weak", layers=EXAMPLE_LAYERS, d600=None)
    assert pp.h3_directions(no_d600)["h3b"] == 0                    # D600 vacío vale 0: (0.25 + 0) / 2 < 0.26
    assert pp.edge_direction(case(2, layers={})) == 0


def test_h3_span_goes_up_to_the_190th_opinion_and_leaves_out_missing_models():
    cases = [case(k, o2=1) for k in range(190)] + [case(200, o2=1)]
    cases.insert(10, case(50.5, o2=0))                               # sin opinión pero dentro del tramo: entra
    cases.insert(11, case(60.5, o2=None, o2_status="insufficient_history"))  # sin modelo: queda fuera
    span, status = pp.h3_span(cases)
    assert status == "ok"
    ids = {c.trade_id for c in span}
    assert len(span) == 191 and "t200" not in ids
    assert pp.h3_span(cases[:100])[1] == "faltan 92 opiniones"


def test_evaluate_h3_counts_better_and_worse():
    # 10 análisis donde el P0 sistemático débil deja el edge en Choppy: 6 eran errores (mejor) y 4 aciertos (peor)
    span = [case(k, o2=1, strength="Weak", layers=EXAMPLE_LAYERS, d600=None, s4s=-1 if k < 6 else 1)
            for k in range(10)]
    r = pp.evaluate_h3(span, n_draws=2_000)
    assert (r["h3a"]["better"], r["h3a"]["worse"]) == (6, 4)
    assert r["h3a"]["p_sign"] == pytest.approx(pp.sign_test_one_sided(6, 4))
    assert r["real"]["net"] == 4 - 6 and r["h3a"]["net"] == 0
    assert r["h3a"]["verdict"].startswith("no confirmada")


def test_build_report_without_population_says_what_is_missing():
    report = pp.build_report([], {"XAUUSD": {"population": 0}}, n_draws=1_000)
    assert [c["status"] for c in report["cuts"]] == ["faltan 63", "faltan 127", "faltan 190"]
    assert report["h1"]["status"] == "faltan 66" and report["h2"]["status"] == "faltan 29"
    assert report["h3"]["status"] == "faltan 190 opiniones"
    assert "descriptive" not in report
    text = pp.format_report(report)
    assert "Corte 1 (63 opiniones, p < 0.0015): faltan 63" in text


def test_build_report_stops_after_a_decision_and_only_describes_on_request():
    # 63 opiniones casi perfectas, mezcla de Long y Short: sirve en el corte 1 y no se miran los siguientes
    cases = [case(k, o2=1 if k % 2 else -1, s4s=1 if k % 2 else -1, b=0) for k in range(63)]
    cases[0].s4s = 1                                                 # un error, para que no sea trivial
    report = pp.build_report(cases, {}, descriptive=True, n_draws=5_000)
    assert len(report["cuts"]) == 1 and report["cuts"][0]["result"]["verdict"] == pp.VERDICT_SIRVE
    assert report["descriptive"]["todos"]["methods"]["o2"]["hits"] == 62
    assert "Cifras acumuladas" in pp.format_report(report)


def test_valid_log_line_needs_ok_status_and_the_frozen_hash(tmp_path):
    path = tmp_path / "log.jsonl"
    rows = [
        {"trade_id": "a", "model": "O2-v1.0", "status": "pending_candles", "model_hash": "0265dd400d07"},
        {"trade_id": "a", "model": "O2-v1.0", "status": "ok", "model_hash": "0265dd400d07", "direction": 1,
         "strength": "Mid", "supersedes": 1},
        {"trade_id": "b", "model": "O2-v1.0", "status": "ok", "model_hash": "otra", "direction": -1},
        {"trade_id": "c", "model": "O2-v1.0", "status": "insufficient_history", "model_hash": "0265dd400d07"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    latest = pp.latest_lines(pp.read_log(str(path)))
    assert pp.valid_log_line(latest, "a", "O2-v1.0", "0265dd400d07")["direction"] == 1
    assert pp.valid_log_line(latest, "b", "O2-v1.0", "0265dd400d07") is None
    assert pp.valid_log_line(latest, "c", "O2-v1.0") is None
    assert pp.valid_log_line(latest, "z", "O2-v1.0") is None


def test_close_at_uses_the_last_closed_1h_bar():
    bars = [Candle(T0 + timedelta(hours=h), 1, 1, 1, 100.0 + h) for h in range(5)]
    assert pp.close_at(bars, T0 + timedelta(hours=3)) == 102.0       # la vela de las 2:00 cierra a las 3:00
    assert pp.close_at(bars, T0 + timedelta(hours=3, minutes=30)) == 102.0
    assert pp.close_at(bars, T0 + timedelta(hours=9)) is None        # el banco todavía no llega


def test_main_refuses_json_inside_the_data_dir(tmp_path):
    with pytest.raises(SystemExit):
        pp.main(["--data-dir", str(tmp_path), "--json", str(tmp_path / "x.json")])


def test_main_with_an_empty_data_dir_prints_the_status(tmp_path, capsys):
    out_file = tmp_path.parent / f"{tmp_path.name}_report.json"
    assert pp.main(["--data-dir", str(tmp_path), "--json", str(out_file)]) == 0
    assert "faltan 63" in capsys.readouterr().out
    assert json.loads(out_file.read_text(encoding="utf-8"))["population"]["total"] == 0
