"""
tools/auto_resolution.py — el servicio del resolvedor (spec 002, RF-4e, RF-4h, RF-6b; plan.md §1).

Junta, para los análisis de una cuenta:
  1. su DB, leída en solo lectura y con columnas explícitas. Una columna que todavía no existe (por ejemplo
     `analysis_start_time` antes de T35, o en una cuenta que el CLI no migró, como US100) cuenta como vacía (RF-6b);
  2. el símbolo MT5 de su `asset` (`no_mt5_symbol` si no está en `MT5_SYMBOL_MAP`, RF-4h);
  3. el estado del reloj del símbolo en el banco (`clock_unverified` o `clock_misaligned` si no está verificado,
     RF-4e), y sus velas;
  4. el resolvedor puro (`core/candle_resolution.py`) y la etiqueta Overlap (`core/outcome_metrics.py`).

Devuelve la propuesta ya traducida a los valores del wizard (`ResolutionType`, `StructuralResolution`,
`FailureReason`), que es lo que muestran el Efficiency Audit (T41-T44), el reporte (T32) y el backfill (T47).
Nunca escribe nada: ni en la DB ni en el banco.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Mapping, Optional

from sqlalchemy import text

from cli.schemas.audit_efficiency import FailureReason, ResolutionType, StructuralResolution
from config.auto_resolution import (
    MAX_HORIZON,
    MT5_SYMBOL_MAP,
    REASON_CLOCK_UNVERIFIED,
    REASON_NO_MT5_SYMBOL,
)
from core.candle_resolution import (
    LADDER,
    MARK_PRICE_LADDER,
    OUTCOME_CONFIRMED,
    OUTCOME_INVALIDATED,
    STRUCTURAL_EXPANSION,
    STRUCTURAL_MINIMAL,
    STRUCTURAL_NA,
    STRUCTURAL_REVERTED,
    FAILURE_LIQUIDITY_SWEEP,
    FAILURE_NA,
    FAILURE_OVERLAP,
    AnalysisResolution,
    Candle,
    MarkPriceCheck,
    StructuralProposal,
    check_mark_price,
    propose_structural,
    resolve_analysis,
)
from core.outcome_metrics import AnalysisOutcome, OverlapLabel, analysis_anchor, overlap_labels
from tools.candle_bank import bank_csv_path, read_bank_status, read_candle_csv
from tools.p2_backtest import open_readonly_session

# Columnas de `unified_department` que lee el servicio, en este orden (RF-6b: nunca `SELECT *`).
ANALYSIS_COLUMNS = ("id", "asset", "created_at", "is_backdated", "analysis_start_time", "market_bias",
                    "edge_validation_price", "structural_invalidation", "mark_price", "mark_price_time", "saved_at")

# Códigos neutros del resolvedor → valores de los enums del wizard (cli/schemas/audit_efficiency.py).
RESOLUTION_TYPE_BY_OUTCOME = {
    OUTCOME_CONFIRMED: ResolutionType.CONFIRMED.value,
    OUTCOME_INVALIDATED: ResolutionType.INVALIDATED.value,
}
STRUCTURAL_RESOLUTION_BY_CODE = {
    STRUCTURAL_REVERTED: StructuralResolution.CONFIRMED_REVERTED.value,
    STRUCTURAL_EXPANSION: StructuralResolution.CONFIRMED_EXPANSION.value,
    STRUCTURAL_MINIMAL: StructuralResolution.CONFIRMED_MINIMAL.value,
    STRUCTURAL_NA: StructuralResolution.NA.value,
}
FAILURE_REASON_BY_CODE = {
    FAILURE_NA: FailureReason.NA.value,
    FAILURE_LIQUIDITY_SWEEP: FailureReason.LIQUIDITY_SWEEP.value,
    FAILURE_OVERLAP: FailureReason.OVERLAP.value,
}


@dataclass(frozen=True)
class AnalysisRow:
    """Lo que el servicio lee de un análisis. Una columna ausente en la DB llega como `None` (RF-6b)."""
    trade_id: str
    asset: Optional[str]
    created_at: datetime
    is_backdated: bool
    analysis_start_time: Optional[datetime]
    market_bias: Optional[str]
    edge_validation_price: Optional[float]
    structural_invalidation: Optional[float]
    mark_price: Optional[float]
    mark_price_time: Optional[datetime] = None  # columnas de T35: hasta entonces, vacías
    saved_at: Optional[datetime] = None


def parse_datetime(value) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def parse_float(value) -> Optional[float]:
    return None if value is None else float(value)


def read_analysis_rows(db_path: str) -> List[AnalysisRow]:
    """Los análisis de la DB de una cuenta, en solo lectura (`mode=ro`) y con columnas explícitas (RF-6b)."""
    session = open_readonly_session(db_path)
    try:
        existing = {row[1] for row in session.execute(text("PRAGMA table_info(unified_department)"))}
        present = [column for column in ANALYSIS_COLUMNS if column in existing]
        rows = session.execute(text(f"SELECT {', '.join(present)} FROM unified_department ORDER BY created_at")).all()
    finally:
        session.close()
    result = []
    for row in rows:
        values = {column: None for column in ANALYSIS_COLUMNS}
        values.update(zip(present, row))
        result.append(AnalysisRow(
            trade_id=values["id"], asset=values["asset"], created_at=parse_datetime(values["created_at"]),
            is_backdated=bool(values["is_backdated"]), analysis_start_time=parse_datetime(values["analysis_start_time"]),
            market_bias=values["market_bias"], edge_validation_price=parse_float(values["edge_validation_price"]),
            structural_invalidation=parse_float(values["structural_invalidation"]),
            mark_price=parse_float(values["mark_price"]), mark_price_time=parse_datetime(values["mark_price_time"]),
            saved_at=parse_datetime(values["saved_at"])))
    return result


def load_bank_candles(bank_dir: str) -> Dict[str, List[Candle]]:
    """Las velas de la escalera (1M a 1H) del banco de un símbolo, como `Candle`."""
    candles: Dict[str, List[Candle]] = {}
    for tf in LADDER:
        df = read_candle_csv(bank_csv_path(bank_dir, tf))
        if not df.empty:
            candles[tf] = [Candle(row.time.to_pydatetime(), float(row.open), float(row.high), float(row.low),
                                  float(row.close)) for row in df.itertuples(index=False)]
    return candles


def bank_clock_reason(bank_dir: str) -> Optional[str]:
    """`None` si el reloj del símbolo está verificado; si no, el motivo de `status.json` (RF-4e, N10). Un banco sin
    `status.json` nunca se verificó: `clock_unverified`."""
    try:
        status = read_bank_status(bank_dir)
    except (ValueError, OSError):
        return REASON_CLOCK_UNVERIFIED
    if not status:
        return REASON_CLOCK_UNVERIFIED
    clock = status.get("clock")
    return None if clock == "verified" else (clock or REASON_CLOCK_UNVERIFIED)


# Al guardar un análisis solo se leen las velas de la hora anterior al Mark Price: alcanza para la vela de 15M que lo
# contiene, y el guardado no carga el banco entero.
_MARK_PRICE_READ_WINDOW = timedelta(hours=1)


def check_saved_mark_price(asset: Optional[str], mark_price: float, mark_price_time: datetime, bank_root: str,
                           symbol_map: Optional[Mapping[str, str]] = None) -> Optional[MarkPriceCheck]:
    """
    RF-3: el chequeo del Mark Price de un análisis recién guardado, con la escalera 1M → 5M → 15M de
    `core/candle_resolution.check_mark_price`. `None` si no se puede chequear: el asset no tiene símbolo MT5, el reloj
    del símbolo no está verificado o el banco no tiene vela en esa hora. Solo lee; nunca escribe.
    """
    symbol = (MT5_SYMBOL_MAP if symbol_map is None else symbol_map).get(asset)
    if symbol is None:
        return None
    bank_dir = os.path.join(bank_root, symbol)
    if bank_clock_reason(bank_dir):
        return None
    candles: Dict[str, List[Candle]] = {}
    for tf in MARK_PRICE_LADDER:
        df = read_candle_csv(bank_csv_path(bank_dir, tf))
        near = df[(df["time"] > mark_price_time - _MARK_PRICE_READ_WINDOW) & (df["time"] <= mark_price_time)]
        if not near.empty:
            candles[tf] = [Candle(row.time.to_pydatetime(), float(row.open), float(row.high), float(row.low),
                                  float(row.close)) for row in near.itertuples(index=False)]
    check = check_mark_price(mark_price, mark_price_time, candles)
    return None if check.fits is None else check


@dataclass(frozen=True)
class AutoProposal:
    """
    La propuesta para un análisis. `reason` dice por qué falta algo: `no_mt5_symbol`, `clock_unverified`,
    `clock_misaligned`, `no_levels`, `no_history`, `pending_candles`, `ambiguous` u `open`; es `None` con un toque.
    Los campos `resolution_type`, `structural_resolution` y `failure_reason` ya son valores del wizard, o `None`
    cuando no se proponen (RF-5b: `open` no propone tipo).
    """
    trade_id: str
    symbol: Optional[str]
    anchor: datetime
    reason: Optional[str]
    resolution: Optional[AnalysisResolution] = None
    structural: Optional[StructuralProposal] = None
    overlap: Optional[OverlapLabel] = None
    resolution_type: Optional[str] = None
    structural_resolution: Optional[str] = None
    failure_reason: Optional[str] = None
    resolution_time: Optional[datetime] = None
    structural_mae: Optional[float] = None
    structural_mfe: Optional[float] = None


def _overlap_limit(resolution: AnalysisResolution) -> Optional[datetime]:
    """Hasta cuándo un análisis nuevo vuelve Overlap a este (plan.md §3.3): el toque, la vela ambigua o, sin toque,
    hasta donde llegó el camino (el fin del banco o del horizonte). Sin camino (no_levels, no_history) no se sabe."""
    touch = resolution.first_touch
    if touch is None:
        return None
    if resolution.outcome in (OUTCOME_CONFIRMED, OUTCOME_INVALIDATED):
        return touch.touch_time
    return touch.path_end


class AccountResolver:
    """Resuelve los análisis de la DB de una cuenta. Lee la DB una vez y las velas de cada símbolo una vez."""

    def __init__(self, db_path: str, bank_root: str, account: str,
                 symbol_map: Optional[Mapping[str, str]] = None, max_horizon: int = MAX_HORIZON):
        self.account = account
        self.bank_root = bank_root
        self.symbol_map = MT5_SYMBOL_MAP if symbol_map is None else symbol_map
        self.max_horizon = max_horizon
        self.rows = read_analysis_rows(db_path)
        self._by_id = {row.trade_id: row for row in self.rows}
        self._banks: Dict[str, tuple] = {}

    def anchor_of(self, row: AnalysisRow) -> datetime:
        return analysis_anchor(row.analysis_start_time, row.created_at, row.is_backdated)

    def bank_for(self, symbol: str):
        """`(motivo del reloj o None, velas de la escalera)` del símbolo, leídas una sola vez."""
        if symbol not in self._banks:
            bank_dir = os.path.join(self.bank_root, symbol)
            reason = bank_clock_reason(bank_dir)
            self._banks[symbol] = (reason, {} if reason else load_bank_candles(bank_dir))
        return self._banks[symbol]

    def propose_all(self) -> List[AutoProposal]:
        return [self.propose(row.trade_id) for row in self.rows]

    def outcomes(self, proposals: Optional[List[AutoProposal]] = None) -> List[AnalysisOutcome]:
        """Los resultados de la cuenta para `core/outcome_metrics.py` (S1, S4). Un análisis sin toque lleva como
        `outcome` el código del resolvedor o el motivo (`no_levels`, `clock_unverified`, ...) y no cuenta."""
        proposals = self.propose_all() if proposals is None else proposals
        result = []
        for proposal in proposals:
            row = self._by_id[proposal.trade_id]
            outcome = proposal.resolution.outcome if proposal.resolution else proposal.reason
            result.append(AnalysisOutcome(proposal.trade_id, self.account, proposal.anchor, row.is_backdated,
                                          row.market_bias, outcome, proposal.resolution_time))
        return result

    def propose(self, trade_id: str) -> AutoProposal:
        row = self._by_id[trade_id]
        anchor = self.anchor_of(row)
        symbol = self.symbol_map.get(row.asset)
        if symbol is None:
            return AutoProposal(trade_id, None, anchor, REASON_NO_MT5_SYMBOL)
        clock_reason, candles = self.bank_for(symbol)
        if clock_reason:
            return AutoProposal(trade_id, symbol, anchor, clock_reason)

        resolution = resolve_analysis(anchor, candles, row.edge_validation_price, row.structural_invalidation,
                                      max_horizon=self.max_horizon)
        overlap = self._overlap(row, anchor, resolution)
        structural = propose_structural(resolution, candles, row.edge_validation_price, row.structural_invalidation,
                                        row.mark_price, overlap=overlap is not None)
        touched = resolution.outcome in (OUTCOME_CONFIRMED, OUTCOME_INVALIDATED)
        if overlap is not None:
            resolution_type = ResolutionType.OVERLAP_INVALIDATION.value
        else:
            resolution_type = RESOLUTION_TYPE_BY_OUTCOME.get(resolution.outcome)
        return AutoProposal(
            trade_id, symbol, anchor,
            reason=None if touched else resolution.outcome,
            resolution=resolution, structural=structural, overlap=overlap,
            resolution_type=resolution_type,
            structural_resolution=STRUCTURAL_RESOLUTION_BY_CODE.get(structural.structural_resolution),
            failure_reason=FAILURE_REASON_BY_CODE.get(structural.failure_reason),
            resolution_time=resolution.first_touch.touch_time if touched else None,
            structural_mae=resolution.structural_mae, structural_mfe=resolution.structural_mfe,
        )

    def _overlap(self, row: AnalysisRow, anchor: datetime, resolution: AnalysisResolution) -> Optional[OverlapLabel]:
        limit = _overlap_limit(resolution)
        if limit is None:
            return None
        me = AnalysisOutcome(row.trade_id, self.account, anchor, row.is_backdated, row.market_bias,
                             resolution.outcome, overlap_limit=limit)
        others = [AnalysisOutcome(other.trade_id, self.account, self.anchor_of(other), other.is_backdated,
                                  other.market_bias, "")
                  for other in self.rows if other.trade_id != row.trade_id]
        return overlap_labels([me, *others]).get(row.trade_id)
