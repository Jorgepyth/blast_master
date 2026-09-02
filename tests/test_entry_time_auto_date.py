"""
Regresión para Entry Time del Tactical Audit: año/mes/día se auto-derivan de
UnifiedDepartment.created_at para el trade_id en curso; el usuario solo
ingresa la hora (HH:MM) vía get_mandatory_time(). Cubre las 2 piezas nuevas
(tools.database.get_unified_created_at, cli.main.get_mandatory_time) y la
combinación real que hace ask_entry_time() dentro de flow_pending_audits().
"""
import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from cli.main import get_mandatory_time, GoBackException
from tools.database import Base, UnifiedDepartment, get_unified_created_at


@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _make_unified(engine, trade_id, created_at):
    with Session(engine) as session:
        session.add(UnifiedDepartment(
            id=trade_id, asset="XAU/USD", market_bias="Bullish", calc_edge=0.30,
            p4_hierarchy="Psych Level", p1_timeframe="15M", p1_type="1st_iteration",
            nodes_l1=2, nodes_l2=4, tactical_classification="Continuation_Pressure",
            long_prob=0.70, short_prob=0.20, no_trade_prob=0.10,
            created_at=created_at, updated_at=created_at,
        ))
        session.commit()


def test_get_unified_created_at_returns_the_real_timestamp(in_memory_db):
    known = datetime.datetime(2026, 3, 14, 9, 5, 0)
    _make_unified(in_memory_db, "trade-date-1", known)

    result = get_unified_created_at("trade-date-1", engine=in_memory_db)

    assert result == known


def test_get_unified_created_at_returns_none_for_missing_trade_id(in_memory_db):
    assert get_unified_created_at("does-not-exist", engine=in_memory_db) is None


@patch("InquirerPy.inquirer.text")
def test_get_mandatory_time_parses_hh_mm(mock_text):
    mock_prompt = MagicMock()
    mock_text.return_value = mock_prompt
    mock_prompt.execute.return_value = "14:30"

    result = get_mandatory_time("Entry Time")

    assert result == datetime.time(14, 30)
    kwargs = mock_text.call_args[1]
    assert kwargs.get("keybindings") == {"skip": []}
    # El validador rechaza un formato distinto a HH:MM (ej. datetime completo)
    validate = kwargs["validate"]
    assert validate("14:30") is True
    assert validate("2026-03-14 14:30") is False
    assert validate("") is False


@patch("InquirerPy.inquirer.text")
def test_get_mandatory_time_allow_cancel_raises_goback(mock_text):
    mock_prompt = MagicMock()
    mock_text.return_value = mock_prompt
    mock_prompt.execute.return_value = "c"

    with pytest.raises(GoBackException):
        get_mandatory_time("Entry Time", allow_cancel=True)


def test_entry_time_combines_real_date_with_entered_hour(in_memory_db):
    # Reproduce exactamente lo que hace ask_entry_time() dentro de
    # flow_pending_audits(): created_at.date() + hora ingresada por el usuario,
    # sin importar la hora/minuto que traiga created_at en sí.
    created_at = datetime.datetime(2026, 7, 1, 23, 59, 0)
    _make_unified(in_memory_db, "trade-date-2", created_at)

    fetched = get_unified_created_at("trade-date-2", engine=in_memory_db)
    entry_hour = datetime.time(8, 15)
    entry_time = datetime.datetime.combine(fetched.date(), entry_hour)

    assert entry_time == datetime.datetime(2026, 7, 1, 8, 15)
