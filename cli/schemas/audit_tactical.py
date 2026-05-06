from enum import Enum
from typing import Optional
from datetime import datetime
from pydantic import BaseModel

class TradeStatus(str, Enum):
    GOOD_EXECUTION = "Trade_taken_good_execution"
    BAD_EXECUTION = "Trade_taken_bad_execution"
    NO_TAKEN = "Trade_no_taken"

class ComplianceState(str, Enum):
    INVALID_EDGE = "Invalid_edge"
    NO_EDGE = "No_edge"
    EDGE_VALID = "Edge_valid"
    BIAS_BAD_ENTRY = "Bias_but_bad_entry"

class TacticalAudit(BaseModel):
    tactical_id: str
    trade_status: TradeStatus
    compliance: ComplianceState
    entry_time: Optional[datetime] = None
    exit_time: Optional[datetime] = None
    pnl_and_cost: Optional[float] = None
    mfe: Optional[float] = None
    mae: Optional[float] = None
