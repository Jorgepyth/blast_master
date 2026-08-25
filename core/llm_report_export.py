"""
core/llm_report_export.py — Reporte Markdown+JSON optimizado para consumo por LLM.

A diferencia de `core/report_export.py` (HTML+CSS, para lectura humana), este
módulo produce un único documento Markdown pensado para copiar/pegar o subir
a una conversación de LLM (ej. Claude) y pedirle análisis/recomendaciones de
trading. Decisiones de diseño, justificadas (no arbitrarias):

- **Sin imágenes**: un LLM de texto no puede "verlas", y aunque tuviera
  visión, extraer números precisos de un PNG es peor que leerlos de una
  tabla/JSON. Si se quiere referenciar un gráfico, se menciona en texto que
  existe en el reporte HTML — no se duplica.
- **Tablas en Markdown plano, no `Styler` con CSS**: el color no aporta
  nada a un LLM (lee el número), y el CSS inline de `Styler.to_html()` es
  puro bloat de tokens acá.
- **Datos crudos por trade en JSON, no en una tabla Markdown**: con ~35
  columnas una tabla Markdown sería casi ilegible; JSON es exacto y sin
  ambigüedad de parseo, y permite que el LLM cruce/filtre registros
  puntuales en vez de solo ver agregados.
- **`context` (dominio del proyecto) se recibe como parámetro, no se
  hardcodea acá**: mismo criterio que `ReportSection.narrative` en
  `report_export.py` — el contenido narrativo lo escribe el notebook
  (puede cambiar con el tiempo), este módulo solo sabe renderizarlo.
"""
from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

import numpy as np
import pandas as pd

DEFAULT_TRADE_COLUMNS: List[str] = [
    # Identidad / timing
    "id", "entry_time", "exit_time", "session", "market_state", "tier_setup", "setup_type", "exit_type",
    # Señal estructural (P0-P4 + edge)
    "market_bias", "calc_edge", "long_prob", "short_prob", "specific_bias_compliance",
    "bias_a", "real_bias_b", "resolution_type",
    "p0_direction", "p1_direction", "p2_direction", "p3_direction", "p4_direction",
    "p0_strength", "p1_strength", "p2_strength", "p3_strength", "p4_strength",
    "p0_score", "p1_score", "p2_score", "p3_score", "p4_score",
    # Ejecución / resultado
    "trade_decision", "order_filled", "followed_plan", "confirmation_status",
    "r_r", "r_multiple", "pnl_and_cost", "mfe_favorable", "mae_adverse",
    "entry_price", "closing_price", "take_profit", "stop_loss", "size", "risk_usd",
    # Comportamiento / psicología (lo más accionable para "cómo cambiar mi trading")
    "primary_emotion", "anxiety_level", "impatience_level", "mental_clarity_level",
    "emotions", "behavioral_errors", "cognitive_patterns", "lesson_learned",
    "gates_failed", "confirmations_count",
]


@dataclass
class MarkdownSection:
    """Una sección del reporte: narrativa + tablas + código fuente (memoria de cálculo)."""

    title: str
    narrative: str = ""
    tables: List[pd.DataFrame] = field(default_factory=list)
    source_functions: List[Callable] = field(default_factory=list)


def _dataframe_to_markdown_table(df: pd.DataFrame, round_digits: int = 4) -> str:
    """Tabla GFM manual (sin dependencia de `tabulate`)."""
    if df.empty:
        return "_(sin datos)_"

    d = df.copy()
    for col in d.columns:
        if pd.api.types.is_float_dtype(d[col]):
            d[col] = d[col].round(round_digits)

    headers = [str(c) for c in d.columns]
    header_row = "| " + " | ".join(headers) + " |"
    separator_row = "| " + " | ".join(["---"] * len(headers)) + " |"
    data_rows = []
    # d.iterrows() fuerza cada fila a un único dtype (upcastea enteros a float
    # si conviven con columnas float) — to_dict(orient="records") preserva el
    # tipo real de cada columna por celda.
    for record in d.to_dict(orient="records"):
        cells = ["" if pd.isna(v) else str(v) for v in record.values()]
        data_rows.append("| " + " | ".join(cells) + " |")

    return "\n".join([header_row, separator_row, *data_rows])


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    # OJO: pd.NaT es instancia de datetime.datetime (comportamiento de pandas) y
    # de np.floating/np.integer no lo es, así que esta comprobación de nulidad
    # debe ir ANTES de los checks de tipo — si no, pd.NaT.isoformat() devuelve
    # el string literal "NaT" en vez de None.
    try:
        is_na = pd.isna(value)
    except (TypeError, ValueError):
        is_na = False
    if isinstance(is_na, bool) and is_na:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def build_trade_records(
    df: pd.DataFrame,
    columns: Sequence[str] = DEFAULT_TRADE_COLUMNS,
) -> List[Dict[str, Any]]:
    """
    Selecciona `columns` (las que existan en `df`, en ese orden) y devuelve
    una lista de dicts JSON-serializable: NaN->None, Timestamp->ISO string,
    tipos numpy->tipos nativos de Python.
    """
    present_cols = [c for c in columns if c in df.columns]
    records = []
    for _, row in df[present_cols].iterrows():
        records.append({col: _json_safe(row[col]) for col in present_cols})
    return records


def _source_block(func: Callable) -> str:
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError):
        source = f"# No se pudo obtener el código fuente de {func!r}"
    name = getattr(func, "__name__", str(func))
    return f"**Memoria de cálculo — `{name}`**\n\n```python\n{source}\n```"


def render_llm_markdown_report(
    context: str,
    sections: List[MarkdownSection],
    trade_records: List[Dict[str, Any]],
    title: str,
    generated_at: Optional[datetime] = None,
) -> str:
    """Arma el Markdown completo: título, contexto de dominio, secciones, apéndice JSON."""
    generated_at = generated_at or datetime.now()
    timestamp = generated_at.strftime("%Y-%m-%d %H:%M:%S")

    parts = [f"# {title}", "", f"_Generado el {timestamp}_", "", "## Contexto", "", context, ""]

    for section in sections:
        parts.append(f"## {section.title}")
        parts.append("")
        if section.narrative:
            parts.append(section.narrative)
            parts.append("")
        for table in section.tables:
            parts.append(_dataframe_to_markdown_table(table))
            parts.append("")
        for func in section.source_functions:
            parts.append(_source_block(func))
            parts.append("")

    parts.append("## Apéndice — datos crudos por trade")
    parts.append("")
    parts.append(
        f"Lista de {len(trade_records)} trades en JSON, uno por registro, con las columnas "
        "de identidad/timing, señal estructural (P0-P4), ejecución/resultado y "
        "comportamiento/psicología. No incluye los 15 booleans individuales de "
        "gates/confirmaciones (`g1-g7`/`c1-c8`) — ya resumidos en `gates_failed`/`confirmations_count`."
    )
    parts.append("")
    parts.append("```json")
    parts.append(json.dumps(trade_records, indent=2, ensure_ascii=False))
    parts.append("```")

    return "\n".join(parts)


def save_markdown_report(content: str, output_path: Union[str, Path]) -> Path:
    path = Path(output_path)
    path.write_text(content, encoding="utf-8")
    return path
