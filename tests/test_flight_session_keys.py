"""
T61b (spec 002): crear una Flight Session con una clave que ya existe se rechaza. En la demo T61 se tipeó `000`, la
clave de la cuenta real de XAU: la sesión nueva pisó su entrada en `.data/flight_sessions.json` y, al borrarla, la
entrada desapareció. El archivo de sesiones va en tmp_path; ninguna DB se crea.
"""
import json
from unittest.mock import patch

import pytest

import cli.main as cli_main
from cli.main import FlightSessionManager, SessionKeyTakenError

XAU = {"name": "XAUUSDT.P", "db_name": "flight_account_001_xauusd.db", "created_at": "2026-06-04 06:29",
       "last_played": "2026-09-23 15:58"}


@pytest.fixture
def sessions_file(tmp_path, monkeypatch):
    path = tmp_path / "flight_sessions.json"
    path.write_text(json.dumps({"000": XAU}, indent=4))
    monkeypatch.setattr(cli_main, "FLIGHT_SESSIONS_FILE", str(path))
    # El flujo de sesiones activa la sesión nueva en globales del CLI: se restauran al terminar el test, para que la
    # sesión falsa no quede activa en los tests que vienen después.
    monkeypatch.setattr(cli_main, "ACTIVE_SESSION", cli_main.ACTIVE_SESSION)
    monkeypatch.setattr(cli_main, "ACTIVE_ENGINE", cli_main.ACTIVE_ENGINE)
    return path


def test_a_key_that_already_exists_is_refused_and_the_file_is_not_touched(sessions_file):
    before = sessions_file.read_bytes()
    with pytest.raises(SessionKeyTakenError, match="000 is already used by XAUUSDT.P"):
        FlightSessionManager.create_session("000", "demo-t61")
    assert sessions_file.read_bytes() == before


def test_spaces_around_the_key_do_not_get_past_the_check(sessions_file):
    with pytest.raises(SessionKeyTakenError):
        FlightSessionManager.create_session(" 000 ", "demo")


def test_a_free_key_works_as_always(sessions_file):
    key, data = FlightSessionManager.create_session("009", "demo-t61")
    assert (key, data["name"], data["db_name"]) == ("009", "demo-t61", "flight_account_009_demot61.db")
    stored = json.loads(sessions_file.read_text())
    assert stored["000"] == XAU and stored["009"]["db_name"] == "flight_account_009_demot61.db"


def test_the_wizard_asks_for_the_key_again_and_says_which_keys_are_used(sessions_file, monkeypatch, capsys):
    """El flujo de "Provision New Flight Session": `000` se rechaza con un aviso y se vuelve a pedir la clave."""
    answers = iter(["000", "009", "demo"])
    monkeypatch.setattr(cli_main, "get_mandatory_text", lambda *a, **k: next(answers))
    created = {}

    def fake_create(key, name):
        created["key"] = key
        return key, {"name": name, "db_name": f"flight_account_{key}_{name}.db"}

    real_create = FlightSessionManager.create_session

    def checked_create(key, name):
        if key in FlightSessionManager.load_sessions():
            return real_create(key, name)  # levanta SessionKeyTakenError
        return fake_create(key, name)      # no crea ninguna DB en el test

    monkeypatch.setattr(FlightSessionManager, "create_session", staticmethod(checked_create))
    with patch("cli.main.inquirer.select") as select, patch("tools.database.init_db"), \
            patch("tools.database.copy_assets_to_current_db"), patch("builtins.input", return_value=""):
        select.return_value.execute.side_effect = ["create"]
        cli_main.flow_flight_sessions()
    out = capsys.readouterr().out
    assert "Account key 000 is already used by XAUUSDT.P (flight_account_001_xauusd.db)" in out
    assert "Keys in use: 000" in out
    assert created["key"] == "009"
    assert json.loads(sessions_file.read_text()) == {"000": XAU}  # la clave usada nunca se pisó
