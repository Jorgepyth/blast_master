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
    thesis: str

class EfficiencyAnalysis(BaseModel):
    p0_direction: Direction
    p0_strength: Strength
    p0_thesis: str
    p2_direction: Direction
    p2_strength: Strength
    p2_thesis: str
    p3_direction: Direction
    p3_strength: Strength
    p3_thesis: str
    
    Calc_edge: float
    Market_Bias: str
    edge_description: str
    Long_prob: float = 0.0
    Short_prob: float = 0.0
    No_trade_prob: float = 0.0

    @model_validator(mode='after')
    def calculate_derived_fields(self) -> 'EfficiencyAnalysis':
        import math
        scaling_factor = 0.2833
        e_long = math.exp(self.Calc_edge * scaling_factor)
        e_short = math.exp(-self.Calc_edge * scaling_factor)
        e_no_trade = math.exp(1.54)
        
        total = e_long + e_short + e_no_trade
        self.Long_prob = round(e_long / total, 3)
        self.Short_prob = round(e_short / total, 3)
        self.No_trade_prob = round(1.0 - self.Long_prob - self.Short_prob, 3)
        
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
