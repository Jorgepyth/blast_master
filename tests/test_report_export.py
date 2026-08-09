import base64

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import pytest

from core.report_export import (
    ReportSection,
    _figure_to_base64_img,
    _table_to_html,
    render_html_report,
    save_html_report,
)
from core.report_formatting import style_signed_column


@pytest.fixture(autouse=True)
def _close_all_figures():
    yield
    plt.close("all")


def test_figure_to_base64_img_produces_valid_png_data_uri():
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [1, 4, 9])
    img_tag = _figure_to_base64_img(fig)

    assert img_tag.startswith('<img src="data:image/png;base64,')
    b64_part = img_tag.split("base64,")[1].split('"')[0]
    decoded = base64.b64decode(b64_part)
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"


def test_table_to_html_plain_dataframe():
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    out = _table_to_html(df)
    assert "report-table" in out
    assert ">1<" in out and ">4<" in out


def test_table_to_html_preserves_styler_gradient():
    df = pd.DataFrame({"Parametro": ["P0", "P1"], "Profit Factor": [1.73, 0.87]})
    styler = style_signed_column(df, "Profit Factor", center=1.0)
    out = _table_to_html(styler)
    assert "background-color" in out


def _dummy_func(x):
    return x * 2


def test_render_html_report_includes_titles_narrative_and_source():
    fig, ax = plt.subplots()
    ax.plot([1, 2], [3, 4])
    df = pd.DataFrame({"a": [1, 2]})

    sec1 = ReportSection(title="Sección Uno", narrative="Texto narrativo de prueba", tables=[df])
    sec2 = ReportSection(title="Sección Dos", figures=[fig], source_functions=[_dummy_func])

    out = render_html_report([sec1, sec2], title="Reporte de Prueba", subtitle="Subtitulo")

    assert "Reporte de Prueba" in out
    assert "Subtitulo" in out
    assert "Sección Uno" in out and "Sección Dos" in out
    assert "Texto narrativo de prueba" in out
    assert "def _dummy_func(x):" in out  # memoria de cálculo: código fuente real
    assert 'href="#sección-uno"' in out
    assert 'id="sección-uno"' in out


def test_save_html_report_writes_file(tmp_path):
    output_path = tmp_path / "report.html"
    result = save_html_report("<html>contenido</html>", output_path)
    assert result == output_path
    assert output_path.read_text(encoding="utf-8") == "<html>contenido</html>"
