import json

import numpy as np
import pandas as pd
import pytest

from core.llm_report_export import (
    MarkdownSection,
    _dataframe_to_markdown_table,
    build_trade_records,
    render_llm_markdown_report,
    save_markdown_report,
)


def test_dataframe_to_markdown_table_formats_gfm_and_rounds_without_upcasting_ints():
    df = pd.DataFrame({"a": [1, 2], "b": [3.14159, 2.71828]})
    out = _dataframe_to_markdown_table(df)
    assert out == "| a | b |\n| --- | --- |\n| 1 | 3.1416 |\n| 2 | 2.7183 |"


def test_dataframe_to_markdown_table_empty():
    assert _dataframe_to_markdown_table(pd.DataFrame()) == "_(sin datos)_"


def test_build_trade_records_handles_nat_nan_and_numpy_types():
    df = pd.DataFrame({
        "id": ["a1", "a2"],
        "entry_time": [pd.Timestamp("2026-01-01T10:00:00"), pd.NaT],
        "r_multiple": [np.float64(1.5), np.nan],
        "gates_failed": [np.int64(2), np.int64(0)],
        "extra_not_in_default_columns": [1, 2],
    })
    records = build_trade_records(df, columns=["id", "entry_time", "r_multiple", "gates_failed"])

    assert records == [
        {"id": "a1", "entry_time": "2026-01-01T10:00:00", "r_multiple": 1.5, "gates_failed": 2},
        {"id": "a2", "entry_time": None, "r_multiple": None, "gates_failed": 0},
    ]
    json.dumps(records)  # no debe lanzar TypeError


def test_build_trade_records_only_keeps_columns_present_in_df():
    df = pd.DataFrame({"id": ["a1"], "r_multiple": [1.0]})
    records = build_trade_records(df, columns=["id", "r_multiple", "columna_inexistente"])
    assert records == [{"id": "a1", "r_multiple": 1.0}]


def _dummy_func(x):
    return x + 1


def test_render_llm_markdown_report_includes_context_sections_and_json_appendix():
    df = pd.DataFrame({"a": [1, 2], "b": [3.14159, 2.71828]})
    records = [{"id": "a1", "r_multiple": 1.5}, {"id": "a2", "r_multiple": None}]
    sec = MarkdownSection(
        title="Sección KPIs", narrative="Texto narrativo", tables=[df], source_functions=[_dummy_func]
    )

    out = render_llm_markdown_report("Contexto de prueba", [sec], records, title="Reporte LLM de Prueba")

    assert "# Reporte LLM de Prueba" in out
    assert "Contexto de prueba" in out
    assert "## Sección KPIs" in out
    assert "Texto narrativo" in out
    assert "def _dummy_func(x):" in out
    assert "## Apéndice — datos crudos por trade" in out

    json_block = out.split("```json\n")[1].split("\n```")[0]
    assert json.loads(json_block) == records


def test_save_markdown_report_writes_file(tmp_path):
    output_path = tmp_path / "report.md"
    result = save_markdown_report("# contenido", output_path)
    assert result == output_path
    assert output_path.read_text(encoding="utf-8") == "# contenido"
