import numpy as np
import pandas as pd
import pytest

from core.backtest_engine import (
    baseline_legacy_metrics,
    evaluate_real_r_by_filter,
    reconstruct_execution_outcome,
    run_department_confluence_backtest,
    run_icd_backtest,
    sweep_icd_thresholds,
)


def test_reconstruct_execution_outcome_covers_all_compliance_values():
    df = pd.DataFrame([
        dict(calc_edge=0.5, compliance="Edge_valid"),
        dict(calc_edge=-0.3, compliance="Edge_valid"),
        dict(calc_edge=0.4, compliance="Invalid_edge"),
        dict(calc_edge=-0.2, compliance="Invalid_edge"),
        dict(calc_edge=0.6, compliance="No_edge"),
    ])
    out = reconstruct_execution_outcome(df)
    assert out["true_outcome"].tolist() == [1.0, -1.0, -1.0, 1.0, 0.0]


def _icd_df():
    return pd.DataFrame([
        # icd = 0.55 >= 0.5 -> executa; Edge_valid, calc_edge>0 -> true_outcome=+1; proposed=+1 -> win
        dict(p0_score=2, p1_score=2, calc_edge=0.5, compliance="Edge_valid"),
        # icd = -0.55 -> executa; Edge_valid, calc_edge<0 -> true_outcome=-1; proposed=-1 -> win
        dict(p0_score=-2, p1_score=-2, calc_edge=-0.4, compliance="Edge_valid"),
        # icd = 0.55 -> executa; Invalid_edge invierte true_outcome a -1; proposed=+1 -> loss
        dict(p0_score=2, p1_score=2, calc_edge=0.3, compliance="Invalid_edge"),
        # icd = 0.15 -> por debajo de threshold 0.5, no ejecuta (Invalid_edge evitado)
        dict(p0_score=1, p1_score=0, calc_edge=-0.2, compliance="Invalid_edge"),
        # No_edge: cuenta en muestra_analizada pero no en trades_totales_legacy
        dict(p0_score=0, p1_score=0, calc_edge=0.1, compliance="No_edge"),
    ])


_ICD_KWARGS = dict(score_cols=["p0_score", "p1_score"], weights={"p0_score": 0.30, "p1_score": 0.25})


def test_run_icd_backtest_filters_by_threshold_and_scores_win_loss():
    detail, res = run_icd_backtest(_icd_df(), threshold=0.5, **_ICD_KWARGS)
    assert res["status"] == "OK"
    assert detail["proposed_signal"].tolist() == [1, 1, 1, 0, 0]
    assert res["metricas_ejecucion"] == {
        "muestra_analizada": 5,
        "trades_totales_legacy": 4,
        "trades_totales_propuestos": 3,
        "frecuencia_operativa_ratio": 0.75,
        "trades_invalidos_evitados": 1,
    }
    assert res["metricas_rendimiento"] == {
        "win_rate_percent": 66.7,
        "net_profit_r_escalado": 0.55,
        "profit_factor_escalado": 2.0,
    }


def test_run_icd_backtest_execution_zero_when_threshold_unreachable():
    detail, res = run_icd_backtest(_icd_df(), threshold=0.9, **_ICD_KWARGS)
    assert res == {"status": "EXECUTION_ZERO", "message": "Umbral demasiado restrictivo."}
    assert (detail["proposed_signal"] == 0).all()


def test_sweep_icd_thresholds_returns_one_row_per_threshold():
    out = sweep_icd_thresholds(_icd_df(), thresholds=[0.1, 0.5, 0.9], **_ICD_KWARGS)
    assert out["threshold"].tolist() == [0.1, 0.5, 0.9]
    assert out["status"].tolist() == ["OK", "OK", "EXECUTION_ZERO"]

    row_01 = out[out["threshold"] == 0.1].iloc[0]
    assert row_01["trades_totales_propuestos"] == 4
    assert row_01["win_rate_percent"] == pytest.approx(75.0)
    assert row_01["net_profit_r_escalado"] == pytest.approx(0.7)
    assert row_01["profit_factor_escalado"] == pytest.approx(2.27)

    row_09 = out[out["threshold"] == 0.9].iloc[0]
    assert row_09["trades_totales_propuestos"] == 0
    assert np.isnan(row_09["win_rate_percent"])


def test_baseline_legacy_metrics_counts_all_edge_valid_and_invalid_as_executed():
    df = pd.DataFrame([
        dict(calc_edge=0.5, compliance="Edge_valid"),
        dict(calc_edge=-0.3, compliance="Edge_valid"),
        dict(calc_edge=0.4, compliance="Invalid_edge"),
        dict(calc_edge=0.2, compliance="No_edge"),
    ])
    res = baseline_legacy_metrics(df)
    assert res["status"] == "OK"
    assert res["metricas_ejecucion"]["trades_totales_propuestos"] == 3
    assert res["metricas_ejecucion"]["frecuencia_operativa_ratio"] == 1.0
    assert res["metricas_rendimiento"] == {
        "win_rate_percent": 66.7,
        "net_profit_r_escalado": 1.0,
        "profit_factor_escalado": 2.0,
    }


def _confluence_df():
    row_full_confluence = dict(
        p0_direction="Long", p0_strength="Strong",
        p1_direction="Long", p1_strength="Strong",
        p2_direction="Long", p2_strength="Strong",
        p3_direction="Long", p3_strength="Strong",
        p4_direction="Long", p4_strength="Strong",
        calc_edge=0.5, compliance="Edge_valid",
    )
    row_choppy = dict(
        p0_direction="Long", p0_strength="Weak",
        p1_direction="Long", p1_strength="Mid",
        p2_direction="Short", p2_strength="Weak",
        p3_direction="Neutral", p3_strength="Strong",
        p4_direction="Long", p4_strength="Mid",
        calc_edge=-0.2, compliance="Invalid_edge",
    )
    row_disagreement = dict(
        p0_direction="Long", p0_strength="Strong",
        p1_direction="Short", p1_strength="Strong",
        p2_direction="Long", p2_strength="Strong",
        p3_direction="Long", p3_strength="Strong",
        p4_direction="Short", p4_strength="Strong",
        calc_edge=0.3, compliance="Edge_valid",
    )
    return pd.DataFrame([row_full_confluence, row_choppy, row_disagreement])


def test_run_department_confluence_backtest_full_choppy_and_disagreement():
    detail, summary = run_department_confluence_backtest(_confluence_df())

    assert detail["market_bias"].tolist() == ["Bullish", "Choppy", "Bullish"]
    assert detail["executed"].tolist() == [1, 1, 0]
    assert detail["allocation_scale"].tolist() == [1.0, 0.5, 0.0]

    assert summary["status"] == "OK"
    assert summary["metricas_departamento_partido"] == {
        "trades_totales_propuestos": 2,
        "trades_confluencia_total_1r": 1,
        "trades_entorno_choppy_0_5r": 1,
        "win_rate_percent": 100.0,
        "net_profit_r_final": 1.5,
        "profit_factor": 1.5,
    }


def test_run_department_confluence_backtest_zero_trades():
    df = _confluence_df().iloc[[2]]  # solo la fila de desacuerdo
    detail, summary = run_department_confluence_backtest(df)
    assert summary == {"status": "ZERO_TRADES", "message": "Filtros demasiado restrictivos."}


def test_evaluate_real_r_by_filter_chains_onto_run_icd_backtest_detail():
    df = _icd_df().copy()
    df["r_multiple"] = [1.0, 2.0, None, -1.0, 0.5]
    detail, _ = run_icd_backtest(df, threshold=0.5, **_ICD_KWARGS)
    res = evaluate_real_r_by_filter(detail, filter_col="proposed_signal")
    assert res["trades_que_pasan_filtro"] == 3
    assert res["trades_que_pasan_filtro_excluidos_por_r_null"] == 1
    assert res["r_multiple_sum_filtro"] == 3.0  # 1.0 + 2.0, la fila None se excluye


def test_evaluate_real_r_by_filter_excludes_nulls_instead_of_treating_as_zero():
    df = pd.DataFrame([
        dict(executed=1, r_multiple=2.0),
        dict(executed=1, r_multiple=None),
        dict(executed=0, r_multiple=-1.0),
        dict(executed=1, r_multiple=1.5),
        dict(executed=0, r_multiple=None),
    ])
    res = evaluate_real_r_by_filter(df, filter_col="executed")
    assert res == {
        "muestra_total": 5,
        "muestra_total_r_valido": 3,
        "muestra_total_excluida_por_r_null": 2,
        "r_multiple_sum_total": 2.5,
        "r_multiple_avg_total": 0.833,
        "trades_que_pasan_filtro": 3,
        "trades_que_pasan_filtro_r_valido": 2,
        "trades_que_pasan_filtro_excluidos_por_r_null": 1,
        "r_multiple_sum_filtro": 3.5,
        "r_multiple_avg_filtro": 1.75,
    }
