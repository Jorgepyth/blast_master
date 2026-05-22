import pytest
from unittest.mock import MagicMock, patch
from cli.main import (
    bind_pause,
    GoBackException,
    RestartFlowException,
    AnalysisSession,
    AuditSession,
    get_enum_choice,
    get_multi_enum_choice,
    get_mandatory_text,
    get_optional_text,
    get_mandatory_int,
    get_mandatory_float,
    get_mandatory_datetime
)
from enum import Enum

class DummyEnum(Enum):
    VAL1 = "Val 1"
    VAL2 = "Val 2"
    SKIP = "Skip"

def test_bind_pause_registers_keys():
    mock_prompt = MagicMock()
    bind_pause(mock_prompt)
    
    # Assert that register_kb was called for key bindings c-x, c-s, c-z
    calls = [call[0][0] for call in mock_prompt.register_kb.call_args_list]
    assert "c-x" in calls or any("c-x" in str(arg) for arg in calls)
    assert "c-s" in calls or any("c-s" in str(arg) for arg in calls)
    assert "c-z" in calls or any("c-z" in str(arg) for arg in calls)

def test_analysis_session_history_and_goback():
    session = AnalysisSession(trade_id="test-trade-id")
    
    # Mock some functions that return values
    func1 = MagicMock(return_value="val1")
    func2 = MagicMock(return_value="val2")
    
    # Call first prompt
    res1 = session.prompt("param1", func1)
    assert res1 == "val1"
    assert session.state["param1"] == "val1"
    assert "param1" in session.history
    
    # Call second prompt
    res2 = session.prompt("param2", func2)
    assert res2 == "val2"
    assert session.state["param2"] == "val2"
    assert "param2" in session.history
    
    # Now simulate a GoBackException on the third prompt
    func3 = MagicMock(side_effect=GoBackException())
    
    with pytest.raises(RestartFlowException):
        session.prompt("param3", func3)
        
    # Assert that param3 is not in state/history, and param2 (the previous one) was popped from state
    assert "param3" not in session.state
    assert "param2" not in session.state
    assert "param2" not in session.history
    assert "param1" in session.state # param1 is still preserved

def test_analysis_session_cannot_go_back_further():
    session = AnalysisSession(trade_id="test-trade-id")
    func1 = MagicMock(side_effect=GoBackException())
    
    with pytest.raises(RestartFlowException):
        session.prompt("param1", func1)
        
    assert not session.history
    assert not session.state

@patch("InquirerPy.inquirer.select")
def test_get_enum_choice_keybindings(mock_select):
    mock_prompt = MagicMock()
    mock_select.return_value = mock_prompt
    
    # We call it but expect it to fail execution or return mocked value
    mock_prompt.execute.return_value = DummyEnum.VAL1
    
    res = get_enum_choice("Test Enum", DummyEnum)
    
    assert res == DummyEnum.VAL1
    # Check that keybindings={"skip": []} was passed to inquirer.select
    mock_select.assert_called_once()
    kwargs = mock_select.call_args[1]
    assert kwargs.get("keybindings") == {"skip": []}

@patch("InquirerPy.inquirer.checkbox")
def test_get_multi_enum_choice_keybindings(mock_checkbox):
    mock_prompt = MagicMock()
    mock_checkbox.return_value = mock_prompt
    mock_prompt.execute.return_value = [DummyEnum.VAL1]
    
    res = get_multi_enum_choice("Test Multi Enum", DummyEnum)
    
    assert res == [DummyEnum.VAL1]
    mock_checkbox.assert_called_once()
    kwargs = mock_checkbox.call_args[1]
    assert kwargs.get("keybindings") == {"skip": []}

@patch("InquirerPy.inquirer.text")
def test_get_mandatory_text_keybindings(mock_text):
    mock_prompt = MagicMock()
    mock_text.return_value = mock_prompt
    mock_prompt.execute.return_value = "hello"
    
    res = get_mandatory_text("Test Text")
    
    assert res == "hello"
    mock_text.assert_called_once()
    kwargs = mock_text.call_args[1]
    assert kwargs.get("keybindings") == {"skip": []}
