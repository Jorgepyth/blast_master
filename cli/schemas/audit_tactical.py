from enum import Enum
from typing import Optional, List, Any
from datetime import datetime
from pydantic import BaseModel, Field, model_serializer, field_validator

class ComplianceState(str, Enum):
    INVALID_EDGE = "Invalid_edge"
    NO_EDGE = "No_edge"
    EDGE_VALID = "Edge_valid"
    BIAS_BAD_ENTRY = "Bias_but_bad_entry"
    SKIP = "Skip"

class TierSetup(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"
    SKIP = "Skip"

class MarketState(str, Enum):
    TREND = "Trend"
    RANGE = "Range"
    SKIP = "Skip"

class Session(str, Enum):
    ASIA_OFF = "Asia/Off"
    NEW_YORK = "New York"
    LONDON = "London"
    SKIP = "Skip"

class ExitType(str, Enum):
    SCALE_OUT = "Scale-out"
    STOP_ORDER = "Stop Order"
    MANUAL = "Manual"
    SKIP = "Skip"

class TradeDecision(str, Enum):
    LONG = "Long"
    SHORT = "Short"
    SKIP = "Skip"

class FollowedPlan(str, Enum):
    YES = "Yes"
    NO = "No"
    KINDA_NO_EDGE = "kinda_no_edge_but_followed_probability"
    SKIP = "Skip"

class PrimaryEmotion(str, Enum):
    EQUANIMITY = "Equanimity"
    ANXIETY = "Anxiety"
    FEAR_OF_BEING_WRONG = "Fear of being wrong"
    SHAME = "Shame"
    BOREDOM = "Boredom"
    SELF_DOUBT = "Self-doubt"
    GROUNDED_CONFIDENCE = "Grounded Confidence"
    IMPATIENCE = "Impatience"
    SKIP = "Skip"

class SetupType(str, Enum):
    TREND_PULLBACK = "trend_pullback"
    RANGE_REVERSION = "range_reversion"
    RANGE_BREAKOUT = "range_breakout"
    MINOR_TREND_PULLBACK = "minor_trend_pullback"
    MAJOR_TREND_PULLBACK = "major_trend_pullback"
    FRACTAL_CHANGE_OF_TREND = "fractal_change_of_trend"
    BREAKOUT_RETEST = "breakout_retest"
    SKIP = "Skip"

class HTFTrendContext(str, Enum):
    BEARISH = "bearish"
    SIDEWAYS = "sideways"
    BULLISH = "bullish"
    SKIP = "Skip"

class ConfirmationStatus(str, Enum):
    YES = "Yes"
    NO = "No"
    YES_BAD_ENTRY = "Yes but bad entry point (too tight)"
    YES_CLOSED_EARLY = "Yes but closed too early - Fear"
    SKIP = "Skip"

class Emotions(str, Enum):
    CONSISTENCY = "Consistency"
    STATISTICAL_THINKING = "Statistical Thinking"
    FOMO = "FOMO"
    HOPE_HOLD = "Hope-Hold"
    ACCOUNTABILITY = "Accountability"
    DECISIVENESS = "Decisiveness"
    DETACHED_NEUTRALITY = "Detached Neutraility"
    LOSS_ACCEPTANCE = "Loss Acceptance"
    COURAGE = "Courage"
    IMPATIENCE = "Impatience"
    BOREDOM = "Boredom"
    SKIP = "Skip"

class BehavioralErrors(str, Enum):
    CLOSED_TOO_EARLY = "Closed too early"
    LACK_OF_DISCIPLINE = "Lack of Discipline"
    OVERTRADING = "Overtrading"
    TRADED_ON_PHONE = "Traded on the phone"
    REVENGE_TRADE = "Revenge Trade"
    HESITATION = "Hesitation"
    SKIP = "Skip"

class CognitivePatterns(str, Enum):
    NA = "N/A"
    SKIP = "Skip"

class TacticalAudit(BaseModel):
    tactical_id: str
    compliance: ComplianceState
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None

    # Categorical data
    tier_setup: Optional[TierSetup] = None
    market_state: Optional[MarketState] = None
    session: Optional[Session] = None
    exit_type: Optional[ExitType] = None
    trade_decision: Optional[TradeDecision] = None
    followed_plan: Optional[FollowedPlan] = None
    primary_emotion: Optional[PrimaryEmotion] = None
    setup_type: Optional[SetupType] = None
    htf_trend_context: Optional[HTFTrendContext] = None
    confirmation_status: Optional[ConfirmationStatus] = None

    # Numeric scales
    anxiety_level: Optional[int] = Field(default=None, ge=1, le=10)
    impatience_level: Optional[int] = Field(default=None, ge=1, le=10)
    mental_clarity_level: Optional[int] = Field(default=None, ge=1, le=10)

    # Multi-Select fields
    emotions: Optional[List[Emotions]] = None
    behavioral_errors: Optional[List[BehavioralErrors]] = None
    cognitive_patterns: Optional[List[CognitivePatterns]] = None

    # Financial and execution metrics
    risk_usd: Optional[float] = None
    size: Optional[float] = None
    r_r: Optional[float] = None
    entry_price: Optional[float] = None
    closing_price: Optional[float] = None
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    pnl_and_cost: Optional[float] = None
    captured_mae: Optional[float] = Field(default=None, ge=0, le=10)
    captured_mfe: Optional[float] = Field(default=None, ge=0, le=10)

    # Text blocks
    lesson_learned: Optional[str] = None

    @model_serializer(mode='wrap')
    def serialize_model(self, handler):
        d = handler(self)
        for field in ['risk_usd', 'size', 'entry_price', 'closing_price', 'take_profit', 'stop_loss', 'pnl_and_cost']:
            if d.get(field) is not None:
                d[field] = round(d[field] * 100_000_000)
        return d
        
    @field_validator('risk_usd', 'size', 'entry_price', 'closing_price', 'take_profit', 'stop_loss', 'pnl_and_cost', mode='before')
    @classmethod
    def parse_atomic(cls, v: Any) -> Optional[float]:
        if v is None:
            return None
        # If the input is an integer, we safely assume it's an atomic value from the database
        # (CLI input always passes through get_optional_float and returns a float type)
        if isinstance(v, int):
            return float(v) / 100_000_000.0
        return float(v)
