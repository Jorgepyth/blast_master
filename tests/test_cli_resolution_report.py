"""
T33 (spec 002): la salida del reporte de comparación en Markdown (en inglés, N30) y el subcomando `resolution-report`
(RF-6, plan.md §4). Cuentas y banco de fixture en tmp_path (las de tests/test_resolution_report.py); nunca escribe en
una DB: lo cuida el audit hook de tests/conftest.py y acá, además, el `mtime` de cada DB.
"""
import dataclasses
import os
from datetime import datetime

import pytest
from click.testing import CliRunner

import config.auto_resolution as auto_cfg
import tools.database
from tests.test_auto_resolution import _write_bank, _write_db
from tests.test_resolution_report import ANALYSES, MANUAL, _add_manual_audits
from core.outcome_metrics import WinRate
from tools.database import init_db
from tools.resolution_report import WinRatePair, build_report, render_markdown

ACCOUNTS = {"000": "xau.db", "003": "us100.db"}
HEADERS = ["# Resolution report", "## Account 000 (xau.db)", "## Account 003 (us100.db)", "### Win rate",
           "### Agreement with the manual audit", "### Differences", "### Audit delay", "### Overlaps",
           "### Mark Prices outside their candles", "### Backdated loading delay"]


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    # `cli()` abre el engine de cuenta para cualquier subcomando: se lo fija en memoria.
    monkeypatch.setattr(tools.database, "engine_default", init_db("sqlite:///:memory:"))
    data_dir, bank_root = tmp_path / "data", tmp_path / "bank"
    data_dir.mkdir()
    bank_root.mkdir()
    _write_bank(bank_root)
    _write_db(data_dir / "xau.db", ANALYSES)
    _add_manual_audits(data_dir / "xau.db", MANUAL)
    _write_db(data_dir / "us100.db", [{"id": "n1", "asset": "US100", "created_at": datetime(2026, 6, 1)}])
    monkeypatch.setattr(auto_cfg, "ACCOUNTS_DATA_DIR", str(data_dir))
    monkeypatch.setattr(auto_cfg, "CANDLE_BANK_DIR", str(bank_root))
    monkeypatch.setattr(auto_cfg, "REAL_ACCOUNTS", dict(ACCOUNTS))
    return data_dir, bank_root


def _invoke(*args):
    from cli.main import cli
    return CliRunner().invoke(cli, ["resolution-report", *args], env={"COLUMNS": "250"})


def _mtimes(data_dir):
    return {name: os.stat(data_dir / name).st_mtime_ns for name in ACCOUNTS.values()}


# --- render_markdown ---------------------------------------------------------------

@pytest.fixture
def markdown(accounts):
    data_dir, bank_root = accounts
    return render_markdown(build_report(str(data_dir), str(bank_root), real_accounts=ACCOUNTS),
                           generated_at=datetime(2026, 10, 2, 8, 0))


def test_the_markdown_has_every_block_in_english(markdown):
    for header in HEADERS:
        assert header in markdown
    assert "Generated 2026-10-02 08:00" in markdown


def test_counts_and_missing_reasons(markdown):
    assert "- Resolved by candles: 3 of 5 (60%)" in markdown
    assert "- Missing: 2 (40%): no_levels 1, pending_candles 1" in markdown
    assert "- Missing: 1 (100%): clock_unverified 1" in markdown


def test_win_rate_table_with_the_manual_rate_on_the_same_analyses(markdown):
    header = "| Criterion | Scope | Candles | Manual (Valid) | Outside 48 h | Late, counted as loss | Backdated excluded |"
    table = markdown.split(header, 1)[1].split("\n")
    assert table[2] == "| Strict S4 | All | 2/2 = 100% | 1/2 = 50% | 0 | 0 | 1 |"  # la cifra principal, primera (N51)
    assert "| S1 | Directional | 2/2 = 100% | 1/2 = 50% | 0 | 0 | 1 |" in markdown
    assert "| S4 | All | 2/2 = 100% | 1/2 = 50% | 0 | 0 | 1 |" in markdown
    assert "| S1 | All | 0/0 = n/a | 0/0 = n/a | 0 | 0 | 0 |" in markdown  # US100: nada resuelto


def test_agreement_per_field_and_one_row_per_difference_with_compliance(markdown):
    assert "| resolution_type | 1 | 1 | 3 |" in markdown
    assert "| structural_mae | 1 | 1 | 3 |" in markdown
    # a3 es Overlap, pero desde N52 las velas proponen lo de su toque (Confirmed), no "Overlap Invalidation".
    assert "| a3 | resolution_type | Confirmed (A equal to B) | Invalidated (B not equal to A) | Invalid |" in markdown
    assert "| a3 | failure_reason | N/A | Overlap -- nuevo bias antes de resolucion | Invalid |" in markdown
    assert "| a3 | structural_mae | 99.00 | 95.00 | Invalid |" in markdown
    assert "| a1 |" not in markdown.split("### Differences")[1].split("###")[0]  # a1 coincide en todo


def test_audit_delay_overlaps_mark_prices_and_backdated(markdown):
    assert "- 2 analyses with audit and touch: median 3.0 h, min 2.0 h, max 4.0 h" in markdown
    assert "| a1 | validation | 2026-06-01 01:00 | a3 | 2026-06-01 00:40 |" in markdown
    assert "| a5 | 120.00 | 1M | 14.50 | no |" in markdown
    assert "No backdated analysis has `saved_at` yet." in markdown


def test_each_criterion_has_its_own_row_and_only_fields_that_differ_are_listed(accounts):
    data_dir, bank_root = accounts
    (xau,) = build_report(str(data_dir), str(bank_root), real_accounts={"000": "xau.db"})
    a3 = next(c for c in xau.comparisons if c.trade_id == "a3")
    a3 = dataclasses.replace(a3, agreement=dict(a3.agreement, structural_mae=None))  # MAE sin comparar
    xau = dataclasses.replace(xau, comparisons=[a3],
                              s4_directional=WinRatePair(WinRate(wins=1, n=1, outside=1, excluded_backdated=0), 1),
                              s4_strict_directional=WinRatePair(WinRate(1, 2, 0, 0, late=1), 1))
    markdown = render_markdown([xau], generated_at=datetime(2026, 10, 2))
    assert "| S4 | Directional | 1/1 = 100% | 1/1 = 100% | 1 | 0 | 0 |" in markdown
    assert "| Strict S4 | Directional | 1/2 = 50% | 1/2 = 50% | 0 | 1 | 0 |" in markdown
    assert "| a3 | resolution_type |" in markdown and "| a3 | structural_mae |" not in markdown


# --- resolution-report ---------------------------------------------------------------

def test_writes_the_markdown_prints_a_summary_and_exits_0_without_touching_the_databases(accounts, tmp_path):
    data_dir, _ = accounts
    before = _mtimes(data_dir)
    out_dir = tmp_path / "out"

    result = _invoke("--output-dir", str(out_dir))

    assert result.exit_code == 0, result.output
    (written,) = os.listdir(out_dir)
    assert written.startswith("resolution_report_") and written.endswith(".md") and len(written) == 34
    markdown = (out_dir / written).read_text(encoding="utf-8")
    assert all(header in markdown for header in HEADERS)
    assert "000 xau.db: 3/5 resolved" in result.output and "003 us100.db: 0/1 resolved" in result.output
    assert "Report written to" in result.output
    assert _mtimes(data_dir) == before


def test_the_default_output_folder_is_reports_inside_the_accounts_data_folder(accounts):
    data_dir, _ = accounts
    result = _invoke()
    assert result.exit_code == 0, result.output
    assert len(os.listdir(data_dir / "reports")) == 1  # .data/reports/: fuera de git, porque tiene datos del journal


def test_account_limits_the_report(accounts, tmp_path):
    result = _invoke("--output-dir", str(tmp_path / "out"), "--account", "003")
    assert result.exit_code == 0, result.output
    (written,) = os.listdir(tmp_path / "out")
    markdown = (tmp_path / "out" / written).read_text(encoding="utf-8")
    assert "## Account 003" in markdown and "## Account 000" not in markdown


@pytest.mark.parametrize("args", [["--account", "009"], []])
def test_an_unknown_account_or_a_missing_database_exits_1_without_a_report(accounts, tmp_path, args):
    data_dir, _ = accounts
    if not args:
        os.remove(data_dir / "us100.db")  # una cuenta de REAL_ACCOUNTS sin su DB
    result = _invoke("--output-dir", str(tmp_path / "out"), *args)
    assert result.exit_code == 1
    assert not (tmp_path / "out").exists()
    assert ("009" if args else "us100.db") in result.output


def test_a_missing_candle_bank_exits_1(accounts, tmp_path, monkeypatch):
    monkeypatch.setattr(auto_cfg, "CANDLE_BANK_DIR", str(tmp_path / "no_bank"))
    result = _invoke("--output-dir", str(tmp_path / "out"))
    assert result.exit_code == 1
    assert "candle bank" in result.output.lower() and not (tmp_path / "out").exists()
