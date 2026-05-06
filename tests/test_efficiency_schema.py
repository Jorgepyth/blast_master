import pytest
from cli.schemas.efficiency import EfficiencyAnalysis, Direction, Strength

def test_perfect_long_setup():
    analysis = EfficiencyAnalysis(
        P0_direction=Direction.LONG, P0_strength=Strength.STRONG,
        P2_direction=Direction.LONG, P2_strength=Strength.STRONG,
        P3_direction=Direction.LONG, P3_strength=Strength.STRONG,
    )
    assert analysis.Calc_edge == 3.0
    assert analysis.Market_Bias == "Bullish"
    assert round(analysis.Long_prob, 2) == 0.85
    assert analysis.Short_prob == 0.15
    assert round(analysis.No_trade_prob, 2) == 0.00

def test_purely_neutral_setup():
    analysis = EfficiencyAnalysis(
        P0_direction=Direction.NEUTRAL, P0_strength=Strength.MID,
        P2_direction=Direction.NEUTRAL, P2_strength=Strength.MID,
        P3_direction=Direction.NEUTRAL, P3_strength=Strength.MID,
    )
    assert analysis.Calc_edge == 0.0
    assert analysis.Market_Bias == "Choppy / Neutral"
    assert analysis.Long_prob == 0.15
    assert analysis.Short_prob == 0.15
    assert analysis.No_trade_prob == 0.70
