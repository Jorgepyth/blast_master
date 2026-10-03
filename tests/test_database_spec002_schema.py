"""
T35 (spec 002): columnas nuevas, ORM de `backfill_history` y shim de `init_db` (RF-13d, RF-13e, RF-14, RF-14b,
RF-18, NFR-1; plan.md §2.1, §2.2).

Las DBs son archivos en tmp_path con el esquema de antes de la spec: una DB `:memory:` creada con `create_all` nunca
ejercita un `ALTER TABLE` del shim (CLAUDE.md).
"""
import sqlite3
from datetime import datetime

import pytest
from sqlalchemy import create_engine, exc, text
from sqlalchemy.orm import sessionmaker

from tools.database import Base, BackfillHistory, EfficiencyAudit, TacticalAudit, UnifiedDepartment, init_db
from tools.p2_backtest import assemble_p2_systematic_rows, open_readonly_session

NEW_COLUMNS = {
    "unified_department": ("analysis_start_time", "mark_price_time", "saved_at"),
    "efficiency_audit": ("audit_registration_time", "resolution_time_source"),
}
BACKFILL_COLUMNS = ["id", "run_id", "run_at", "kind", "table_name", "record_id", "field", "old_value", "new_value",
                    "source"]
CREATED = datetime(2026, 6, 1, 9, 30)


def _unified(trade_id, **fields):
    return UnifiedDepartment(
        id=trade_id, state="READY_FOR_NOTION", asset="XAUUSDT.P", market_bias="Bullish", calc_edge=0.5,
        p4_hierarchy="x", p1_timeframe="15M", p1_type="x", nodes_l1=1, nodes_l2=1, tactical_classification="x",
        long_prob=0.5, short_prob=0.5, no_trade_prob=0.0, created_at=CREATED, edge_validation_price=110.0,
        structural_invalidation=90.0, mark_price=101.0, **fields)


def drop_spec002_schema(path):
    """Deja una DB creada con el ORM de hoy como las reales antes de la spec 002: sin las 5 columnas nuevas ni
    `backfill_history`. La usan también los fixtures de T30 y T32 (O1: DBs con y sin esas columnas)."""
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS backfill_history"))
        for table, columns in NEW_COLUMNS.items():
            for column in columns:
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
    engine.dispose()


def _old_schema_db(path):
    """Una DB como las reales de hoy: una fila en cada tabla, sin las 5 columnas nuevas ni `backfill_history`."""
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(_unified("a1"))
        session.add(EfficiencyAudit(id="a1", bias_a="BOS", resolution_type="Confirmed (A equal to B)",
                                    resolution_time=datetime(2026, 6, 2, 18, 0), structural_mae=99.5))
        session.add(TacticalAudit(id="x1", trade_id="a1", entry_price=100.0))
        session.commit()
    engine.dispose()
    drop_spec002_schema(path)


def _columns(path, table):
    with sqlite3.connect(path) as conn:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _counts(path):
    with sqlite3.connect(path) as conn:
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("unified_department", "efficiency_audit", "tactical_audit")}


def test_init_db_adds_the_five_columns_and_backfill_history_to_an_old_database(tmp_path):
    path = tmp_path / "old.db"
    _old_schema_db(path)
    assert "saved_at" not in _columns(path, "unified_department")  # el fixture es de verdad el esquema viejo
    before = _counts(path)

    init_db(f"sqlite:///{path}").dispose()

    for table, columns in NEW_COLUMNS.items():
        assert set(columns) <= set(_columns(path, table))
    assert _columns(path, "backfill_history") == BACKFILL_COLUMNS
    assert _counts(path) == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        row = conn.execute("SELECT analysis_start_time, mark_price_time, saved_at, mark_price FROM unified_department"
                           ).fetchone()
        assert row == (None, None, None, 101)  # las filas viejas quedan con las columnas nuevas vacías
        audit = conn.execute("SELECT resolution_time, audit_registration_time, resolution_time_source "
                             "FROM efficiency_audit").fetchone()
        assert audit == ("2026-06-02 18:00:00.000000", None, None)  # el shim no mueve valores (eso es el backfill)


def test_running_init_db_twice_does_not_fail_or_duplicate(tmp_path):
    path = tmp_path / "old.db"
    _old_schema_db(path)
    init_db(f"sqlite:///{path}").dispose()
    init_db(f"sqlite:///{path}").dispose()
    for table in ("unified_department", "efficiency_audit", "backfill_history"):
        columns = _columns(path, table)
        assert len(columns) == len(set(columns))


def test_the_new_fields_round_trip_through_the_orm(tmp_path):
    engine = init_db(f"sqlite:///{tmp_path / 'new.db'}")
    times = dict(analysis_start_time=datetime(2026, 6, 1, 9, 14), mark_price_time=datetime(2026, 6, 1, 9, 26),
                 saved_at=datetime(2026, 6, 1, 9, 31))
    with sessionmaker(bind=engine)() as session:
        session.add(_unified("a1", **times))
        session.add(EfficiencyAudit(id="a1", bias_a="BOS", audit_registration_time=datetime(2026, 6, 4, 18, 2),
                                    resolution_time_source="candles"))
        session.commit()
    with sessionmaker(bind=engine)() as session:
        unified = session.get(UnifiedDepartment, "a1")
        assert {name: getattr(unified, name) for name in times} == times
        audit = session.get(EfficiencyAudit, "a1")
        assert (audit.audit_registration_time, audit.resolution_time_source) == (datetime(2026, 6, 4, 18, 2), "candles")
    engine.dispose()


def _history_row(**overrides):
    fields = dict(run_id="7f3a2c1e", run_at=datetime(2026, 10, 5, 14, 20), kind="legacy_move",
                  table_name="efficiency_audit", record_id="a1", field="audit_registration_time", old_value=None,
                  new_value="2026-06-24 18:40:07", source="legacy")
    fields.update(overrides)
    return BackfillHistory(**fields)


@pytest.mark.parametrize("statement", ["UPDATE backfill_history SET new_value = 'x'", "DELETE FROM backfill_history"])
def test_backfill_history_only_accepts_inserts(tmp_path, statement):
    engine = init_db(f"sqlite:///{tmp_path / 'new.db'}")
    with sessionmaker(bind=engine)() as session:
        session.add(_history_row())
        session.commit()
        assert session.query(BackfillHistory).one().id == 1  # autoincremental
    with pytest.raises(exc.IntegrityError, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text(statement))
    with sessionmaker(bind=engine)() as session:
        assert session.query(BackfillHistory).one().new_value == "2026-06-24 18:40:07"
    engine.dispose()


def test_the_append_only_guard_also_exists_on_a_migrated_old_database(tmp_path):
    path = tmp_path / "old.db"
    _old_schema_db(path)
    engine = init_db(f"sqlite:///{path}")
    with sessionmaker(bind=engine)() as session:
        session.add(_history_row())
        session.commit()
    with pytest.raises(exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM backfill_history"))
    engine.dispose()


@pytest.mark.parametrize("missing", ["kind", "source", "run_id"])
def test_backfill_history_requires_its_not_null_fields(tmp_path, missing):
    engine = init_db(f"sqlite:///{tmp_path / 'new.db'}")
    with sessionmaker(bind=engine)() as session:
        session.add(_history_row(**{missing: None}))
        with pytest.raises(exc.IntegrityError):
            session.commit()
    engine.dispose()


def test_read_only_tools_still_read_a_database_the_cli_has_not_migrated(tmp_path):
    # Una cuenta que el CLI no abrió desde la migración no tiene las columnas nuevas (CLAUDE.md, US100): el código de
    # solo lectura tiene que pedir columnas explícitas, nunca la entidad ORM completa.
    path = tmp_path / "old.db"
    _old_schema_db(path)
    session = open_readonly_session(str(path))
    try:
        rows, exclusions = assemble_p2_systematic_rows(session, None)
    finally:
        session.close()
    assert [r.trade_id for r in rows] == [] and [e.trade_id for e in exclusions] == ["a1"]  # sin entry_time
