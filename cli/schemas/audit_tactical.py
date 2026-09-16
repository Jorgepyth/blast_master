import logging
from enum import Enum
from typing import Optional, List, Any
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, Field, model_serializer, model_validator

class SkipReason(str, Enum):
    PRECIO_NUNCA_LLEGO = "precio_nunca_llego"
    DISCRECIONAL_NO_TOMADO = "discrecional_no_tomado"
    FALLO_TECNICO = "fallo_tecnico"
    INVALIDADA_ANTES_DE_LLENAR = "invalidada_antes_de_llenar"
    SIN_SETUP_IDENTIFICADO = "sin_setup_identificado"
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
    S1_CLEAN = "S1: Sí - ejecución limpia"
    S2_EARLY = "S2: Sí, pero entrada muy estrecha/temprana"
    S3_LATE = "S3: Sí, pero entrada tardía (persiguió precio)"
    S4_FEAR_CLOSE = "S4: Sí, pero cerró antes de tiempo (miedo)"
    S5_MOVED_SLTP = "S5: Sí, pero movió SL/TP después de entrar"
    S6_FEAR_NO_ENTRY = "S6: No - setup válido, nunca entró (miedo)"
    S7_REVENGE_FORCED = "S7: No - forzó entrada pese a gate fallido (revenge)"
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

# Curated subset offered to the user going forward (picker UI only).
# The full Emotions enum above is left untouched so historical records tagged
# with a cut/merged value (e.g. "hesitation", "mantained courage") keep parsing fine.
# Cut (duplicates of behavioral_errors or the anxiety_level/impatience_level scales):
#   traded on the phone, hesitation, lack of discipline, closed too early,
#   anxiety, impatience, revenge trade, overtrading
# Merged away (near-duplicate constructs, one canonical value kept):
#   system hoping -> hope hold, focus/presence -> flow/in the zone,
#   detatched neutrality -> equanimity, calm/serenity -> equanimity,
#   mantained courage -> courange
ACTIVE_EMOTIONS = [
    Emotions.CONSISTENCY, Emotions.STATISTICAL_THINKING, Emotions.ACCOUNTABILITY, Emotions.DECISIVENESS,
    Emotions.BLAMING, Emotions.HOPE_HOLD, Emotions.EGO_ATTACHMENT, Emotions.ANXIETY_IMMEDIATE_RESULTS,
    Emotions.FLOW_ZONE, Emotions.EQUANIMITY, Emotions.LOSS_ACCEPTANCE, Emotions.GROUNDED_CONFIDENCE,
    Emotions.PATIENCE, Emotions.BOREDOM, Emotions.SHAME, Emotions.FRUSTRATION, Emotions.FEAR_NOT_GOOD_ENOUGH,
    Emotions.FOMO, Emotions.FEAR_OF_LOSING, Emotions.FEAR_BEING_WRONG, Emotions.OVERLEVERAGING,
    Emotions.GREED, Emotions.COURAGE, Emotions.FORBIDDEN_FRIENDSHIP,
]

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

class StopDeviationReason(str, Enum):
    RISK_BUDGET_CONSTRAINT = "risk_budget_constraint"
    ALT_INVALIDATION_LEVEL = "alt_invalidation_level"
    TIME_STOP_SUBSTITUTE   = "time_stop_substitute"
    DISCRETIONARY_NO_BASIS = "discretionary_no_basis"
    SUSPECTED_DATA_ERROR   = "suspected_data_error"
    OTHER_CODED            = "other_coded"

# Texto completo mostrado al operador (cli/main.py:ask_stop_deviation_reason). El
# `.value` de arriba es lo que se persiste; este dict es solo de presentación --
# a diferencia de FailureReason (cli/schemas/audit_efficiency.py), donde el texto
# largo vive directo en el .value.
STOP_DEVIATION_REASON_LABELS: dict = {
    StopDeviationReason.RISK_BUDGET_CONSTRAINT: "Restricción de riesgo/tamaño -- Tu riesgo máximo permitido ($/%) o el tamaño mínimo de lote disponible no te dejaban usar la distancia completa hasta structural_invalidation sin exceder el riesgo. En vez de reducir el tamaño de posición, acortaste el stop.",
    StopDeviationReason.ALT_INVALIDATION_LEVEL: "Nivel de invalidación alternativo -- Identificaste un nivel técnico más cercano al entry (micro-estructura, order block, FVG de un timeframe menor) que consideras invalidación válida para esta operación, distinto al nivel macro registrado en el análisis estructural de Fase 1.",
    StopDeviationReason.TIME_STOP_SUBSTITUTE: "Sustituto de time-stop -- Tu plan real es salir por comportamiento de precio/tiempo antes de llegar a la invalidación estructural completa. El stop de precio es un límite de seguridad, no tu criterio de salida principal.",
    StopDeviationReason.DISCRETIONARY_NO_BASIS: "Discrecional sin base técnica -- No hay un nivel, regla o cálculo que respalde el stop más angosto. Fue una decisión en el momento (comodidad, ansiedad, deseo de reducir exposición) sin justificación técnica.",
    StopDeviationReason.SUSPECTED_DATA_ERROR: "Posible error de captura -- Sospechas que el stop que tecleaste, o el structural_invalidation guardado en el análisis original, tiene un error. Marca esto para revisión manual.",
    StopDeviationReason.OTHER_CODED: "Otra razón -- Ninguna de las anteriores aplica.",
}

class TacticalAudit(BaseModel):
    tactical_id: str
    tactical_row_id: Optional[str] = None  # existing tactical_audit.id being edited; None = create a new row
    # Instrument symbol (unified_department.asset). Nullable so existing construction
    # sites that don't pass it still work. Used by the validator to resolve
    # contract_size via tools/pnl_calculator for risk_usd / notional_size_usd.
    asset: Optional[str] = None
    order_filled: bool = True
    skip_reason: Optional[SkipReason] = None
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
    # Stop Deviation Journaling (auditable, no bloqueante) -- solo se puebla cuando
    # stop_slippage_r > 0. Ver cli/main.py (ask_stop_deviation_reason).
    stop_deviation_reason: Optional[StopDeviationReason] = None

    # New manual categorical/text fields
    ltf_trend_context: Optional[TrendContext] = None
    confirmation_5m_15m: Optional[str] = None
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
    # Stop Deviation Journaling: (stop_loss vs unified_department.structural_invalidation),
    # calculado siempre en cli/main.py cuando hay dato base; None si no hay
    # structural_invalidation. Nunca bloquea el guardado.
    stop_slippage_r: Optional[float] = None
    mae: Optional[float] = Field(default=None, ge=0.0, le=10.0)
    mfe: Optional[float] = Field(default=None, ge=0.0, le=10.0)

    # Text blocks
    lesson_learned: Optional[str] = None
    visual_lesson_path: Optional[str] = None
    # Justificación obligatoria cuando el gate emocional (anxiety_level >= 4,
    # cli/main.py) se dispara. Gate independiente del Tier D/F -- ver
    # ARCHITECTURE.md §15.
    emotional_gate_override_reason: Optional[str] = None
    # Stop Deviation Journaling: nota libre opcional, nunca validada ni usada en
    # reportes cuantitativos -- contexto humano suplementario.
    stop_deviation_note: Optional[str] = None

    # Motor B (Gates)
    g1_trend_15m: Optional[bool] = False
    g2_fractal_trend: Optional[bool] = False
    g3_limit_order: Optional[bool] = False
    g4_breathing: Optional[bool] = False
    g5_manual_cooldown: Optional[bool] = False
    g6_sl_validated: Optional[bool] = False
    g7_tp_validated: Optional[bool] = False

    # Motor B (Confirmations)
    c1_kl_support: Optional[bool] = False
    c2_fractal_std: Optional[bool] = False
    c3_fractal_1m: Optional[bool] = False
    c4_fractal_1h: Optional[bool] = False
    c5_kl_target: Optional[bool] = False
    c6_liquidity: Optional[bool] = False
    c7_retracement: Optional[bool] = False
    c8_convergence_15m: Optional[bool] = False

    # Motor B (Metrics)
    gates_failed: Optional[int] = None
    confirmations_count: Optional[int] = None
    mfe_potencial_estimado: Optional[float] = None

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
    captured_mae: Optional[float] = None

    @model_validator(mode='after')
    def calculate_automated_fields(self):
        ep = self.entry_price
        cp = self.closing_price
        tp = self.take_profit
        sl = self.stop_loss
        size = self.size
        cost = self.cost or 0.0
        mfe = self.mfe
        mae = self.mae

        if ep is not None and sl is not None and size is not None:
            # trade_decision = "Long" if entry_price > stop_loss else "Short"
            td = "Long" if ep > sl else "Short"
            self.trade_decision = td

            # Campos monetarios (notional_size, notional_size_usd, risk_usd,
            # capital_at_risk, y más abajo pnl / pnl_and_cost): fórmula
            # `precio · size · contract_size`. El contract_size del instrumento se
            # resuelve una sola vez vía tools/pnl_calculator (config/contract_specs.py).
            # Sin asset, o símbolo NO VERIFICADO / desconocido -> _cs = None: TODOS
            # estos campos quedan en None + warn-log; el resto del tactical_audit se
            # guarda igual (nunca bloquea el guardado).
            # Fase 2f (2026-09-06): notional_size / capital_at_risk / pnl dejaron de
            # ignorar contract_size (antes usaban `ep*size` / `size*(ep-sl)` / etc.
            # crudos, 100x mal en XAUUSDT.P). risk_usd / notional_size_usd ya lo
            # aplicaban desde Fase 2a.
            from decimal import Decimal as _D
            from tools import pnl_calculator as _pnl
            try:
                _cs = _pnl.resolve_spec(self.asset)["contract_size"]
            except (_pnl.UnverifiedSymbolError, _pnl.UnknownSymbolError) as _e:
                _cs = None
                logging.getLogger(__name__).warning(
                    "campos monetarios sin calcular (tactical_id=%s, asset=%r): %s",
                    self.tactical_id, self.asset, _e,
                )

            if _cs is not None:
                _epd, _sld, _szd = _D(str(ep)), _D(str(sl)), _D(str(size))
                self.notional_size = _epd * _szd * _cs
                self.notional_size_usd = _epd * _szd * _cs
                self.risk_usd = abs(_epd - _sld) * _szd * _cs
                if td == "Long":
                    self.capital_at_risk = _szd * (_epd - _sld) * _cs
                else:
                    self.capital_at_risk = _szd * (_sld - _epd) * _cs
            else:
                self.notional_size = None
                self.notional_size_usd = None
                self.risk_usd = None
                self.capital_at_risk = None

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
                # pnl = (closing_price - entry_price) * size * contract_size [Invert terms for Short]
                # Fase 2f: sin contract_size resuelto (_cs None) -> pnl / pnl_and_cost None,
                # mismo criterio que el resto de los campos monetarios.
                if _cs is not None:
                    _epd, _cpd, _szd = _D(str(ep)), _D(str(cp)), _D(str(size))
                    if td == "Long":
                        self.pnl = (_cpd - _epd) * _szd * _cs
                    else:
                        self.pnl = (_epd - _cpd) * _szd * _cs
                    # pnl_and_cost = pnl - cost
                    self.pnl_and_cost = self.pnl - _D(str(cost))
                else:
                    self.pnl = None
                    self.pnl_and_cost = None

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

                # captured_mae
                if self.r_multiple < 0 and mae and mae > 0.0:
                    self.captured_mae = abs(self.r_multiple) / mae
                else:
                    self.captured_mae = 0.0

        # entry_time and exit_time are expected to be naive UTC

        if self.entry_time and self.exit_time:
            # Both are now standardized to naive UTC
            delta = self.exit_time - self.entry_time
            total_seconds = int(delta.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            self.trade_duration = f"{hours}h {minutes}m"

        if self.entry_time:
            utc_check_time = self.entry_time + timedelta(hours=6)
            hr = utc_check_time.hour
            
            if 13 <= hr < 16:
                self.session = Session.LONDON_NY_OVERLAP.value
            elif 16 <= hr < 21:
                self.session = Session.NEW_YORK.value
            elif 8 <= hr < 13:
                self.session = Session.LONDON.value
            else:
                self.session = Session.ASIA_OFF.value

        return self

