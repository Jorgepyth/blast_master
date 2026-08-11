"""
tools/report_data.py — Carga y prepara los DataFrames usados por los reportes
(HTML/LLM) generados por `cli/main.py report` y por `jupyter/data_analysis.ipynb`.

Toca la DB (a diferencia de `core/*`, deliberadamente sin acceso a DB) — vive
en `tools/` por eso, no en `core/`.

Deuda técnica reconocida: replica las mismas queries/merges que las celdas
`0943b418`/`567e23d5`/`128ddea1`/`7449e646` de `jupyter/data_analysis.ipynb`.
El notebook no se refactorizó para usar este módulo (sus celdas ya están
verificadas y en producción) — si se cambia una query acá, replicar el
cambio allá, y viceversa, hasta que se unifiquen en una futura ronda.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

P_LAYER_COLUMNS = [
    "id",
    "p0_direction", "p1_direction", "p2_direction", "p3_direction", "p4_direction",
    "p0_strength", "p1_strength", "p2_strength", "p3_strength", "p4_strength",
    "p0_score", "p1_score", "p2_score", "p3_score", "p4_score",
]

MERGED_COLUMNS = [
    "id", "long_prob", "short_prob", "no_trade_prob", "calc_edge", "market_bias", "efficiency_timeframe",
    "edge_validation_price", "structural_invalidation", "bias_a", "real_bias_b", "resolution_type",
    "specific_bias_compliance", "false_regime_rate", "compliance", "r_multiple", "trade_status", "size",
    "pnl_and_cost", "created_at", "entry_time", "exit_time", "trade_decision", "edge_description",
    "p0_direction", "p1_direction", "p2_direction", "p3_direction", "p4_direction",
    "p0_strength", "p1_strength", "p2_strength", "p3_strength", "p4_strength",
    "p0_score", "p1_score", "p2_score", "p3_score", "p4_score",
]
# de-duplicar preservando orden (evita listar p0-p4 dos veces si se edita arriba)
MERGED_COLUMNS = list(dict.fromkeys(MERGED_COLUMNS))


@dataclass
class ReportData:
    tactical_df: pd.DataFrame
    unified_df: pd.DataFrame
    efficiency_df: pd.DataFrame
    layer_df: pd.DataFrame
    wide: pd.DataFrame
    merged: pd.DataFrame
    llm_df: pd.DataFrame


def _pivot_layers(layer_df: pd.DataFrame) -> pd.DataFrame:
    """Pivotea analysis_layer (long) a wide: una fila por trade, columnas p0..p4_{direction,strength,score}."""
    if layer_df.empty:
        return pd.DataFrame(columns=P_LAYER_COLUMNS)

    cols = ["direction", "strength", "score", "thesis"]
    wide = layer_df.pivot(index="trade_id", columns="layer_name", values=cols)
    wide.columns = [f"{layer.lower()}_{col}" for col, layer in wide.columns]
    wide = wide.reset_index().rename(columns={"trade_id": "id"})
    for col in P_LAYER_COLUMNS:
        if col not in wide.columns:
            # NaN, no None: las columnas *_score son numéricas y consumidas con
            # np.sign() aguas abajo (core/edge_analysis.py, core/backtest_engine.py)
            # — None (dtype object) rompe esa comparación si algún trade no tiene
            # todas las 5 capas.
            wide[col] = float("nan") if col.endswith("_score") else None
    score_cols = [c for c in P_LAYER_COLUMNS if c.endswith("_score")]
    # El pivot puede devolver dtype object en las columnas *_score (ej. con muy
    # pocas filas/una sola capa presente) — forzar float64 explícito evita que
    # np.sign() falle más adelante con "unorderable types".
    wide[score_cols] = wide[score_cols].apply(pd.to_numeric, errors="coerce")
    return wide[P_LAYER_COLUMNS]


def load_report_data(engine: Engine) -> ReportData:
    """
    Lee unified_department/analysis_layer/efficiency_audit/tactical_audit
    (filtrado a XAUUSD) y arma `wide` (P0-P4 pivoteado), `merged` (para
    Fase 2/3, foco cuantitativo) y `llm_df` (para el reporte LLM, incluye
    comportamiento/psicología) — mismas queries/merges que el notebook.
    """
    with engine.connect() as conn:
        unified_df = pd.read_sql(text("SELECT * FROM unified_department WHERE asset LIKE '%XAUUSD%'"), conn)
        layer_df = pd.read_sql(text("SELECT * FROM analysis_layer"), conn)
        efficiency_df = pd.read_sql(text("SELECT * FROM efficiency_audit"), conn)
        tactical_df = pd.read_sql(text("SELECT * FROM tactical_audit"), conn)

    wide = _pivot_layers(layer_df)

    filt_unified = unified_df[[c for c in [
        "id", "calc_edge", "market_bias", "long_prob", "short_prob", "no_trade_prob",
        "edge_validation_price", "structural_invalidation", "trade_status", "edge_description",
    ] if c in unified_df.columns]]
    filt_eff = efficiency_df[[c for c in [
        "id", "resolution_type", "false_regime_rate", "specific_bias_compliance",
        "created_at", "bias_a", "real_bias_b", "efficiency_timeframe",
    ] if c in efficiency_df.columns]].sort_values(by="created_at")
    filt_tact = tactical_df[[c for c in [
        "id", "compliance", "r_multiple", "entry_time", "exit_time", "trade_decision",
        "pnl_and_cost", "size", "mae_adverse", "mfe_favorable", "session",
    ] if c in tactical_df.columns]]

    merged = (
        filt_unified.merge(filt_eff, on="id", how="left")
        .merge(filt_tact, on="id", how="left")
        .merge(wide, on="id", how="left")
    )
    merged = merged[[c for c in MERGED_COLUMNS if c in merged.columns]]
    if "resolution_type" in merged.columns:
        merged = merged[merged["resolution_type"] != "Open"]

    llm_df = (
        tactical_df
        .merge(
            unified_df[[c for c in ["id", "calc_edge", "market_bias", "long_prob", "short_prob"] if c in unified_df.columns]],
            on="id", how="left",
        )
        .merge(
            efficiency_df[[c for c in ["id", "specific_bias_compliance", "bias_a", "real_bias_b", "resolution_type"] if c in efficiency_df.columns]],
            on="id", how="left",
        )
        .merge(wide, on="id", how="left")
    )
    if "resolution_type" in llm_df.columns:
        llm_df = llm_df[llm_df["resolution_type"] != "Open"]

    return ReportData(
        tactical_df=tactical_df,
        unified_df=unified_df,
        efficiency_df=efficiency_df,
        layer_df=layer_df,
        wide=wide,
        merged=merged,
        llm_df=llm_df,
    )
