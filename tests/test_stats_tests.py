"""
core/stats_tests.py — valores de referencia.

Los números esperados son los que se calcularon a mano (math.comb) durante
la investigación del edge (2026-09-22) y se citan en el plan y el reporte.
Si alguno cambia, cambia una conclusión ya comunicada.
"""
import math

import pytest

from core.stats_tests import (
    binomial_test_two_sided,
    bootstrap_mean_ci,
    holm_adjust,
    mcnemar_exact,
    min_detectable_gap,
    n_required_one_sample,
    pearson_r,
    permutation_corr_test,
    wilson_interval,
)


@pytest.mark.parametrize("k,n,esperado", [
    (22, 36, 0.243),   # edge vs azar, reloj con el bug
    (24, 36, 0.065),   # edge vs azar, reloj corregido
    (18, 36, 1.000),   # exactamente la mitad
    (36, 36, 0.000),
])
def test_binomial_dos_colas_valores_de_referencia(k, n, esperado):
    assert binomial_test_two_sided(k, n) == pytest.approx(esperado, abs=5e-4)


def test_binomial_es_simetrico_con_p_medio():
    assert binomial_test_two_sided(10, 30) == pytest.approx(binomial_test_two_sided(20, 30))


def test_binomial_con_p_distinto_de_medio():
    # P(X=0 | n=10, p=0.1) = 0.3487 es el valor más probable: p-valor 1
    assert binomial_test_two_sided(1, 10, 0.1) == pytest.approx(1.0)
    assert binomial_test_two_sided(6, 10, 0.1) < 0.001


def test_binomial_rechaza_k_fuera_de_rango():
    with pytest.raises(ValueError):
        binomial_test_two_sided(11, 10)


@pytest.mark.parametrize("b,c,esperado", [
    (4, 9, 0.267),    # edge vs "siempre short", reloj con el bug
    (7, 5, 0.774),    # edge vs tendencia 1D, reloj con el bug
    (8, 4, 0.388),    # edge vs tendencia 1D, reloj corregido
    (0, 0, 1.000),
])
def test_mcnemar_exacto_valores_de_referencia(b, c, esperado):
    assert mcnemar_exact(b, c) == pytest.approx(esperado, abs=5e-4)


def test_mcnemar_es_simetrico():
    assert mcnemar_exact(3, 11) == mcnemar_exact(11, 3)


def test_wilson_valor_de_referencia():
    lo, hi = wilson_interval(22, 36)
    assert lo == pytest.approx(0.449, abs=1e-3)
    assert hi == pytest.approx(0.752, abs=1e-3)


def test_wilson_contiene_la_proporcion_y_no_se_sale_de_0_1():
    for k, n in ((0, 10), (10, 10), (3, 7)):
        lo, hi = wilson_interval(k, n)
        assert 0 <= lo <= k / n <= hi <= 1


def test_wilson_sin_muestra_es_nan():
    assert all(math.isnan(v) for v in wilson_interval(0, 0))


def test_holm_ejemplo_conocido():
    assert holm_adjust([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])


def test_holm_nunca_baja_un_p_ni_pasa_de_uno():
    ps = [0.2, 0.001, 0.9, 0.04]
    adj = holm_adjust(ps)
    assert all(a >= p for a, p in zip(adj, ps))
    assert all(a <= 1 for a in adj)


def test_bootstrap_es_reproducible_y_contiene_la_media():
    datos = [1.9, -1.0, -1.0, 0.5, 2.2, -1.0, 1.1, -0.4]
    ci1 = bootstrap_mean_ci(datos, n_boot=2000, seed=7)
    ci2 = bootstrap_mean_ci(datos, n_boot=2000, seed=7)
    assert ci1 == ci2
    media = sum(datos) / len(datos)
    assert ci1[0] < media < ci1[1]


def test_bootstrap_semilla_distinta_cambia_el_resultado():
    datos = [1.9, -1.0, -1.0, 0.5, 2.2, -1.0, 1.1, -0.4]
    assert bootstrap_mean_ci(datos, n_boot=500, seed=1) != bootstrap_mean_ci(datos, n_boot=500, seed=2)


def test_permutacion_detecta_correlacion_fuerte():
    x = list(range(40))
    y = [1.0 if i >= 20 else 0.0 for i in x]
    r, p = permutation_corr_test(x, y, n_perm=2000, seed=1)
    assert r > 0.8 and p < 0.01


def test_permutacion_sin_variacion_da_p_uno():
    r, p = permutation_corr_test([1, 2, 3, 4], [1, 1, 1, 1], n_perm=100, seed=1)
    assert math.isnan(r) and p == 1.0


def test_permutacion_es_reproducible():
    x = [0.3, 0.5, 0.8, 0.4, 0.6, 0.35, 0.7]
    y = [0, 1, 1, 0, 1, 0, 0]
    assert permutation_corr_test(x, y, 500, 3) == permutation_corr_test(x, y, 500, 3)
    assert permutation_corr_test(x, y, 500, 3)[0] == pytest.approx(pearson_r(x, y))


def test_n_requerido_valores_de_referencia():
    assert n_required_one_sample(0.5, 0.61) == 160
    assert n_required_one_sample(0.5, 0.667) == 68
    assert n_required_one_sample(0.5, 0.5) is None


def test_brecha_detectable_valor_de_referencia():
    assert min_detectable_gap(36, 0.61) == pytest.approx(0.228, abs=1e-3)
    assert min_detectable_gap(0, 0.5) == math.inf


# --------------------------------------------------------------------------
# net_score / permutation_max_net_test (verificación de sobreajuste)
# --------------------------------------------------------------------------

import numpy as np  # noqa: E402

from core.stats_tests import net_score, permutation_max_net_test  # noqa: E402


def test_net_score_ignora_abstenciones():
    truth = np.array([1, -1, 1, -1])
    assert net_score(np.array([1, -1, 0, 1]), truth).tolist() == [1]   # 2 aciertos, 1 fallo
    assert net_score(np.array([[1, 1, 1, 1], [0, 0, 0, 0]]), truth).tolist() == [0, 0]


def test_permutacion_detecta_un_predictor_con_senal_aunque_se_elija_entre_ruido():
    rng = np.random.default_rng(0)
    truth = rng.choice([-1, 1], size=80)
    ruido = rng.choice([-1, 0, 1], size=(6, 80))
    bueno = np.where(rng.random(80) < 0.85, truth, -truth)
    obs, p, _ = permutation_max_net_test(np.vstack([ruido, bueno]), truth, n_perm=2000, seed=1)
    assert p < 0.01


def test_permutacion_no_premia_elegir_el_mejor_entre_puro_ruido():
    rng = np.random.default_rng(5)
    truth = rng.choice([-1, 1], size=80)
    ruido = rng.choice([-1, 0, 1], size=(7, 80))
    _, p, _ = permutation_max_net_test(ruido, truth, n_perm=2000, seed=2)
    assert p > 0.05


def test_predictor_constante_nunca_da_senal_aunque_el_periodo_sea_direccional():
    """'Siempre short' en un período 75% bajista acierta mucho, pero no sabe nada."""
    truth = np.array([-1] * 60 + [1] * 20)
    siempre_short = -np.ones(80)
    obs, p, null = permutation_max_net_test(siempre_short, truth, n_perm=500, seed=3)
    assert obs == 40
    assert np.all(null == 40), "mezclar la verdad no cambia el neto de un predictor constante"
    assert p == 1.0


def test_permutacion_es_reproducible():
    rng = np.random.default_rng(9)
    truth = rng.choice([-1, 1], size=40)
    preds = rng.choice([-1, 0, 1], size=(3, 40))
    a = permutation_max_net_test(preds, truth, n_perm=300, seed=4)
    b = permutation_max_net_test(preds, truth, n_perm=300, seed=4)
    assert a[0] == b[0] and a[1] == b[1] and np.array_equal(a[2], b[2])
