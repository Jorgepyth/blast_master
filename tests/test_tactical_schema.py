import pytest
from cli.schemas.tactical import (
    TacticalAnalysis,
    Hierarchy,
    Timeframe,
    FractalType,
    TacticalClassification
)
from cli.schemas.efficiency import Direction, Strength

def test_perfect_long_tactical_setup():
    analysis = TacticalAnalysis(
        p4_direction=Direction.LONG, p4_strength=Strength.STRONG, p4_hierarchy=Hierarchy.HARD_LEVEL,
        p1_direction=Direction.LONG, p1_strength=Strength.STRONG, p1_timeframe=Timeframe.M15,
        p1_type=FractalType.FIRST_ITERATION, nodes_l1=2, nodes_l2=1,
        tactical_classification=TacticalClassification.CONTINUATION_PRESSURE
    )
    assert analysis.calc_edge == 4.0
    assert analysis.long_prob == 0.85
    assert analysis.short_prob == 0.15
    assert analysis.tactical_classification == TacticalClassification.CONTINUATION_PRESSURE

def test_zero_edge_overrides_classification():
    analysis = TacticalAnalysis(
        p4_direction=Direction.NEUTRAL, p4_strength=Strength.MID, p4_hierarchy=Hierarchy.SOFT_LEVEL,
        p1_direction=Direction.NEUTRAL, p1_strength=Strength.MID, p1_timeframe=Timeframe.H1,
        p1_type=FractalType.CONVERGENT_FRACTALS, nodes_l1=0, nodes_l2=0,
        tactical_classification=TacticalClassification.REVERSAL_PRESSURE
    )
    assert analysis.calc_edge == 0.0
    assert analysis.tactical_classification == TacticalClassification.NA
