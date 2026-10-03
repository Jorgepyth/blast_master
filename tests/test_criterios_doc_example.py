"""
T34 (spec 002, RF-21): `docs/criterios-de-acierto.md` apunta la implementación a `core/outcome_metrics.py`, y su
ejemplo para cuadernos corre tal cual está escrito. El test saca el bloque de código del documento y lo ejecuta con la
configuración apuntando a las cuentas y al banco de fixture de tests/test_resolution_report.py.
"""
import re
from pathlib import Path

import pytest

import config.auto_resolution as auto_cfg
from tests.test_auto_resolution import _write_bank, _write_db
from tests.test_resolution_report import ANALYSES

DOC = Path(__file__).resolve().parent.parent / "docs" / "criterios-de-acierto.md"


def _notebook_example() -> str:
    section = DOC.read_text(encoding="utf-8").split("## Uso desde un cuaderno", 1)[1].split("\n## ", 1)[0]
    (code,) = re.findall(r"```python\n(.*?)```", section, flags=re.S)
    return code


def test_the_doc_points_to_the_single_implementation():
    text = DOC.read_text(encoding="utf-8")
    assert "core/outcome_metrics.py" in text
    assert "Hasta que exista" not in text  # el módulo ya existe: overlap_2d.py deja de ser la referencia


def test_the_notebook_example_runs_on_the_fixture(tmp_path, monkeypatch, capsys):
    data_dir, bank_root = tmp_path / "data", tmp_path / "bank"
    data_dir.mkdir()
    bank_root.mkdir()
    _write_bank(bank_root)
    _write_db(data_dir / "xau.db", ANALYSES)
    monkeypatch.setattr(auto_cfg, "ACCOUNTS_DATA_DIR", str(data_dir))
    monkeypatch.setattr(auto_cfg, "CANDLE_BANK_DIR", str(bank_root))
    monkeypatch.setattr(auto_cfg, "REAL_ACCOUNTS", {"000": "xau.db"})

    exec(compile(_notebook_example(), str(DOC), "exec"), {})

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Strict S4: 2/2 = 100%, outside 48 h 0, late 0, backdated excluded 1"  # la principal, primero
    assert "S1: 2/2 = 100%, outside 48 h 0, late 0, backdated excluded 1" in lines
    assert "S4: 2/2 = 100%, outside 48 h 0, late 0, backdated excluded 1" in lines
    assert "a1 Overlap: B = a3, first touch 2026-06-01 01:00:00" in lines
    assert "a3 Overlap: B = a6, first touch 2026-06-01 01:00:00" in lines


def test_outcomes_carry_what_s1_and_s4_need(tmp_path):
    from tools.auto_resolution import AccountResolver
    (tmp_path / "bank").mkdir()
    _write_bank(tmp_path / "bank")
    _write_db(tmp_path / "xau.db", ANALYSES)
    resolver = AccountResolver(str(tmp_path / "xau.db"), str(tmp_path / "bank"), account="000")

    by_id = {o.analysis_id: o for o in resolver.outcomes()}

    assert (by_id["a1"].outcome, by_id["a1"].touch_time.hour, by_id["a1"].account) == ("confirmed", 1, "000")
    assert (by_id["a4"].outcome, by_id["a4"].touch_time) == ("no_levels", None)  # sin toque: el motivo
    assert by_id["a6"].is_backdated and by_id["a1"].market_bias == "Bullish"
    assert by_id["a1"].anchor == resolver.anchor_of(resolver.rows[0])
