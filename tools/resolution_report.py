"""
tools/resolution_report.py — los datos del reporte de comparación (spec 002, RF-6, RF-3b, RF-17, RF-21).

Por cada cuenta de `REAL_ACCOUNTS`, compara lo que proponen las velas (`tools/auto_resolution.py`) con lo que el
operador cargó a mano en el Efficiency Audit:
  - cuántos se resolvieron, el % faltante y por qué (RF-6);
  - la coincidencia por campo (`resolution_type`, `structural_mae`/`structural_mfe`, `structural_resolution`,
    `failure_reason`) y la lista de los que difieren;
  - `specific_bias_compliance` al lado, solo informativo, sin porcentaje (N23);
  - la demora del audit: la hora en que se registró menos la del toque;
  - S1 y S4 para todos y para los direccionales, con el win rate manual sobre los mismos análisis (N35, RF-21);
  - cada Overlap con su primer toque y su B (N36, N37);
  - los Mark Price que no caen en sus velas (RF-3, RF-3b);
  - en los retroactivos con `saved_at`, cuánto después se cargaron (N33, R11).

Solo arma los datos; el Markdown y el comando los hace T33. Nunca escribe: las DBs se leen en `mode=ro`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Mapping, Optional, Tuple

from sqlalchemy import text

from config.auto_resolution import ANCHOR_FALLBACK_MIN, MARK_PRICE_TOLERANCE, REAL_ACCOUNTS, S4_WINDOW_H
from core.candle_resolution import MarkPriceCheck, check_mark_price, check_mark_price_in_period
from core.outcome_metrics import AnalysisOutcome, WinRate, counted_outcomes, s1, s4
from tools.auto_resolution import AccountResolver, AutoProposal, parse_datetime, parse_float
from tools.p2_backtest import open_readonly_session

# Columnas de `efficiency_audit` que lee el reporte (RF-6b). `audit_registration_time` llega con T35.
EFFICIENCY_COLUMNS = ("id", "resolution_type", "real_bias_b", "structural_resolution", "failure_reason",
                      "specific_bias_compliance", "resolution_time", "audit_registration_time", "structural_mae",
                      "structural_mfe")
COMPARED_FIELDS = ("resolution_type", "structural_mae", "structural_mfe", "structural_resolution", "failure_reason")
# MAE y MFE son precios tipeados a mano: coinciden si la diferencia es de 0.1% o menos (la tolerancia de N16).
PRICE_AGREEMENT_TOLERANCE = MARK_PRICE_TOLERANCE


@dataclass(frozen=True)
class ManualAudit:
    """Lo que el operador cargó en el Efficiency Audit. Un `resolution_type = "Open"` con `real_bias_b` vacío es un
    audit nunca hecho y cuenta como vacío (N9)."""
    trade_id: str
    resolution_type: Optional[str]
    structural_resolution: Optional[str]
    failure_reason: Optional[str]
    specific_bias_compliance: Optional[str]
    resolution_time: Optional[datetime]
    audit_registration_time: Optional[datetime]
    structural_mae: Optional[float]
    structural_mfe: Optional[float]


def read_manual_audits(db_path: str) -> Dict[str, ManualAudit]:
    """Los Efficiency Audit de una cuenta, en solo lectura y con columnas explícitas; una ausente cuenta como vacía."""
    session = open_readonly_session(db_path)
    try:
        tables = {row[0] for row in session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        if "efficiency_audit" not in tables:
            return {}
        existing = {row[1] for row in session.execute(text("PRAGMA table_info(efficiency_audit)"))}
        present = [column for column in EFFICIENCY_COLUMNS if column in existing]
        rows = session.execute(text(f"SELECT {', '.join(present)} FROM efficiency_audit")).all()
    finally:
        session.close()
    audits = {}
    for row in rows:
        values = {column: None for column in EFFICIENCY_COLUMNS}
        values.update(zip(present, row))
        resolution_type = values["resolution_type"]
        if resolution_type == "Open" and values["real_bias_b"] is None:
            resolution_type = None
        audits[values["id"]] = ManualAudit(
            values["id"], resolution_type, values["structural_resolution"], values["failure_reason"],
            values["specific_bias_compliance"], parse_datetime(values["resolution_time"]),
            parse_datetime(values["audit_registration_time"]), parse_float(values["structural_mae"]),
            parse_float(values["structural_mfe"]))
    return audits


def _agrees(field_name: str, automatic, manual) -> Optional[bool]:
    if automatic is None or manual is None:
        return None
    if field_name in ("structural_mae", "structural_mfe"):
        return abs(automatic - manual) <= PRICE_AGREEMENT_TOLERANCE * abs(manual)
    return automatic == manual


@dataclass(frozen=True)
class AnalysisComparison:
    """Un análisis: la propuesta de las velas, lo manual, la coincidencia por campo (`None` = no se compara) y la
    demora del audit en horas."""
    trade_id: str
    proposal: AutoProposal
    manual: Optional[ManualAudit]
    agreement: Dict[str, Optional[bool]]
    compliance: Optional[str]
    audit_delay_h: Optional[float]

    @property
    def differs(self) -> bool:
        return any(value is False for value in self.agreement.values())


@dataclass(frozen=True)
class WinRatePair:
    """Un win rate por velas y, sobre los mismos análisis, cuántos marcó el operador como `Valid`."""
    candles: WinRate
    manual_wins: int


@dataclass(frozen=True)
class OverlapEntry:
    trade_id: str
    level: Optional[str]
    touch_time: Optional[datetime]
    b_id: str
    b_anchor: datetime


@dataclass(frozen=True)
class MarkPriceMisfit:
    trade_id: str
    mark_price: float
    check: MarkPriceCheck
    with_mark_price_time: bool


@dataclass
class AccountReport:
    account: str
    db_name: str
    total: int
    resolved: int
    missing_reasons: Dict[str, int]
    comparisons: List[AnalysisComparison]
    s1_all: WinRatePair
    s4_all: WinRatePair
    s1_directional: WinRatePair
    s4_directional: WinRatePair
    overlaps: List[OverlapEntry]
    mark_price_misfits: List[MarkPriceMisfit]
    backdated_delays: List[Tuple[str, float]] = field(default_factory=list)

    @property
    def missing_pct(self) -> Optional[float]:
        return (self.total - self.resolved) / self.total if self.total else None

    @property
    def differences(self) -> List[AnalysisComparison]:
        return [comparison for comparison in self.comparisons if comparison.differs]


def _compare(proposal: AutoProposal, manual: Optional[ManualAudit]) -> AnalysisComparison:
    agreement = {name: _agrees(name, getattr(proposal, name), getattr(manual, name) if manual else None)
                 for name in COMPARED_FIELDS}
    registered = (manual.audit_registration_time or manual.resolution_time) if manual else None
    delay = None
    if registered is not None and proposal.resolution_time is not None:
        delay = (registered - proposal.resolution_time).total_seconds() / 3600
    return AnalysisComparison(proposal.trade_id, proposal, manual, agreement,
                              manual.specific_bias_compliance if manual else None, delay)


def _pair(outcomes: List[AnalysisOutcome], manual: Mapping[str, ManualAudit], window_h: Optional[float],
          directional_only: bool) -> WinRatePair:
    rate = s4(outcomes, directional_only=directional_only, window_h=window_h) if window_h is not None else (
        s1(outcomes, directional_only=directional_only))
    same = counted_outcomes(outcomes, window_h=window_h, directional_only=directional_only)
    valid = sum(1 for item in same
                if manual.get(item.analysis_id) and manual[item.analysis_id].specific_bias_compliance == "Valid")
    return WinRatePair(rate, valid)


def build_account_report(account: str, db_path: str, bank_root: str) -> AccountReport:
    resolver = AccountResolver(db_path, bank_root, account=account)
    manual = read_manual_audits(db_path)
    proposals = resolver.propose_all()
    rows = {row.trade_id: row for row in resolver.rows}

    resolved = [p for p in proposals if p.resolution_time is not None]
    missing: Dict[str, int] = {}
    for proposal in proposals:
        if proposal.resolution_time is None:
            missing[proposal.reason] = missing.get(proposal.reason, 0) + 1

    outcomes = []
    for proposal in proposals:
        row = rows[proposal.trade_id]
        outcome = proposal.resolution.outcome if proposal.resolution else proposal.reason
        outcomes.append(AnalysisOutcome(proposal.trade_id, account, proposal.anchor, row.is_backdated,
                                        row.market_bias, outcome, proposal.resolution_time))

    overlaps = [OverlapEntry(p.trade_id, p.resolution.first_touch.level if p.resolution.first_touch else None,
                             p.resolution_time, p.overlap.b_id, p.overlap.b_anchor)
                for p in proposals if p.overlap is not None]

    misfits = []
    for proposal in proposals:
        row = rows[proposal.trade_id]
        if row.mark_price is None or proposal.symbol is None:
            continue
        clock_reason, candles = resolver.bank_for(proposal.symbol)
        if clock_reason:
            continue
        if row.mark_price_time is not None:
            check = check_mark_price(row.mark_price, row.mark_price_time, candles)
        else:
            check = check_mark_price_in_period(row.mark_price, row.created_at - timedelta(minutes=ANCHOR_FALLBACK_MIN),
                                               row.created_at, candles)
        if check.fits is False:
            misfits.append(MarkPriceMisfit(proposal.trade_id, row.mark_price, check, row.mark_price_time is not None))

    backdated = [(row.trade_id, (row.saved_at - row.created_at).total_seconds() / 3600)
                 for row in resolver.rows if row.is_backdated and row.saved_at is not None]

    return AccountReport(
        account=account, db_name=os.path.basename(db_path), total=len(proposals), resolved=len(resolved),
        missing_reasons=missing, comparisons=[_compare(p, manual.get(p.trade_id)) for p in proposals],
        s1_all=_pair(outcomes, manual, None, False), s4_all=_pair(outcomes, manual, S4_WINDOW_H, False),
        s1_directional=_pair(outcomes, manual, None, True), s4_directional=_pair(outcomes, manual, S4_WINDOW_H, True),
        overlaps=overlaps, mark_price_misfits=misfits, backdated_delays=backdated,
    )


def build_report(accounts_data_dir: str, bank_root: str,
                 real_accounts: Optional[Mapping[str, str]] = None) -> List[AccountReport]:
    """Un `AccountReport` por cada cuenta de `real_accounts` (por defecto `REAL_ACCOUNTS`) cuyo archivo exista."""
    real_accounts = REAL_ACCOUNTS if real_accounts is None else real_accounts
    reports = []
    for account, db_name in real_accounts.items():
        db_path = os.path.join(accounts_data_dir, db_name)
        if os.path.exists(db_path):
            reports.append(build_account_report(account, db_path, bank_root))
    return reports
