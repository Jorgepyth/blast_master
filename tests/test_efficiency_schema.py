import pytest
from cli.schemas.efficiency import EfficiencyAnalysis, Direction, Strength

def test_perfect_long_setup():
    analysis = EfficiencyAnalysis(
        p0_direction=Direction.LONG, p0_strength=Strength.STRONG,
        p2_direction=Direction.LONG, p2_strength=Strength.STRONG,
        p3_direction=Direction.LONG, p3_strength=Strength.STRONG,
    )
    assert analysis.Calc_edge == 3.0
    assert analysis.Market_Bias == "Bullish"
    assert round(analysis.Long_prob, 2) == 0.85
    assert analysis.Short_prob == 0.15
    assert round(analysis.No_trade_prob, 2) == 0.00

def test_purely_neutral_setup():
    analysis = EfficiencyAnalysis(
        p0_direction=Direction.NEUTRAL, p0_strength=Strength.MID,
        p2_direction=Direction.NEUTRAL, p2_strength=Strength.MID,
        p3_direction=Direction.NEUTRAL, p3_strength=Strength.MID,
    )
    assert analysis.Calc_edge == 0.0
    assert analysis.Market_Bias == "Choppy / Neutral"
    assert analysis.Long_prob == 0.15
    assert analysis.Short_prob == 0.15
    assert analysis.No_trade_prob == 0.70
