"""
Las horas del análisis en `flow_new_analysis` (spec 002):
- T37: `analysis_start_time` al confirmar P0 por primera vez, sin cambiar con `RestartFlowException`; la hora tipeada
  en un retroactivo y en un clon [2] (RF-13b gana); la hora de elección en un clon [1] (RF-13, RF-13b, RF-13c).

El reloj es falso (`cli.main._now_gt`): devuelve `at(n)`, donde n es la cantidad de `inquirer.select` ya respondidos.
Así el test sabe en qué punto del wizard se tomó cada hora (`at(3)` = justo después de asset, P0 Direction y P0
Strength). Mismo manejo del wizard que tests/test_wizard_safety_net.py (T7).
"""
import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import cli.main as cli_main
import tools.database
from cli.main import GoBackException
from cli.schemas.audit_efficiency import StructuralBias
from cli.schemas.efficiency import Direction, Strength
from cli.schemas.tactical import FractalType, Hierarchy, TacticalClassification, Timeframe
from tools.database import Base, UnifiedDepartment

BASE = datetime.datetime(2026, 10, 2, 9, 0)
TYPED = datetime.datetime(2026, 9, 30, 14, 0)
ANSWERED = {"selects": 0}


def at(n):
    return BASE + datetime.timedelta(minutes=n)


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(tools.database, "engine_default", engine)
    return engine


@pytest.fixture
def clock(monkeypatch):
    ANSWERED["selects"] = 0
    calls = []

    def fake_now():
        calls.append(at(ANSWERED["selects"]))
        return calls[-1]

    monkeypatch.setattr(cli_main, "_now_gt", fake_now)
    return calls


def _counting(answers):
    answers = iter(answers)

    def answer(*args, **kwargs):
        value = next(answers)
        ANSWERED["selects"] += 1
        return value

    return answer


def _selects(extra_p0_str=False):
    """Los `inquirer.select` del wizard, del asset al "feed a Tactical Audit now?" (T7)."""
    p0 = [Direction.LONG, Strength.STRONG] + ([Strength.WEAK] if extra_p0_str else [])
    return ["XAU/USD", *p0,
            Direction.LONG, Strength.STRONG,               # p2
            Direction.LONG, Strength.STRONG,               # p3
            Direction.LONG, Strength.STRONG,               # p1
            Timeframe.M15, FractalType.FIRST_ITERATION,
            Direction.LONG, Strength.STRONG,               # p4
            Hierarchy.PSYCH_LEVEL, "1H", StructuralBias.BOS, TacticalClassification.CONTINUATION_PRESSURE,
            "save", "no"]


def _run(db, selects, texts=("",) * 3, mandatory_text=None, **kwargs):
    mandatory_text = mandatory_text or (lambda *a, **k: "some thesis text")
    with patch("InquirerPy.inquirer.select") as select_, patch("InquirerPy.inquirer.text") as text_, \
            patch("cli.main.get_mandatory_text", side_effect=mandatory_text), \
            patch("cli.main.get_mandatory_int", return_value=2), \
            patch("cli.main.handle_visual_lesson_assignment", return_value="nan"), \
            patch("cli.main.flow_pending_audits"), patch("builtins.input", return_value=""):
        select_.return_value = MagicMock(**{"execute.side_effect": _counting(selects)})
        text_.return_value = MagicMock(**{"execute.side_effect": list(texts)})
        cli_main.flow_new_analysis(**kwargs)
    with Session(db) as session:
        return session.scalars(select(UnifiedDepartment)).one()


# --- T37: analysis_start_time ------------------------------------------------------

def test_a_new_analysis_starts_when_p0_strength_is_confirmed(db, clock):
    record = _run(db, _selects())
    assert record.analysis_start_time == at(3)  # asset, P0 Direction, P0 Strength
    assert record.is_backdated is False


def test_going_back_and_confirming_p0_again_keeps_the_first_time(db, clock):
    calls = {"n": 0}

    def mandatory_text(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:            # P2 Thesis: el operador vuelve atrás, a la fuerza de P0
            raise GoBackException()
        return "some thesis text"

    record = _run(db, _selects(extra_p0_str=True), mandatory_text=mandatory_text)
    assert record.analysis_start_time == at(3)  # la segunda confirmación (at(4)) no la cambia


def test_a_backdated_analysis_keeps_the_typed_time(db, clock):
    record = _run(db, _selects(), backdated_timestamp=TYPED)
    assert record.analysis_start_time == TYPED and record.created_at == TYPED and record.is_backdated is True


def test_a_clone_in_mode_1_keeps_the_time_of_the_choice(db, clock):
    chosen = datetime.datetime(2026, 10, 2, 8, 55)
    cloned = {"p0_dir": Direction.LONG, "p0_str": Strength.STRONG, "p0_thesis": "x"}
    record = _run(db, ["XAU/USD", *_selects()[3:]], cloned_state=cloned, started_at=chosen)  # P0 ya viene del clon
    assert record.analysis_start_time == chosen


def test_the_clone_choice_returns_the_typed_time_or_the_time_of_choosing_1(clock):
    with patch("InquirerPy.inquirer.select") as select_:
        select_.return_value = MagicMock(**{"execute.return_value": "current"})
        assert cli_main.ask_clone_timestamps() == (None, at(0))
    with patch("InquirerPy.inquirer.select") as select_, \
            patch("cli.main.get_mandatory_datetime", return_value=TYPED):
        select_.return_value = MagicMock(**{"execute.return_value": "custom"})
        assert cli_main.ask_clone_timestamps() == (TYPED, None)


def test_a_discarded_analysis_saves_nothing(db, clock):
    selects = _selects()
    selects[selects.index("save")] = "discard"
    with patch("InquirerPy.inquirer.select") as select_, patch("InquirerPy.inquirer.text") as text_, \
            patch("cli.main.get_mandatory_text", return_value="t"), patch("cli.main.get_mandatory_int", return_value=2), \
            patch("cli.main.handle_visual_lesson_assignment", return_value="nan"), \
            patch("cli.main.flow_pending_audits"), patch("builtins.input", return_value=""):
        select_.return_value = MagicMock(**{"execute.side_effect": selects[:-1]})
        text_.return_value = MagicMock(**{"execute.return_value": ""})
        cli_main.flow_new_analysis()
    with Session(db) as session:
        assert session.scalars(select(UnifiedDepartment)).all() == []
