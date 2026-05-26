import pytest
from cli.schemas.efficiency import EfficiencyAnalysis, Direction, Strength

def test_perfect_long_setup():
    analysis = EfficiencyAnalysis(
        p0_direction=Direction.LONG, p0_strength=Strength.STRONG, p0_thesis="P0 Thesis",
        p2_direction=Direction.LONG, p2_strength=Strength.STRONG, p2_thesis="P2 Thesis",
        p3_direction=Direction.LONG, p3_strength=Strength.STRONG, p3_thesis="P3 Thesis",
        Calc_edge=3.0,
        Market_Bias="Bullish",
        edge_description="Edge description"
    )
    assert analysis.Calc_edge == 3.0
    assert analysis.Market_Bias == "Bullish"
    assert analysis.Long_prob == 0.315
    assert analysis.Short_prob == 0.058
    assert analysis.No_trade_prob == 0.627

def test_purely_neutral_setup():
    analysis = EfficiencyAnalysis(
        p0_direction=Direction.NEUTRAL, p0_strength=Strength.MID, p0_thesis="P0 Thesis",
        p2_direction=Direction.NEUTRAL, p2_strength=Strength.MID, p2_thesis="P2 Thesis",
        p3_direction=Direction.NEUTRAL, p3_strength=Strength.MID, p3_thesis="P3 Thesis",
        Calc_edge=0.0,
        Market_Bias="Choppy / Neutral",
        edge_description="Edge description"
    )
    assert analysis.Calc_edge == 0.0
    assert analysis.Market_Bias == "Choppy / Neutral"
    assert analysis.Long_prob == 0.150
    assert analysis.Short_prob == 0.150
    assert analysis.No_trade_prob == 0.700
