import re

import pandas as pd
import pytest

from core.report_formatting import dict_to_metrics_frame, flatten_dict, style_signed_column


def test_flatten_dict_nested_two_levels():
    d = {"a": 1, "b": {"c": 2, "d": {"e": 3}}}
    assert flatten_dict(d) == {"a": 1, "b.c": 2, "b.d.e": 3}


def test_flatten_dict_empty():
    assert flatten_dict({}) == {}


def test_flatten_dict_no_nesting_returns_equivalent():
    d = {"x": 1, "y": 2}
    assert flatten_dict(d) == d


def test_dict_to_metrics_frame_rounds_and_flattens_nested_keys():
    d = {"win_rate": 0.257142857142, "mfe_stats": {"mfe_mean_R": 2.03857142857}}
    out = dict_to_metrics_frame(d)
    assert out.to_dict(orient="records") == [
        {"Métrica": "win_rate", "Valor": 0.2571},
        {"Métrica": "mfe_stats.mfe_mean_R", "Valor": 2.0386},
    ]


def test_style_signed_column_assigns_distinct_colors_by_value():
    df = pd.DataFrame({"Parametro": ["P0", "P1", "P2"], "Profit Factor": [1.73, 1.04, 0.87]})
    styler = style_signed_column(df, "Profit Factor", cmap="RdYlGn", center=1.0)
    html = styler.to_html()
    colors = re.findall(r"background-color:\s*(#[0-9a-fA-F]{6})", html)
    assert len(colors) == 3
    assert len(set(colors)) == 3  # los 3 valores distintos reciben colores distintos
