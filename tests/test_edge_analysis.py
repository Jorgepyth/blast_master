import numpy as np
import pandas as pd
import pytest

from core.edge_analysis import (
    analisis_edge,
    analizar_correlaciones,
    get_market_outcome,
    profit_factor_by_edge_bins,
)


@pytest.mark.parametrize("bias,compliance,expected", [
    ("Bullish", "Valid", 1),
    ("Bullish", "Invalid", -1),
    ("Bearish", "Valid", -1),
    ("Bearish", "Invalid", 1),
    ("Bullish", None, 0),
])
def test_get_market_outcome(bias, compliance, expected):
    row = pd.Series({"market_bias": bias, "specific_bias_compliance": compliance})
    assert get_market_outcome(row) == expected


def _edge_df():
    # p0_score: perfectamente alineado con el resultado real.
    # p1_score: perfectamente invertido.
    # p2_score: sin señal (siempre 0).
    return pd.DataFrame([
        dict(market_bias="Bullish", specific_bias_compliance="Valid",   p0_score=2,  p1_score=-2, p2_score=0),
        dict(market_bias="Bullish", specific_bias_compliance="Valid",   p0_score=1,  p1_score=-1, p2_score=0),
        dict(market_bias="Bearish", specific_bias_compliance="Valid",   p0_score=-2, p1_score=2,  p2_score=0),
        dict(market_bias="Bullish", specific_bias_compliance="Invalid", p0_score=2,  p1_score=-2, p2_score=0),
        dict(market_bias="Bearish", specific_bias_compliance="Invalid", p0_score=-1, p1_score=1,  p2_score=0),
    ])


def test_analisis_edge_ranks_aligned_param_above_inverted_and_flat():
    out = analisis_edge(_edge_df(), score_cols=("p0_score", "p1_score", "p2_score"))
    assert list(out["Parametro"]) == ["P0", "P1", "P2"]  # ordenado por Profit Factor desc

    p0 = out[out["Parametro"] == "P0"].iloc[0]
    assert p0["Net Profit"] == 1 and p0["Profit Factor"] == pytest.approx(1.5)
    assert p0["Win Rate (%)"] == pytest.approx(60.0)
    assert p0["Participacion (Wins %)"] == pytest.approx(100.0)  # alineado en las 3 filas Valid

    p1 = out[out["Parametro"] == "P1"].iloc[0]
    assert p1["Net Profit"] == -1 and p1["Profit Factor"] == pytest.approx(0.67)
    assert p1["Participacion (Wins %)"] == pytest.approx(0.0)  # nunca alineado en las filas Valid

    p2 = out[out["Parametro"] == "P2"].iloc[0]
    assert p2["Net Profit"] == 0 and p2["Profit Factor"] == pytest.approx(0.0)
    assert np.isnan(p2["Correlacion"])  # columna constante -> correlación indefinida


def test_analisis_edge_zero_system_wins_participation_is_zero_not_div_error():
    df = pd.DataFrame([
        dict(market_bias="Bullish", specific_bias_compliance="Invalid", p0_score=2, p1_score=-2),
        dict(market_bias="Bearish", specific_bias_compliance="Invalid", p0_score=-1, p1_score=1),
    ])
    out = analisis_edge(df, score_cols=("p0_score", "p1_score"))
    assert (out["Participacion (Wins %)"] == 0.0).all()

    p1 = out[out["Parametro"] == "P1"].iloc[0]
    assert p1["Profit Factor"] == pytest.approx(2.0)  # sin pérdidas -> pf = gross_win
    assert p1["Win Rate (%)"] == pytest.approx(100.0)


def test_analizar_correlaciones_known_matrix():
    df = pd.DataFrame([
        dict(p0_score=2, p1_score=-2, p2_score=1),
        dict(p0_score=1, p1_score=-1, p2_score=1),
        dict(p0_score=-1, p1_score=1, p2_score=-1),
        dict(p0_score=-2, p1_score=2, p2_score=-2),
        dict(p0_score=0, p1_score=0, p2_score=2),
    ])
    corr = analizar_correlaciones(df, score_cols=("p0_score", "p1_score", "p2_score"))
    assert corr.loc["p0_score", "p1_score"] == pytest.approx(-1.0)  # perfectamente opuestos
    assert corr.loc["p0_score", "p2_score"] == pytest.approx(0.666886, rel=1e-4)


def test_profit_factor_by_edge_bins_higher_magnitude_bin_has_higher_win_rate():
    df = pd.DataFrame([
        dict(calc_edge=0.05, market_bias="Bullish", specific_bias_compliance="Invalid"),
        dict(calc_edge=-0.08, market_bias="Bearish", specific_bias_compliance="Invalid"),
        dict(calc_edge=0.10, market_bias="Bullish", specific_bias_compliance="Invalid"),
        dict(calc_edge=0.35, market_bias="Bullish", specific_bias_compliance="Valid"),
        dict(calc_edge=-0.40, market_bias="Bearish", specific_bias_compliance="Valid"),
        dict(calc_edge=0.45, market_bias="Bullish", specific_bias_compliance="Valid"),
    ])
    out = profit_factor_by_edge_bins(df, bins=2)
    assert len(out) == 2
    assert out["n_setups"].tolist() == [3, 3]
    assert out["win_rate"].tolist() == [0.0, 100.0]  # bin de menor |calc_edge| -> peor acierto
