"""
tools/edge_evaluation.py — preguntas 1-3, baselines y reporte.

Sin CSV ni MT5: las filas se construyen a mano (EvalRow) o con un provider
falso que registra qué timestamps se le pidieron, para verificar el candado
point-in-time. Sin DB real.
"""
from datetime import datetime, timedelta
from itertools import product

import numpy as np
import pandas as pd
import pytest

from cli.main import determine_market_bias
from core.math_engine import calculate_edge_score
from tools.edge_evaluation import (
    BIAS_THRESHOLD,
    CURRENT_WEIGHTS,
    EQUAL_WEIGHTS,
    LAYERS,
    MOMENTUM_BARS,
    P2_ONLY_WEIGHTS,
    AccountData,
    EvalRow,
    ExecutionGap,
    discordant_pairs,
    execution_gap,
    executed_frame,
    grid_index,
    load_account,
    months_spanned,
    point_in_time_baselines,
    power_projection,
    render_report,
    run_question_1,
    run_question_2,
    run_question_3,
    select_best,
    simplex_grid,
    walk_forward_folds,
    weight_contributions,
)
from tools.p2_backtest import TFIndicatorSnapshot

T0 = datetime(2026, 6, 1, 8, 0)


def _row(i, edge_dir="long", gt="long", calc_edge=None, b1=None, scores=None, account="001_xauusd",
         anchor_source="tactical_audit", r_multiple=None, order_filled=None, thesis_dir="long",
         trade_dir=None, gt_incompleto=False, asset="XAUUSDT.P"):
    if calc_edge is None:
        calc_edge = {"long": 0.5, "short": -0.5, None: 0.1}[edge_dir]
    bias = determine_market_bias(calc_edge)
    return EvalRow(
        account=account, asset=asset, trade_id=f"t{i:03d}", anchor=T0 + timedelta(days=i),
        anchor_source=anchor_source, ref_price=100.0, calc_edge=calc_edge, edge_bias=bias,
        edge_dir={"Bullish": "long", "Bearish": "short"}.get(bias), scores=scores or {},
        thesis_dir=thesis_dir, gt_thesis=None if gt_incompleto else gt, gt_incompleto=gt_incompleto,
        baselines={"B1": b1, "B2": None, "B3": None},
        order_filled=order_filled, r_multiple=r_multiple, trade_dir=trade_dir,
    )


# --------------------------------------------------------------------------
# Sincronía con producción
# --------------------------------------------------------------------------

def test_pesos_actuales_coinciden_con_calculate_edge_score():
    for i, layer in enumerate(LAYERS):
        unit = [0, 0, 0, 0, 0]
        unit[i] = 2
        assert calculate_edge_score(*unit) == pytest.approx(CURRENT_WEIGHTS[i]), layer


def test_umbral_coincide_con_determine_market_bias():
    assert determine_market_bias(BIAS_THRESHOLD) == "Bullish"
    assert determine_market_bias(-BIAS_THRESHOLD) == "Bearish"
    assert determine_market_bias(BIAS_THRESHOLD - 0.001) == "Choppy / Neutral"


def test_weight_contributions_reproduce_determine_market_bias():
    """Con los pesos actuales, la grilla debe decidir igual que producción."""
    grid = simplex_grid()
    idx = grid_index(grid, CURRENT_WEIGHTS)
    rng = np.random.default_rng(0)
    X = rng.integers(-2, 3, size=(200, 5)).astype(float)
    y = np.ones(200)
    got = weight_contributions(X, y, grid)[:, idx]
    for xi, g in zip(X, got):
        bias = determine_market_bias(calculate_edge_score(*xi))
        assert g == {"Bullish": 1, "Bearish": -1, "Choppy / Neutral": 0}[bias]


# --------------------------------------------------------------------------
# Grilla, walk-forward y selección
# --------------------------------------------------------------------------

def test_grilla_del_simplex():
    grid = simplex_grid()
    assert grid.shape == (10626, 5)
    assert np.allclose(grid.sum(axis=1), 1.0)
    assert (grid >= 0).all()
    for w in (CURRENT_WEIGHTS, EQUAL_WEIGHTS, P2_ONLY_WEIGHTS):
        grid_index(grid, w)


def test_walk_forward_sin_fuga_de_datos():
    folds = walk_forward_folds(47, initial=20, block=10)
    assert [len(t) for _, t in folds] == [10, 10, 7]
    for train, test in folds:
        assert max(train) < min(test), "ningún trade de test puede estar en su entrenamiento"
        assert min(train) == 0, "la ventana es expansiva"
    assert max(folds[-1][1]) == 46, "cubre hasta el último trade"


def test_empates_se_resuelven_hacia_los_pesos_actuales():
    grid = simplex_grid()
    assert select_best(np.zeros(len(grid)), grid, CURRENT_WEIGHTS) == grid_index(grid, CURRENT_WEIGHTS)


def test_pregunta_2_encuentra_una_senal_plantada():
    """
    Solo P4 predice la dirección; P0-P3 son ruido que empuja al edge actual
    al revés. El óptimo fuera de muestra debe cargar peso en P4 y ganarle a
    los pesos actuales en walk-forward.
    """
    rng = np.random.default_rng(42)
    rows = []
    for i in range(80):
        gt = "long" if rng.random() < 0.5 else "short"
        s = 1 if gt == "long" else -1
        scores = {"P0": -2 * s, "P1": int(rng.integers(-2, 3)), "P2": int(rng.integers(-2, 3)),
                  "P3": int(rng.integers(-2, 3)), "P4": 2 * s}
        rows.append(_row(i, gt=gt, scores=scores))
    study = run_question_2(rows)
    assert study is not None
    assert study.in_sample_best[LAYERS.index("P4")] >= 0.5
    assert study.wf["óptimo"].accuracy > study.wf["actuales"].accuracy
    assert study.signal is True


def test_pregunta_2_sin_muestra_suficiente_devuelve_none():
    rows = [_row(i, scores={k: 1 for k in LAYERS}) for i in range(5)]
    assert run_question_2(rows) is None


# --------------------------------------------------------------------------
# Baselines point-in-time
# --------------------------------------------------------------------------

class RecordingProvider:
    def __init__(self, price=110.0, ema200=100.0, closes=None):
        self.price, self.ema200 = price, ema200
        self.closes = closes if closes is not None else [100.0 + i for i in range(MOMENTUM_BARS + 1)]
        self.calls = []

    def get_indicator_snapshot(self, tf, as_of):
        self.calls.append(("snapshot", tf, as_of))
        return TFIndicatorSnapshot(price=self.price, ema20=self.price, ema200=self.ema200,
                                   adx14=30, bars_available=900)

    def get_past_closes(self, tf, as_of, n):
        self.calls.append(("closes", tf, as_of))
        return self.closes[-n:]


def test_baselines_piden_datos_exactamente_en_el_anchor():
    """El provider filtra time < as_of; pedir más tarde que el anchor sería mirar el futuro."""
    prov = RecordingProvider()
    point_in_time_baselines(prov, T0)
    assert prov.calls, "debió consultar al provider"
    assert all(as_of == T0 for _, _, as_of in prov.calls)


def test_baselines_de_tendencia_y_momentum():
    arriba = point_in_time_baselines(RecordingProvider(price=110, ema200=100), T0)
    abajo = point_in_time_baselines(
        RecordingProvider(price=90, ema200=100, closes=[120.0 - i for i in range(MOMENTUM_BARS + 1)]), T0)
    assert arriba == {"B1": "long", "B2": "long", "B3": "long"}
    assert abajo == {"B1": "short", "B2": "short", "B3": "short"}


def test_momentum_sin_historia_suficiente_se_abstiene():
    out = point_in_time_baselines(RecordingProvider(closes=[100.0, 101.0]), T0)
    assert out["B3"] is None


# --------------------------------------------------------------------------
# Pregunta 1
# --------------------------------------------------------------------------

def test_pregunta_1_estadisticos_y_holm():
    rows = []
    for i in range(20):                       # edge acierta 16/20
        gt = "long" if i % 2 else "short"
        edge = gt if i < 16 else ("short" if gt == "long" else "long")
        b1 = "long"                           # la tendencia siempre dice long
        rows.append(_row(i, edge_dir=edge, gt=gt, b1=b1,
                         calc_edge=(0.6 if edge == "long" else -0.6) * (1 if i < 16 else 0.5)))
    q = {t.key: t for t in run_question_1(rows, seed=1)}
    assert q["1a"].statistic.startswith("16/20")
    assert q["1a"].p_value == pytest.approx(0.0118, abs=1e-3)
    b, c, n = discordant_pairs(rows, "B1")
    assert (b, c, n) == (8, 2, 20)
    assert all(t.p_holm is not None and t.p_holm >= t.p_value for t in q.values())
    assert q["1c"].direction_ok, "los aciertos tienen |edge| mayor que los fallos"


def test_abstenciones_e_incompletos_no_cuentan_como_apuestas():
    rows = [_row(0, edge_dir="long", gt="long"),
            _row(1, edge_dir=None, gt="long"),
            _row(2, edge_dir="long", gt_incompleto=True)]
    assert run_question_1(rows, seed=1)[0].statistic.startswith("1/1")


# --------------------------------------------------------------------------
# Pregunta 3 y brecha de ejecución
# --------------------------------------------------------------------------

def _executed_rows():
    specs = [  # (r, gt_thesis, thesis_dir, trade_dir)
        (+2.0, "long", "long", "long"),    # tesis confirmada, ganó
        (-1.0, "long", "long", "long"),    # tesis confirmada, perdió -> stop antes
        (+1.0, "short", "long", "long"),   # tesis invalidada, ganó -> TP antes
        (-1.0, "short", "long", "long"),   # tesis invalidada, perdió
        (-1.0, "short", "long", "long"),
    ]
    return [_row(i, gt=g, thesis_dir=t, trade_dir=d, r_multiple=r, order_filled=True)
            for i, (r, g, t, d) in enumerate(specs)]


def test_executed_frame_excluye_no_llenados_y_mark_price():
    rows = _executed_rows() + [
        _row(10, r_multiple=3.0, order_filled=False),
        _row(11, r_multiple=None, order_filled=True),
        _row(12, anchor_source="mark_price", r_multiple=5.0, order_filled=True),
    ]
    assert len(executed_frame(rows)) == 5


def test_pregunta_3_expectancy_y_profit_factor():
    q3 = run_question_3(executed_frame(_executed_rows()), seed=1)
    assert q3.n == 5
    assert q3.expectancy == pytest.approx(0.0)
    assert q3.profit_factor == pytest.approx(1.0)
    assert q3.ci[0] < 0 < q3.ci[1]
    assert q3.signal is False


def test_brecha_de_ejecucion():
    gap = execution_gap(executed_frame(_executed_rows()))
    assert (gap.confirmed_won, gap.confirmed_lost, gap.invalidated_won, gap.invalidated_lost) == (1, 1, 1, 2)
    # Dirección del mercado según el trade vs según la tesis: discrepan
    # exactamente en los dos casos de ejecución (stop antes / TP antes).
    assert (gap.direction_agree, gap.direction_disagree) == (3, 2)


# --------------------------------------------------------------------------
# Potencia
# --------------------------------------------------------------------------

def test_meses_abarcados():
    assert months_spanned([T0, T0 + timedelta(days=61)]) == pytest.approx(2.0, abs=0.01)
    assert months_spanned([T0]) == 1.0


def test_proyeccion_de_potencia():
    rows = [_row(i, edge_dir="long", gt="long" if i < 14 else "short", b1="short") for i in range(20)]
    lines = {pl.question: pl for pl in power_projection(rows, pd.DataFrame())}
    pl = lines["1a (vs azar)"]
    assert pl.n_now == 20 and pl.observed.startswith("70.0%")
    assert pl.n_needed is not None and pl.months_more is not None


# --------------------------------------------------------------------------
# Reporte
# --------------------------------------------------------------------------

def _synthetic_accounts():
    rows = []
    rng = np.random.default_rng(3)
    for i in range(60):
        gt = "long" if rng.random() < 0.5 else "short"
        s = 1 if gt == "long" else -1
        scores = {k: int(rng.integers(-2, 3)) for k in LAYERS}
        scores["P4"] = 2 * s if i % 3 else -2 * s
        edge = calculate_edge_score(*(scores[k] for k in LAYERS))
        r = _row(i, gt=gt, calc_edge=edge, scores=scores, b1="long",
                 r_multiple=float(rng.normal(0.2, 1.2)) if i % 2 else None,
                 order_filled=True if i % 2 else None, trade_dir="long", thesis_dir="long")
        rows.append(r)
    return [AccountData(label="001_xauusd", db_path="x.db", ohlc_dir="/tmp", rows=rows)]


def test_reporte_reproducible_con_la_misma_semilla():
    acc = _synthetic_accounts()
    a = render_report(acc, seed=11, generated_at=T0)
    b = render_report(acc, seed=11, generated_at=T0)
    assert a == b


def _broken_tables(markdown):
    """Tablas cuyas filas no tienen todas la misma cantidad de columnas."""
    broken, checked, tabla = [], 0, []
    for line in markdown.splitlines() + [""]:
        if line.startswith("|"):
            tabla.append(line.replace("\\|", ""))
            continue
        if tabla:
            checked += 1
            if len({t.count("|") for t in tabla}) != 1:
                broken.append("\n".join(tabla))
            tabla = []
    return broken, checked


def test_el_detector_de_tablas_rotas_funciona():
    ok = "| a | b |\n|---|---|\n| 1 | 2 |\n"
    rota = "| a | b |\n|---|---|\n| r = +0.1 entre |edge| y acierto | 2 |\n"
    escapada = "| a | b |\n|---|---|\n| entre \\|edge\\| | 2 |\n"
    assert _broken_tables(ok) == ([], 1)
    assert len(_broken_tables(rota)[0]) == 1
    assert _broken_tables(escapada) == ([], 1)


def test_ninguna_tabla_del_reporte_queda_rota():
    """Regresión: un '|' sin escapar dentro de una celda parte la fila en más columnas."""
    broken, checked = _broken_tables(render_report(_synthetic_accounts(), seed=11, generated_at=T0))
    assert checked >= 8, f"se esperaban al menos 8 tablas, se revisaron {checked}"
    assert not broken, "tablas rotas:\n\n" + "\n\n".join(broken)


def test_reporte_marca_el_baseline_con_retrospectiva():
    report = render_report(_synthetic_accounts(), seed=11, generated_at=T0)
    assert "retrospectiva" in report
    assert "La proyección es optimista" in report


def test_cuenta_sin_velas_se_omite_sin_romper(tmp_path):
    acc = load_account(str(tmp_path / "flight_account_009_zzz.db"), str(tmp_path / "no_existe"), True)
    assert acc.skipped and "no existe" in acc.skipped
    report = render_report([acc] + _synthetic_accounts(), seed=1, generated_at=T0)
    assert "omitida" in report


# --------------------------------------------------------------------------
# DB de cuenta con esquema viejo (regresión 2026-09-22, cuenta US100)
# --------------------------------------------------------------------------

def test_db_sin_migrar_se_lee_sin_error(tmp_path):
    """
    US100 no se abría en el CLI desde antes de la migración aditiva del
    2026-09-16 y no tenía stop_slippage_r & cía. Pedir la entidad completa
    reventaba con "no such column". El pipeline es de solo lectura: tiene
    que funcionar sobre el esquema que haya, sin migrar.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from tools.database import AnalysisLayer, Base, TacticalAudit, UnifiedDepartment
    from tools.edge_evaluation import build_account_rows

    engine = create_engine(f"sqlite:///{tmp_path / 'viejo.db'}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(UnifiedDepartment(
        id="a1", state="READY_FOR_NOTION", asset="US100", market_bias="Bullish", calc_edge=0.5,
        p4_hierarchy="x", p1_timeframe="15M", p1_type="x", nodes_l1=1, nodes_l2=1,
        tactical_classification="x", long_prob=0.5, short_prob=0.5, no_trade_prob=0.0,
        created_at=T0, edge_validation_price=110.0, structural_invalidation=90.0,
    ))
    for layer in LAYERS:
        s.add(AnalysisLayer(trade_id="a1", department="x", layer_name=layer, score=1))
    s.add(TacticalAudit(trade_id="a1", entry_time=T0, entry_price=100.0, stop_loss=95.0,
                        order_filled=True, r_multiple=1.5))
    s.commit()
    s.close()
    with engine.begin() as conn:
        for col in ("stop_slippage_r", "stop_deviation_reason", "stop_deviation_note",
                    "emotional_gate_override_reason"):
            conn.execute(text(f"ALTER TABLE tactical_audit DROP COLUMN {col}"))

    from core.p2_ground_truth import OhlcBar

    class Provider(RecordingProvider):
        def get_forward_path(self, as_of, max_bars=2160):
            return [OhlcBar(as_of + timedelta(hours=1), 111.0, 99.0)]  # toca la validación

    rows, exclusions = build_account_rows(sessionmaker(bind=engine)(), Provider(), "003_us100")
    assert not exclusions and len(rows) == 1
    r = rows[0]
    assert r.r_multiple == 1.5 and r.order_filled is True
    assert r.trade_dir == "long"
    assert r.stop_slippage_r is None, "sin la columna, queda None en vez de reventar"
    assert r.gt_thesis == "long"
