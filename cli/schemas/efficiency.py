from enum import Enum
from pydantic import BaseModel, model_validator

class Direction(str, Enum):
    LONG = "Long"
    SHORT = "Short"
    NEUTRAL = "Neutral"

class Strength(str, Enum):
    STRONG = "Strong"
    MID = "Mid"
    WEAK = "Weak"

class EfficiencyAnalysis(BaseModel):
    P0_direction: Direction
    P0_strength: Strength
    P2_direction: Direction
    P2_strength: Strength
    P3_direction: Direction
    P3_strength: Strength

    P0_score: float = 0.0
    P2_score: float = 0.0
    P3_score: float = 0.0
    Calc_edge: float = 0.0
    Market_Bias: str = ""
    Long_prob: float = 0.0
    Short_prob: float = 0.0
    No_trade_prob: float = 0.0

    @model_validator(mode='after')
    def calculate_derived_fields(self) -> 'EfficiencyAnalysis':
        def get_direction_weight(direction: Direction) -> int:
            if direction == Direction.LONG: return 1
            if direction == Direction.SHORT: return -1
            return 0
            
        def get_strength_weight(strength: Strength) -> int:
            if strength == Strength.STRONG: return 3
            if strength == Strength.MID: return 2
            if strength == Strength.WEAK: return 1
            return 0

        self.P0_score = float(get_direction_weight(self.P0_direction) * get_strength_weight(self.P0_strength))
        self.P2_score = float(get_direction_weight(self.P2_direction) * get_strength_weight(self.P2_strength))
        self.P3_score = float(get_direction_weight(self.P3_direction) * get_strength_weight(self.P3_strength))
        
        self.Calc_edge = (self.P0_score * 0.5) + (self.P2_score * 0.3) + (self.P3_score * 0.2)
        
        if self.Calc_edge > 0.5:
            self.Market_Bias = "Bullish"
        elif self.Calc_edge < -0.5:
            self.Market_Bias = "Bearish"
        else:
            self.Market_Bias = "Choppy / Neutral"
            
        # Using exact float for 0.85/3.0 to hit 0.85 with Calc_edge=3.0
        multiplier = 0.2833333333333333
        
        self.Long_prob = max(0.15, min(0.85, self.Calc_edge * multiplier))
        self.Short_prob = max(0.15, min(0.85, -self.Calc_edge * multiplier))
        self.No_trade_prob = 1.0 - self.Long_prob - self.Short_prob
        
        return self
