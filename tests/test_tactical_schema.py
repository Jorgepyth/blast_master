import pytest
from decimal import Decimal
from cli.schemas.tactical import (
    TacticalAnalysis,
    Hierarchy,
    Timeframe,
    FractalType,
    TacticalClassification
)
from cli.schemas.efficiency import Direction, Strength
from core.math_engine import calculate_probabilities


def test_perfect_long_tactical_setup():
    """
    edge=4.0 se normaliza a EDGE_CAP=1.0.
    Modelo Lineal Conservador:
      edge_norm = 1.0, no_trade_prob = 0.5 (MIN), action_prob = 0.5
      long_prob  = 0.5 * (0.5 + 0.5*1.0) = 0.5
      short_prob = 0.5 * (0.5 - 0.5*1.0) = 0.0
    """
    analysis = TacticalAnalysis(
        p4_direction=Direction.LONG, p4_strength=Strength.STRONG, p4_hierarchy=Hierarchy.HARD_LEVEL,
        p1_direction=Direction.LONG, p1_strength=Strength.STRONG, p1_timeframe=Timeframe.M15,
        p1_type=FractalType.FIRST_ITERATION, nodes_l1=2, nodes_l2=1,
        tactical_classification=TacticalClassification.CONTINUATION_PRESSURE,
        p4_thesis="P4 Thesis",
        p1_thesis="P1 Thesis",
        calc_edge=4.0
    )
    assert analysis.calc_edge == 4.0
    assert abs(analysis.long_prob - 0.5) < 1e-7
    assert abs(analysis.short_prob - 0.0) < 1e-7
    assert abs(analysis.no_trade_prob - 0.5) < 1e-7
    assert analysis.tactical_classification == TacticalClassification.CONTINUATION_PRESSURE


def test_zero_edge_overrides_classification():
    """
    edge=0.0: no_trade_prob = 0.8 (MAX), long = short = 0.1
    """
    analysis = TacticalAnalysis(
        p4_direction=Direction.NEUTRAL, p4_strength=Strength.MID, p4_hierarchy=Hierarchy.SOFT_LEVEL,
        p1_direction=Direction.NEUTRAL, p1_strength=Strength.MID, p1_timeframe=Timeframe.H1,
        p1_type=FractalType.CONVERGENT_FRACTALS, nodes_l1=0, nodes_l2=0,
        tactical_classification=TacticalClassification.REVERSAL_PRESSURE,
        p4_thesis="P4 Thesis",
        p1_thesis="P1 Thesis",
        calc_edge=0.0
    )
    assert analysis.calc_edge == 0.0
    assert analysis.tactical_classification == TacticalClassification.NA
    assert abs(analysis.no_trade_prob - 0.8) < 1e-7
    assert abs(analysis.long_prob - 0.1) < 1e-7
    assert abs(analysis.short_prob - 0.1) < 1e-7


def test_reference_edge_0225():
    """
    Caso de referencia canonico: edge=0.225
    Expected: no_trade=0.7325, long=0.16384375, short=0.10365625
    """
    probs = calculate_probabilities(0.225)
    assert abs(float(probs.no_trade_prob) - 0.7325) < 1e-7
    assert abs(float(probs.long_prob) - 0.16384375) < 1e-7
    assert abs(float(probs.short_prob) - 0.10365625) < 1e-7
    total = probs.long_prob + probs.short_prob + probs.no_trade_prob
    assert abs(float(total) - 1.0) < 1e-7


def test_negative_edge_symmetry():
    """
    Para edge=-e, long y short deben invertirse simetricamente.
    """
    edge = 0.5
    probs_pos = calculate_probabilities(edge)
    probs_neg = calculate_probabilities(-edge)
    assert abs(float(probs_pos.long_prob) - float(probs_neg.short_prob)) < 1e-9
    assert abs(float(probs_pos.short_prob) - float(probs_neg.long_prob)) < 1e-9
    assert abs(float(probs_pos.no_trade_prob) - float(probs_neg.no_trade_prob)) < 1e-9


def test_sum_always_one():
    """La suma de las tres probabilidades debe ser 1.0 para cualquier edge."""
    for edge in [-1.0, -0.5, -0.225, 0.0, 0.225, 0.5, 1.0, 4.0]:
        probs = calculate_probabilities(edge)
        total = probs.long_prob + probs.short_prob + probs.no_trade_prob
        assert abs(float(total) - 1.0) < 1e-7, f"Suma != 1.0 para edge={edge}: {total}"
