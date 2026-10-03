"""
T40 (spec 002, RF-7, RF-7e; plan.md decisión T11): el parámetro `default` de `get_enum_choice` y `get_mandatory_float`,
y el helper nuevo `get_optional_datetime`. Con un valor propuesto, el prompt lo muestra como `(auto: ...)` y se lo
pasa a InquirerPy como default (Enter lo acepta). Sin default, InquirerPy recibe exactamente lo mismo que antes.
"""
import datetime
from unittest.mock import MagicMock, patch

import pytest

from cli.main import get_enum_choice, get_mandatory_float, get_optional_datetime
from cli.schemas.audit_efficiency import ResolutionType


def _prompt(answer):
    prompt = MagicMock()
    prompt.execute.return_value = answer
    return prompt


# --- get_enum_choice ----------------------------------------------------------------------------------------------

def test_enum_choice_without_default_is_unchanged():
    with patch("InquirerPy.inquirer.select", return_value=_prompt(ResolutionType.CONFIRMED)) as select:
        assert get_enum_choice("Resolution Type", ResolutionType) == ResolutionType.CONFIRMED
    kwargs = select.call_args.kwargs
    assert kwargs["message"] == "Resolution Type >" and "default" not in kwargs


def test_enum_choice_with_default_marks_it_auto_and_preselects_it():
    with patch("InquirerPy.inquirer.select", return_value=_prompt(ResolutionType.INVALIDATED)) as select:
        get_enum_choice("Resolution Type", ResolutionType, default=ResolutionType.INVALIDATED)
    kwargs = select.call_args.kwargs
    assert kwargs["default"] == ResolutionType.INVALIDATED
    assert kwargs["message"] == "Resolution Type (auto: Invalidated (B not equal to A)) >"


def test_enum_choice_accepts_the_default_as_its_text_value():
    with patch("InquirerPy.inquirer.select", return_value=_prompt(ResolutionType.CONFIRMED)) as select:
        get_enum_choice("Resolution Type", ResolutionType, default=ResolutionType.CONFIRMED.value)
    assert select.call_args.kwargs["default"] == ResolutionType.CONFIRMED


@pytest.mark.parametrize("default", [ResolutionType.OPEN, "not a value"])
def test_a_default_that_is_not_among_the_choices_is_ignored(default):
    with patch("InquirerPy.inquirer.select", return_value=_prompt(ResolutionType.CONFIRMED)) as select:
        get_enum_choice("Resolution Type", ResolutionType, exclude=[ResolutionType.OPEN], default=default)
    kwargs = select.call_args.kwargs
    assert "default" not in kwargs and kwargs["message"] == "Resolution Type >"


# --- get_mandatory_float -------------------------------------------------------------------------------------------

def test_float_without_default_is_unchanged():
    with patch("InquirerPy.inquirer.text", return_value=_prompt("4350.5")) as text:
        assert get_mandatory_float("Structural MAE") == 4350.5
    kwargs = text.call_args.kwargs
    assert kwargs["message"] == "Structural MAE >" and "default" not in kwargs


def test_float_with_default_prefills_it_and_marks_it_auto():
    with patch("InquirerPy.inquirer.text", return_value=_prompt("4369.62")) as text:
        assert get_mandatory_float("Structural MFE", default=4369.62) == 4369.62
    kwargs = text.call_args.kwargs
    assert kwargs["default"] == "4369.62" and kwargs["message"] == "Structural MFE (auto: 4369.62) >"


def test_a_float_default_never_uses_scientific_notation():
    with patch("InquirerPy.inquirer.text", return_value=_prompt("0.00005")) as text:
        get_mandatory_float("x", default=0.00005)
    assert text.call_args.kwargs["default"] == "0.00005"


# --- get_optional_datetime -----------------------------------------------------------------------------------------

TOUCH = datetime.datetime(2026, 8, 21, 9, 15)


def test_optional_datetime_with_default_shows_auto_and_its_precision():
    with patch("InquirerPy.inquirer.text", return_value=_prompt("2026-08-21 09:15")) as text:
        assert get_optional_datetime("Resolution Time", default=TOUCH, precision="±1 min") == TOUCH
    kwargs = text.call_args.kwargs
    assert kwargs["default"] == "2026-08-21 09:15"
    assert kwargs["message"] == "Resolution Time (YYYY-MM-DD HH:MM) (auto: 2026-08-21 09:15, ±1 min) >"


def test_optional_datetime_returns_the_corrected_value_or_none_when_left_empty():
    with patch("InquirerPy.inquirer.text", return_value=_prompt("2026-08-21 10:00")):
        assert get_optional_datetime("Resolution Time", default=TOUCH) == datetime.datetime(2026, 8, 21, 10, 0)
    with patch("InquirerPy.inquirer.text", return_value=_prompt("   ")):
        assert get_optional_datetime("Resolution Time", default=TOUCH) is None


def test_optional_datetime_without_default_says_optional_and_has_no_default():
    with patch("InquirerPy.inquirer.text", return_value=_prompt("")) as text:
        assert get_optional_datetime("Resolution Time") is None
    kwargs = text.call_args.kwargs
    assert kwargs["message"] == "Resolution Time (YYYY-MM-DD HH:MM) [Optional] >" and "default" not in kwargs


def test_optional_datetime_validates_the_format_but_accepts_empty():
    with patch("InquirerPy.inquirer.text", return_value=_prompt("")) as text:
        get_optional_datetime("Resolution Time")
    validate = text.call_args.kwargs["validate"]
    assert validate("") and validate("2026-08-21 09:15")
    assert not validate("21/08/2026") and not validate("2026-08-21")


def test_a_plain_enum_also_accepts_its_text_value_as_default():
    from enum import Enum

    class Plain(Enum):  # sin `str`: el miembro no es igual a su texto
        ONE = "one"
        TWO = "two"

    with patch("InquirerPy.inquirer.select", return_value=_prompt(Plain.TWO)) as select:
        get_enum_choice("Pick", Plain, default="two")
    assert select.call_args.kwargs["default"] is Plain.TWO
