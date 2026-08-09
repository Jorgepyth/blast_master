"""
core/edge_analysis.py — Validación del edge predictivo (Fase 2).

Funciones puras (DataFrame in, DataFrame out — cero acceso a DB), mismo
espíritu que core/math_engine.py y core/analytics_engine.py.

Objetivo: evaluar si las capas P0-P4 (analysis_layer.score, pivoteadas en el
notebook como p0_score..p4_score) realmente aportan alpha al calc_edge, y si
los pesos estáticos actuales (P0=0.30, P1=0.25, P2=0.15, P3=0.10, P4=0.20,
ver cli/main.py) están justificados por el desempeño real de cada parámetro.

Dos conceptos de "compliance" existen en este repo — no confundirlos:
  - specific_bias_compliance (efficiency_audit): si el SESGO ESTRUCTURAL
    (market_bias) fue validado. Es el que usa este módulo.
  - compliance (tactical_audit): si la EJECUCIÓN táctica del trade fue
    válida. No tiene nada que ver con qué tan bueno es un parámetro P0-P4
    prediciendo dirección — usar el campo equivocado acá invalidaría todo
    el análisis.

Explícitamente fuera de alcance (Fase 3, no de esta fase):
  - InterimBacktester / SplitSystemEvaluator: backtesting con ejecución y
    asignación de tamaño simuladas. profit_factor_by_edge_bins() es solo
    una tabla diagnóstica de acierto direccional por rango de calc_edge,
    no un backtest.
"""
from __future__ import annotations

from typing import Sequence, Tuple

import numpy as np
import pandas as pd

DEFAULT_SCORE_COLS: Tuple[str, ...] = ("p0_score", "p1_score", "p2_score", "p3_score", "p4_score")


def get_market_outcome(
    row,
    bias_col: str = "market_bias",
    compliance_col: str = "specific_bias_compliance",
) -> int:
    """
    Dirección real hacia la que se movió el mercado (+1 alcista, -1 bajista,
    0 indeterminado), derivada de combinar la predicción (market_bias) con
    si esa predicción fue validada (specific_bias_compliance).
    """
    bias = row[bias_col]
    compliance = row[compliance_col]
    if bias == "Bullish":
        if compliance == "Valid":
            return 1
        if compliance == "Invalid":
            return -1
    elif bias == "Bearish":
        if compliance == "Valid":
            return -1
        if compliance == "Invalid":
            return 1
    return 0


def analisis_edge(
    df: pd.DataFrame,
    score_cols: Sequence[str] = DEFAULT_SCORE_COLS,
    bias_col: str = "market_bias",
    compliance_col: str = "specific_bias_compliance",
    win_compliance_value: str = "Valid",
) -> pd.DataFrame:
    """
    Por cada parámetro P0-P4: Net Profit, Profit Factor, Win Rate,
    Participación (Wins %) y Correlación de Pearson contra el resultado real
    del mercado. Ordenado por Profit Factor descendente.
    """
    df_clean = df[df[compliance_col].notna()].copy()
    df_clean["Market_Outcome"] = df_clean.apply(
        lambda r: get_market_outcome(r, bias_col, compliance_col), axis=1
    )

    total_system_wins = int((df_clean[compliance_col] == win_compliance_value).sum())
    winning_trades = df_clean[df_clean[compliance_col] == win_compliance_value]

    results = []
    for col in score_cols:
        corr = df_clean[col].corr(df_clean["Market_Outcome"])

        active_trades = df_clean[df_clean[col] != 0].copy()
        if active_trades.empty:
            net_profit = 0
            pf = 0.0
            win_rate = 0.0
        else:
            trade_result = np.where(
                np.sign(active_trades[col]) == np.sign(active_trades["Market_Outcome"]), 1, -1
            )
            gross_win = int((trade_result == 1).sum())
            gross_loss = int((trade_result == -1).sum())
            net_profit = gross_win - gross_loss
            pf = float(gross_win) if gross_loss == 0 else gross_win / gross_loss
            win_rate = (gross_win / len(active_trades)) * 100

        aligned_wins = winning_trades[
            (winning_trades[col] != 0)
            & (np.sign(winning_trades[col]) == np.sign(winning_trades["Market_Outcome"]))
        ].shape[0]
        participation = (aligned_wins / total_system_wins) * 100 if total_system_wins > 0 else 0.0

        results.append({
            "Parametro": col.split("_")[0].upper(),
            "Net Profit": net_profit,
            "Profit Factor": round(pf, 2),
            "Win Rate (%)": round(win_rate, 1),
            "Participacion (Wins %)": round(participation, 1),
            "Correlacion": round(corr, 2) if pd.notna(corr) else np.nan,
        })

    metrics_df = pd.DataFrame(results)
    return metrics_df.sort_values(by="Profit Factor", ascending=False).reset_index(drop=True)


def analizar_correlaciones(
    df: pd.DataFrame,
    score_cols: Sequence[str] = DEFAULT_SCORE_COLS,
) -> pd.DataFrame:
    """Matriz de correlación de Spearman entre los scores de los parámetros P0-P4."""
    return df[list(score_cols)].corr(method="spearman")


def profit_factor_by_edge_bins(
    df: pd.DataFrame,
    edge_col: str = "calc_edge",
    bias_col: str = "market_bias",
    compliance_col: str = "specific_bias_compliance",
    bins: int = 3,
) -> pd.DataFrame:
    """
    Tabla diagnóstica: win rate de acierto direccional (bias vs Market_Outcome)
    por rango (quantil) de |calc_edge| — para ver si mayor magnitud de edge
    correlaciona con mayor acierto estructural. No es un backtest (sin
    ejecución/asignación simulada) — eso es Fase 3.
    """
    d = df[df[compliance_col].notna()].copy()
    d["Market_Outcome"] = d.apply(lambda r: get_market_outcome(r, bias_col, compliance_col), axis=1)
    d["edge_mag"] = d[edge_col].abs()
    d["bias_sign"] = np.where(d[bias_col] == "Bullish", 1, np.where(d[bias_col] == "Bearish", -1, 0))
    d["is_correct"] = (d["bias_sign"] == d["Market_Outcome"]).astype(int)

    edge_bins = pd.qcut(d["edge_mag"], q=bins, duplicates="drop")
    out = d.groupby(edge_bins, observed=False).agg(
        n_setups=("is_correct", "count"),
        win_rate=("is_correct", "mean"),
    ).reset_index()
    out = out.rename(columns={"edge_mag": "edge_range"})
    out["win_rate"] = (out["win_rate"] * 100).round(1)
    return out
