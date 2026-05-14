import os
import uuid
import datetime
from enum import Enum
from typing import Dict, Any, List, Optional
from sqlalchemy import String, DateTime, Float, Integer, ForeignKey, select, JSON, BigInteger, SmallInteger, Boolean, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session, relationship
from sqlalchemy import create_engine

class LifecycleState(str, Enum):
    ANALYSIS = "ANALYSIS"
    PENDING_TACTICS = "PENDING_TACTICS"
    OPEN = "OPEN"
    PENDING_AUDITS = "PENDING_AUDITS"
    READY_FOR_NOTION = "READY_FOR_NOTION"
    COMPLETED = "COMPLETED"
    SYNCED = "SYNCED"
    FAILED = "FAILED"

class Base(DeclarativeBase):
    pass

class AssetBalance(Base):
    __tablename__ = "asset_balance"
    
    asset_code: Mapped[str] = mapped_column(String, primary_key=True)
    amount_atomic: Mapped[int] = mapped_column(BigInteger)
    scale: Mapped[int] = mapped_column(SmallInteger)

class EmotionCatalog(Base):
    __tablename__ = "emotion_catalog"
    
    emotion_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

class AssetConfig(Base):
    __tablename__ = "asset_config"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_name: Mapped[str] = mapped_column(String, unique=True)
    category: Mapped[str] = mapped_column(String)
    code: Mapped[str] = mapped_column(String, unique=True)
    display_name: Mapped[str] = mapped_column(String)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class AnalysisLayer(Base):
    __tablename__ = "analysis_layer"
    
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    trade_id: Mapped[str] = mapped_column(String, ForeignKey("efficiency_department.id", ondelete="CASCADE"))
    department: Mapped[str] = mapped_column(String)
    layer_name: Mapped[str] = mapped_column(String)
    direction: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    strength: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    thesis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    efficiency_department: Mapped["EfficiencyDepartment"] = relationship(back_populates="analysis_layers")

class EfficiencyDepartment(Base):
    __tablename__ = "efficiency_department"
    
    id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, default=LifecycleState.ANALYSIS.value)
    asset: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC), onupdate=lambda: datetime.datetime.now(datetime.UTC))
    
    market_bias: Mapped[str] = mapped_column(String)
    calc_edge: Mapped[float] = mapped_column(Float)
    edge_description: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    analysis_layers: Mapped[List["AnalysisLayer"]] = relationship(back_populates="efficiency_department", cascade="all, delete-orphan")
    efficiency_audit: Mapped[Optional["EfficiencyAudit"]] = relationship(back_populates="efficiency_department", cascade="all, delete-orphan", single_parent=True)
    tactical_department: Mapped[Optional["TacticalDepartment"]] = relationship(back_populates="efficiency_department", cascade="all, delete-orphan", single_parent=True)
    tactical_audit: Mapped[Optional["TacticalAudit"]] = relationship(back_populates="efficiency_department", cascade="all, delete-orphan", single_parent=True)

class EfficiencyAudit(Base):
    __tablename__ = "efficiency_audit"
    
    id: Mapped[str] = mapped_column(String, ForeignKey("efficiency_department.id", ondelete="CASCADE"), primary_key=True)
    bias_a: Mapped[str] = mapped_column(String)
    resolution_type: Mapped[Optional[str]] = mapped_column(String, nullable=True, default="Open")
    real_bias_b: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    structural_resolution: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    specific_bias_compliance: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    false_regime_rate: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    resolution_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    
    lesson_learned: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC), onupdate=lambda: datetime.datetime.now(datetime.UTC))

    efficiency_department: Mapped["EfficiencyDepartment"] = relationship(back_populates="efficiency_audit")

class TacticalDepartment(Base):
    __tablename__ = "tactical_department"
    
    id: Mapped[str] = mapped_column(String, ForeignKey("efficiency_department.id", ondelete="CASCADE"), primary_key=True)
    trade_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    p4_hierarchy: Mapped[str] = mapped_column(String)
    p1_timeframe: Mapped[str] = mapped_column(String)
    p1_type: Mapped[str] = mapped_column(String)
    nodes_l1: Mapped[int] = mapped_column(Integer)
    nodes_l2: Mapped[int] = mapped_column(Integer)
    tactical_classification: Mapped[str] = mapped_column(String)
    
    calc_edge: Mapped[float] = mapped_column(Float)
    long_prob: Mapped[float] = mapped_column(Float)
    short_prob: Mapped[float] = mapped_column(Float)
    no_trade_prob: Mapped[float] = mapped_column(Float)
    
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=lambda: datetime.datetime.now(datetime.UTC), onupdate=lambda: datetime.datetime.now(datetime.UTC))

    efficiency_department: Mapped["EfficiencyDepartment"] = relationship(back_populates="tactical_department")

class TacticalAudit(Base):
    __tablename__ = "tactical_audit"
    
    id: Mapped[str] = mapped_column(String, ForeignKey("efficiency_department.id", ondelete="CASCADE"), primary_key=True)
    compliance: Mapped[str] = mapped_column(String)
    entry_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    exit_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    # Categorical data
    tier_setup: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    market_state: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    session: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    exit_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    trade_decision: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    followed_plan: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    primary_emotion: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    setup_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    htf_trend_context: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    confirmation_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Numeric scales
    anxiety_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    impatience_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mental_clarity_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Multi-Select fields
    emotions: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    behavioral_errors: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    cognitive_patterns: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # Financial and execution metrics
    risk_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    size: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    r_r: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entry_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    closing_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl_and_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mae_adverse: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    captured_mae: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mfe_favorable: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Text blocks
    lesson_learned: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    efficiency_department: Mapped["EfficiencyDepartment"] = relationship(back_populates="tactical_audit")

engine_default = None

def init_db(db_url: str = "sqlite:///.data/journal.db"):
    global engine_default
    if db_url.startswith("sqlite:///.data"):
        os.makedirs(os.path.dirname(db_url.split("sqlite:///")[1]), exist_ok=True)
    engine_default = create_engine(db_url)
    Base.metadata.create_all(engine_default)
    
    # Safe migration for legacy state
    from sqlalchemy import text
    with Session(engine_default) as session:
        session.execute(text("UPDATE efficiency_department SET state = 'PENDING_TACTICAL_AUDIT' WHERE state = 'CLOSED_PENDING'"))
        session.commit()
        
    return engine_default

def get_assets(engine=None) -> List[str]:
    eng = engine or engine_default
    with Session(eng) as session:
        return [a.asset_name for a in session.scalars(select(AssetConfig)).all()]

def add_asset(asset_name: str, category: str = "Crypto", code: str = None, display_name: str = None, engine=None) -> None:
    if code is None:
        code = asset_name
    if display_name is None:
        display_name = asset_name

    eng = engine or engine_default
    with Session(eng) as session:
        existing = session.scalar(select(AssetConfig).where(AssetConfig.asset_name == asset_name))
        if not existing:
            new_asset = AssetConfig(asset_name=asset_name, category=category, code=code, display_name=display_name)
            session.add(new_asset)
            session.commit()

def _to_primitives(model: Any) -> dict:
    if hasattr(model, 'model_dump'):
        d = model.model_dump()
    elif isinstance(model, dict):
        d = model.copy()
    else:
        return {}
    return {k: v.value if hasattr(v, 'value') else v for k, v in d.items()}

def create_record(record_id: str, payload_data: dict, engine=None) -> str:
    from rich.console import Console
    console = Console()
    eng = engine or engine_default
    with Session(eng) as session:
        try:
            asset = payload_data.get("asset")
            d = payload_data.get("efficiency", {})
            
            new_record = EfficiencyDepartment(
                id=record_id,
                state=LifecycleState.ANALYSIS.value,
                asset=asset,
                market_bias=d.get('Market_Bias'),
                calc_edge=d.get('Calc_edge')
            )
            session.add(new_record)
            
            layers = payload_data.get("efficiency_layers", [])
            for layer_dict in layers:
                al = AnalysisLayer(
                    trade_id=record_id,
                    department='EFFICIENCY',
                    layer_name=layer_dict.get('layer_name'),
                    direction=layer_dict.get('direction'),
                    strength=layer_dict.get('strength'),
                    score=layer_dict.get('score'),
                    thesis=layer_dict.get('thesis')
                )
                session.add(al)

            session.commit()
            return record_id
        except Exception as e:
            session.rollback()
            console.print(f"[bold red]Database Error in create_record: {e}[/bold red]")
            return False

def update_record_state(record_id: str, new_state: LifecycleState, append_payload: dict = None, engine=None):
    from rich.console import Console
    console = Console()
    eng = engine or engine_default
    append_payload = append_payload or {}
    with Session(eng) as session:
        try:
            stmt = select(EfficiencyDepartment).where(EfficiencyDepartment.id == record_id)
            record = session.scalars(stmt).first()
            if not record:
                return False
                
            record.state = new_state.value

            if 'trade_status' in append_payload:
                ts_val = append_payload['trade_status']
                if record.tactical_department:
                    record.tactical_department.trade_status = ts_val
                    record.tactical_department.updated_at = datetime.datetime.now(datetime.UTC)
            
            if 'audit_efficiency' in append_payload:
                ae_dict = append_payload['audit_efficiency']
                valid_keys = {c.key for c in EfficiencyAudit.__table__.columns}
                filtered_ae = {k: v for k, v in ae_dict.items() if k in valid_keys}
                
                if record.efficiency_audit:
                    for k, v in filtered_ae.items():
                        setattr(record.efficiency_audit, k, v)
                    record.efficiency_audit.updated_at = datetime.datetime.now(datetime.UTC)
                else:
                    new_ae = EfficiencyAudit(id=record_id, **filtered_ae)
                    session.add(new_ae)
                    
            if 'tactical' in append_payload:
                t_dict = append_payload['tactical']
                valid_keys = {c.key for c in TacticalDepartment.__table__.columns}
                filtered_t = {k: v for k, v in t_dict.items() if k in valid_keys}
                
                if record.tactical_department:
                    for k, v in filtered_t.items():
                        setattr(record.tactical_department, k, v)
                    record.tactical_department.updated_at = datetime.datetime.now(datetime.UTC)
                else:
                    new_t = TacticalDepartment(id=record_id, **filtered_t)
                    session.add(new_t)
                    
                # Handle AnalysisLayers for P4, P1 (Assuming they are added initially or appended)
                # To prevent duplication on update, we could delete existing TACTICAL layers for this trade.
                session.query(AnalysisLayer).filter(
                    AnalysisLayer.trade_id == record_id, 
                    AnalysisLayer.department == 'TACTICAL'
                ).delete()
                
                t_layers = append_payload.get('tactical_layers', [])
                for layer_dict in t_layers:
                    al = AnalysisLayer(
                        trade_id=record_id,
                        department='TACTICAL',
                        layer_name=layer_dict.get('layer_name'),
                        direction=layer_dict.get('direction'),
                        strength=layer_dict.get('strength'),
                        score=layer_dict.get('score'),
                        thesis=layer_dict.get('thesis')
                    )
                    session.add(al)

            if 'bias_a' in append_payload:
                bias_a_val = append_payload['bias_a']
                if record.efficiency_audit:
                    record.efficiency_audit.bias_a = bias_a_val
                    record.efficiency_audit.updated_at = datetime.datetime.now(datetime.UTC)
                else:
                    new_ae = EfficiencyAudit(id=record_id, bias_a=bias_a_val)
                    session.add(new_ae)
                    
            if 'audit_tactical' in append_payload:
                at_dict = append_payload['audit_tactical']
                if 'size_btc' in at_dict and 'size' not in at_dict:
                    at_dict['size'] = at_dict['size_btc']
                    
                valid_keys = {c.key for c in TacticalAudit.__table__.columns}
                filtered_at = {k: v for k, v in at_dict.items() if k in valid_keys}
                
                if record.tactical_audit:
                    for k, v in filtered_at.items():
                        setattr(record.tactical_audit, k, v)
                else:
                    new_at = TacticalAudit(id=record_id, **filtered_at)
                    session.add(new_at)

            record.updated_at = datetime.datetime.now(datetime.UTC)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            console.print(f"[bold red]Database Error in update_record_state: {e}[/bold red]")
            return False

def get_records_by_state(state: LifecycleState | List[LifecycleState], engine=None) -> List[dict]:
    eng = engine or engine_default
    with Session(eng) as session:
        if isinstance(state, list):
            state_vals = [s.value for s in state]
            stmt = select(EfficiencyDepartment).where(EfficiencyDepartment.state.in_(state_vals))
        else:
            stmt = select(EfficiencyDepartment).where(EfficiencyDepartment.state == state.value)
        records = session.scalars(stmt).all()
        
        results = []
        for r in records:
            payload = {
                "asset": r.asset,
                "efficiency": {
                    "Market_Bias": r.market_bias,
                    "Calc_edge": r.calc_edge,
                },
                "analysis_layers": [
                    {
                        "department": al.department,
                        "layer_name": al.layer_name,
                        "direction": al.direction,
                        "strength": al.strength
                    } for al in r.analysis_layers
                ]
            }
            if r.efficiency_audit:
                payload["audit_efficiency"] = {
                    "bias_a": r.efficiency_audit.bias_a,
                    "resolution_type": r.efficiency_audit.resolution_type,
                    "real_bias_b": r.efficiency_audit.real_bias_b,
                    "structural_resolution": r.efficiency_audit.structural_resolution,
                    "failure_reason": r.efficiency_audit.failure_reason,
                    "specific_bias_compliance": r.efficiency_audit.specific_bias_compliance,
                    "false_regime_rate": r.efficiency_audit.false_regime_rate
                }
            if r.tactical_department:
                payload["tactical"] = {
                    "tactical_classification": r.tactical_department.tactical_classification,
                    "calc_edge": r.tactical_department.calc_edge
                }
            if r.tactical_audit:
                payload["audit_tactical"] = {
                    "compliance": r.tactical_audit.compliance
                }
                
            results.append({
                "id": r.id,
                "state": r.state,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
                "payload": payload
            })
            
        return results

