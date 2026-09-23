"""
core/stats_tests.py — tests estadísticos para muestras chicas, sin scipy.

Funciones puras (sin DB, sin I/O), mismo espíritu que core/math_engine.py.
El env de este repo no tiene scipy/statsmodels y el proyecto evita sumar
dependencias (ver tools/p2_backtest.py, ADX escrito a mano), así que todo
sale de `math` + `numpy`:

- Tests exactos (binomial, McNemar) vía math.comb: con n de decenas son
  exactos y baratos, y no dependen de aproximaciones normales que fallan
  justo en las muestras chicas de este proyecto.
- Remuestreo (bootstrap, permutación) con numpy.random.default_rng y semilla
  explícita: dos corridas con la misma semilla dan el mismo número.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import numpy as np

Z_95 = 1.959963984540054
Z_POWER_80 = 0.8416212335729143


def _binom_pmf(k: int, n: int, p: float) -> float:
    return math.comb(n, k) * p ** k * (1 - p) ** (n - k)


def binomial_test_two_sided(k: int, n: int, p: float = 0.5) -> float:
    """
    p-valor exacto de dos colas: suma de P(X=i) para todo i cuya
    probabilidad no supera la del observado. Es la definición estándar
    (la misma que usa scipy.stats.binomtest); con p=0.5 equivale a
    2 * min(P(X<=k), P(X>=k)).
    """
    if n == 0:
        return 1.0
    if not 0 <= k <= n:
        raise ValueError(f"k={k} fuera de [0, {n}]")
    observed = _binom_pmf(k, n, p)
    tol = observed * 1e-7
    total = sum(pr for pr in (_binom_pmf(i, n, p) for i in range(n + 1)) if pr <= observed + tol)
    return min(1.0, total)


def mcnemar_exact(b: int, c: int) -> float:
    """
    McNemar exacto sobre los pares discordantes: b = solo acertó A, c = solo
    acertó B. Bajo H0 cada discordante es una moneda al aire, así que es un
    binomial de dos colas con p=0.5 sobre b+c. Los pares donde ambos aciertan
    o ambos fallan no informan nada sobre cuál es mejor, y no entran.
    """
    return binomial_test_two_sided(min(b, c), b + c, 0.5)


def wilson_interval(k: int, n: int, z: float = Z_95) -> Tuple[float, float]:
    """IC de Wilson para una proporción. Se porta bien con n chico y p cerca de 0/1."""
    if n == 0:
        return (float("nan"), float("nan"))
    ph = k / n
    den = 1 + z * z / n
    center = (ph + z * z / (2 * n)) / den
    half = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    # En los extremos la cota es exactamente 0 o 1; la coma flotante daría
    # 0.9999999999999999 y el intervalo no contendría a p = 1.
    lo = 0.0 if k == 0 else max(0.0, center - half)
    hi = 1.0 if k == n else min(1.0, center + half)
    return (lo, hi)


def holm_adjust(pvalues: Sequence[float]) -> List[float]:
    """
    Corrección de Holm-Bonferroni: p ajustados, en el mismo orden de
    entrada. Controla la probabilidad de al menos un falso positivo entre
    las hipótesis probadas juntas; más potente que Bonferroni puro.
    """
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, (m - rank) * pvalues[i])
        adjusted[i] = min(1.0, running)
    return adjusted


def bootstrap_mean_ci(
    values: Sequence[float],
    n_boot: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> Tuple[float, float]:
    """IC percentil del promedio por bootstrap no paramétrico."""
    x = np.asarray(values, dtype=float)
    if x.size == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, x.size, size=(n_boot, x.size))].mean(axis=1)
    return (float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2)))


def pearson_r(x: Sequence[float], y: Sequence[float]) -> float:
    xa, ya = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if xa.size < 2 or xa.std() == 0 or ya.std() == 0:
        return float("nan")
    return float(np.corrcoef(xa, ya)[0, 1])


def permutation_corr_test(
    x: Sequence[float],
    y: Sequence[float],
    n_perm: int = 10_000,
    seed: int = 0,
) -> Tuple[float, float]:
    """
    (r, p de dos colas) para la correlación de Pearson, por permutación:
    cuántas veces una asignación al azar de y produce una |r| al menos tan
    grande como la observada. Con y binario (acierto 0/1) es la correlación
    punto-biserial. El +1 en numerador y denominador evita p=0 exacto.
    """
    xa, ya = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    r_obs = pearson_r(xa, ya)
    if math.isnan(r_obs):
        return (r_obs, 1.0)
    rng = np.random.default_rng(seed)
    xc = xa - xa.mean()
    perms = np.array([rng.permutation(ya) for _ in range(n_perm)])
    yc = perms - perms.mean(axis=1, keepdims=True)
    r_perm = (yc @ xc) / (np.sqrt((yc ** 2).sum(axis=1)) * np.sqrt((xc ** 2).sum()))
    extreme = int(np.sum(np.abs(r_perm) >= abs(r_obs) - 1e-12))
    return (r_obs, (extreme + 1) / (n_perm + 1))


def min_detectable_gap(n: int, p: float, z_alpha: float = Z_95, z_beta: float = Z_POWER_80) -> float:
    """
    Brecha mínima (en proporción) detectable con n observaciones, 5% de
    significancia y 80% de potencia, aproximación normal. Orientativo: dice
    si una pregunta tiene chance de responderse, no la responde.
    """
    if n <= 0:
        return float("inf")
    return (z_alpha + z_beta) * math.sqrt(p * (1 - p) / n)


def n_required_one_sample(p0: float, p1: float, z_alpha: float = Z_95, z_beta: float = Z_POWER_80) -> Optional[int]:
    """Observaciones necesarias para distinguir p1 de p0 (5% / 80%). None si p1 == p0."""
    if p1 == p0:
        return None
    num = z_alpha * math.sqrt(p0 * (1 - p0)) + z_beta * math.sqrt(p1 * (1 - p1))
    return math.ceil((num / (p1 - p0)) ** 2)


def net_score(predictions: np.ndarray, truth: np.ndarray) -> np.ndarray:
    """
    Aciertos menos fallos, ignorando abstenciones. predictions: +1/-1/0
    (0 = no opina), una fila por predictor o un vector; truth: +1/-1.
    """
    p = np.atleast_2d(np.asarray(predictions))
    t = np.asarray(truth)
    active = p != 0
    return (active & (p == t)).sum(axis=1) - (active & (p != t)).sum(axis=1)


def permutation_max_net_test(
    predictions: np.ndarray,
    truth: np.ndarray,
    n_perm: int = 10_000,
    seed: int = 0,
) -> Tuple[int, float, np.ndarray]:
    """
    ¿El MEJOR de varios predictores es mejor que el azar, sabiendo que se lo
    eligió después de mirar los datos? Mezcla la verdad al azar n_perm veces y,
    en cada mezcla, se queda con el mejor neto entre TODOS los predictores. El
    p-valor es la fracción de mezclas cuyo mejor neto iguala o supera al mejor
    observado (estadístico máximo, estilo Westfall-Young).

    Dos propiedades importan acá:
    - Corrige la selección: elegir el mejor de 7 variantes infla el resultado,
      y el azar también tiene 7 intentos por mezcla.
    - Respeta la tasa base: mezclar conserva la proporción long/short, así que
      un predictor que dice siempre lo mismo obtiene el mismo neto en todas las
      mezclas y nunca da señal, aunque el período haya sido muy direccional.

    Devuelve (mejor neto observado, p-valor, distribución nula del máximo).
    """
    p = np.atleast_2d(np.asarray(predictions))
    t = np.asarray(truth)
    observed = int(net_score(p, t).max())
    rng = np.random.default_rng(seed)
    null = np.empty(n_perm)
    for i in range(n_perm):
        null[i] = net_score(p, rng.permutation(t)).max()
    p_value = (int(np.sum(null >= observed)) + 1) / (n_perm + 1)
    return observed, p_value, null
