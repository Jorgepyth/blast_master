from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, model_validator

class Direction(str, Enum):
    LONG = "Long"
    SHORT = "Short"
    NEUTRAL = "Neutral"

class Strength(str, Enum):
    STRONG = "Strong"
    MID = "Mid"
    WEAK = "Weak"

class AnalysisLayerInput(BaseModel):
    layer_name: str
    direction: Direction
    strength: Strength
    score: int = 0
    thesis: Optional[str] = None

class EfficiencyAnalysis(BaseModel):
    p0_direction: Direction
    p0_strength: Strength
    p0_thesis: Optional[str] = None
    p2_direction: Direction
    p2_strength: Strength
    p2_thesis: Optional[str] = None
    p3_direction: Direction
    p3_strength: Strength
    p3_thesis: Optional[str] = None
    
    Calc_edge: float = 0.0
    Market_Bias: str = ""
    edge_description: Optional[str] = None
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

        total_score = 0.0
        
        # P0
        s0 = get_direction_weight(self.p0_direction) * get_strength_weight(self.p0_strength)
        total_score += s0 * 0.5
        
        # P2
        s2 = get_direction_weight(self.p2_direction) * get_strength_weight(self.p2_strength)
        total_score += s2 * 0.3
        
        # P3
        s3 = get_direction_weight(self.p3_direction) * get_strength_weight(self.p3_strength)
        total_score += s3 * 0.2

        self.Calc_edge = total_score
        
        if self.Calc_edge > 0.5:
            self.Market_Bias = "Bullish"
        elif self.Calc_edge < -0.5:
            self.Market_Bias = "Bearish"
        else:
            self.Market_Bias = "Choppy / Neutral"
            
        self.Long_prob = max(0.15, min(0.85, self.Calc_edge * 0.2833))
        self.Short_prob = max(0.15, min(0.85, -self.Calc_edge * 0.2833))
        self.No_trade_prob = 1.0 - self.Long_prob - self.Short_prob
        
        return self

    def to_db_layers(self) -> List[AnalysisLayerInput]:
        def get_direction_weight(direction: Direction) -> int:
            if direction == Direction.LONG: return 1
            if direction == Direction.SHORT: return -1
            return 0
            
        def get_strength_weight(strength: Strength) -> int:
            if strength == Strength.STRONG: return 3
            if strength == Strength.MID: return 2
            if strength == Strength.WEAK: return 1
            return 0
            
        layers = []
        layers.append(AnalysisLayerInput(
            layer_name="P0", direction=self.p0_direction, strength=self.p0_strength, 
            score=get_direction_weight(self.p0_direction) * get_strength_weight(self.p0_strength), thesis=self.p0_thesis
        ))
        layers.append(AnalysisLayerInput(
            layer_name="P2", direction=self.p2_direction, strength=self.p2_strength, 
            score=get_direction_weight(self.p2_direction) * get_strength_weight(self.p2_strength), thesis=self.p2_thesis
        ))
        layers.append(AnalysisLayerInput(
            layer_name="P3", direction=self.p3_direction, strength=self.p3_strength, 
            score=get_direction_weight(self.p3_direction) * get_strength_weight(self.p3_strength), thesis=self.p3_thesis
        ))
        return layers
