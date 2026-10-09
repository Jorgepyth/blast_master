"""Tests de tools/p0_visor.py: búsqueda por short-id, lo que ve una TF en el ancla, explicación y gráficos."""
import sqlite3
from datetime import datetime, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pytest  # noqa: E402

from core.candle_resolution import Candle  # noqa: E402
from core.p0_fib import PENDING, REJECTION  # noqa: E402
from core.p0_structure import BULLISH, INSUFFICIENT  # noqa: E402
from tools import p0_sistematico as ps  # noqa: E402
from tools import p0_visor as pv  # noqa: E402

T0 = datetime(2026, 6, 1, 0, 0)
H = timedelta(hours=1)

# La misma serie de tests/test_p0_sistematico.py: tramo alcista 8 → 11 (A 10, B 20), entrada a 0.382 en la vela 14 y
# rechazo en la 15, más dos velas que siguen subiendo.
ROWS = [
    (10.0, 9.0, 9.5), (11.0, 9.8, 10.5), (12.0, 11.0, 11.5), (11.5, 10.0, 10.2), (11.0, 9.2, 9.6),
    (12.5, 10.0, 12.0), (13.5, 11.5, 13.0), (13.0, 11.0, 11.5), (12.0, 10.0, 10.5), (13.0, 10.5, 12.8),
    (15.0, 12.5, 14.8), (20.0, 14.5, 19.5), (19.8, 18.0, 18.5), (18.6, 16.5, 17.0), (17.2, 16.0, 16.2),
    (17.3, 16.1, 17.2), (18.0, 17.0, 17.8), (18.5, 17.5, 18.2),
]


@pytest.fixture
def market():
    candles = {"1H": [Candle(T0 + i * H, (h + lo) / 2, h, lo, c) for i, (h, lo, c) in enumerate(ROWS)]}
    return ps.Market("SYN", candles)


def test_tf_view_before_the_result_marks_it_pending_and_shows_what_came_later(market):
    tau = T0 + 15 * H  # cierre de la vela 14: entró a 0.382, el rechazo (vela 15) todavía no se conoce
    v = pv.tf_view(market, "1H", tau, window=30, future=5)
    assert v.state == BULLISH and (v.leg.a.index, v.leg.b.index) == (8, 11)
    assert v.last_closed == 14 and v.bars.future.sum() == 3
    assert v.invalidation == 10.0  # último HL
    ev = next(e for e in v.events["p0"] if e.r == 0.382)
    assert ev.outcome_at_tau == PENDING and ev.arrival == 1
    assert ev.outcome_final == REJECTION                 # lo que pasó después, para mirar
    assert ev.rejection_price == pytest.approx(16.18 + 1.0)
    assert (v.score_structure, v.score_fib) == (1, 1)    # sin resultado todavía: dir


def test_tf_view_after_the_result_scores_it(market):
    v = pv.tf_view(market, "1H", T0 + 16 * H, window=30)
    ev = next(e for e in v.events["p0"] if e.r == 0.382)
    assert ev.outcome_at_tau == REJECTION and v.score_fib == 2
    labels = {(p.kind, p.index): p.label for p in v.pivots if p.in_sequence}
    assert labels[(1, 11)] == "HH" and labels[(-1, 8)] == "HL"


def test_tf_view_never_uses_pivots_not_yet_confirmed(market):
    v = pv.tf_view(market, "1H", T0 + 12 * H, window=30)  # el techo 11 se confirma al cierre de la 13
    assert all(p.confirmed_at <= T0 + 12 * H for p in v.pivots)
    assert (1, 11) not in {(p.kind, p.index) for p in v.pivots}


def test_insufficient_history_has_no_leg(market):
    v = pv.tf_view(market, "1H", T0 + 6 * H, window=30)
    assert v.state == INSUFFICIENT and v.leg is None and v.levels["p0"] == {}
    assert "historia insuficiente" in pv.explain_tf(v)


def test_explain_tf_mentions_the_leg_the_event_and_the_score(market):
    text = pv.explain_tf(pv.tf_view(market, "1H", T0 + 15 * H, window=30, future=5))
    assert "Tramo A→B (d→e): A = 10.00" in text and "B = 20.00" in text
    assert "0.382" in text and "después fue rechazo" in text and "B–H (con Fibonacci) +1" in text


def test_plots_render_without_errors(market):
    v = pv.tf_view(market, "1H", T0 + 16 * H, window=30, future=2)
    info = pv.AnalysisInfo("abcdef12-x", "SYN", T0, T0 + 16 * H, False, False, "Bullish", 19.0, 15.0, "Long", "Mid",
                           "1HT-BL", {"1H": "BL"}, {})
    fig, (a1, a2) = plt.subplots(2, 1)
    pv.plot_fractals(a1, v, info)
    pv.plot_fibonacci(a2, v, info)
    plt.close(fig)
    empty = pv.tf_view(market, "1H", T0 + 6 * H, window=30)
    fig, ax = plt.subplots()
    pv.plot_fibonacci(ax, empty)
    plt.close(fig)


def _db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE unified_department (id TEXT, asset TEXT, created_at TEXT, is_backdated INTEGER, "
                "analysis_start_time TEXT, market_bias TEXT, edge_validation_price REAL, "
                "structural_invalidation REAL, mark_price REAL, calc_edge NUMERIC)")
    con.execute("CREATE TABLE analysis_layer (trade_id TEXT, layer_name TEXT, direction TEXT, strength TEXT, "
                "thesis TEXT)")
    for tid, edge in (("2834cbeb-aaaa", 0.4), ("2834cbff-bbbb", -0.1), ("9fb9e581-95fc-4539-9d2f-407eab042bbc", 0.3)):
        con.execute("INSERT INTO unified_department VALUES (?, 'XAUUSDT.P', '2026-06-01 10:00:00', ?, NULL, "
                    "'Bullish', 4100, 4000, 4050, ?)", (tid, 1 if tid.startswith("9fb9") else 0, edge))
        con.execute("INSERT INTO analysis_layer VALUES (?, 'P0', 'Long', 'Mid', '1DT-BL\n12HT-BR == 1DT')", (tid,))
    con.commit()
    con.close()


def test_find_analysis_by_short_id(tmp_path):
    db = str(tmp_path / "acc.db")
    _db(db)
    info = pv.find_analysis(db, "XAUUSD", "2834CBEB")
    assert info.trade_id == "2834cbeb-aaaa" and info.short_id == "2834cbeb"
    assert info.anchor == datetime(2026, 6, 1, 9, 40) and info.bias == "Bullish"
    assert info.labels == {"1D": "BL", "12H": "BR"} and info.refs == {"12H": "1D"}
    recovered = pv.find_analysis(db, "XAUUSD", "9fb9e581")
    assert recovered.recovered and recovered.anchor == datetime(2026, 6, 1, 10, 0)  # su hora tipeada
    with pytest.raises(ValueError, match="coincide con 2"):
        pv.find_analysis(db, "XAUUSD", "2834cb")
    with pytest.raises(ValueError, match="no hay ningún análisis"):
        pv.find_analysis(db, "XAUUSD", "ffff")


def test_what_happened_uses_the_geometric_first_touch(market):
    # Ancla al cierre de la vela 13 (precio 17.0); arriba 18.2, abajo 15.5. La vela 17 toca 18.2 primero.
    info = pv.AnalysisInfo("abcdef12-x", "SYN", T0 + 13 * H, T0 + 14 * H, False, False, "Bullish", 18.2, 15.5, "Long",
                           "Mid", "", {}, {})
    out = pv.what_happened(info, market, {"operador": 1, "B": -1, "C": 0})
    assert out["primer toque"].startswith("arriba") and out["horas desde el ancla"] == 3.0
    assert out["operador"] == "Long: S1 acierta, S4 estricto acierta"
    assert out["B"] == "Short: S1 pierde, S4 estricto pierde" and out["C"] == "no opina"
    no_levels = pv.AnalysisInfo("abcdef12-y", "SYN", T0, T0 + 14 * H, False, False, None, None, None, None, None, "",
                                {}, {})
    assert "motivo" in pv.what_happened(no_levels, market, {})


def test_letters_name_the_last_pivots_in_order(market):
    v = pv.tf_view(market, "1H", T0 + 16 * H, window=30)
    # Secuencia: techo 2, piso 4, techo 6, piso 8, techo 11 → a, b, c, d, e (a = el más viejo).
    assert [(p.kind, p.index) for p in v.letters.values()] == [(1, 2), (-1, 4), (1, 6), (-1, 8), (1, 11)]
    assert list(v.letters) == ["a", "b", "c", "d", "e"]
    assert v.letter_of[(1, 11)] == "e"


def test_choosing_the_system_leg_reproduces_the_system(market):
    tau = T0 + 16 * H
    system = pv.tf_view(market, "1H", tau, window=30)
    mine = pv.tf_view(market, "1H", tau, window=30, choice=("d", "e"))
    assert mine.chosen and not system.chosen
    assert (mine.leg.a.index, mine.leg.b.index) == (system.leg.a.index, system.leg.b.index)
    assert (mine.score_structure, mine.score_fib, mine.score_placebo) == system.system_scores
    assert mine.system_leg == system.leg


def test_choosing_another_leg_recalculates_and_keeps_the_system_reading(market):
    tau = T0 + 16 * H
    mine = pv.tf_view(market, "1H", tau, window=30, choice=("b", "c"))
    assert (mine.leg.a.index, mine.leg.b.index) == (4, 6) and mine.state == BULLISH
    assert mine.levels["p0"][0.382][0] == pytest.approx(13.5 - 0.382 * (13.5 - 9.2))
    assert (mine.system_leg.a.index, mine.system_leg.b.index) == (8, 11)
    forced = pv.tf_view(market, "1H", tau, window=30, choice=("b", "c"), state_choice="rango")
    assert forced.state == "range" and (forced.score_structure, forced.score_fib) == (0, 0)
    assert "tu elección" in pv.explain_tf(mine) and "El sistema había leído" in pv.explain_tf(mine)


@pytest.mark.parametrize("choice, state, message", [
    (("a", "c"), None, "mismo tipo"),
    (("e", "d"), None, "anterior"),
    (("x", "e"), None, "no existe"),
    (("d", "e"), "lateral", "estado"),
])
def test_invalid_choices_are_explained(market, choice, state, message):
    with pytest.raises(ValueError, match=message):
        pv.tf_view(market, "1H", T0 + 16 * H, window=30, choice=choice, state_choice=state)


def test_a_pivot_can_be_chosen_by_the_date_of_its_candle(market):
    tau = T0 + 16 * H
    by_letter = pv.tf_view(market, "1H", tau, window=30, choice=("b", "c"))
    by_date = pv.tf_view(market, "1H", tau, window=30, choice=("2026-06-01 04:00", "2026-06-01 06:00"))
    mixed = pv.tf_view(market, "1H", tau, window=30, choice=("2026-06-01 04:00", "c"))
    assert by_date.leg == by_letter.leg == mixed.leg
    assert pv.pivot_name(by_date, by_date.leg.a) == "b"
    table = pv.list_pivots(market, "1H", tau)
    assert list(table.letra) == ["a", "b", "c", "d", "e"]
    assert table["fecha para ELECCION"].iloc[1] == "2026-06-01 04:00" and table.tipo.iloc[1] == "piso"


@pytest.mark.parametrize("choice, message", [
    (("2026-06-01", "e"), "agregá la hora"),                   # ese día tiene varios pisos
    (("2026-06-01 05:00", "e"), "no hay un pivote confirmado"),
    (("a", "2026-06-01 06:00"), "mismo tipo"),
    (("ayer", "e"), "ni una fecha"),
])
def test_invalid_dates_are_explained(market, choice, message):
    with pytest.raises(ValueError, match=message):
        pv.tf_view(market, "1H", T0 + 16 * H, window=30, choice=choice)


def test_model_summary_uses_the_chosen_legs(market):
    tau = T0 + 16 * H
    same = {"1H": pv.tf_view(market, "1H", tau, window=30, choice=("d", "e"))}
    assert pv.model_summary(market, tau, same).equals(pv.model_summary(market, tau))
    flat = {"1H": pv.tf_view(market, "1H", tau, window=30, choice=("d", "e"), state_choice="rango")}
    table = pv.model_summary(market, tau, flat)
    assert set(table["P0 (x0)"]) == {0}  # con la única TF en rango, ningún modelo opina
    fig, ax = plt.subplots()
    pv.plot_fractals(ax, flat["1H"])
    plt.close(fig)


def test_model_summary_has_every_model(market):
    table = pv.model_summary(market, T0 + 16 * H)
    assert list(table.modelo) == [s.name for s in ps.ALL_SPECS]
    assert set(table.dirección) <= {"Long", "Short", "no opina"}
