from enum import Enum
from typing import Optional, List, Any
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, Field, model_serializer, model_validator

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
    LONDON_NY_OVERLAP = "London/NY Overlap"
    SKIP = "Skip"

class ExitType(str, Enum):
    STOP_ORDER = "stop order"
    TIME = "time"
    MANUAL = "manual"
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

class TrendContext(str, Enum):
    SIDEWAYS = "sideways"
    BEARISH = "bearish"
    BULLISH = "bullish"
    SKIP = "Skip"

class ConfirmationStatus(str, Enum):
    YES_BAD_ENTRY = "yes but bad entry poing(too tight)"
    YES_CLOSED_EARLY = "yes but closed too early - Fear"
    NO = "No"
    YES = "Yes"
    YES_LATE_ENTRY = "Yes but late entry"
    SKIP = "Skip"

class ConfirmationParams(str, Enum):
    KL_SUPPORT_RESISTANCE = "KL as support/resistance"
    CONVERGENCE_MAIN_TREND = "convergence with main trend"
    CONVERGENCE_5M_TREND = "convergence with 5M trend"
    LIMIT_ORDER = "limit order"
    FRACTAL_5_10_15 = "5-10-15 min fractal confirmation"
    FRACTAL_1M = "1 min fractal confirmation"
    FRACTAL_1H = "1H fractal confirmation (continuation or inflection)"
    DIVERGENCE_MAIN_TREND = "divergence with main trend"
    DIVERGENCE_5M_TREND = "divergence with 5M trend"
    KEY_LEVEL_TARGET = "key level as target"
    GRABBED_LIQUIDITY = "grabbed liquidity"
    RETRACEMENT_0_4_0_6 = "retracement 0.4-0.6 in X trend"
    CONVERGENCE_X_Y = "convergence in X & Y Trend"
    DIVERGENCE_X_Y = "Divergence in X & Y Trend"
    BREATHING_PRE_TRADE = "breathing pre-trade + process visualization"
    SKIP = "Skip"

class Emotions(str, Enum):
    TRADED_ON_PHONE = "traded on the phone"
    CONSISTENCY = "consistency"
    STATISTICAL_THINKING = "statistical thinking"
    ACCOUNTABILITY = "accountability"
    DECISIVENESS = "decisiveness"
    BLAMING = "blaming"
    SYSTEM_HOPING = "system hoping"
    HOPE_HOLD = "hope hold"
    HESITATION = "hesitation"
    EGO_ATTACHMENT = "ego attatchment to PNL"
    LACK_OF_DISCIPLINE = "lack of discipline"
    ANXIETY_IMMEDIATE_RESULTS = "anxiety for immediate results"
    FOCUS_PRESENCE = "focus / presence"
    DETACHED_NEUTRALITY = "detatched neutrality"
    FLOW_ZONE = "flow / in the zone"
    EQUANIMITY = "equanimity - same mindest after win or loss"
    LOSS_ACCEPTANCE = "loss acceptance"
    GROUNDED_CONFIDENCE = "grounded confidence on your edge"
    CALM_SERENITY = "calm / serenity"
    CLOSED_TOO_EARLY = "closed too early"
    PATIENCE = "patience"
    ANXIETY = "anxiety"
    BOREDOM = "boredom"
    SHAME = "shame"
    FRUSTRATION = "frustration"
    FEAR_NOT_GOOD_ENOUGH = "fear of not being good enough"
    FOMO = "FOMO"
    FEAR_OF_LOSING = "fear of losing"
    FEAR_BEING_WRONG = "fear of being wrong"
    IMPATIENCE = "impatience"
    REVENGE_TRADE = "revenge trade"
    OVERLEVERAGING = "overleveraging"
    OVERTRADING = "overtrading"
    GREED = "greed"
    COURAGE = "courange"
    MAINTAINED_COURAGE = "mantained courage"
    FORBIDDEN_FRIENDSHIP = "forbidden friendship"
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
    exit_type: Optional[ExitType] = None
    followed_plan: Optional[FollowedPlan] = None
    primary_emotion: Optional[PrimaryEmotion] = None
    setup_type: Optional[SetupType] = None
    htf_trend_context: Optional[HTFTrendContext] = None
    confirmation_status: Optional[ConfirmationStatus] = None

    # New manual categorical/text fields
    ltf_trend_context: Optional[TrendContext] = None
    pre_trade_emotions: Optional[str] = None
    mid_trade_emotions: Optional[str] = None
    post_trade_emotions: Optional[str] = None
    confirmation_params: Optional[List[ConfirmationParams]] = None

    # Numeric scales
    anxiety_level: Optional[int] = Field(default=None, ge=1, le=5)
    impatience_level: Optional[int] = Field(default=None, ge=1, le=5)
    mental_clarity_level: Optional[int] = Field(default=None, ge=1, le=5)

    # Multi-Select fields
    emotions: Optional[List[Emotions]] = None
    behavioral_errors: Optional[List[BehavioralErrors]] = None
    cognitive_patterns: Optional[List[CognitivePatterns]] = None

    # Manual Financial and Execution Metrics
    cost: Optional[float] = 0.0
    size: Optional[float] = None
    entry_price: Optional[float] = None
    closing_price: Optional[float] = None
    could_hit_tp: Optional[str] = None
    take_profit: Optional[float] = None
    stop_loss: Optional[float] = None
    mae: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    mfe: Optional[float] = Field(default=None, ge=0.0, le=10.0)

    # Text blocks
    lesson_learned: Optional[str] = None

    # Automated fields
    trade_decision: Optional[str] = None
    r_r: Optional[float] = None
    r_multiple: Optional[float] = None
    notional_size_usd: Optional[float] = None
    risk_usd: Optional[float] = None
    notional_size: Optional[float] = None
    capital_at_risk: Optional[float] = None
    dist_to_sl: Optional[float] = None
    dist_to_tp: Optional[float] = None
    pnl: Optional[float] = None
    pnl_and_cost: Optional[float] = None
    trade_duration: Optional[str] = None
    session: Optional[str] = None
    captured_mfe: Optional[float] = None

    @model_validator(mode='after')
    def calculate_automated_fields(self):
        ep = self.entry_price
        cp = self.closing_price
        tp = self.take_profit
        sl = self.stop_loss
        size = self.size
        cost = self.cost or 0.0
        mfe = self.mfe

        if ep is not None and sl is not None and size is not None:
            # trade_decision = "Long" if entry_price > stop_loss else "Short"
            td = "Long" if ep > sl else "Short"
            self.trade_decision = td

            # notional_size_usd = entry_price * size
            self.notional_size_usd = ep * size
            self.notional_size = ep * size

            # risk_usd = size * abs(entry_price - stop_loss)
            self.risk_usd = size * abs(ep - sl)
            
            if td == "Long":
                self.capital_at_risk = size * (ep - sl)
            else:
                self.capital_at_risk = size * (sl - ep)

            # dist_to_sl = abs(entry_price - stop_loss) / entry_price
            if ep != 0:
                self.dist_to_sl = abs(ep - sl) / ep
            else:
                self.dist_to_sl = 0.0

            if tp is not None and ep != 0:
                # dist_to_tp = abs(entry_price - take_profit) / entry_price
                self.dist_to_tp = abs(ep - tp) / ep

                # r_r = (take_profit - entry_price) / abs(entry_price - stop_loss) [Invert numerator for Short]
                sl_dist_abs = abs(ep - sl)
                if sl_dist_abs != 0:
                    if td == "Long":
                        self.r_r = (tp - ep) / sl_dist_abs
                    else:
                        self.r_r = (ep - tp) / sl_dist_abs
                else:
                    self.r_r = 0.0

            if cp is not None:
                # pnl = (closing_price - entry_price) * size [Invert terms for Short]
                if td == "Long":
                    self.pnl = (cp - ep) * size
                else:
                    self.pnl = (ep - cp) * size

                # pnl_and_cost = pnl - cost
                self.pnl_and_cost = self.pnl - cost

                # r_multiple = (closing_price - entry_price) / abs(entry_price - stop_loss) [Invert numerator for Short]
                sl_dist_abs = abs(ep - sl)
                if sl_dist_abs != 0:
                    if td == "Long":
                        self.r_multiple = (cp - ep) / sl_dist_abs
                    else:
                        self.r_multiple = (ep - cp) / sl_dist_abs
                else:
                    self.r_multiple = 0.0

                # captured_mfe = 0.0 if r_multiple < 0 or mfe == 0 else (r_multiple / mfe)
                if self.r_multiple < 0 or not mfe or mfe == 0.0:
                    self.captured_mfe = 0.0
                else:
                    self.captured_mfe = self.r_multiple / mfe

        if self.entry_time and self.exit_time:
            # trade_duration = "Xh Ym"
            delta = self.exit_time - self.entry_time
            total_seconds = int(delta.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            self.trade_duration = f"{hours}h {minutes}m"

        if self.entry_time:
            # session = Evaluated on entry_time local Guatemala timezone (UTC-6)
            # Ensure entry_time is timezone aware, assuming input is local if naive
            dt = self.entry_time
            if dt.tzinfo is None:
                guatemala_tz = timezone(timedelta(hours=-6))
                dt = dt.replace(tzinfo=guatemala_tz)
            else:
                dt = dt.astimezone(timezone(timedelta(hours=-6)))
                
            hr = dt.hour
            # session = "London/NY Overlap" if 7 <= hr < 10 else "New York" if 10 <= hr < 15 else "London" if 2 <= hr < 7 else "Asia/Off"
            if 7 <= hr < 10:
                self.session = Session.LONDON_NY_OVERLAP.value
            elif 10 <= hr < 15:
                self.session = Session.NEW_YORK.value
            elif 2 <= hr < 7:
                self.session = Session.LONDON.value
            else:
                self.session = Session.ASIA_OFF.value

        return self

