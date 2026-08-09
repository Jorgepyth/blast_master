"""
core/report_export.py — Reporte HTML+CSS autocontenido.

Funciones puras (objetos ya calculados in/HTML string out — cero acceso a
DB, cero relectura del .ipynb del disco). Arma un reporte a partir de los
mismos objetos que ya existen en el namespace del notebook al terminar de
correrlo (kpis, tablas, Figures de matplotlib), evitando el problema de que
`jupyter nbconvert --execute --inplace` recién escribe el .ipynb a disco al
final de la ejecución — una celda que intentara auto-exportar el archivo
leería una versión desactualizada.

"Memoria de cálculo": cada sección puede adjuntar el código fuente real de
la(s) función(es) de `core/` que produjeron sus resultados, vía
`inspect.getsource()` — documenta la lógica real, no la línea de
orquestación del notebook, y no se desincroniza con el código porque no se
duplica a mano.
"""
from __future__ import annotations

import base64
import html as html_module
import inspect
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Callable, List, Optional, Union

import matplotlib.pyplot as plt
import pandas as pd

try:
    from pandas.io.formats.style import Styler
except ImportError:  # pragma: no cover
    Styler = None  # type: ignore


@dataclass
class ReportSection:
    """Una sección del reporte: narrativa + tablas + figuras + código fuente."""

    title: str
    narrative: str = ""
    tables: List[Union[pd.DataFrame, "Styler"]] = field(default_factory=list)
    figures: List[plt.Figure] = field(default_factory=list)
    source_functions: List[Callable] = field(default_factory=list)


def _slugify(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


def _figure_to_base64_img(fig: plt.Figure) -> str:
    """Serializa una Figure de matplotlib como <img> con PNG embebido en base64."""
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("ascii")
    return f'<img src="data:image/png;base64,{encoded}" alt="gráfico">'


def _table_to_html(table: Union[pd.DataFrame, "Styler"]) -> str:
    """Convierte un DataFrame o Styler a HTML. Preserva el estilo si ya es un Styler."""
    if Styler is not None and isinstance(table, Styler):
        return table.to_html()
    return table.to_html(classes="report-table", border=0, index=False)


def _source_block(func: Callable) -> str:
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError):
        source = f"# No se pudo obtener el código fuente de {func!r}"
    escaped = html_module.escape(source)
    name = getattr(func, "__name__", str(func))
    return f'<div class="source-block"><div class="source-label">{html_module.escape(name)}</div><pre><code>{escaped}</code></pre></div>'


_CSS = """
:root {
  --bg: #f7f8fa; --card-bg: #ffffff; --text: #1f2430; --muted: #5b6472;
  --border: #e2e5ea; --accent: #3db7e4; --code-bg: #282c34; --code-text: #d4d4d4;
}
* { box-sizing: border-box; }
body { margin: 0; padding: 0 1.5rem 4rem; background: var(--bg); color: var(--text);
       font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
.container { max-width: 1100px; margin: 0 auto; }
header.report-header { padding: 2.5rem 0 1.5rem; border-bottom: 1px solid var(--border); margin-bottom: 2rem; }
header.report-header h1 { margin: 0 0 0.25rem; font-size: 1.9rem; }
header.report-header .subtitle { color: var(--muted); font-size: 1.05rem; margin: 0 0 0.5rem; }
header.report-header .timestamp { color: var(--muted); font-size: 0.85rem; }
nav.toc { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
          padding: 1rem 1.5rem; margin-bottom: 2rem; }
nav.toc h2 { font-size: 1rem; margin: 0 0 0.5rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
nav.toc ul { margin: 0; padding-left: 1.2rem; }
nav.toc a { color: var(--accent); text-decoration: none; }
nav.toc a:hover { text-decoration: underline; }
section.report-section { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px;
                          padding: 1.5rem 2rem; margin-bottom: 1.75rem; }
section.report-section h2 { margin-top: 0; padding-bottom: 0.5rem; border-bottom: 1px solid var(--border); }
.narrative { color: var(--text); line-height: 1.55; }
.table-wrap { overflow-x: auto; margin: 1rem 0; }
table.report-table, .table-wrap table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
table.report-table th, table.report-table td, .table-wrap th, .table-wrap td {
  border: 1px solid var(--border); padding: 0.4rem 0.7rem; text-align: left; }
table.report-table tr:hover, .table-wrap tr:hover { background: #f0f4f8; }
.figure-wrap { margin: 1rem 0; text-align: center; }
.figure-wrap img { max-width: 100%; height: auto; border: 1px solid var(--border); border-radius: 6px; }
.source-block { margin-top: 1rem; }
.source-label { font-size: 0.8rem; color: var(--muted); text-transform: uppercase;
                letter-spacing: 0.04em; margin-bottom: 0.3rem; }
.source-block pre { background: var(--code-bg); color: var(--code-text); border-radius: 6px;
                     padding: 1rem; overflow-x: auto; font-size: 0.82rem; line-height: 1.45; }
.calc-memory-title { font-weight: 600; margin-top: 1.5rem; margin-bottom: 0.25rem; }
@media print {
  section.report-section { break-inside: avoid; box-shadow: none; }
  body { background: #fff; }
}
"""


def render_html_report(
    sections: List[ReportSection],
    title: str,
    subtitle: str = "",
    generated_at: Optional[datetime] = None,
) -> str:
    """Arma el HTML completo (CSS inline, sin dependencias externas)."""
    generated_at = generated_at or datetime.now()
    timestamp = generated_at.strftime("%Y-%m-%d %H:%M:%S")

    toc_items = []
    section_blocks = []
    for section in sections:
        anchor = _slugify(section.title)
        toc_items.append(f'<li><a href="#{anchor}">{html_module.escape(section.title)}</a></li>')

        parts = [f'<section class="report-section" id="{anchor}">', f"<h2>{html_module.escape(section.title)}</h2>"]
        if section.narrative:
            parts.append(f'<p class="narrative">{html_module.escape(section.narrative)}</p>')
        for table in section.tables:
            parts.append(f'<div class="table-wrap">{_table_to_html(table)}</div>')
        for fig in section.figures:
            parts.append(f'<div class="figure-wrap">{_figure_to_base64_img(fig)}</div>')
        if section.source_functions:
            parts.append('<div class="calc-memory-title">Memoria de cálculo</div>')
            for func in section.source_functions:
                parts.append(_source_block(func))
        parts.append("</section>")
        section_blocks.append("\n".join(parts))

    html_doc = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>{html_module.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="container">
<header class="report-header">
  <h1>{html_module.escape(title)}</h1>
  {f'<p class="subtitle">{html_module.escape(subtitle)}</p>' if subtitle else ""}
  <p class="timestamp">Generado el {timestamp}</p>
</header>
<nav class="toc">
  <h2>Contenido</h2>
  <ul>
    {"".join(toc_items)}
  </ul>
</nav>
{"".join(section_blocks)}
</div>
</body>
</html>"""
    return html_doc


def save_html_report(html_content: str, output_path: Union[str, Path]) -> Path:
    """Escribe el reporte a disco y devuelve la ruta resultante."""
    path = Path(output_path)
    path.write_text(html_content, encoding="utf-8")
    return path
