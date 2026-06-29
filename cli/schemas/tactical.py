from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, model_validator
from cli.schemas.efficiency import Direction, Strength, AnalysisLayerInput

class Hierarchy(str, Enum):
    SOFT_LEVEL = "Soft_Level (1H/4H)"
    HARD_LEVEL = "Hard_Level (Daily,Weekly,Monthly)"
    PSYCH_LEVEL = "Psych Level"
    SKIP = "Skip"

class Timeframe(str, Enum):
    M5 = "5M"
    M15 = "15M"
    M30 = "30M"
    H1 = "1H"
    H4 = "4H"
    H12 = "12H"
    SKIP = "Skip"

class FractalType(str, Enum):
    FIRST_ITERATION = "1st_iteration"
    MAX_ITERATION = "Max_iteration"
    DOUBLE_FRACTAL = "Double_fractal"
    EXTENDED_FIRST_LEVEL = "Extended_first_level"
    CONVERGENT_FRACTALS = "Convergent_fractals"
    SKIP = "Skip"

class TacticalClassification(str, Enum):
    CONTINUATION_PRESSURE = "Continuation_Pressure"
    REVERSAL_PRESSURE = "Reversal_Pressure"
    RANGE_ROTATION = "Range_Rotation"
    LIQUIDITY_SWEEP_ROTATION = "Liquidity_Sweep_Rotation"
    NA = "N/A"
    SKIP = "Skip"

class TradeStatus(str, Enum):
    GOOD_EXECUTION = "Trade_taken_good_execution"
    BAD_EXECUTION = "Trade_taken_bad_execution"
    NO_TAKEN = "Trade_no_taken"
    SKIP = "Skip"

class TacticalAnalysis(BaseModel):
    p4_direction: Direction
    p4_strength: Strength
    p4_thesis: str
    p1_direction: Direction
    p1_strength: Strength
    p1_thesis: str
    
    p4_hierarchy: Hierarchy
    p1_timeframe: Timeframe
    p1_type: FractalType
    nodes_l1: int
    nodes_l2: int
    tactical_classification: TacticalClassification
    trade_status: Optional[TradeStatus] = None

    calc_edge: float
    long_prob: float = 0.0
    short_prob: float = 0.0
    no_trade_prob: float = 0.0

    @model_validator(mode='after')
    def calculate_derived_tactics(self) -> 'TacticalAnalysis':
        if self.calc_edge == 0.0:
            self.tactical_classification = TacticalClassification.NA
            
        import math
        import os
        
        scaling_factor = float(os.getenv("TACTICAL_SCALING_FACTOR", 0.2833))
        no_trade_exponent = float(os.getenv("NO_TRADE_BASE_EXPONENT", 1.54))
        
        e_long = math.exp(self.calc_edge * scaling_factor)
        e_short = math.exp(-self.calc_edge * scaling_factor)
        e_no_trade = math.exp(no_trade_exponent)
        
        total = e_long + e_short + e_no_trade
        self.long_prob = round(e_long / total, 3)
        self.short_prob = round(e_short / total, 3)
        self.no_trade_prob = round(1.0 - self.long_prob - self.short_prob, 3)

        return self

    def to_db_layers(self) -> List[AnalysisLayerInput]:
        def get_direction_weight(direction: Direction) -> int:
            if direction == Direction.LONG: return 1
            if direction == Direction.SHORT: return -1
            return 0
            
        def get_strength_weight(strength: Strength) -> int:
            if strength == Strength.STRONG: return 2
            if strength == Strength.MID: return 1
            if strength == Strength.WEAK: return 0
            return 0

        layers = []
        s4 = 0 if self.p4_direction == Direction.NEUTRAL else get_direction_weight(self.p4_direction) * get_strength_weight(self.p4_strength)
        layers.append(AnalysisLayerInput(
            layer_name="P4", direction=self.p4_direction, strength=self.p4_strength, 
            score=s4, thesis=self.p4_thesis
        ))
        s1 = 0 if self.p1_direction == Direction.NEUTRAL else get_direction_weight(self.p1_direction) * get_strength_weight(self.p1_strength)
        layers.append(AnalysisLayerInput(
            layer_name="P1", direction=self.p1_direction, strength=self.p1_strength, 
            score=s1, thesis=self.p1_thesis
        ))
        return layers
