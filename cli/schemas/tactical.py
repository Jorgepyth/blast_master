from enum import Enum
from typing import Optional
from pydantic import BaseModel, model_validator
from cli.schemas.efficiency import Direction, Strength

class Hierarchy(str, Enum):
    SOFT_LEVEL = "Soft_Level (1H/4H)"
    HARD_LEVEL = "Hard_Level (Daily,Weekly,Monthly)"
    PSYCH_LEVEL = "Psych Level"

class Timeframe(str, Enum):
    M5 = "5M"
    M15 = "15M"
    M30 = "30M"
    H1 = "1H"
    H4 = "4H"

class FractalType(str, Enum):
    FIRST_ITERATION = "1st_iteration"
    MAX_ITERATION = "Max_iteration"
    DOUBLE_FRACTAL = "Double_fractal"
    EXTENDED_FIRST_LEVEL = "Extended_first_level"
    CONVERGENT_FRACTALS = "Convergent_fractals"

class TacticalClassification(str, Enum):
    CONTINUATION_PRESSURE = "Continuation_Pressure"
    REVERSAL_PRESSURE = "Reversal_Pressure"
    RANGE_ROTATION = "Range_Rotation"
    LIQUIDITY_SWEEP_ROTATION = "Liquidity_Sweep_Rotation"
    NA = "N/A"

class TacticalAnalysis(BaseModel):
    p4_direction: Direction
    p4_strength: Strength
    p4_hierarchy: Hierarchy
    p1_direction: Direction
    p1_strength: Strength
    p1_timeframe: Timeframe
    p1_type: FractalType
    nodes_l1: int
    nodes_l2: int
    tactical_classification: TacticalClassification
    p4_thesis: Optional[str] = None
    p1_thesis: Optional[str] = None

    p1_score: float = 0.0
    p4_score: float = 0.0
    calc_edge: float = 0.0
    long_prob: float = 0.0
    short_prob: float = 0.0
    no_trade_prob: float = 0.0

    @model_validator(mode='after')
    def calculate_derived_tactics(self) -> 'TacticalAnalysis':
        def get_direction_weight(direction: Direction) -> int:
            if direction == Direction.LONG: return 1
            if direction == Direction.SHORT: return -1
            return 0
            
        def get_strength_weight(strength: Strength) -> int:
            if strength == Strength.STRONG: return 2
            if strength == Strength.MID: return 1
            if strength == Strength.WEAK: return 0
            return 0

        if self.p1_direction == Direction.NEUTRAL:
            self.p1_score = 0.0
        else:
            self.p1_score = float(get_direction_weight(self.p1_direction) * get_strength_weight(self.p1_strength))

        if self.p4_direction == Direction.NEUTRAL:
            self.p4_score = 0.0
        else:
            self.p4_score = float(get_direction_weight(self.p4_direction) * get_strength_weight(self.p4_strength))

        self.calc_edge = self.p1_score + self.p4_score

        if self.calc_edge == 0.0:
            self.tactical_classification = TacticalClassification.NA

        self.long_prob = max(0.15, min(0.85, self.calc_edge * 0.2125))
        self.short_prob = max(0.15, min(0.85, -self.calc_edge * 0.2125))
        self.no_trade_prob = 1.0 - self.long_prob - self.short_prob

        return self
