"""
core/backtest_engine.py — Backtesting con ejecución y sizing simulados (Fase 3).

Funciones puras (DataFrame in/out o dict out — cero acceso a DB), mismo
espíritu que core/math_engine.py, core/analytics_engine.py y
core/edge_analysis.py. Fase 2 respondió "¿el score de cada parámetro predice
la dirección real?" con una tabla diagnóstica; Fase 3 responde "¿si hubiéramos
aplicado una regla de filtro/sizing distinta, el resultado habría sido
mejor?" simulando qué trades se habrían tomado y con qué tamaño.

Lógica portada de `notion_api_analysis/notion_to_csv/data_analysis.ipynb`
(celdas 6-9, clases `InterimBacktester` y `SplitSystemEvaluator`), adaptada a
funciones puras (patrón de este repo, no clases) y al esquema real de
blast_master, verificado campo por campo antes de portar:
  - Compliance/compliance: mismos 3 valores exactos (Edge_valid/Invalid_edge/
    No_edge).
  - P{i}_direction/strength: mismos valores exactos (Long/Short/Neutral,
    Strong/Mid/Weak).
  - Agrupación EFFICIENCY (P0,P2,P3) vs TACTICAL (P1,P4) usada por
    run_department_confluence_backtest: no es una suposición, es el valor
    real de `analysis_layer.department` en la DB.

Qué "compliance" usar acá (no confundir con Fase 2): este módulo usa
`tactical_audit.compliance` (validez de la EJECUCIÓN táctica: Edge_valid/
Invalid_edge/No_edge) para reconstruir el resultado real de mercado, porque
es el campo que la referencia usa para eso. Fase 2 (`edge_analysis.py`) usa
en cambio `efficiency_audit.specific_bias_compliance` (validez del SESGO
ESTRUCTURAL) para la misma reconstrucción, porque responde una pregunta
distinta. Ambos son correctos para lo que miden — no intercambiarlos.

Limitación aceptada y documentada (no un bug): a diferencia del dataset de
`notion_api_analysis`, blast_master sí tiene el R-multiple REALIZADO por
trade (`tactical_audit.r_multiple`, Fase 0), pero no está poblado en todas
las filas (más ausente en `No_edge`, donde muchos trades nunca cerraron con
datos completos). Por eso el proxy direccional (±1 según acierto de
dirección, fiel a la referencia) se mantiene como métrica siempre computable,
y `evaluate_real_r_by_filter()` se agrega aparte para medir el R real solo
donde existe, reportando explícitamente cuántas filas quedan excluidas.
"""
from __future__ import annotations

from typing import Dict, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd

DEFAULT_SCORE_COLS: Tuple[str, ...] = ("p0_score", "p1_score", "p2_score", "p3_score", "p4_score")
DEFAULT_DIR_COLS: Tuple[str, ...] = ("p0_direction", "p1_direction", "p2_direction", "p3_direction", "p4_direction")
DEFAULT_STR_COLS: Tuple[str, ...] = ("p0_strength", "p1_strength", "p2_strength", "p3_strength", "p4_strength")

DEFAULT_ICD_WEIGHTS: Dict[str, float] = {
    "p0_score": 0.30,
    "p1_score": 0.25,
    "p2_score": 0.15,
    "p3_score": 0.10,
    "p4_score": 0.20,
}

_DIR_MAP = {"Long": 1, "Short": -1, "Neutral": 0}
_EFFICIENCY_STRENGTH_MAP = {"Strong": 3, "Mid": 2, "Weak": 1}
_TACTICAL_STRENGTH_MAP = {"Strong": 2, "Mid": 1, "Weak": 0}


def reconstruct_execution_outcome(
    df: pd.DataFrame,
    edge_col: str = "calc_edge",
    compliance_col: str = "compliance",
) -> pd.DataFrame:
    """
    Agrega la columna `true_outcome`: dirección real hacia la que se movió el
    mercado (+1/-1/0), derivada de `sign(calc_edge)` invertido según si la
    ejecución fue válida (`compliance`). `No_edge` (o cualquier otro valor)
    → 0, mismo criterio que la referencia.
    """
    out = df.copy()
    legacy_direction = np.sign(out[edge_col])
    conditions = [out[compliance_col] == "Edge_valid", out[compliance_col] == "Invalid_edge"]
    choices = [legacy_direction, -legacy_direction]
    out["true_outcome"] = np.select(conditions, choices, default=0)
    return out


def run_icd_backtest(
    df: pd.DataFrame,
    threshold: float = 0.35,
    score_cols: Mapping[str, float] = None,
    weights: Mapping[str, float] = None,
    edge_col: str = "calc_edge",
    compliance_col: str = "compliance",
) -> Tuple[pd.DataFrame, dict]:
    """
    Puerto de `InterimBacktester.run_backtest`. Recalcula un ICD propio a
    partir de `score_cols` ponderados por `weights` (defaults = pesos reales
    de cli/main.py), filtra a `|icd| >= threshold`, y mide el acierto
    direccional (proxy ±1, escalado por `|icd|`) contra `true_outcome`.

    Devuelve (df_detalle, resumen_dict) — el DataFrame detalle incluye
    `proposed_signal`/`proposed_direction`/`icd` para poder encadenarlo a
    `evaluate_real_r_by_filter` (usando `proposed_signal` como `filter_col`).
    """
    weights = dict(weights) if weights is not None else dict(DEFAULT_ICD_WEIGHTS)
    cols = list(score_cols) if score_cols is not None else list(weights.keys())

    working = df[df[compliance_col].notna()].copy()
    working = reconstruct_execution_outcome(working, edge_col=edge_col, compliance_col=compliance_col)

    icd = sum(working[col] * weights[col] for col in cols) / 2.0
    working["icd"] = icd
    working["proposed_signal"] = np.where(working["icd"].abs() >= threshold, 1, 0)
    working["proposed_direction"] = np.sign(working["icd"])

    executed = working[working["proposed_signal"] == 1].copy()
    if executed.empty:
        return working, {"status": "EXECUTION_ZERO", "message": "Umbral demasiado restrictivo."}

    executed["trade_result_flat"] = np.where(
        executed["proposed_direction"] == executed["true_outcome"], 1, -1
    )
    executed["allocation_scale"] = executed["icd"].abs()
    executed["trade_result_scaled"] = executed["trade_result_flat"] * executed["allocation_scale"]

    total_trades_legacy = int((working[compliance_col] != "No_edge").sum())
    total_trades_proposed = len(executed)

    wins = executed[executed["trade_result_flat"] == 1]
    losses = executed[executed["trade_result_flat"] == -1]

    win_rate = (len(wins) / total_trades_proposed) * 100
    gross_win_r = wins["trade_result_scaled"].sum()
    gross_loss_r = abs(losses["trade_result_scaled"].sum())
    net_profit_r = gross_win_r - gross_loss_r
    profit_factor = gross_win_r / gross_loss_r if gross_loss_r > 0 else gross_win_r

    trades_invalid_avoided = int(
        ((working[compliance_col] == "Invalid_edge") & (working["proposed_signal"] == 0)).sum()
    )

    return working, {
        "status": "OK",
        "metricas_ejecucion": {
            "muestra_analizada": len(working),
            "trades_totales_legacy": total_trades_legacy,
            "trades_totales_propuestos": total_trades_proposed,
            "frecuencia_operativa_ratio": round(total_trades_proposed / total_trades_legacy, 2)
            if total_trades_legacy > 0
            else np.nan,
            "trades_invalidos_evitados": trades_invalid_avoided,
        },
        "metricas_rendimiento": {
            "win_rate_percent": round(win_rate, 1),
            "net_profit_r_escalado": round(float(net_profit_r), 2),
            "profit_factor_escalado": round(float(profit_factor), 2),
        },
    }


def sweep_icd_thresholds(df: pd.DataFrame, thresholds: Sequence[float], **kwargs) -> pd.DataFrame:
    """
    Corre `run_icd_backtest` para cada threshold y arma una tabla tidy
    (una fila por threshold), puerto del grid-search manual de la referencia.
    """
    rows = []
    for threshold in thresholds:
        _, result = run_icd_backtest(df, threshold=threshold, **kwargs)
        if result["status"] == "EXECUTION_ZERO":
            rows.append({
                "threshold": threshold,
                "status": "EXECUTION_ZERO",
                "trades_totales_propuestos": 0,
                "win_rate_percent": np.nan,
                "net_profit_r_escalado": np.nan,
                "profit_factor_escalado": np.nan,
            })
        else:
            rows.append({
                "threshold": threshold,
                "status": "OK",
                **result["metricas_ejecucion"],
                **result["metricas_rendimiento"],
            })
    return pd.DataFrame(rows)


def baseline_legacy_metrics(
    df: pd.DataFrame,
    compliance_col: str = "compliance",
    edge_col: str = "calc_edge",
) -> dict:
    """
    Métricas del sistema real tal como se ejecutó: todo `Edge_valid`/
    `Invalid_edge` cuenta como "ejecutado" (sin filtro de threshold), proxy
    de acierto direccional sin escalar (1R plano). Ancla de comparación
    contra `sweep_icd_thresholds`.
    """
    working = reconstruct_execution_outcome(df, edge_col=edge_col, compliance_col=compliance_col)
    executed = working[working[compliance_col].isin(["Edge_valid", "Invalid_edge"])].copy()

    if executed.empty:
        return {"status": "EXECUTION_ZERO", "message": "Sin trades ejecutados en la muestra."}

    executed["proposed_direction"] = np.sign(executed[edge_col])
    executed["trade_result_flat"] = np.where(
        executed["proposed_direction"] == executed["true_outcome"], 1, -1
    )

    wins = executed[executed["trade_result_flat"] == 1]
    losses = executed[executed["trade_result_flat"] == -1]

    win_rate = (len(wins) / len(executed)) * 100
    gross_win_r = float(len(wins))
    gross_loss_r = float(len(losses))
    net_profit_r = gross_win_r - gross_loss_r
    profit_factor = gross_win_r / gross_loss_r if gross_loss_r > 0 else gross_win_r

    return {
        "status": "OK",
        "metricas_ejecucion": {
            "muestra_analizada": len(working),
            "trades_totales_legacy": len(executed),
            "trades_totales_propuestos": len(executed),
            "frecuencia_operativa_ratio": 1.0,
            "trades_invalidos_evitados": 0,
        },
        "metricas_rendimiento": {
            "win_rate_percent": round(win_rate, 1),
            "net_profit_r_escalado": round(net_profit_r, 2),
            "profit_factor_escalado": round(float(profit_factor), 2),
        },
    }


def _custom_layer_score(direction: pd.Series, strength: pd.Series, strength_map: dict) -> pd.Series:
    dir_val = direction.map(_DIR_MAP).fillna(0)
    str_val = strength.map(strength_map).fillna(0)
    return dir_val * str_val


def run_department_confluence_backtest(
    df: pd.DataFrame,
    dir_cols: Sequence[str] = DEFAULT_DIR_COLS,
    str_cols: Sequence[str] = DEFAULT_STR_COLS,
    efficiency_layers: Sequence[int] = (0, 2, 3),
    tactical_layers: Sequence[int] = (1, 4),
    edge_col: str = "calc_edge",
    compliance_col: str = "compliance",
) -> Tuple[pd.DataFrame, dict]:
    """
    Puerto de `SplitSystemEvaluator.evaluate_split_proposal`. Recalcula
    scores propios desde direction/strength crudos con dos mapas asimétricos
    distintos al `i_cd` real (`_EFFICIENCY_STRENGTH_MAP` para P0/P2/P3,
    `_TACTICAL_STRENGTH_MAP` para P1/P4 — deliberado, es la propuesta
    alternativa que se evalúa, no el sistema en producción). Bias macro =
    suma capas EFFICIENCY, dirección táctica = suma capas TACTICAL; ejecuta
    solo en las 4 combinaciones de confluencia (1.0 si concuerdan, 0.5 en
    entorno choppy con dirección).

    Devuelve (df_detalle, resumen_dict) — el DataFrame detalle incluye
    `executed`/`proposed_direction`/`allocation_scale` para poder
    encadenarlo a `evaluate_real_r_by_filter`.
    """
    working = df[df[compliance_col].notna()].copy()
    working = reconstruct_execution_outcome(working, edge_col=edge_col, compliance_col=compliance_col)

    for i in efficiency_layers:
        working[f"p{i}_custom_score"] = _custom_layer_score(
            working[dir_cols[i]], working[str_cols[i]], _EFFICIENCY_STRENGTH_MAP
        )
    for i in tactical_layers:
        working[f"p{i}_custom_score"] = _custom_layer_score(
            working[dir_cols[i]], working[str_cols[i]], _TACTICAL_STRENGTH_MAP
        )

    working["calc_edge_eff"] = sum(working[f"p{i}_custom_score"] for i in efficiency_layers)
    working["market_bias"] = np.select(
        [working["calc_edge_eff"] > 0, working["calc_edge_eff"] < 0],
        ["Bullish", "Bearish"],
        default="Choppy",
    )

    working["calc_edge_tac"] = sum(working[f"p{i}_custom_score"] for i in tactical_layers)
    working["tactical_direction"] = np.sign(working["calc_edge_tac"])

    conditions = [
        (working["market_bias"] == "Bullish") & (working["tactical_direction"] > 0),
        (working["market_bias"] == "Bearish") & (working["tactical_direction"] < 0),
        (working["market_bias"] == "Choppy") & (working["tactical_direction"] > 0),
        (working["market_bias"] == "Choppy") & (working["tactical_direction"] < 0),
    ]
    allocation_choices = [1.0, 1.0, 0.5, 0.5]
    direction_choices = [1.0, -1.0, 1.0, -1.0]

    working["executed"] = np.select(conditions, [1, 1, 1, 1], default=0)
    working["proposed_direction"] = np.select(conditions, direction_choices, default=0.0)
    working["allocation_scale"] = np.select(conditions, allocation_choices, default=0.0)

    executed = working[working["executed"] == 1].copy()
    if executed.empty:
        return working, {"status": "ZERO_TRADES", "message": "Filtros demasiado restrictivos."}

    executed["trade_result_flat"] = np.where(
        executed["proposed_direction"] == executed["true_outcome"], 1, -1
    )
    executed["trade_result_scaled"] = executed["trade_result_flat"] * executed["allocation_scale"]

    wins = executed[executed["trade_result_flat"] == 1]
    losses = executed[executed["trade_result_flat"] == -1]
    gross_win_r = executed.loc[executed["trade_result_flat"] == 1, "trade_result_scaled"].sum()
    gross_loss_r = abs(executed.loc[executed["trade_result_flat"] == -1, "trade_result_scaled"].sum())

    summary = {
        "status": "OK",
        "metricas_departamento_partido": {
            "trades_totales_propuestos": len(executed),
            "trades_confluencia_total_1r": int((executed["allocation_scale"] == 1.0).sum()),
            "trades_entorno_choppy_0_5r": int((executed["allocation_scale"] == 0.5).sum()),
            "win_rate_percent": round((len(wins) / len(executed)) * 100, 1),
            "net_profit_r_final": round(float(gross_win_r - gross_loss_r), 2),
            "profit_factor": round(
                float(gross_win_r / gross_loss_r) if gross_loss_r > 0 else float(gross_win_r), 2
            ),
        },
    }
    return working, summary


def evaluate_real_r_by_filter(df: pd.DataFrame, filter_col: str, r_col: str = "r_multiple") -> dict:
    """
    A diferencia de las funciones anteriores (proxy direccional ±1), esta
    mide el R-multiple REALMENTE realizado (`tactical_audit.r_multiple`,
    Fase 0) para los trades que `filter_col` marca como ejecutados/pasan el
    filtro, comparado contra el total de la muestra. Solo tiene sentido
    sobre trades que YA se ejecutaron de verdad — no puede inferir el R de
    un trade contrafactual que nunca se tomó, por eso excluye (no imputa a
    0) las filas con `r_col` NULL y reporta cuántas se excluyeron.
    """
    total_n = len(df)
    total_valid = df[df[r_col].notna()]
    total_excluded = total_n - len(total_valid)

    passed = df[(df[filter_col] == 1) | (df[filter_col] == True)]  # noqa: E712
    passed_valid = passed[passed[r_col].notna()]
    passed_excluded = len(passed) - len(passed_valid)

    return {
        "muestra_total": total_n,
        "muestra_total_r_valido": len(total_valid),
        "muestra_total_excluida_por_r_null": int(total_excluded),
        "r_multiple_sum_total": round(float(total_valid[r_col].sum()), 2) if len(total_valid) else np.nan,
        "r_multiple_avg_total": round(float(total_valid[r_col].mean()), 3) if len(total_valid) else np.nan,
        "trades_que_pasan_filtro": len(passed),
        "trades_que_pasan_filtro_r_valido": len(passed_valid),
        "trades_que_pasan_filtro_excluidos_por_r_null": int(passed_excluded),
        "r_multiple_sum_filtro": round(float(passed_valid[r_col].sum()), 2) if len(passed_valid) else np.nan,
        "r_multiple_avg_filtro": round(float(passed_valid[r_col].mean()), 3) if len(passed_valid) else np.nan,
    }
