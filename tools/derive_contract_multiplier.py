#!/usr/bin/env python3
"""
derive_contract_multiplier.py -- Deriva contract_size por simbolo desde un
reporte de historial MT5 (xlsx o csv), y genera el bloque CONTRACT_SPECS de
config/contract_specs.py con su etiqueta de confianza.

CRITICO: el reporte MT5 apila 4 secciones con headers propios en el mismo
archivo/hoja:
    Positions  (open/close de cada posicion, con Profit real)
    Orders     (ordenes colocadas; Volume viene como "0.01 / 0")
    Deals      (ejecuciones atomicas + operaciones de balance)
    Open Positions (posiciones abiertas al momento del reporte)
Parsearlo como una tabla unica contamina columnas entre secciones. Este script
SEGMENTA por seccion (buscando el titulo en la columna A) ANTES de cualquier
calculo. `segment_sections()` y `read_rows()` se exportan para reuso por
tools/match_historical_trades.py (Track B Paso 1).

Para la seccion Positions:
    dP    = close_price - open_price
    raw   = Profit / (Volume * dP)
    mult  = -raw   si type in {sell, sell limit, sell stop}   (signo invertido)
          =  raw   en otro caso
Se descartan filas con Volume == 0, dP == 0 o Profit == 0.

Agregacion por symbol: n, mediana, desviacion estandar poblacional, desviacion
relativa (|pstdev / mediana|).

Regla de confianza:
    n >= --min-n (30) y rel_std < --max-rel-std (0.001)  -> "VERIFICADO"
    cualquier otro caso                                  -> "NO VERIFICADO"
Un simbolo "NO VERIFICADO" queda con contract_size=None; tools/pnl_calculator.py
RECHAZA calcular con el hasta que un humano lo confirme contra MT5 -> clic derecho
sobre el simbolo en Market Watch -> Specification, y lo marque "VERIFICADO".

Uso:
    # dry-run: imprime tabla resumen + el bloque propuesto, no escribe nada
    python tools/derive_contract_multiplier.py --input tools/migration_data/ReportHistory-87050257.xlsx

    # reescribe SOLO el bloque generado de config/contract_specs.py (imprime diff antes)
    python tools/derive_contract_multiplier.py --input <archivo> --force

Solo stdlib: csv + zipfile + xml.etree para xlsx. Sin dependencias nuevas.
El archivo de reporte MT5 NUNCA se commitea (tools/migration_data/ esta en .gitignore).
"""
import argparse
import csv
import difflib
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from statistics import median, pstdev

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

SECTION_TITLES = ("Positions", "Orders", "Deals", "Open Positions")
SELL_TYPES = {"sell", "sell limit", "sell stop"}

DEFAULT_EMIT = os.path.join(ROOT_DIR, "config", "contract_specs.py")
_GEN_START = "# --- generated block: tools/derive_contract_multiplier.py reescribe SOLO esto ---"
_GEN_END = "# --- end generated block ---"


# --------------------------------------------------------------------------- #
# Lectura del archivo -> lista de filas como dict {columna_letra: valor_str}
# --------------------------------------------------------------------------- #
def _col_letter(idx: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA'."""
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def _read_xlsx_rows(path: str) -> list[dict]:
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            sroot = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in sroot.findall("m:si", _NS):
                shared.append("".join(t.text or "" for t in si.iter(_XLSX_NS + "t")))
        sheet_name = sorted(
            n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)
        )[0]
        wroot = ET.fromstring(z.read(sheet_name))

    def cell_value(c) -> str:
        t = c.get("t")
        v = c.find("m:v", _NS)
        is_node = c.find("m:is", _NS)
        if t == "s" and v is not None:
            return shared[int(v.text)]
        if t == "inlineStr" and is_node is not None:
            return "".join(x.text or "" for x in is_node.iter(_XLSX_NS + "t"))
        return v.text if v is not None else ""

    rows = []
    for r in wroot.findall(".//m:sheetData/m:row", _NS):
        row = {}
        for c in r.findall("m:c", _NS):
            ref = c.get("r") or ""
            m = re.match(r"([A-Z]+)", ref)
            if not m:
                continue
            row[m.group(1)] = (cell_value(c) or "").strip()
        rows.append(row)
    return rows


def _read_csv_rows(path: str) -> list[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for record in csv.reader(fh):
            rows.append(
                {_col_letter(i): (val or "").strip() for i, val in enumerate(record)}
            )
    return rows


def read_rows(path: str) -> list[dict]:
    """Lee un reporte MT5 (.xlsx o .csv) a lista de filas {columna_letra: str}."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xlsx":
        return _read_xlsx_rows(path)
    if ext == ".csv":
        return _read_csv_rows(path)
    raise ValueError(f"Formato no soportado: {ext!r} (usar .xlsx o .csv)")


# --------------------------------------------------------------------------- #
# Segmentador de secciones -- reusado por Track B Paso 1
# --------------------------------------------------------------------------- #
def _is_blank(row: dict) -> bool:
    return not any((v or "").strip() for v in row.values())


def _is_title_row(row: dict) -> str | None:
    """Devuelve el titulo si `row` es una fila-titulo de seccion, si no None.
    Una fila-titulo tiene el texto del titulo en la columna A y todo lo demas vacio."""
    a = (row.get("A") or "").strip()
    if a in SECTION_TITLES:
        others = [v for k, v in row.items() if k != "A" and (v or "").strip()]
        if not others:
            return a
    return None


def segment_sections(rows: list[dict]) -> dict:
    """
    Segmenta las filas crudas del reporte MT5 en sus 4 secciones.

    -> {titulo: {"header": {col: nombre}, "rows": [ {col: valor}, ... ]}}

    La fila siguiente al titulo es el header; las filas de datos van hasta el
    siguiente titulo, una fila totalmente vacia, o una fila sin id en la
    columna B (fin de datos / fila de subtotal / bloque de cuenta).
    """
    sections: dict = {}
    i = 0
    n = len(rows)
    while i < n:
        title = _is_title_row(rows[i])
        if title is None:
            i += 1
            continue
        header_idx = i + 1
        if header_idx >= n:
            sections[title] = {"header": {}, "rows": []}
            break
        header = dict(rows[header_idx])
        data = []
        j = header_idx + 1
        while j < n:
            row = rows[j]
            if _is_title_row(row) is not None or _is_blank(row):
                break
            if not (row.get("B") or "").strip():
                break
            data.append(row)
            j += 1
        sections[title] = {"header": header, "rows": data}
        i = j
    return sections


# --------------------------------------------------------------------------- #
# Calculo de multiplicadores (seccion Positions)
# --------------------------------------------------------------------------- #
def _header_index(header: dict) -> dict:
    """Mapea nombre-de-columna -> lista de letras de columna (puede repetirse:
    Positions tiene 'Time' y 'Price' dos veces)."""
    out: dict = {}
    for col, name in sorted(header.items()):
        key = (name or "").strip()
        if key:
            out.setdefault(key, []).append(col)
    return out


def _to_decimal(s):
    try:
        return Decimal(str(s).strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None


def compute_positions_multipliers(positions_section: dict) -> dict:
    """
    -> {symbol: [Decimal, ...]}  multiplicadores empiricos de cada fila valida.
    """
    header = positions_section["header"]
    hidx = _header_index(header)
    try:
        col_symbol = hidx["Symbol"][0]
        col_type = hidx["Type"][0]
        col_volume = hidx["Volume"][0]
        col_open = hidx["Price"][0]   # primer "Price" = precio de apertura
        col_close = hidx["Price"][1]  # segundo "Price" = precio de cierre
        col_profit = hidx["Profit"][0]
    except (KeyError, IndexError) as exc:
        raise ValueError(
            f"Header de Positions inesperado: {header!r} (falta {exc})"
        ) from None

    by_symbol: dict = {}
    for row in positions_section["rows"]:
        symbol = (row.get(col_symbol) or "").strip()
        typ = (row.get(col_type) or "").strip().lower()
        volume = _to_decimal(row.get(col_volume))
        open_p = _to_decimal(row.get(col_open))
        close_p = _to_decimal(row.get(col_close))
        profit = _to_decimal(row.get(col_profit))
        if not symbol or volume is None or open_p is None or close_p is None or profit is None:
            continue
        dp = close_p - open_p
        if volume == 0 or dp == 0 or profit == 0:
            continue
        raw = profit / (volume * dp)
        mult = -raw if typ in SELL_TYPES else raw
        by_symbol.setdefault(symbol, []).append(mult)
    return by_symbol


def summarize(by_symbol: dict, min_n: int, max_rel_std: Decimal) -> dict:
    """
    -> {symbol: {n, median, pstdev, rel_std, confidence, contract_size, empirical_hint}}
    """
    out: dict = {}
    for symbol, mults in sorted(by_symbol.items()):
        n = len(mults)
        md = median(mults)
        sd = pstdev(mults) if n > 1 else Decimal("0")
        rel = abs(sd / md) if md != 0 else None
        verified = n >= min_n and rel is not None and rel < max_rel_std
        out[symbol] = {
            "n": n,
            "median": md,
            "pstdev": sd,
            "rel_std": rel,
            "confidence": "VERIFICADO" if verified else "NO VERIFICADO",
            "contract_size": md if verified else None,
            "empirical_hint": (md if (not verified and n >= 2) else None),
        }
    return out


# --------------------------------------------------------------------------- #
# Render del bloque generado de config/contract_specs.py
# --------------------------------------------------------------------------- #
def _fmt_decimal(d: Decimal, places: int) -> str:
    q = d.quantize(Decimal(10) ** -places)
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def render_generated_block(summary: dict, source_name: str, derived_at: str) -> str:
    lines = [_GEN_START, "CONTRACT_SPECS = {"]
    for symbol, info in summary.items():
        src = f"{source_name} · Positions · n={info['n']}"
        if info["confidence"] == "VERIFICADO":
            src += " · rel_std<0.1%"
        lines.append(f'    "{symbol}": {{')
        if info["contract_size"] is not None:
            lines.append(
                f'        "contract_size": Decimal("{_fmt_decimal(info["contract_size"], 8)}"),'
            )
        else:
            lines.append('        "contract_size": None,')
        lines.append(f'        "confidence": "{info["confidence"]}",')
        if info["empirical_hint"] is not None:
            lines.append(
                f'        "empirical_hint": Decimal("{_fmt_decimal(info["empirical_hint"], 6)}"),'
                "  # solo referencia -- nunca usado en el calculo"
            )
        lines.append(f'        "source": "{src}",')
        lines.append(f'        "derived_at": "{derived_at}",')
        lines.append("    },")
    lines.append("}")
    lines.append(_GEN_END)
    return "\n".join(lines)


def emit_to_file(emit_path: str, block: str, force: bool) -> bool:
    """Reemplaza el texto entre los centinelas en `emit_path`. Imprime el diff.
    Escribe solo si `force`. Devuelve True si hubo (o habria) cambio."""
    with open(emit_path, encoding="utf-8") as fh:
        original = fh.read()
    if _GEN_START not in original or _GEN_END not in original:
        raise ValueError(
            f"{emit_path} no tiene los centinelas {_GEN_START!r} / {_GEN_END!r}"
        )
    pre, rest = original.split(_GEN_START, 1)
    _, post = rest.split(_GEN_END, 1)
    updated = pre + block + post

    if updated == original:
        print(f"[=] {emit_path}: el bloque generado ya esta al dia, nada que hacer.")
        return False

    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=f"a/{os.path.relpath(emit_path, ROOT_DIR)}",
        tofile=f"b/{os.path.relpath(emit_path, ROOT_DIR)}",
    )
    sys.stdout.writelines(diff)
    print()
    if not force:
        print(f"[dry-run] pasa --force para escribir {emit_path}")
        return True
    with open(emit_path, "w", encoding="utf-8") as fh:
        fh.write(updated)
    print(f"[ok] {emit_path} actualizado.")
    return True


# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True, help="Reporte MT5 (.xlsx o .csv)")
    p.add_argument("--emit", default=DEFAULT_EMIT, help=f"Archivo a actualizar (default: {DEFAULT_EMIT})")
    p.add_argument("--force", action="store_true", help="Escribe el bloque generado (default: dry-run)")
    p.add_argument("--min-n", type=int, default=30, help="n minimo para VERIFICADO (default 30)")
    p.add_argument("--max-rel-std", type=Decimal, default=Decimal("0.001"),
                   help="desviacion relativa maxima para VERIFICADO (default 0.001 = 0.1%%)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    rows = read_rows(args.input)
    sections = segment_sections(rows)
    if "Positions" not in sections:
        print("ERROR: no se encontro la seccion 'Positions' en el reporte.", file=sys.stderr)
        return 1

    by_symbol = compute_positions_multipliers(sections["Positions"])
    summary = summarize(by_symbol, args.min_n, args.max_rel_std)

    print(f"Secciones detectadas: {', '.join(sections)}")
    print(f"Positions: {len(sections['Positions']['rows'])} filas de datos\n")
    print(f"{'symbol':12s} {'n':>5s} {'median':>16s} {'rel_std':>12s}   confianza")
    print("-" * 60)
    for symbol, info in summary.items():
        rel = "n/a" if (info["rel_std"] is None or info["n"] < 2) else f"{float(info['rel_std']):.3e}"
        print(f"{symbol:12s} {info['n']:5d} {_fmt_decimal(info['median'], 6):>16s} {rel:>12s}   {info['confidence']}")
    print()

    source_name = os.path.basename(args.input)
    # Reusa el derived_at existente del archivo si se puede, si no hoy.
    derived_at = "2026-09-03"
    block = render_generated_block(summary, source_name, derived_at)
    print(block)
    print()

    if os.path.exists(args.emit):
        emit_to_file(args.emit, block, args.force)
    else:
        print(f"[aviso] {args.emit} no existe; solo se imprimio el bloque.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
