from decimal import Decimal
from enum import Enum
from pydantic import BaseModel, field_validator, model_validator
from datetime import datetime
from typing import Optional

from config.auto_resolution import (
    REASON_AMBIGUOUS,
    REASON_CLOCK_MISALIGNED,
    REASON_CLOCK_UNVERIFIED,
    REASON_NO_HISTORY,
    REASON_NO_LEVELS,
    REASON_NO_MT5_SYMBOL,
    REASON_PENDING_CANDLES,
)
from core.candle_resolution import OUTCOME_OPEN

# Spec 002 (RF-14b, N25; plan.md §2.1): de dónde salió `resolution_time`, o por qué quedó vacío.
RESOLUTION_TIME_SOURCE_CANDLES = "candles"      # lo propusieron las velas y el operador lo aceptó
RESOLUTION_TIME_SOURCE_CORRECTED = "corrected"  # el operador cambió el valor propuesto
RESOLUTION_TIME_SOURCES = (
    RESOLUTION_TIME_SOURCE_CANDLES, RESOLUTION_TIME_SOURCE_CORRECTED, REASON_PENDING_CANDLES, REASON_NO_HISTORY,
    REASON_CLOCK_UNVERIFIED, REASON_CLOCK_MISALIGNED, REASON_AMBIGUOUS, REASON_NO_LEVELS, REASON_NO_MT5_SYMBOL,
    OUTCOME_OPEN,
)

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
    # Spec 002 (N1, N25): la hora del primer toque según las velas; puede quedar vacía (RF-7g). La hora en que se
    # guarda el audit va a `audit_registration_time` (RF-14).
    resolution_time: Optional[datetime] = None
    audit_registration_time: Optional[datetime] = None
    resolution_time_source: Optional[str] = None

    specific_bias_compliance: str = ""
    false_regime_rate: str = ""

    # Text blocks
    notes: Optional[str] = None
    lesson_learned: Optional[str] = None
    edge_description: Optional[str] = None

    # Structural_MAE/MFE: precio real alcanzado en contra/a favor de la tesis
    # entre la creación del análisis y su resolución. Entrada manual (no hay
    # feed de precio en el sistema) — mismo criterio que mark_price/
    # edge_validation_price/structural_invalidation en UnifiedDepartment.
    structural_mae: Optional[Decimal] = None
    structural_mfe: Optional[Decimal] = None

    @field_validator("resolution_time_source")
    @classmethod
    def _known_source(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in RESOLUTION_TIME_SOURCES:
            raise ValueError(f"unknown resolution_time_source {value!r}; expected one of {RESOLUTION_TIME_SOURCES}")
        return value

    @model_validator(mode='after')
    def _source_with_a_time(self) -> 'EfficiencyAudit':
        if self.resolution_time is None and self.resolution_time_source in (
                RESOLUTION_TIME_SOURCE_CANDLES, RESOLUTION_TIME_SOURCE_CORRECTED):
            raise ValueError(f"resolution_time_source {self.resolution_time_source!r} needs a resolution_time")
        return self

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
