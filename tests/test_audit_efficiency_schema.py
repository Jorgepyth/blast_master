import pytest
from datetime import datetime
from cli.schemas.audit_efficiency import (
    EfficiencyAudit,
    StructuralBias,
    ResolutionType,
    StructuralResolution,
    FailureReason
)

def test_false_signal_logic():
    # Bias_A != No_Bias, Real_Bias_B == No_Bias -> "False Signal", "Invalid"
    audit = EfficiencyAudit(
        efficiency_id="test-1",
        bias_a=StructuralBias.BOS,
        resolution_type=ResolutionType.INVALIDATED,
        real_bias_b=StructuralBias.NO_BIAS_CHOPPY,
        structural_resolution=StructuralResolution.NA,
        failure_reason=FailureReason.REGIME_DECAY,
        resolution_time=datetime.now()
    )
    assert audit.specific_bias_compliance == "Invalid"
    assert audit.false_regime_rate == "False Signal"

def test_true_positive_logic():
    # Bias_A != No_Bias, Real_Bias_B != No_Bias, Bias_A == Real_Bias_B -> "True Positive", "Valid"
    audit = EfficiencyAudit(
        efficiency_id="test-2",
        bias_a=StructuralBias.CHOCH,
        resolution_type=ResolutionType.CONFIRMED,
        real_bias_b=StructuralBias.CHOCH,
        structural_resolution=StructuralResolution.CONFIRMED_EXPANSION,
        failure_reason=FailureReason.NA,
        resolution_time=datetime.now()
    )
    assert audit.specific_bias_compliance == "Valid"
    assert audit.false_regime_rate == "True Positive"

def test_true_negative_logic():
    audit = EfficiencyAudit(
        efficiency_id="test-3",
        bias_a=StructuralBias.NO_BIAS_CHOPPY,
        resolution_type=ResolutionType.CONFIRMED,
        real_bias_b=StructuralBias.NO_BIAS_CHOPPY,
        structural_resolution=StructuralResolution.NA,
        failure_reason=FailureReason.NA,
        resolution_time=datetime.now()
    )
    assert audit.specific_bias_compliance == "Valid"
    assert audit.false_regime_rate == "True Negative"

def test_false_positive_logic():
    # Bias_A != No_Bias, Real_Bias_B != No_Bias, Bias_A != Real_Bias_B -> "False Positive", "Invalid"
    audit = EfficiencyAudit(
        efficiency_id="test-4",
        bias_a=StructuralBias.CHOCH,
        resolution_type=ResolutionType.INVALIDATED,
        real_bias_b=StructuralBias.BOS,
        structural_resolution=StructuralResolution.NA,
        failure_reason=FailureReason.REVERSAL,
        resolution_time=datetime.now()
    )
    assert audit.specific_bias_compliance == "Invalid"
    assert audit.false_regime_rate == "False Positive"
