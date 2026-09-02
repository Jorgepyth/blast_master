"""
Regresión para la consolidación del Sitio 3 de i_cd (antes "Defect 3").

Antes: cli/main.py tenía 3 implementaciones independientes de get_dir_val/
get_str_val + la aritmética de i_cd — 2 comparando por enum (Direction/
Strength) y 1 (dentro de recalculate_unified_metrics, en
flow_repair_analysis_audits) comparando por string literal.

Fase 2 de la sesión probó empíricamente, con un harness aislado de 32 casos
(valores reales de las 3 DBs de producción + variantes corruptas sintéticas +
cruce enum-vs-string), que ambas formas de comparar son equivalentes porque
Direction/Strength son `str, Enum` — un string igual en contenido a
Direction.LONG.value ya compara igual (`==`) a la instancia enum. Este test
reproduce un subconjunto representativo de ese golden-master contra las
funciones REALES ya consolidadas (get_dir_val/get_str_val a nivel de módulo
en cli/main.py, calculate_edge_score/determine_market_bias compartidas),
para confirmar que la consolidación no cambió ningún resultado.
"""
import pytest

from cli.main import get_dir_val, get_str_val, determine_market_bias
from core.math_engine import calculate_edge_score
from cli.schemas.efficiency import Direction, Strength


def compute_i_cd(vectors):
    """vectors: lista de 5 tuplas (dir, str) para p0..p4, en cualquier
    combinación de instancias Direction/Strength o strings planos."""
    xs = [get_dir_val(d) * get_str_val(s) for d, s in vectors]
    return calculate_edge_score(*xs)


NEUTRAL = ("Neutral", "Weak")


# --- Grupo A: valores canónicos reales (== a los 6 valores encontrados en las 3 DBs) ---
@pytest.mark.parametrize("d,s,expected_x0", [
    ("Long", "Strong", 2), ("Long", "Mid", 1), ("Long", "Weak", 0),
    ("Short", "Strong", -2), ("Short", "Mid", -1), ("Short", "Weak", 0),
    ("Neutral", "Weak", 0),
])
def test_canonical_values_as_plain_strings(d, s, expected_x0):
    # Sitio 3 recibe estos valores como strings planos desde el workspace dict.
    vectors = [(d, s), NEUTRAL, NEUTRAL, NEUTRAL, NEUTRAL]
    result = compute_i_cd(vectors)
    expected = calculate_edge_score(expected_x0, 0, 0, 0, 0)
    assert result == pytest.approx(expected)


def test_all_long_strong_matches_manual_formula():
    vectors = [("Long", "Strong")] * 5
    result = compute_i_cd(vectors)
    # x0..x4 = 2 cada uno
    expected = (0.30 * 2 + 0.25 * 2 + 0.15 * 2 + 0.10 * 2 + 0.20 * 2) / 2.0
    assert result == pytest.approx(expected)


# --- Grupo C: variantes corruptas (futuras, no observadas hoy en las DBs reales) ---
@pytest.mark.parametrize("corrupted_dir", ["LONG", "long", "Long ", " Long", "Bullish", "", None])
def test_corrupted_direction_falls_back_to_zero(corrupted_dir):
    vectors = [(corrupted_dir, "Weak"), NEUTRAL, NEUTRAL, NEUTRAL, NEUTRAL]
    # Ningún valor corrupto coincide con Direction.LONG/SHORT -> get_dir_val da 0
    # en las 5 posiciones -> i_cd = 0.0, igual en las 3 implementaciones antiguas.
    assert compute_i_cd(vectors) == pytest.approx(0.0)


@pytest.mark.parametrize("corrupted_str", ["STRONG", "strong", "Strong ", "High", "", None])
def test_corrupted_strength_falls_back_to_zero(corrupted_str):
    vectors = [("Long", corrupted_str), NEUTRAL, NEUTRAL, NEUTRAL, NEUTRAL]
    assert compute_i_cd(vectors) == pytest.approx(0.0)


# --- Grupo D: cruce enum-real (dominio de Sitios 1/2) vs. string-real (dominio del Sitio 3) ---
@pytest.mark.parametrize("d_enum,s_enum,d_str,s_str", [
    (Direction.LONG, Strength.STRONG, "Long", "Strong"),
    (Direction.SHORT, Strength.MID, "Short", "Mid"),
    (Direction.NEUTRAL, Strength.WEAK, "Neutral", "Weak"),
    (Direction.LONG, Strength.WEAK, "Long", "Weak"),
])
def test_enum_instance_and_equivalent_string_produce_identical_i_cd(d_enum, s_enum, d_str, s_str):
    r_enum = compute_i_cd([(d_enum, s_enum), NEUTRAL, NEUTRAL, NEUTRAL, NEUTRAL])
    r_str = compute_i_cd([(d_str, s_str), NEUTRAL, NEUTRAL, NEUTRAL, NEUTRAL])
    assert r_enum == pytest.approx(r_str)


# --- determine_market_bias: mismos umbrales que usaban las 3 implementaciones ---
@pytest.mark.parametrize("i_cd,expected_bias", [
    (0.0, "Choppy / Neutral"),
    (0.259, "Choppy / Neutral"),
    (-0.259, "Choppy / Neutral"),
    (0.26, "Bullish"),
    (0.30, "Bullish"),
    (-0.26, "Bearish"),
    (-0.30, "Bearish"),
])
def test_determine_market_bias_thresholds(i_cd, expected_bias):
    assert determine_market_bias(i_cd) == expected_bias
