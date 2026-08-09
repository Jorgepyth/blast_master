"""
core/report_formatting.py — Formato de reportes (Fase 4).

Funciones puras de presentación (dict/DataFrame in, DataFrame/Styler out —
cero acceso a DB). Resuelve un problema real, no solo cosmético: los dicts
anidados que devuelven `analytics_engine`/`backtest_engine` (kpis, resúmenes
de backtest) se imprimían como `repr()` de Python crudo en el notebook
(15+ decimales, sin tabla) porque `pd.set_option('display.float_format', ...)`
solo afecta a DataFrames, no a dicts.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


def flatten_dict(d: Dict[str, Any], parent_key: str = "", sep: str = ".") -> Dict[str, Any]:
    """Aplana un dict anidado: {"a": {"b": 1}} -> {"a.b": 1}. No toca listas/None/etc."""
    items: Dict[str, Any] = {}
    for key, value in d.items():
        new_key = f"{parent_key}{sep}{key}" if parent_key else str(key)
        if isinstance(value, dict):
            items.update(flatten_dict(value, new_key, sep=sep))
        else:
            items[new_key] = value
    return items


def dict_to_metrics_frame(
    d: Dict[str, Any],
    round_digits: int = 4,
    metric_col: str = "Métrica",
    value_col: str = "Valor",
) -> pd.DataFrame:
    """
    Aplana `d` y arma una tabla de 2 columnas (Métrica/Valor), redondeando
    floats a `round_digits`. Reemplaza el `print(dict)`/repr crudo.
    """
    flat = flatten_dict(d)
    rows = []
    for key, value in flat.items():
        if isinstance(value, float):
            value = round(value, round_digits)
        rows.append({metric_col: key, value_col: value})
    return pd.DataFrame(rows)


def style_signed_column(
    df: pd.DataFrame,
    col: str,
    cmap: str = "RdYlGn",
    center: float = 0.0,
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
):
    """
    Gradiente de color rojo/verde sobre `col`, centrado en `center` (0.0 para
    columnas tipo "Net Profit"/"R promedio", 1.0 para "Profit Factor" — su
    punto de equilibrio real). Devuelve un pandas Styler.
    """
    values = pd.to_numeric(df[col], errors="coerce")
    finite = values[np.isfinite(values)]
    if vmin is None or vmax is None:
        max_abs = float((finite - center).abs().max()) if len(finite) else 1.0
        max_abs = max_abs if max_abs > 0 else 1.0
        vmin = center - max_abs if vmin is None else vmin
        vmax = center + max_abs if vmax is None else vmax
    return df.style.background_gradient(subset=[col], cmap=cmap, vmin=vmin, vmax=vmax)
