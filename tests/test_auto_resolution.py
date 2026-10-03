"""
T30 (spec 002): `tools/auto_resolution.py`, el servicio que junta la DB de una cuenta, el banco de velas y el
resolvedor (RF-4e, RF-4h, RF-6b). DB y banco de fixture en tmp_path; la DB tiene el esquema de hoy, sin las columnas
nuevas de T35 (como la de US100).
"""
import os
from datetime import datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cli.schemas.audit_efficiency import FailureReason, ResolutionType, StructuralResolution
from config.auto_resolution import (
    REASON_CLOCK_MISALIGNED,
    REASON_CLOCK_UNVERIFIED,
    REASON_NO_LEVELS,
    REASON_NO_MT5_SYMBOL,
    REASON_PENDING_CANDLES,
)
from tools.auto_resolution import AccountResolver, AnalysisRow, read_analysis_rows
from tools.candle_bank import build_status_payload, write_bank_status
from tools.database import Base, UnifiedDepartment

T0 = datetime(2026, 6, 1, 0, 0)
TOUCH = T0 + timedelta(hours=1)  # la vela de 1M que toca 110


def _write_bank(bank_root, symbol="XAUUSD", clock="verified", minutes=3000):
    """1M plano en 100 hasta la 01:00; a la 01:00 toca 110.5; después, plano en 105 (sin volver al Mark Price)."""
    rows = []
    for i in range(minutes):
        t = T0 + timedelta(minutes=i)
        high, low = (101.0, 99.0) if t < TOUCH else (110.5, 104.5) if t == TOUCH else (105.5, 104.5)
        rows.append({"time": t, "open": (high + low) / 2, "high": high, "low": low, "close": (high + low) / 2})
    bank_dir = bank_root / symbol
    bank_dir.mkdir(parents=True)
    pd.DataFrame(rows).to_csv(bank_dir / "1M.csv", index=False)
    if clock is not None:
        write_bank_status(str(bank_dir), build_status_payload(
            symbol, clock, "overlap" if clock == "verified" else None, "unverified", "r1", "merged", {}))


def _write_db(path, analyses, old_schema=True):
    """`analyses`: dicts con id, asset, created_at y opcionales evp, si, mark_price, is_backdated, market_bias. Por
    defecto la DB queda con el esquema de antes de T35, como US100 (O1); `old_schema=False` deja las columnas nuevas."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        for a in analyses:
            session.add(UnifiedDepartment(
                id=a["id"], state="READY_FOR_NOTION", asset=a.get("asset", "XAUUSDT.P"),
                market_bias=a.get("market_bias", "Bullish"), calc_edge=0.5, p4_hierarchy="x", p1_timeframe="15M",
                p1_type="x", nodes_l1=1, nodes_l2=1, tactical_classification="x", long_prob=0.5, short_prob=0.5,
                no_trade_prob=0.0, created_at=a["created_at"], is_backdated=a.get("is_backdated", False),
                edge_validation_price=a.get("evp", 110.0), structural_invalidation=a.get("si", 90.0),
                mark_price=a.get("mark_price", 101.0)))
        session.commit()
    engine.dispose()
    if old_schema:
        from tests.test_database_spec002_schema import drop_spec002_schema
        drop_spec002_schema(path)


@pytest.fixture
def env(tmp_path):
    bank_root = tmp_path / "bank"
    bank_root.mkdir()
    return tmp_path, bank_root


def _resolver(tmp_path, bank_root, analyses, **bank_kwargs):
    if not (bank_root / bank_kwargs.get("symbol", "XAUUSD")).exists():
        _write_bank(bank_root, **bank_kwargs)
    db = tmp_path / "account.db"
    _write_db(db, analyses)
    return AccountResolver(str(db), str(bank_root), account="000")


A = {"id": "a1", "created_at": T0 + timedelta(minutes=30)}  # ancla 00:10 (created_at − 20 min)


def test_full_proposal_of_a_confirmed_analysis_in_wizard_values(env):
    tmp_path, bank_root = env
    proposal = _resolver(tmp_path, bank_root, [A]).propose("a1")

    assert proposal.reason is None and proposal.symbol == "XAUUSD"
    assert proposal.anchor == T0 + timedelta(minutes=10)
    assert proposal.resolution_type == ResolutionType.CONFIRMED.value
    assert proposal.structural_resolution == StructuralResolution.CONFIRMED_MINIMAL.value
    assert proposal.failure_reason == FailureReason.NA.value
    assert proposal.resolution_time == TOUCH
    assert (proposal.structural_mae, proposal.structural_mfe) == (99.0, 110.5)
    assert proposal.overlap is None


def test_an_asset_without_mt5_symbol_is_not_resolved(env):
    tmp_path, bank_root = env
    proposal = _resolver(tmp_path, bank_root, [dict(A, asset="ETHUSDT.P")]).propose("a1")
    assert (proposal.reason, proposal.symbol, proposal.resolution_type) == (REASON_NO_MT5_SYMBOL, None, None)


@pytest.mark.parametrize("clock, reason", [
    ("clock_unverified", REASON_CLOCK_UNVERIFIED),
    ("clock_misaligned", REASON_CLOCK_MISALIGNED),
    (None, REASON_CLOCK_UNVERIFIED),  # sin status.json: el banco nunca se verificó
])
def test_a_symbol_whose_clock_is_not_verified_is_not_resolved(env, clock, reason):
    tmp_path, bank_root = env
    proposal = _resolver(tmp_path, bank_root, [A], clock=clock).propose("a1")
    assert (proposal.reason, proposal.resolution, proposal.resolution_type) == (reason, None, None)


def test_levels_on_the_same_side_are_no_levels(env):
    tmp_path, bank_root = env
    proposal = _resolver(tmp_path, bank_root, [dict(A, evp=105.0, si=108.0)]).propose("a1")
    assert (proposal.reason, proposal.resolution_type, proposal.resolution_time) == (REASON_NO_LEVELS, None, None)


def test_an_overlap_is_only_a_label_and_the_proposal_follows_the_touch(env):
    """N52: con un análisis B iniciado antes del toque, a1 se propone igual que sin B; solo cambia la etiqueta."""
    tmp_path, bank_root = env
    b = {"id": "b1", "created_at": T0 + timedelta(minutes=50)}  # ancla 00:30, antes del toque de a1 (01:00)
    proposal = _resolver(tmp_path, bank_root, [A, b]).propose("a1")

    assert proposal.overlap.b_id == "b1"
    assert proposal.resolution_type == ResolutionType.CONFIRMED.value
    assert proposal.structural_resolution == StructuralResolution.CONFIRMED_MINIMAL.value
    assert proposal.failure_reason == FailureReason.NA.value
    assert proposal.resolution_time == TOUCH  # N37: la hora del primer toque, también en un Overlap
    assert (proposal.structural_mae, proposal.structural_mfe) == (99.0, 110.5)


def test_an_overlap_that_has_not_touched_yet_has_the_label_and_no_type(env):
    tmp_path, bank_root = env
    b = {"id": "b1", "created_at": T0 + timedelta(minutes=40)}
    resolver = _resolver(tmp_path, bank_root, [A, b], minutes=55)   # el banco termina antes del toque
    proposal = resolver.propose("a1")
    assert proposal.reason == REASON_PENDING_CANDLES and proposal.overlap.b_id == "b1"
    assert (proposal.resolution_type, proposal.structural_resolution, proposal.failure_reason,
            proposal.resolution_time) == (None, None, None, None)


def test_open_proposes_no_resolution_type(env):
    tmp_path, bank_root = env
    _write_bank(bank_root)
    # Sin velas de 1H, el horizonte nunca se cumple: se arma un banco de 1H propio, sin toque en 2160 h.
    one_hour = pd.DataFrame([{"time": T0 + timedelta(hours=h), "open": 100, "high": 101, "low": 99, "close": 100}
                             for h in range(-2, 2200)])  # desde las 22:00 del día anterior: hay vela cerrada en el ancla
    one_hour.to_csv(bank_root / "XAUUSD" / "1H.csv", index=False)
    os.remove(bank_root / "XAUUSD" / "1M.csv")
    proposal = _resolver(tmp_path, bank_root, [A]).propose("a1")
    assert (proposal.reason, proposal.resolution_type) == ("open", None)


def test_reading_tolerates_missing_new_columns_and_uses_analysis_start_time_when_present(env):
    tmp_path, bank_root = env
    db = tmp_path / "account.db"
    _write_db(db, [A])
    assert read_analysis_rows(str(db))[0].analysis_start_time is None  # la columna no existe: cuenta como vacía

    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE unified_department ADD COLUMN analysis_start_time DATETIME"))
        conn.execute(text("UPDATE unified_department SET analysis_start_time = '2026-06-01 00:05:00.000000'"))
    engine.dispose()
    _write_bank(bank_root)
    proposal = AccountResolver(str(db), str(bank_root), account="000").propose("a1")
    assert proposal.anchor == T0 + timedelta(minutes=5)


def test_with_the_new_schema_the_three_times_are_read(env):
    tmp_path, _ = env
    db = tmp_path / "account.db"
    _write_db(db, [A], old_schema=False)
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(text("UPDATE unified_department SET analysis_start_time = '2026-06-01 00:05:00.000000', "
                          "mark_price_time = '2026-06-01 00:20:00.000000', saved_at = '2026-06-01 00:31:00.000000'"))
    engine.dispose()
    (row,) = read_analysis_rows(str(db))
    assert (row.analysis_start_time, row.mark_price_time, row.saved_at) == (
        T0 + timedelta(minutes=5), T0 + timedelta(minutes=20), T0 + timedelta(minutes=31))


def test_the_database_is_read_without_writing_a_single_byte(env):
    tmp_path, bank_root = env
    resolver = _resolver(tmp_path, bank_root, [A, {"id": "b1", "created_at": T0 + timedelta(minutes=50)}])
    before = (tmp_path / "account.db").read_bytes()
    proposals = resolver.propose_all()
    assert [p.trade_id for p in proposals] == ["a1", "b1"]
    assert (tmp_path / "account.db").read_bytes() == before


def test_rows_carry_what_the_metrics_need(env):
    tmp_path, bank_root = env
    _write_db(tmp_path / "account.db", [dict(A, is_backdated=True, market_bias="Bearish")])
    (row,) = read_analysis_rows(str(tmp_path / "account.db"))
    assert row == AnalysisRow("a1", "XAUUSDT.P", T0 + timedelta(minutes=30), True, None, "Bearish", 110.0, 90.0, 101.0)


# --- Excepciones de toque (T30b, N46) --------------------------------------------------------------------------------

def test_a_touch_exception_turns_an_untouched_target_into_a_confirmed_touch_at_the_closest_candle(env):
    tmp_path, bank_root = env
    # Objetivo 111: el banco llega a 110.5 a la 01:00 y nunca lo toca (pending_candles sin la excepción).
    analysis = dict(A, evp=111.0)
    _write_bank(bank_root)
    _write_db(tmp_path / "account.db", [analysis])
    plain = AccountResolver(str(tmp_path / "account.db"), str(bank_root), account="000").propose("a1")
    assert plain.reason == REASON_PENDING_CANDLES

    resolver = AccountResolver(str(tmp_path / "account.db"), str(bank_root), account="000",
                               touch_exceptions={"a1": ("validation", "touched on the operator's chart")})
    proposal = resolver.propose("a1")

    assert proposal.resolution_type == ResolutionType.CONFIRMED.value and proposal.reason is None
    assert proposal.resolution_time == TOUCH
    assert proposal.resolution.first_touch.exception is True


def test_the_configured_exceptions_are_full_ids_with_a_known_level_and_a_note():
    from config.auto_resolution import TOUCH_EXCEPTIONS
    from core.p2_ground_truth import LEVEL_INVALIDATION, LEVEL_VALIDATION
    assert "4b17b903-407d-4a5e-b238-1491ab64679b" in TOUCH_EXCEPTIONS
    for trade_id, (level, note) in TOUCH_EXCEPTIONS.items():
        assert len(trade_id) == 36 and level in (LEVEL_VALIDATION, LEVEL_INVALIDATION) and note


def test_by_default_the_resolver_uses_the_configured_exceptions(env, monkeypatch):
    import tools.auto_resolution as auto_resolution
    tmp_path, bank_root = env
    monkeypatch.setattr(auto_resolution, "TOUCH_EXCEPTIONS", {"a1": ("validation", "note")})
    proposal = _resolver(tmp_path, bank_root, [dict(A, evp=111.0)]).propose("a1")
    assert proposal.resolution_type == ResolutionType.CONFIRMED.value



# --- Retroactivos recuperados (T29b, N50) ----------------------------------------------------------------------------

def test_recovered_backdated_analyses_count_and_keep_their_typed_anchor(env):
    tmp_path, bank_root = env
    _write_bank(bank_root)
    _write_db(tmp_path / "account.db", [dict(A, is_backdated=True), {"id": "b1", "created_at": A["created_at"],
                                                                      "is_backdated": True}])
    resolver = AccountResolver(str(tmp_path / "account.db"), str(bank_root), account="000",
                               recovered_backdated={"a1"})
    by_id = {o.analysis_id: o for o in resolver.outcomes()}
    assert by_id["a1"].is_backdated is False and by_id["b1"].is_backdated is True  # b1 es un retroactivo nuevo
    assert by_id["a1"].anchor == A["created_at"]  # el ancla sigue siendo la hora tipeada


def test_outcomes_carry_how_far_the_candles_were_observed_without_a_touch(env):
    tmp_path, bank_root = env
    _write_bank(bank_root)  # 3000 minutos de 1M desde T0; objetivo 111: nunca se toca
    _write_db(tmp_path / "account.db", [dict(A, evp=111.0)])
    (outcome,) = AccountResolver(str(tmp_path / "account.db"), str(bank_root), account="000").outcomes()
    assert outcome.outcome == REASON_PENDING_CANDLES and outcome.path_end == T0 + timedelta(minutes=3000)


def test_the_configured_recovered_backdated_are_the_7_full_ids_of_xau():
    from config.auto_resolution import RECOVERED_BACKDATED
    assert len(RECOVERED_BACKDATED) == 7 and all(len(trade_id) == 36 for trade_id in RECOVERED_BACKDATED)
    assert "f24b9653-ad3b-4eb0-9a78-badb9a64a09f" in RECOVERED_BACKDATED


def test_by_default_the_resolver_uses_the_configured_recovered_backdated(env, monkeypatch):
    import tools.auto_resolution as auto_resolution
    tmp_path, bank_root = env
    monkeypatch.setattr(auto_resolution, "RECOVERED_BACKDATED", frozenset({"a1"}))
    (outcome,) = _resolver(tmp_path, bank_root, [dict(A, is_backdated=True)]).outcomes()
    assert outcome.is_backdated is False
