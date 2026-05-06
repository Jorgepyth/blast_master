import uuid
import datetime
from enum import Enum
from typing import Dict, Any, List, Optional
from sqlalchemy import String, DateTime, JSON, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from sqlalchemy import create_engine

class LifecycleState(str, Enum):
    ANALYSIS = "ANALYSIS"
    OPEN = "OPEN"
    CLOSED_PENDING = "CLOSED_PENDING"
    READY_FOR_NOTION = "READY_FOR_NOTION"
    SYNCED = "SYNCED"
    FAILED = "FAILED"

class Base(DeclarativeBase):
    pass

class TradeRecord(Base):
    __tablename__ = "trade_records"
    
    id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, default=LifecycleState.ANALYSIS.value)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

engine_default = None

import os

def init_db(db_url: str = "sqlite:///.data/journal.db"):
    global engine_default
    if db_url.startswith("sqlite:///.data"):
        os.makedirs(".data", exist_ok=True)
    engine_default = create_engine(db_url, connect_args={'timeout': 15})
    Base.metadata.create_all(engine_default)
    return engine_default

def create_record(record_id: str, payload_data: dict, engine=None) -> str:
    eng = engine or engine_default
    with Session(eng) as session:
        new_record = TradeRecord(
            id=record_id,
            state=LifecycleState.ANALYSIS.value,
            payload=payload_data
        )
        session.add(new_record)
        session.commit()
        return record_id

def update_record_state(record_id: str, new_state: LifecycleState, append_payload: Optional[dict] = None, engine=None):
    eng = engine or engine_default
    with Session(eng) as session:
        stmt = select(TradeRecord).where(TradeRecord.id == record_id)
        record = session.scalars(stmt).first()
        if record:
            record.state = new_state.value
            if append_payload:
                current_payload = record.payload or {}
                new_payload = current_payload.copy()
                new_payload.update(append_payload)
                record.payload = new_payload
            session.commit()
            return True
        return False

def get_records_by_state(state: LifecycleState, engine=None) -> List[dict]:
    eng = engine or engine_default
    with Session(eng) as session:
        stmt = select(TradeRecord).where(TradeRecord.state == state.value)
        records = session.scalars(stmt).all()
        return [
            {
                "id": r.id,
                "state": r.state,
                "payload": r.payload,
                "created_at": r.created_at,
                "updated_at": r.updated_at
            }
            for r in records
        ]
