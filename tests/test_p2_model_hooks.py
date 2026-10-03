"""
T56 (spec 002): los disparos del registro del P2 sistemático (RF-12, RF-12b, RF-12d, N40, N42): al guardar un análisis
(si el banco ya cubre su ancla), el catch-up después de cada fusión del banco, y el comando `p2-model`, que nunca
escribe el registro. Banco sintético y DBs de cuenta en tmp_path (los de tests/test_p2_model_feedback.py).
"""
import json
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import cli.main as cli_main
import config.auto_resolution as auto_cfg
import tools.database
import tools.p2_backtest as p2_backtest
import tools.p2_model_feedback as p2_feedback
from tests.test_analysis_times import _run, _xau_selects, clock  # noqa: F401 -- fixture
from tests.test_candle_sync import Env
from tests.test_p2_model_feedback import D_ONLY, H_TEST, N1_START, N2_START, _write_bank, _write_db
from tools.candle_bank import SyncResult
from tools.database import Base, UnifiedDepartment, init_db


@pytest.fixture
def real(tmp_path, monkeypatch):
    """Una cuenta real `000` (xau.db) como cuenta activa del CLI, con su carpeta de datos y su banco en tmp_path."""
    data, bank_root = tmp_path / "data", tmp_path / "bank"
    data.mkdir()
    engine = create_engine(f"sqlite:///{data / 'xau.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(tools.database, "engine_default", engine)
    monkeypatch.setattr(auto_cfg, "ACCOUNTS_DATA_DIR", str(data))
    monkeypatch.setattr(auto_cfg, "CANDLE_BANK_DIR", str(bank_root))
    monkeypatch.setattr(auto_cfg, "REAL_ACCOUNTS", {"000": "xau.db"})
    monkeypatch.setattr(auto_cfg, "P2_LOG_MODELS", dict(D_ONLY))
    monkeypatch.setattr(auto_cfg, "AUTO_EXPORT", False)
    yield engine, data, bank_root
    engine.dispose()


def _lines(path):
    path = Path(path)
    return [json.loads(raw) for raw in path.read_text().splitlines()] if path.exists() else []


def _save(engine, capsys):
    record = _run(engine, _xau_selects(), texts=("4568", "4584", "4520"))
    return record, capsys.readouterr().out


# --- Al guardar ------------------------------------------------------------------------------


def test_saving_with_candles_logs_each_model_and_the_wizard_shows_nothing_of_it(real, clock, capsys):
    engine, data, bank_root = real
    _write_bank(bank_root)
    record, out = _save(engine, capsys)
    (line,) = _lines(data / "p2_model_log.jsonl")
    assert (line["trade_id"], line["model"], line["status"]) == (record.id, "D", "ok")
    assert line["anchor"] == record.analysis_start_time.isoformat()
    assert line["operator_p2"] == {"direction": "Long", "strength": "Strong", "score": 2}
    assert "P2 model" not in out and "20315fe7bfe2" not in out and "-> +2" not in out


def test_saving_without_candles_logs_nothing_and_shows_the_same(real, clock, capsys, tmp_path, monkeypatch):
    """El mismo wizard dos veces, con y sin velas, con los mismos ids: la salida es idéntica (D3)."""
    import uuid
    from tests.test_analysis_times import ANSWERED
    engine, data, bank_root = real
    real_uuid4 = uuid.uuid4

    def run(db_name):
        counter = iter(range(1, 10 ** 6))
        monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=next(counter)))
        db_engine = create_engine(f"sqlite:///{data / db_name}")
        Base.metadata.create_all(db_engine)
        monkeypatch.setattr(tools.database, "engine_default", db_engine)
        monkeypatch.setattr(auto_cfg, "REAL_ACCOUNTS", {"000": db_name})
        ANSWERED["prompts"] = 0
        try:
            return _save(db_engine, capsys)[1]
        finally:
            monkeypatch.setattr(uuid, "uuid4", real_uuid4)
            db_engine.dispose()

    _write_bank(bank_root)
    with_candles = run("with.db")
    assert len(_lines(data / "p2_model_log.jsonl")) == 1
    (data / "p2_model_log.jsonl").unlink()
    import shutil
    shutil.rmtree(bank_root)
    without = run("without.db")
    assert not (data / "p2_model_log.jsonl").exists()
    assert with_candles == without


def test_a_flight_session_is_never_logged(real, clock, capsys, monkeypatch):
    engine, data, bank_root = real
    _write_bank(bank_root)
    monkeypatch.setattr(auto_cfg, "REAL_ACCOUNTS", {"000": "another.db"})
    _save(engine, capsys)
    assert not (data / "p2_model_log.jsonl").exists()


def test_a_model_list_problem_is_one_line_and_d_is_still_logged(real, clock, capsys, monkeypatch):
    engine, data, bank_root = real
    _write_bank(bank_root)
    monkeypatch.setattr(auto_cfg, "P2_LOG_MODELS", {"D": "2026-09-27", "NOPE": "2026-09-27"})
    _, out = _save(engine, capsys)
    assert "P2 model log skipped: unknown_model:NOPE" in out
    assert [line["model"] for line in _lines(data / "p2_model_log.jsonl")] == ["D"]


def test_a_failing_log_never_blocks_the_save(real, clock, capsys, monkeypatch):
    engine, data, _ = real

    def broken(db_path, trade_id):
        raise OSError("disk full")

    monkeypatch.setattr(p2_feedback, "log_on_save", broken)
    record, out = _save(engine, capsys)
    assert record.id and "rolled back" not in out and "disk full" not in out


# --- Catch-up ---------------------------------------------------------------------------------

ANALYSES = [
    ("n1", "XAUUSDT.P", N1_START, False),
    ("n2", "XAUUSDT.P", N2_START, False),
    ("old", "XAUUSDT.P", None, False),
    ("bd", "XAUUSDT.P", datetime(2026, 10, 1, 8, 0), True),
    ("pre", "XAUUSDT.P", datetime(2026, 9, 20, 9, 0), False),
    ("b1", "BTCUSDT.P", N1_START, False),
]


@pytest.fixture
def accounts(tmp_path):
    data, bank_root = tmp_path / "accounts", tmp_path / "bank"
    data.mkdir()
    _write_db(data / "xau.db", ANALYSES[:5])
    _write_db(data / "btc.db", ANALYSES[5:])
    _write_bank(bank_root)
    _write_bank(bank_root, symbol="BTCUSD")
    return data, bank_root, {"000": "xau.db", "002": "btc.db", "003": "missing.db"}


def _catch_up(accounts, symbol="XAUUSD", models=D_ONLY):
    data, bank_root, real_accounts = accounts
    return p2_feedback.catch_up(symbol, str(data), str(bank_root), real_accounts, models=models)


def test_the_catch_up_logs_the_new_analyses_of_the_symbol_once_per_model(accounts):
    result = _catch_up(accounts)
    log = accounts[0] / "p2_model_log.jsonl"
    assert [(line["trade_id"], line["account"]) for line in _lines(log)] == [("n1", "000"), ("n2", "000")]
    assert len(result.written) == 2
    assert _catch_up(accounts).written == []
    _catch_up(accounts, symbol="BTCUSD")
    assert [(line["trade_id"], line["account"]) for line in _lines(log)][-1] == ("b1", "002")


def test_the_catch_up_does_not_write_pending_analyses_until_the_bank_covers_them(accounts):
    data, bank_root, _ = accounts
    _write_bank(bank_root, last_close=datetime(2026, 10, 1, 12, 0))  # cubre n1 (10-01 10:00) pero no n2 (10-02)
    _catch_up(accounts)
    assert [line["trade_id"] for line in _lines(data / "p2_model_log.jsonl")] == ["n1"]
    _write_bank(bank_root)
    _catch_up(accounts)
    assert [(line["trade_id"], line.get("supersedes")) for line in _lines(data / "p2_model_log.jsonl")] == [
        ("n1", None), ("n2", None)]


def test_adding_a_model_does_not_change_or_duplicate_the_lines_of_d(accounts, monkeypatch):
    data = accounts[0]
    _catch_up(accounts)
    before = (data / "p2_model_log.jsonl").read_text()
    monkeypatch.setattr(p2_backtest, "MODELS_BY_NAME", {**p2_backtest.MODELS_BY_NAME, "H_TEST": H_TEST})
    _catch_up(accounts, models={"D": "2026-09-27", "H_TEST": "2026-10-02"})
    after = (data / "p2_model_log.jsonl").read_text()
    assert after.startswith(before)
    assert [(line["trade_id"], line["model"]) for line in _lines(data / "p2_model_log.jsonl")] == [
        ("n1", "D"), ("n2", "D"), ("n2", "H_TEST")]


def test_a_save_and_a_catch_up_at_the_same_time_write_the_line_once(accounts, monkeypatch):
    """Mientras este proceso calcula, otro (el guardado) escribe la línea del mismo par: esta ya no se escribe."""
    data = accounts[0]
    real_build = p2_feedback.build_line

    def build_while_another_writes(account, analysis, model, *args):
        line = real_build(account, analysis, model, *args)
        if analysis.trade_id == "n1":
            with open(data / "p2_model_log.jsonl", "a", encoding="utf-8") as other:
                other.write(json.dumps({**line, "logged_at": "by the other process"}) + "\n")
        return line

    monkeypatch.setattr(p2_feedback, "build_line", build_while_another_writes)
    result = _catch_up(accounts)
    assert [line["trade_id"] for line in result.written] == ["n2"]
    assert [(line["trade_id"], line["logged_at"]) for line in _lines(data / "p2_model_log.jsonl")][0] == (
        "n1", "by the other process")
    assert len(_lines(data / "p2_model_log.jsonl")) == 2


def test_the_catch_up_runs_after_a_merge_and_never_changes_the_export(tmp_path, monkeypatch):
    calls = []

    def fake_catch_up(symbol, accounts_data_dir, bank_root, real_accounts, symbol_map):
        calls.append((symbol, accounts_data_dir, bank_root))
        return p2_feedback.LogResult(written=[{}, {}], warnings=["unknown_model:NOPE"])

    monkeypatch.setattr(p2_feedback, "catch_up", fake_catch_up)
    env = Env(tmp_path)
    env.seed_bank(12)
    env.seed_payload(15)
    result = env.sync("ok")
    assert result.result == "merged"
    assert calls == [("XAUUSD", str(env.accounts_dir), str(env.bank_root))]
    from tools.candle_sync import format_result_line
    assert format_result_line(result).endswith("; P2 model log: 2 new lines; P2 model log skipped: unknown_model:NOPE")
    assert env.sync("mt5_down").result == "export_failed" and len(calls) == 1  # sin fusión no hay catch-up


def test_a_failing_catch_up_keeps_the_merge(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("log unreadable")

    monkeypatch.setattr(p2_feedback, "catch_up", broken)
    env = Env(tmp_path)
    env.seed_bank(12)
    env.seed_payload(15)
    result = env.sync("ok")
    assert result.result == "merged" and result.bars_added == {"1H": 3}
    from tools.candle_sync import format_result_line
    assert "P2 model log failed (ValueError: log unreadable)" in format_result_line(result)


def test_the_line_says_how_many_lines_but_never_what_the_model_says():
    from tools.candle_sync import format_result_line
    result = SyncResult("XAUUSD", "merged", verified_by="overlap", p2_logged=1)
    assert format_result_line(result).endswith("; P2 model log: 1 new line")


# --- p2-model ----------------------------------------------------------------------------------


@pytest.fixture
def command(tmp_path, monkeypatch):
    monkeypatch.setattr(tools.database, "engine_default", init_db("sqlite:///:memory:"))
    data, bank_root = tmp_path / "data", tmp_path / "bank"
    data.mkdir()
    _write_db(data / "xau.db", [("aaaa1111-n1", "XAUUSDT.P", N1_START, False),
                                ("aaaa2222-n2", "XAUUSDT.P", N2_START, False),
                                ("bbbb0000-old", "XAUUSDT.P", None, False)])
    _write_bank(bank_root)
    monkeypatch.setattr(auto_cfg, "ACCOUNTS_DATA_DIR", str(data))
    monkeypatch.setattr(auto_cfg, "CANDLE_BANK_DIR", str(bank_root))
    monkeypatch.setattr(auto_cfg, "REAL_ACCOUNTS", {"000": "xau.db"})
    monkeypatch.setattr(auto_cfg, "P2_LOG_MODELS", dict(D_ONLY))
    p2_feedback.log_account(str(data / "xau.db"), "000", trade_ids=["aaaa1111-n1"], now=datetime(2026, 10, 3, 13, 0))
    return data


def _p2_model(*args):
    return CliRunner().invoke(cli_main.cli, ["p2-model", *args], env={"COLUMNS": "250"})


def test_p2_model_shows_the_logged_line(command):
    result = _p2_model("--trade-id", "aaaa1111")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == ("Analysis aaaa1111-n1 (000, XAUUSDT.P), anchor 2026-10-01 10:00; operator P2: Long Strong (2)")
    assert lines[1] == "D (logged 2026-10-03 13:00): P2 +1.00 -> +2 | 1W +1 | 1D +1 | 12H +1 | 4H +1 | 1H +1 | 30M +1"


def test_p2_model_computes_the_missing_models_without_writing(command, monkeypatch):
    monkeypatch.setattr(p2_backtest, "MODELS_BY_NAME", {**p2_backtest.MODELS_BY_NAME, "H_TEST": H_TEST})
    monkeypatch.setattr(auto_cfg, "P2_LOG_MODELS", {"D": "2026-09-27", "H_TEST": "2026-10-02"})
    log = command / "p2_model_log.jsonl"
    before = log.read_bytes()
    result = _p2_model("--trade-id", "aaaa2222-n2")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[1:] == [
        "D (not logged): P2 +1.00 -> +2 | 1W +1 | 1D +1 | 12H +1 | 4H +1 | 1H +1 | 30M +1",
        "H_TEST (not logged): P2 +1.00 -> +2 | 1D +1 | 4H +1 | 1H +1"]
    assert log.read_bytes() == before


def test_p2_model_with_a_model_of_the_registry_outside_the_list(command):
    log = command / "p2_model_log.jsonl"
    before = log.read_bytes()
    result = _p2_model("--trade-id", "aaaa1111", "--model", "A")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[1] == "A (not logged): P2 +1.00 -> +2 | 1W +1 | 1D +1 | 12H +1 | 4H +1 | 1H +1"
    assert log.read_bytes() == before


def test_p2_model_on_a_historical_analysis_says_it_is_never_logged(command):
    result = _p2_model("--trade-id", "bbbb0000")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0].endswith("; not a new analysis, so it is never logged")
    assert result.output.splitlines()[1].startswith("D (not logged): ")


@pytest.mark.parametrize("args, message", [
    (("--trade-id", "zzzz"), "Unknown analysis: zzzz"),
    (("--trade-id", "aaaa"), "Ambiguous analysis id aaaa: aaaa1111-n1, aaaa2222-n2"),
    (("--trade-id", "aaaa1111", "--model", "ZZ"), "Unknown model: ZZ"),
])
def test_p2_model_exits_1_for_an_unknown_analysis_or_model(command, args, message):
    result = _p2_model(*args)
    assert result.exit_code == 1 and message in result.output


def test_an_exact_id_wins_over_a_longer_id_with_the_same_start(tmp_path):
    _write_db(tmp_path / "xau.db", [("cccc", "XAUUSDT.P", N1_START, False), ("cccc-2", "XAUUSDT.P", N2_START, False)])
    found = p2_feedback.find_analysis("cccc", str(tmp_path), {"000": "xau.db"})
    assert (found.trade_id, found.anchor, found.is_new) == ("cccc", N1_START, True)
