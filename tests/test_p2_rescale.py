"""
P2_systematic — tests del reescalamiento aprobado de p2_sistematico a la
escala discreta {-2,-1,0,1,2} de p2_discrecional. Sin DB, solo las
funciones puras round_half_away_from_zero / clamp / rescale_p2_sistematico.

Cubre: redondeo hacia arriba, redondeo hacia abajo, empate exacto en .5
(debe irse lejos de cero, NUNCA al par más cercano como haría el round()
nativo de Python), y los extremos ±1.0 -> ±2.
"""
from tools.p2_backtest import clamp, rescale_p2_sistematico, round_half_away_from_zero


def test_round_half_away_from_zero_rounds_up_below_half():
    # 0.61 * 2 = 1.22 -> redondea a 1
    assert round_half_away_from_zero(1.22) == 1


def test_round_half_away_from_zero_rounds_down_below_half():
    # 0.24 * 2 = 0.48 -> redondea a 0
    assert round_half_away_from_zero(0.48) == 0


def test_round_half_away_from_zero_tie_goes_away_from_zero_positive():
    assert round_half_away_from_zero(0.5) == 1
    assert round_half_away_from_zero(1.5) == 2

    # round() nativo usa banker's rounding (al par más cercano) y daría 0 y 2
    # respectivamente para estos dos casos -- justo lo que NO se quiere aquí.
    assert round(0.5) == 0
    assert round(1.5) == 2  # coincide por casualidad en este caso; el de 2.5 no:
    assert round(2.5) == 2  # banker's rounding: va al par (2), no lejos de cero (3)
    assert round_half_away_from_zero(2.5) == 3  # lejos de cero, como se aprobó


def test_round_half_away_from_zero_tie_goes_away_from_zero_negative():
    assert round_half_away_from_zero(-0.5) == -1
    assert round_half_away_from_zero(-1.5) == -2
    assert round_half_away_from_zero(-2.5) == -3  # lejos de cero, no al par (-2)


def test_rescale_extremes_plus_minus_one_map_to_plus_minus_two():
    assert rescale_p2_sistematico(1.0) == 2
    assert rescale_p2_sistematico(-1.0) == -2


def test_rescale_none_stays_none():
    assert rescale_p2_sistematico(None) is None


def test_rescale_rounds_up_case():
    # 0.61 * 2 = 1.22 -> redondea a 1
    assert rescale_p2_sistematico(0.61) == 1


def test_rescale_rounds_down_case():
    # 0.24 * 2 = 0.48 -> redondea a 0
    assert rescale_p2_sistematico(0.24) == 0


def test_rescale_exact_half_tie_case():
    # 0.25 * 2 = 0.5 exacto -> debe irse a 1 (lejos de cero), no a 0.
    assert rescale_p2_sistematico(0.25) == 1
    assert rescale_p2_sistematico(-0.25) == -1


def test_clamp_defensive_bounds():
    assert clamp(3, -2, 2) == 2
    assert clamp(-3, -2, 2) == -2
    assert clamp(1, -2, 2) == 1
