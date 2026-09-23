"""
P2_systematic — tests del registro de modelos (A..E).

Cada modelo es una variante declarativa (ModelSpec) de la misma mecánica.
Estos tests fijan lo que DIFERENCIA a cada uno: la cadena de EMAs, la
confirmación por DI, las temporalidades y los pesos. Sin CSV ni DB.
"""
import pytest

from tools.p2_backtest import (
    MIN_BARS_PER_TF,
    MODEL_A,
    MODEL_B,
    MODEL_C,
    MODEL_D,
    MODEL_E,
    MODEL_F,
    MODELS,
    MODELS_BY_NAME,
    TFIndicatorSnapshot,
    bias_from_emas,
    compute_score_p2_sistematico,
    confirm_with_di,
    strength_from_adx,
)


def _snap(price=110, ema20=105, ema100=102, ema200=100, adx=30,
          plus_di=30.0, minus_di=10.0, bars=MIN_BARS_PER_TF):
    return TFIndicatorSnapshot(price=price, ema20=ema20, ema200=ema200, adx14=adx,
                               bars_available=bars, ema100=ema100,
                               plus_di=plus_di, minus_di=minus_di)


# --------------------------------------------------------------------------
# Integridad del registro
# --------------------------------------------------------------------------

@pytest.mark.parametrize("model", MODELS, ids=[m.name for m in MODELS])
def test_pesos_suman_uno_y_cubren_sus_timeframes(model):
    assert abs(sum(model.weights.values()) - 1.0) < 1e-9
    assert set(model.weights) == set(model.timeframes)


@pytest.mark.parametrize("model", MODELS, ids=[m.name for m in MODELS])
def test_el_peso_mayor_es_unico(model):
    top = max(model.weights.values())
    assert sum(1 for w in model.weights.values() if w == top) == 1, \
        "debe haber una sola temporalidad dominante"


def test_modelo_a_conserva_los_pesos_originales():
    """A es la referencia: si cambia, todas las comparaciones previas se invalidan."""
    assert MODEL_A.weights == {"1W": 0.30, "1D": 0.25, "12H": 0.20, "4H": 0.15, "1H": 0.10}
    assert MODEL_A.ema_chain == (20, 200)
    assert MODEL_A.use_di is False


@pytest.mark.parametrize("model,esperado", [
    (MODEL_B, "12H"), (MODEL_C, "12H"), (MODEL_D, "1H"), (MODEL_E, "1H"), (MODEL_F, "1H"),
])
def test_el_pico_de_peso_esta_en_la_tf_del_punto_medio(model, esperado):
    assert max(model.weights, key=model.weights.get) == esperado


def test_escalera_de_temporalidades_por_modelo():
    assert MODEL_B.timeframes == MODEL_C.timeframes == MODEL_A.timeframes
    assert "30M" in MODEL_D.timeframes and "15M" not in MODEL_D.timeframes
    assert "30M" in MODEL_E.timeframes and "15M" in MODEL_E.timeframes


def test_b_c_d_e_usan_ema100_y_solo_c_d_e_usan_di():
    for m in (MODEL_B, MODEL_C, MODEL_D, MODEL_E):
        assert m.ema_chain == (20, 100, 200)
    assert MODEL_B.use_di is False
    assert all(m.use_di for m in (MODEL_C, MODEL_D, MODEL_E))


# --------------------------------------------------------------------------
# bias_from_emas — la diferencia A vs B
# --------------------------------------------------------------------------

def test_cadena_alcista_alineada_da_mas_uno():
    assert bias_from_emas(_snap(price=110, ema20=105, ema100=102, ema200=100), (20, 100, 200)) == 1


def test_cadena_bajista_alineada_da_menos_uno():
    assert bias_from_emas(_snap(price=90, ema20=95, ema100=98, ema200=100), (20, 100, 200)) == -1


def test_ema100_fuera_de_orden_rompe_la_cadena_de_b_pero_no_la_de_a():
    """El caso que distingue A de B: EMA100 desordenada entre EMA20 y EMA200."""
    snap = _snap(price=110, ema20=105, ema100=99, ema200=100)
    assert bias_from_emas(snap, (20, 200)) == 1, "A solo mira EMA20 y EMA200"
    assert bias_from_emas(snap, (20, 100, 200)) == 0, "B exige además la EMA100 en orden"


def test_falta_ema100_devuelve_none():
    snap = TFIndicatorSnapshot(price=110, ema20=105, ema200=100, adx14=30,
                               bars_available=MIN_BARS_PER_TF)
    assert bias_from_emas(snap, (20, 100, 200)) is None
    assert bias_from_emas(snap, (20, 200)) == 1


# --------------------------------------------------------------------------
# strength_from_adx / confirm_with_di
# --------------------------------------------------------------------------

@pytest.mark.parametrize("adx,esperado", [(30, 1.0), (25.1, 1.0), (25, 0.5), (20, 0.5), (19.9, 0.0)])
def test_umbrales_de_fuerza_del_adx(adx, esperado):
    assert strength_from_adx(adx) == esperado


def test_di_confirma_cuando_coincide_con_las_emas():
    assert confirm_with_di(1, _snap(plus_di=30, minus_di=10)) == 1
    assert confirm_with_di(-1, _snap(plus_di=10, minus_di=30)) == -1


def test_di_anula_el_bias_cuando_lo_contradice():
    """La diferencia B vs C: EMAs alcistas pero -DI domina -> no aporta."""
    assert confirm_with_di(1, _snap(plus_di=10, minus_di=30)) == 0
    assert confirm_with_di(-1, _snap(plus_di=30, minus_di=10)) == 0


def test_di_faltante_devuelve_none():
    snap = TFIndicatorSnapshot(price=110, ema20=105, ema200=100, adx14=30,
                               bars_available=MIN_BARS_PER_TF, ema100=102)
    assert confirm_with_di(1, snap) is None


# --------------------------------------------------------------------------
# compute_score_p2_sistematico por modelo
# --------------------------------------------------------------------------

def test_todas_las_tf_alcistas_y_fuertes_dan_uno_en_cualquier_modelo():
    for model in (MODEL_A, MODEL_B, MODEL_C, MODEL_D, MODEL_E, MODEL_F):
        snaps = {tf: _snap() for tf in model.timeframes}
        score, incompleto = compute_score_p2_sistematico(snaps, model)
        assert incompleto is False
        assert score == pytest.approx(1.0), f"modelo {model.name}"


def test_default_sigue_siendo_modelo_a():
    snaps = {tf: _snap() for tf in MODEL_A.timeframes}
    assert compute_score_p2_sistematico(snaps) == compute_score_p2_sistematico(snaps, MODEL_A)


def test_c_puntua_mas_bajo_que_b_cuando_los_di_contradicen():
    """Mismos datos: B suma, C anula por DI opuesto."""
    snaps = {tf: _snap(plus_di=10, minus_di=30) for tf in MODEL_B.timeframes}
    score_b, _ = compute_score_p2_sistematico(snaps, MODEL_B)
    score_c, _ = compute_score_p2_sistematico(snaps, MODEL_C)
    assert score_b == pytest.approx(1.0)
    assert score_c == pytest.approx(0.0)


def test_modelo_d_sin_snapshot_de_30m_queda_incompleto():
    snaps = {tf: _snap() for tf in MODEL_D.timeframes}
    snaps["30M"] = None
    assert compute_score_p2_sistematico(snaps, MODEL_D) == (None, True)


def test_modelo_e_sin_snapshot_de_15m_queda_incompleto():
    snaps = {tf: _snap() for tf in MODEL_E.timeframes}
    snaps["15M"] = None
    assert compute_score_p2_sistematico(snaps, MODEL_E) == (None, True)


def test_una_tf_con_pocas_barras_invalida_el_modelo_entero():
    snaps = {tf: _snap() for tf in MODEL_B.timeframes}
    snaps["4H"] = _snap(bars=MIN_BARS_PER_TF - 1)
    assert compute_score_p2_sistematico(snaps, MODEL_B) == (None, True)


def test_el_aporte_de_cada_tf_respeta_su_peso():
    """Solo la TF dominante en tendencia; el resto neutro -> score == su peso."""
    model = MODEL_D
    top_tf = max(model.weights, key=model.weights.get)
    snaps = {tf: _snap(price=100, ema20=100, ema100=100, ema200=100) for tf in model.timeframes}
    snaps[top_tf] = _snap()
    score, incompleto = compute_score_p2_sistematico(snaps, model)
    assert incompleto is False
    assert score == pytest.approx(model.weights[top_tf])


def test_registro_indexado_por_nombre_cubre_los_seis():
    assert set(MODELS_BY_NAME) == {"A", "B", "C", "D", "E", "F"}


def test_modelo_f_es_d_sin_30m_con_el_mismo_reparto_relativo():
    assert "30M" not in MODEL_F.timeframes
    assert MODEL_F.ema_chain == MODEL_D.ema_chain and MODEL_F.use_di == MODEL_D.use_di
    for tf in MODEL_F.timeframes:
        assert MODEL_F.weights[tf] / MODEL_F.weights["1H"] == pytest.approx(
            MODEL_D.weights[tf] / MODEL_D.weights["1H"])
