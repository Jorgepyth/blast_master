from enum import Enum
from pydantic import BaseModel, model_validator
from datetime import datetime
from typing import Optional

class StructuralBias(str, Enum):
    BOS = "BOS"
    CHOCH = "CHOCH"
    VALIDATED_RANGE_EXPANSION = "Validated Range Expansion"
    NO_BIAS_CHOPPY = "No_Bias(Choppy)"
    CHOPPY_BULLISH_RANGE_ROTATION = "Choppy-Bullish Range Rotation"
    CHOPPY_BEARISH_RANGE_ROTATION = "Choppy-Bearish Range Rotation"
    TREND_REVERSAL = "Trend Reversal"

class ResolutionType(str, Enum):
    OPEN = "Open"
    OVERLAP_INVALIDATION = "Overlap Invalidation (New Bias before resolution)"
    INVALIDATED = "Invalidated (B not equal to A)"
    CONFIRMED = "Confirmed (A equal to B)"

class StructuralResolution(str, Enum):
    CONFIRMED_REVERTED = "Confirmed pero inmediatamente revertido"
    CONFIRMED_MINIMAL = "Confirmed pero mínima"
    CONFIRMED_EXPANSION = "Confirmed + expansión significativa"
    NA = "N/A"

class FailureReason(str, Enum):
    LIQUIDITY_SWEEP = "Liquidity Sweep -- Hizo el movimiento pero barrió mi punto de invalidación por un tick - error de precisión"
    REGIME_DECAY = "Regime Decay -- Predije X / el mercado se quedo choppy"
    REVERSAL = "Reversal --prefije X / el mercado se fue Bullish/Bearish agresivo"
    OVERLAP = "Overlap -- nuevo bias antes de resolucion"
    RANGE_EXPANSION = "Range Expansion -- predije choppy y el precio hizo un rompimiento del rango"
    NA = "N/A"

class EfficiencyAudit(BaseModel):
    efficiency_id: str
    bias_a: StructuralBias
    resolution_type: ResolutionType
    real_bias_b: StructuralBias
    structural_resolution: StructuralResolution
    failure_reason: FailureReason
    resolution_time: datetime

    specific_bias_compliance: str = ""
    false_regime_rate: str = ""

    # Text blocks
    notes: Optional[str] = None
    lesson_learned: Optional[str] = None
    edge_description: Optional[str] = None

    @model_validator(mode='after')
    def calculate_audit_metrics(self) -> 'EfficiencyAudit':
        # specific_bias_compliance
        if self.bias_a == self.real_bias_b:
            self.specific_bias_compliance = "Valid"
        else:
            self.specific_bias_compliance = "Invalid"

        # false_regime_rate
        if self.bias_a == StructuralBias.NO_BIAS_CHOPPY and self.real_bias_b == StructuralBias.NO_BIAS_CHOPPY:
            self.false_regime_rate = "True Negative"
        elif self.bias_a != StructuralBias.NO_BIAS_CHOPPY and self.real_bias_b == StructuralBias.NO_BIAS_CHOPPY:
            self.false_regime_rate = "False Signal"
        elif self.bias_a == StructuralBias.NO_BIAS_CHOPPY and self.real_bias_b != StructuralBias.NO_BIAS_CHOPPY:
            self.false_regime_rate = "False Negative"
        else:
            if self.bias_a != self.real_bias_b:
                self.false_regime_rate = "False Positive"
            else:
                self.false_regime_rate = "True Positive"

        return self
