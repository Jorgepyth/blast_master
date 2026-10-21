"""
tools/p2_vista.py — La vista P2 de un análisis: un HTML que se abre con Ctrl+J desde el detalle del análisis.

Muestra el P2 del operador (la capa cargada), el P2 sistemático de cada modelo de `P2_LOG_MODELS` (D y D600) con su
cuenta por temporalidad, las velas de esas temporalidades con sus EMA 20, 100 y 200, y el resultado del análisis.
- **Siempre muestra el P2 sistemático,** aunque el análisis siga abierto (decisión del operador, 2026-10-11).
- **El P0 sistemático (O2-v1.0) aparece solo con el análisis resuelto:** tocó un nivel o pasaron 48 h (opción A del
  operador, 2026-10-15; `docs/p0-reglas-opcion2.md`). Antes no entra ni al HTML, para no influir en las lecturas que
  miden H1, H2 y H3.
- **Solo lee** las DBs (`mode=ro`), el banco y el registro del P2. Escribe un solo archivo, el HTML, en
  `${ACCOUNTS_DATA_DIR}/reports/`: tiene datos del journal y queda fuera de git.
- **El P2 de cada modelo sale del registro.** Si no hay línea vigente (un análisis anterior al alta del modelo, o una que
  se reintenta), se calcula al abrir con la misma función que el registro (`model_p2`) y solo velas cerradas en el ancla.

Uso: Ctrl+J en el detalle del análisis, o `python tools/p2_vista.py --trade-id <id> [--account <clave>]`.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import traceback
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import pandas as pd

import config.auto_resolution as cfg
from core.candle_resolution import OUTCOME_CONFIRMED, OUTCOME_INVALIDATED, OUTCOME_OPEN
from core.outcome_metrics import analysis_anchor
from tools import p0_model_feedback as p0log
from tools import p0_opcion2 as o2
from tools import p2_backtest
from tools.auto_resolution import propose_for_trade, read_analysis_rows
from tools.candle_bank import bank_csv_path, read_candle_csv
from tools.p2_model_feedback import RETRY_STATUSES, STATUS_OK, latest_lines, model_p2, read_log

BARS_BEFORE = 90      # velas cerradas en el ancla que se dibujan por temporalidad
BARS_AFTER = 24       # velas posteriores, para "Mostrar lo que pasó después"
GT_OFFSET = timedelta(hours=6)   # las horas del banco y de la DB son de Guatemala (UTC−6, sin horario de verano)
SCALE_WORDS = {-2: "bajista fuerte", -1: "bajista media", 0: "neutral o débil", 1: "alcista media", 2: "alcista fuerte"}
P0_MODEL, P0_MODEL_HASH = "O2-v1.0", "0265dd400d07"
STATE_WORDS = {"bullish": "Alcista", "bearish": "Bajista", "range": "Rango", "insufficient_history": "Sin historia"}
SITUATION_WORDS = {
    "insufficient_history": "sin historia suficiente",
    "range": "rango: sin tendencia",
    "trend_no_event": "tendencia, sin evento en un nivel",
    "trend_retracement_or_1_rejection": "rechazo en un nivel de retroceso",
    "trend_retracement_or_1_rupture": "ruptura de un nivel de retroceso",
    "trend_0_rejection": "rechazo en el nivel 0",
    "trend_0_rupture": "ruptura del nivel 0",
}


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

def _epoch_ms(naive_gt: datetime) -> int:
    """Una hora de Guatemala sin zona, en milisegundos desde 1970 (UTC), para que el navegador la muestre bien."""
    return int((pd.Timestamp(naive_gt) + GT_OFFSET).value // 1_000_000)


def find_analysis(db_path: str, trade_id: str):
    """La fila del análisis, por id completo o por un prefijo único."""
    rows = read_analysis_rows(db_path)
    exact = [r for r in rows if r.trade_id == trade_id]
    matches = exact or [r for r in rows if r.trade_id.startswith(trade_id)]
    if len(matches) != 1:
        raise LookupError(f"{len(matches)} análisis coinciden con {trade_id!r} en {os.path.basename(db_path)}")
    return matches[0]


def operator_layer(db_path: str, trade_id: str, layer: str = "P2") -> Dict[str, Optional[str]]:
    conn = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT direction, strength, thesis FROM analysis_layer WHERE trade_id = ? AND layer_name = ?",
                           (trade_id, layer)).fetchone()
    finally:
        conn.close()
    direction, strength, thesis = row if row else (None, None, None)
    sign = 1 if direction == "Long" else -1 if direction == "Short" else 0
    weight = 2 if strength == "Strong" else 1 if strength == "Mid" else 0
    return {"direction": direction, "strength": strength, "thesis": thesis or "", "value": sign * weight}


def status_text(status: str, spec=None, weekly_bars: Optional[int] = None, symbol: Optional[str] = None) -> str:
    """El motivo por el que un modelo no da valor, en palabras."""
    if status.startswith("insufficient_history:"):
        tf = status.split(":", 1)[1]
        need = spec.min_bars_for(tf) if spec is not None else None
        have = f"; hay {weekly_bars}" if tf == "1W" and weekly_bars is not None else ""
        unit = "velas semanales" if tf == "1W" else f"velas de {tf}"
        return f"pide {need} {unit}{have}" if need else f"le falta historia en {tf}"
    if status.startswith("timeframe_not_in_bank:"):
        return f"falta la temporalidad {status.split(':', 1)[1]} en el banco"
    return {
        cfg.REASON_NO_MT5_SYMBOL: "el activo no tiene símbolo en MT5",
        cfg.REASON_CLOCK_UNVERIFIED: f"el reloj de las velas de {symbol} todavía no está verificado",
        cfg.REASON_CLOCK_MISALIGNED: f"el reloj de las velas de {symbol} no calza",
        cfg.REASON_PENDING_CANDLES: "las velas todavía no llegan a la hora del análisis",
    }.get(status, status)


def model_rows(by_tf: Dict[str, dict], spec) -> List[dict]:
    """La cuenta por temporalidad, con la misma regla que `compute_score_p2_sistematico`."""
    rows = []
    for tf in spec.timeframes:
        d = by_tf.get(tf) or {}
        chain = [d.get("price"), d.get("ema20"), d.get("ema100"), d.get("ema200")]
        if any(v is None for v in chain):
            ema_bias = None
        elif all(chain[i] > chain[i + 1] for i in range(3)):
            ema_bias = 1
        elif all(chain[i] < chain[i + 1] for i in range(3)):
            ema_bias = -1
        else:
            ema_bias = 0
        adx = d.get("adx")
        force = None if adx is None else p2_backtest.strength_from_adx(adx)
        bias = d.get("bias")
        contribution = None if bias is None or force is None else spec.weights[tf] * bias * force
        rows.append({"tf": tf, "weight": spec.weights[tf], "price": d.get("price"), "ema20": d.get("ema20"),
                     "ema100": d.get("ema100"), "ema200": d.get("ema200"), "plus_di": d.get("plus_di"),
                     "minus_di": d.get("minus_di"), "adx": adx, "ema_bias": ema_bias, "bias": bias, "force": force,
                     "contribution": contribution, "bars": d.get("bars")})
    return rows


def systematic_p2(trade_id: str, symbol: Optional[str], anchor: datetime, bank_root: str, log_path: str,
                  models: Sequence[str], weekly_bars: Optional[int]) -> List[dict]:
    """El P2 de cada modelo: la línea vigente del registro o, si no hay una definitiva, calculado ahora."""
    latest = latest_lines(read_log(log_path))
    out = []
    for name in models:
        spec = p2_backtest.MODELS_BY_NAME.get(name)
        if spec is None:
            out.append({"name": name, "status": "unknown_model", "text": "modelo desconocido"})
            continue
        line = latest.get((trade_id, name))
        if line is not None and line.data.get("status") not in RETRY_STATUSES:
            data, source = line.data, "registro"
            status, raw, rescaled, by_tf = data.get("status"), data.get("p2_raw"), data.get("p2_rescaled"), \
                data.get("by_tf") or {}
        else:
            status, raw, rescaled, by_tf = model_p2(spec, symbol, anchor, bank_root)
            source = "calculado al abrir"
        entry = {"name": name, "status": status, "source": source}
        if status == STATUS_OK:
            entry.update({"raw": raw, "rescaled": rescaled, "rows": model_rows(by_tf, spec)})
        else:
            entry["text"] = status_text(status, spec, weekly_bars, symbol)
        out.append(entry)
    return out


def chart_series(bank_dir: str, timeframes: Sequence[str], anchor: datetime, before: int = BARS_BEFORE,
                 after: int = BARS_AFTER) -> Dict[str, dict]:
    """Por temporalidad: las últimas velas cerradas en el ancla y las siguientes, con sus EMA (calculadas sobre toda la
    historia del banco, así que en el ancla valen lo mismo que en el modelo)."""
    charts = {}
    for tf in timeframes:
        df = read_candle_csv(bank_csv_path(bank_dir, tf))
        if df.empty:
            continue
        df = df.sort_values("time").reset_index(drop=True)
        times = pd.to_datetime(df["time"])
        closed = times + pd.Timedelta(minutes=p2_backtest.TIMEFRAME_MINUTES[tf]) <= pd.Timestamp(anchor)
        n_closed = int(closed.sum())
        if n_closed == 0:
            continue
        emas = {n: p2_backtest._ema(df["close"].astype(float), n) for n in (20, 100, 200)}
        lo, hi = max(0, n_closed - before), min(len(df), n_closed + after)
        part = df.iloc[lo:hi]
        charts[tf] = {
            "minutes": p2_backtest.TIMEFRAME_MINUTES[tf],
            "n_before": n_closed - lo,
            "closed_total": n_closed,
            "bars": [[_epoch_ms(t), round(float(o), 5), round(float(h), 5), round(float(l), 5), round(float(c), 5)]
                     for t, o, h, l, c in zip(pd.to_datetime(part["time"]), part["open"], part["high"], part["low"],
                                              part["close"])],
            **{f"ema{n}": [round(float(v), 5) for v in emas[n].iloc[lo:hi]] for n in (20, 100, 200)},
        }
    return charts


def resolution_summary(db_path: str, trade_id: str, bank_root: str, anchor: datetime,
                       now: Optional[datetime] = None) -> dict:
    """El resultado del análisis según las velas (el mismo resolvedor de los audits), en dos líneas, y si ya se resolvió:
    tocó un nivel, o pasaron las 48 h de S4 estricto. `hours_left` dice cuánto falta cuando todavía no."""
    now = now or p0log._now_gt()
    window = timedelta(hours=cfg.S4_WINDOW_H)
    left = max(0.0, (window - (now - anchor)).total_seconds() / 3600)
    by_time = {"resolved": left == 0, "hours_left": None if left == 0 else left}
    try:
        proposal = propose_for_trade(db_path, trade_id, bank_root)
    except Exception as exc:  # la vista no se cae por el resultado: lo dice
        return {"title": "Sin resultado", "note": f"no se pudo calcular ({type(exc).__name__})", **by_time}
    res = proposal.resolution
    if res is not None and res.outcome in (OUTCOME_CONFIRMED, OUTCOME_INVALIDATED) and res.first_touch is not None:
        hours = (res.first_touch.touch_time - anchor).total_seconds() / 3600
        hit = res.outcome == OUTCOME_CONFIRMED
        title = "Tocó el objetivo" if hit else "Tocó la invalidación"
        s4 = "en S4 estricto, acierto" if hit and hours <= cfg.S4_WINDOW_H else "en S4 estricto, falla"
        return {"title": title, "note": f"a las {hours:.0f} h · {s4}", "resolved": True, "hours_left": None,
                "winner": "target" if hit else "invalidation"}
    if res is not None and res.outcome == OUTCOME_OPEN:
        return {"title": "Sin toque", "note": "no tocó ningún nivel en todo el horizonte · en S4 estricto, falla",
                "resolved": True, "hours_left": None, "winner": "none"}
    if not by_time["resolved"]:
        return {"title": "Abierto", "note": f"faltan {left:.0f} h para las 48 h", **by_time}
    reason = proposal.reason or (res.outcome if res else "")
    if reason == cfg.REASON_PENDING_CANDLES:
        return {"title": "Sin toque todavía", "note": "las velas todavía no cubren todo el camino", **by_time}
    return {"title": "Sin resultado", "note": status_text(reason, symbol=proposal.symbol) if reason else "",
            **by_time}


def systematic_p0(trade_id: str, symbol: Optional[str], anchor: datetime, bank_root: str, log_path: str) -> dict:
    """El P0 sistemático (O2-v1.0) del análisis: la línea vigente del registro, con la huella congelada, o calculado
    ahora con la misma función que el registro (velas cerradas en el ancla)."""
    line = latest_lines(read_log(log_path)).get((trade_id, P0_MODEL))
    if line is not None and line.data.get("status") == STATUS_OK and line.data.get("model_hash") == P0_MODEL_HASH:
        status, direction, strength, by_tf = STATUS_OK, line.data.get("direction"), line.data.get("strength"), \
            line.data.get("by_tf") or {}
        source = "registro"
    else:
        status, direction, strength, by_tf = p0log.model_p0(o2.MODELS_BY_NAME[P0_MODEL], symbol, anchor, bank_root)
        source = "calculado al abrir"
    if status != STATUS_OK:
        return {"model": P0_MODEL, "status": status, "source": source, "text": status_text(status, symbol=symbol)}
    rows = []
    for tf in o2.ORDER:
        d = by_tf.get(tf)
        if not d:
            continue
        leg = d.get("leg") or None
        rows.append({"tf": tf, "state": STATE_WORDS.get(d.get("state"), d.get("state") or "—"),
                     "situation": SITUATION_WORDS.get(d.get("situation"), d.get("situation") or "—"),
                     "score": d.get("score"), "leg": None if leg is None else [leg.get("a"), leg.get("b")]})
    return {"model": P0_MODEL, "status": STATUS_OK, "source": source, "direction": direction, "strength": strength,
            "rows": rows}


def edge_comparison(db_path: str, trade_id: str, thesis_dir: int, models: List[dict], p0: dict, result: dict) -> dict:
    """El bias final del edge, con los cinco P manuales y con el P0 y el P2 sistemáticos (P1, P3 y P4 manuales), y qué
    habría pasado con cada uno. El lado sistemático exige el P0, así que solo existe con el análisis resuelto."""
    from cli.main import determine_market_bias, get_dir_val, get_str_val
    from core.math_engine import calculate_edge_score
    layers = [operator_layer(db_path, trade_id, f"P{k}") for k in range(5)]
    xs = [float(L["value"]) for L in layers]
    words = {"Bullish": "Alcista", "Bearish": "Bajista"}

    def outcome(bias: str) -> dict:
        d = 1 if bias == "Bullish" else -1 if bias == "Bearish" else 0
        winner = result.get("winner")
        if not result.get("resolved") or winner is None:
            return {"verdict": "pendiente", "text": "el análisis sigue abierto"}
        if d == 0:
            return {"verdict": "none", "text": "no opina"}
        if thesis_dir == 0:
            return {"verdict": "none", "text": "sin niveles para medirlo"}
        if winner == "none":
            return {"verdict": "miss", "text": "falla: no tocó ningún nivel"}
        right = thesis_dir if winner == "target" else -thesis_dir
        return {"verdict": "hit" if d == right else "miss",
                "text": "acierta" if d == right else "falla"}

    def side(label: str, values: List[float], note: str) -> dict:
        score = calculate_edge_score(*values)
        bias = determine_market_bias(score)
        return {"label": label, "score": round(score, 3), "bias": words.get(bias, "Choppy"), "note": note,
                **outcome(bias)}

    manual = side("Análisis manual", xs, "P0 a P4 como los cargaste")
    ok_p2 = [m for m in models if m.get("status") == STATUS_OK]
    ok_p2 = sorted(ok_p2, key=lambda m: m["name"] != "D600")
    if p0.get("hidden"):
        return {"manual": manual, "systematic": None, "reason": "El P0 sistemático se ve con el análisis resuelto."}
    if p0.get("status") != STATUS_OK or not ok_p2:
        return {"manual": manual, "systematic": None,
                "reason": "El sistema no da P0 o P2 para este análisis, así que no hay bias sistemático."}
    x0 = get_dir_val(p0.get("direction")) * get_str_val(p0.get("strength"))
    p2m = ok_p2[0]
    sysv = list(xs)
    sysv[0], sysv[2] = float(x0), float(p2m["rescaled"])
    systematic = side("Bias sistemático", sysv, f"P0 {p0['model']} y P2 {p2m['name']}; P1, P3 y P4 manuales")
    return {"manual": manual, "systematic": systematic, "reason": None,
            "layers": [{"name": f"P{k}", "manual": xs[k],
                        "systematic": sysv[k], "is_sys": k in (0, 2)} for k in range(5)]}


def build_view(db_path: str, trade_id: str, data_dir: Optional[str] = None, bank_root: Optional[str] = None,
               log_path: Optional[str] = None, models: Optional[Sequence[str]] = None,
               now: Optional[datetime] = None, p0_log_path: Optional[str] = None) -> dict:
    """Todo lo que la vista necesita de un análisis, sin escribir nada. El P0 sistemático solo entra si el análisis ya
    se resolvió; si no, la vista dice cuánto falta y el dato no está en ningún lado del HTML."""
    data_dir = cfg.ACCOUNTS_DATA_DIR if data_dir is None else data_dir
    bank_root = os.path.join(data_dir, "candle_bank") if bank_root is None else bank_root
    log_path = os.path.join(data_dir, cfg.P2_MODEL_LOG_NAME) if log_path is None else log_path
    p0_log_path = os.path.join(data_dir, cfg.P0_MODEL_LOG_NAME) if p0_log_path is None else p0_log_path
    models = list(cfg.P2_LOG_MODELS) if models is None else list(models)
    row = find_analysis(db_path, trade_id)
    anchor = analysis_anchor(row.analysis_start_time, row.created_at, row.is_backdated)
    symbol = cfg.MT5_SYMBOL_MAP.get(row.asset)
    bank_dir = os.path.join(bank_root, symbol) if symbol else None
    timeframes: List[str] = []
    for name in models:
        spec = p2_backtest.MODELS_BY_NAME.get(name)
        for tf in (spec.timeframes if spec else ()):
            if tf not in timeframes:
                timeframes.append(tf)
    charts = chart_series(bank_dir, timeframes, anchor) if bank_dir and os.path.isdir(bank_dir) else {}
    weekly = charts.get("1W", {}).get("closed_total")
    thesis_dir = 0
    if row.edge_validation_price is not None and row.structural_invalidation is not None:
        thesis_dir = 1 if row.edge_validation_price > row.structural_invalidation else -1
    result = resolution_summary(db_path, row.trade_id, bank_root, anchor, now)
    p2_models = systematic_p2(row.trade_id, symbol, anchor, bank_root, log_path, models, weekly)
    p0_view = systematic_p0(row.trade_id, symbol, anchor, bank_root, p0_log_path) if result["resolved"] \
        else {"hidden": True, "hours_left": result["hours_left"]}
    return {
        "generated_at": (now or datetime.now()).strftime("%Y-%m-%d %H:%M"),
        "analysis": {"id": row.trade_id, "id8": row.trade_id[:8], "asset": row.asset, "symbol": symbol,
                     "anchor_ms": _epoch_ms(anchor), "thesis_dir": thesis_dir, "is_backdated": bool(row.is_backdated),
                     "evp": row.edge_validation_price, "si": row.structural_invalidation},
        "operator": operator_layer(db_path, row.trade_id),
        "models": p2_models,
        "charts": charts,
        "result": result,
        "operator_p0": operator_layer(db_path, row.trade_id, "P0"),
        "p0": p0_view,
        "comparison": edge_comparison(db_path, row.trade_id, thesis_dir, p2_models, p0_view, result),
        "scale_words": {str(k): v for k, v in SCALE_WORDS.items()},
    }


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def render_html(view: dict) -> str:
    payload = json.dumps(view, ensure_ascii=False, allow_nan=False).replace("</", "<\\/")
    title = f"Vista P2 · {view['analysis'].get('asset') or ''} · {view['analysis']['id8']}"
    return TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__DATA__", payload)


def write_view(view: dict, out_dir: Optional[str] = None) -> str:
    out_dir = os.path.join(cfg.ACCOUNTS_DATA_DIR, "reports") if out_dir is None else out_dir
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"p2_{view['analysis']['id8']}.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(render_html(view))
    return path


def error_page(trade_id: str, exc: BaseException) -> str:
    detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    return ("<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\"><title>Vista P2</title>"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"></head>"
            "<body style=\"font:15px/1.5 system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 16px\">"
            f"<h1 style=\"font-size:1.3rem\">No se pudo armar la vista P2 de {html.escape(trade_id[:8])}</h1>"
            f"<pre style=\"white-space:pre-wrap\">{html.escape(detail)}</pre></body></html>")


def open_file(path: str) -> bool:
    """Abre el archivo con el navegador del sistema: en WSL con `wslview`, si no con `xdg-open` o `gio`."""
    abs_path = os.path.abspath(path)
    try:
        if platform.system() == "Windows":
            os.startfile(abs_path)  # type: ignore[attr-defined]
            return True
        if platform.system() == "Darwin":
            subprocess.Popen(["open", abs_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        for cmd in (["wslview"], ["xdg-open"], ["gio", "open"]):
            if shutil.which(cmd[0]):
                subprocess.Popen(cmd + [abs_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
    except Exception:
        return False
    return False


def open_p2_view(db_path: str, trade_id: str, out_dir: Optional[str] = None, open_it: bool = True, **kwargs) -> str:
    """Arma, guarda y abre la vista. Si algo falla, guarda y abre una página con el error: Ctrl+J nunca queda mudo."""
    out_dir = os.path.join(cfg.ACCOUNTS_DATA_DIR, "reports") if out_dir is None else out_dir
    try:
        path = write_view(build_view(db_path, trade_id, **kwargs), out_dir)
    except Exception as exc:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"p2_{trade_id[:8]}.html")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(error_page(trade_id, exc))
    if open_it:
        open_file(path)
    return path


def db_path_of(raw_conn) -> Optional[str]:
    """La ruta del archivo de una conexión sqlite3 abierta (la del detalle del análisis)."""
    try:
        for _, name, file in raw_conn.execute("PRAGMA database_list").fetchall():
            if name == "main" and file:
                return file
    except Exception:
        return None
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Arma y abre la vista P2 de un análisis.")
    parser.add_argument("--trade-id", required=True, help="id del análisis, completo o un prefijo único")
    parser.add_argument("--account", help="clave de cuenta (000, 001, 002, 003); sin ella se busca en todas")
    parser.add_argument("--no-open", action="store_true", help="solo guardar el HTML")
    args = parser.parse_args(argv)
    accounts = {args.account: cfg.REAL_ACCOUNTS[args.account]} if args.account else cfg.REAL_ACCOUNTS
    for db_name in accounts.values():
        db_path = os.path.join(cfg.ACCOUNTS_DATA_DIR, db_name)
        if not os.path.exists(db_path):
            continue
        try:
            find_analysis(db_path, args.trade_id)
        except LookupError:
            continue
        print(open_p2_view(db_path, args.trade_id, open_it=not args.no_open))
        return 0
    print(f"No se encontró un único análisis {args.trade_id!r}.", file=sys.stderr)
    return 1


TEMPLATE = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781; --grid: #e1e0d9;
  --axis: #c3c2b7; --border: rgba(11, 11, 11, 0.10); --candle-up: #ffffff; --candle-up-edge: #6f6e69;
  --candle-down: #8f8e88; --wick: #8f8e88; --ema20: #2a78d6; --ema100: #eb6834; --ema200: #1baf7a; --bar: #2a78d6;
  --future: rgba(11, 11, 11, 0.04); --chip: #f0efec;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781; --grid: #2c2c2a;
    --axis: #383835; --border: rgba(255, 255, 255, 0.10); --candle-up: #f4f3ef; --candle-up-edge: #f4f3ef;
    --candle-down: #6e6d68; --wick: #8a8984; --ema20: #3987e5; --ema100: #d95926; --ema200: #199e70; --bar: #3987e5;
    --future: rgba(255, 255, 255, 0.04); --chip: #262624;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781; --grid: #2c2c2a;
  --axis: #383835; --border: rgba(255, 255, 255, 0.10); --candle-up: #f4f3ef; --candle-up-edge: #f4f3ef;
  --candle-down: #6e6d68; --wick: #8a8984; --ema20: #3987e5; --ema100: #d95926; --ema200: #199e70; --bar: #3987e5;
  --future: rgba(255, 255, 255, 0.04); --chip: #262624;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { margin: 0; background: var(--page); color: var(--ink); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 1040px; margin: 0 auto; padding: 28px 16px 48px; }
.eyebrow { margin: 0 0 4px; font-size: 0.78rem; letter-spacing: 0.04em; text-transform: uppercase; color: var(--muted); }
h1 { margin: 0; font-size: 1.6rem; font-weight: 600; line-height: 1.25; }
h2 { margin: 0 0 4px; font-size: 1.15rem; font-weight: 600; }
.sub { margin: 0; color: var(--ink-2); }
.small { font-size: 0.85rem; color: var(--ink-2); }
header { margin-bottom: 24px; }
header .meta { margin-top: 8px; font-size: 0.88rem; color: var(--ink-2); }
section { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 20px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin: 16px 0 20px; }
.tile { border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.tile .label { display: block; font-size: 0.8rem; color: var(--ink-2); }
.tile .value { display: block; margin-top: 2px; font-size: 1.35rem; font-weight: 600; }
.tile .note { display: block; margin-top: 2px; font-size: 0.82rem; color: var(--ink-2); }
.callout { border-left: 3px solid var(--axis); padding: 8px 12px; margin-top: 16px; color: var(--ink-2); font-size: 0.9rem; }
.scale { display: grid; grid-template-columns: 80px minmax(0, 1fr); row-gap: 6px; align-items: center; }
.scale .name { font-size: 0.85rem; color: var(--ink-2); }
.track { position: relative; height: 26px; }
.track .line { position: absolute; left: 0; right: 0; top: 50%; height: 1px; background: var(--axis); }
.track .tick { position: absolute; top: 50%; width: 1px; height: 9px; margin-top: -4px; background: var(--axis); }
.track .dot { position: absolute; top: 50%; width: 12px; height: 12px; margin: -6px 0 0 -6px; border-radius: 50%; box-shadow: 0 0 0 2px var(--surface); }
.scale-labels { position: relative; height: 18px; font-size: 0.75rem; color: var(--muted); }
.scale-labels span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
.scale-labels span:first-child { transform: none; }
.scale-labels span:last-child { transform: translateX(-100%); }
.controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin: 14px 0 10px; }
.tabs { display: inline-flex; flex-wrap: wrap; gap: 4px; }
.tabs button { font: inherit; font-size: 0.85rem; padding: 4px 10px; border-radius: 8px; border: 1px solid var(--border); background: transparent; color: var(--ink-2); cursor: pointer; }
.tabs button[aria-pressed="true"] { background: var(--chip); color: var(--ink); font-weight: 600; }
.tabs button:focus-visible, .chart:focus-visible, summary:focus-visible, label:focus-within { outline: 2px solid var(--ema20); outline-offset: 2px; }
.toggle { font-size: 0.85rem; color: var(--ink-2); display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 0.8rem; color: var(--ink-2); margin-bottom: 6px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.key-up { width: 9px; height: 14px; background: var(--candle-up); border: 1px solid var(--candle-up-edge); border-radius: 1px; }
.key-down { width: 9px; height: 14px; background: var(--candle-down); border-radius: 1px; }
.key-line { width: 16px; height: 2px; border-radius: 1px; }
.chart-box { position: relative; }
.chart { display: block; width: 100%; height: 360px; touch-action: pan-y; }
.chart text { font: 11px system-ui, -apple-system, "Segoe UI", sans-serif; fill: var(--muted); font-variant-numeric: tabular-nums; }
.chart .elabel { fill: var(--ink-2); }
.tip { position: absolute; pointer-events: none; min-width: 150px; padding: 8px 10px; border-radius: 8px; background: var(--surface); border: 1px solid var(--border); box-shadow: 0 4px 16px rgba(0, 0, 0, 0.12); font-size: 0.8rem; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.tip .when { color: var(--muted); margin-bottom: 4px; }
.tip .big { color: var(--ink); font-weight: 600; font-size: 0.95rem; }
.tip .row { display: flex; align-items: center; gap: 6px; justify-content: space-between; }
.tip .row b { color: var(--ink); font-weight: 600; }
.tip .row i { display: inline-block; width: 12px; height: 2px; border-radius: 1px; margin-right: 4px; vertical-align: middle; }
.tbl { overflow-x: auto; margin-top: 12px; }
table { border-collapse: collapse; width: 100%; min-width: 720px; font-size: 0.88rem; }
th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--grid); vertical-align: top; }
th { font-weight: 500; color: var(--ink-2); font-size: 0.8rem; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
td .s { display: block; color: var(--muted); font-size: 0.78rem; font-variant-numeric: tabular-nums; }
tfoot td { border-bottom: 0; font-weight: 600; }
.contrib { display: flex; align-items: center; gap: 8px; justify-content: flex-end; }
.cbar { position: relative; width: 90px; height: 10px; }
.cbar .zero { position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px; background: var(--axis); }
.cbar .fill { position: absolute; top: 0; height: 10px; background: var(--bar); }
.cbar .fill.pos { left: 50%; border-radius: 0 4px 4px 0; }
.cbar .fill.neg { right: 50%; border-radius: 4px 0 0 4px; }
details { margin-top: 14px; }
summary { cursor: pointer; color: var(--ink-2); font-size: 0.88rem; }
.thesis { margin: 10px 0 0; padding: 12px 14px; border-radius: 10px; background: var(--chip); color: var(--ink); white-space: pre-wrap; }
.empty { color: var(--ink-2); font-size: 0.9rem; margin: 12px 0 0; }
footer { font-size: 0.8rem; color: var(--muted); }
footer p { margin: 4px 0; }
@media (max-width: 560px) { h1 { font-size: 1.3rem; } section { padding: 16px; } .chart { height: 300px; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <p class="eyebrow">Vista P2 y P0 · Ctrl+J desde el detalle del análisis</p>
    <h1 id="title"></h1>
    <p class="meta" id="meta"></p>
  </header>
  <section aria-labelledby="t-resumen">
    <h2 id="t-resumen">P2: la tendencia de fondo</h2>
    <p class="sub" id="resumen-frase"></p>
    <div class="tiles" id="tiles"></div>
    <div class="scale" id="scale" aria-label="Tu P2 y el P2 sistemático en la escala de −2 a +2"></div>
  </section>
  <section aria-labelledby="t-p0">
    <h2 id="t-p0">P0: el próximo movimiento</h2>
    <p class="sub" id="p0-frase"></p>
    <div class="tiles" id="p0-tiles"></div>
    <div class="tbl" id="p0-tbl-wrap"><table id="tbl-p0"></table></div>
    <p class="callout" id="p0-note"></p>
  </section>
  <section aria-labelledby="t-velas">
    <h2 id="t-velas">Las velas que vio el modelo</h2>
    <p class="sub small" id="velas-sub"></p>
    <div id="chart-area">
      <div class="controls">
        <div class="tabs" id="tabs" role="group" aria-label="Temporalidad"></div>
        <label class="toggle" id="after-label"><input type="checkbox" id="after"> Mostrar lo que pasó después</label>
      </div>
      <div class="legend">
        <span><i class="key-up"></i>Vela alcista</span>
        <span><i class="key-down"></i>Vela bajista</span>
        <span><i class="key-line" style="background: var(--ema20)"></i>EMA 20</span>
        <span><i class="key-line" style="background: var(--ema100)"></i>EMA 100</span>
        <span><i class="key-line" style="background: var(--ema200)"></i>EMA 200</span>
        <span><i class="key-line" style="background: var(--ema200); height: 0; border-top: 2px dashed var(--ema200)"></i>Precio de validación</span>
        <span><i class="key-line" style="background: var(--ema100); height: 0; border-top: 2px dashed var(--ema100)"></i>Precio de invalidación</span>
      </div>
      <div class="chart-box" id="chart-box">
        <svg class="chart" id="chart" tabindex="0" role="img"></svg>
        <div class="tip" id="tip" hidden></div>
      </div>
      <details>
        <summary>Ver las últimas velas en tabla</summary>
        <div class="tbl"><table id="tbl-velas"></table></div>
      </details>
    </div>
  </section>
  <div id="model-sections"></div>
  <section aria-labelledby="t-lectura">
    <h2 id="t-lectura">Bias final: manual contra sistemático</h2>
    <p class="sub small">El edge con tus cinco capas contra el edge con el P0 y el P2 sistemáticos (P1, P3 y P4 quedan como los cargaste), y qué habría pasado con cada uno.</p>
    <div class="tbl"><table id="tbl-cmp"></table></div>
    <p class="callout" id="cmp-note"></p>
    <details>
      <summary>Lo que escribiste en el P2</summary>
      <p class="thesis" id="thesis"></p>
    </details>
  </section>
  <footer>
    <p id="generated"></p>
    <p>El P0 sistemático aparece solo con el análisis resuelto, para no influir en lo que miden H1, H2 y H3.</p>
  </footer>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
(function () {
  "use strict";
  const D = JSON.parse(document.getElementById("data").textContent);
  const A = D.analysis, OP = D.operator, WORDS = D.scale_words;
  const fmt1 = new Intl.NumberFormat("es-GT", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const fmt0 = new Intl.NumberFormat("es-GT", { maximumFractionDigits: 0 });
  const fmt2s = x => (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(2);
  const signed = v => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v);
  const TZ = "America/Guatemala";
  const fmtDate = t => new Intl.DateTimeFormat("es-GT", { timeZone: TZ, day: "numeric", month: "short", hour: "2-digit",
    minute: "2-digit", hourCycle: "h23" }).format(new Date(t));
  const fmtDay = t => new Intl.DateTimeFormat("es-GT", { timeZone: TZ, day: "numeric", month: "short", year: "2-digit" }).format(new Date(t));
  const fmtLong = t => new Intl.DateTimeFormat("es-GT", { timeZone: TZ, weekday: "long", day: "numeric", month: "long",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(t));
  const fmtTime = t => new Intl.DateTimeFormat("es-GT", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(t));
  const word = v => WORDS[String(v)];
  function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; }
  function svg(tag, attrs) { const e = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; }

  /* Encabezado */
  document.getElementById("title").textContent = (A.asset || "") + " · " + fmtLong(A.anchor_ms);
  const thesisTxt = A.thesis_dir > 0 ? "tesis Long" : A.thesis_dir < 0 ? "tesis Short" : "sin niveles";
  document.getElementById("meta").textContent = "Análisis " + A.id8 + " · " + thesisTxt + (A.is_backdated ? " · retroactivo" : "") +
    " · " + D.result.title.toLowerCase() + (D.result.note ? " (" + D.result.note + ")" : "");
  document.getElementById("generated").textContent = "Generado por blast_master el " + D.generated_at + ". Tiene datos de tu journal: no se comparte ni se sube a git.";

  /* Resumen */
  const ok = D.models.filter(m => m.status === "ok");
  const opTxt = OP.direction ? (OP.direction === "Long" ? "Alcista" : OP.direction === "Short" ? "Bajista" : "Neutral") +
    (OP.strength ? " · " + ({ Strong: "fuerte", Mid: "media", Weak: "débil" }[OP.strength] || OP.strength) : "") : "Sin cargar";
  const frase = document.getElementById("resumen-frase");
  if (!ok.length) frase.textContent = "Ningún modelo sistemático da valor para este análisis.";
  else {
    const m = ok[0];
    frase.textContent = Math.sign(m.rescaled) === Math.sign(OP.value) && OP.direction
      ? "Tu P2 y el P2 sistemático (" + m.name + ") coinciden en la dirección: " + word(m.rescaled) + "."
      : "Tu P2 dice " + word(OP.value) + " y el P2 sistemático (" + m.name + ") dice " + word(m.rescaled) + ".";
  }
  const tiles = [["Tu P2", opTxt, OP.direction ? signed(OP.value) + " en la escala del edge" : "la capa P2 no está cargada"]];
  for (const m of D.models) {
    if (m.status === "ok") tiles.push(["P2 sistemático (" + m.name + ")", signed(m.rescaled) + " · " + word(m.rescaled),
      "suma de las temporalidades: " + fmt2s(m.raw) + " · " + m.source]);
    else tiles.push(["P2 sistemático (" + m.name + ")", "No opina", m.text]);
  }
  tiles.push(["Resultado del análisis", D.result.title, D.result.note]);
  const tileHost = document.getElementById("tiles");
  for (const t of tiles) { const d = el("div", "tile"); d.appendChild(el("span", "label", t[0])); d.appendChild(el("span", "value", t[1])); d.appendChild(el("span", "note", t[2])); tileHost.appendChild(d); }
  const pos = v => ((v + 2) / 4) * 100;
  const scale = document.getElementById("scale");
  function track(name, value, color) {
    scale.appendChild(el("span", "name", name));
    const host = el("div", "track"); host.appendChild(el("i", "line"));
    for (let v = -2; v <= 2; v++) { const k = el("i", "tick"); k.style.left = pos(v) + "%"; host.appendChild(k); }
    const dot = el("i", "dot"); dot.style.left = pos(value) + "%"; dot.style.background = color; dot.title = signed(value) + " · " + word(value);
    host.appendChild(dot); scale.appendChild(host);
  }
  if (OP.direction) track("Tu P2", OP.value, "var(--ink-2)");
  ok.forEach(m => track(m.name, m.rescaled, "var(--bar)"));
  scale.appendChild(el("span"));
  const sl = el("div", "scale-labels");
  [-2, -1, 0, 1, 2].forEach(v => { const s = el("span", null, signed(v)); s.style.left = pos(v) + "%"; sl.appendChild(s); });
  scale.appendChild(sl);

  /* Tablas de cada modelo (si dos dan lo mismo, una sola) */
  const hostModels = document.getElementById("model-sections");
  const groups = [];
  for (const m of ok) {
    const key = JSON.stringify([m.rescaled, m.rows]);
    const g = groups.find(x => x.key === key);
    if (g) g.names.push(m.name); else groups.push({ key: key, names: [m.name], m: m });
  }
  const biasWord = b => (b > 0 ? "Alcista" : b < 0 ? "Bajista" : "Sin sesgo");
  for (const g of groups) {
    const m = g.m, sec = el("section");
    sec.appendChild(el("h2", null, g.names.length > 1 ? "Cómo llegan " + g.names.join(" y ") + " a su valor (dan lo mismo)" : "Cómo llega " + m.name + " a su valor"));
    sec.appendChild(el("p", "sub small", "En cada temporalidad: las medias tienen que estar en orden (precio, EMA 20, EMA 100, EMA 200), los DI tienen que confirmar la dirección, y el ADX dice cuánta fuerza tiene. El aporte es peso × sesgo × fuerza."));
    const wrap = el("div", "tbl"), t = el("table"), head = el("thead"), hr = el("tr");
    [["Temporalidad"], ["Peso", 1], ["Medias"], ["DI"], ["ADX → fuerza", 1], ["Sesgo"], ["Aporte", 1]].forEach(h => hr.appendChild(el("th", h[1] ? "n" : null, h[0])));
    head.appendChild(hr);
    const body = el("tbody"), maxW = Math.max.apply(null, m.rows.map(r => r.weight));
    for (const r of m.rows) {
      const tr = el("tr");
      tr.appendChild(el("td", null, r.tf));
      tr.appendChild(el("td", "n", fmt0.format(r.weight * 100) + "%"));
      const med = el("td", null, r.ema_bias > 0 ? "En orden alcista" : r.ema_bias < 0 ? "En orden bajista" : r.ema_bias === 0 ? "Mezcladas" : "—");
      if (r.price != null) med.appendChild(el("span", "s", [r.price, r.ema20, r.ema100, r.ema200].map(v => fmt0.format(v)).join(" · ")));
      tr.appendChild(med);
      const diTxt = r.ema_bias === 0 ? "No se mira" : r.bias === 0 ? "No confirma" : "Confirma";
      const di = el("td", null, diTxt);
      if (r.plus_di != null) di.appendChild(el("span", "s", "+DI " + fmt1.format(r.plus_di) + " · −DI " + fmt1.format(r.minus_di)));
      tr.appendChild(di);
      tr.appendChild(el("td", "n", r.adx == null ? "—" : fmt1.format(r.adx) + " → ×" + r.force));
      tr.appendChild(el("td", null, biasWord(r.bias)));
      const c = el("td", "n"), cw = el("div", "contrib"), bar = el("div", "cbar");
      bar.appendChild(el("i", "zero"));
      if (r.contribution) { const f = el("i", "fill " + (r.contribution > 0 ? "pos" : "neg")); f.style.width = (Math.abs(r.contribution) / maxW) * 50 + "%"; bar.appendChild(f); }
      cw.appendChild(bar); cw.appendChild(el("span", null, r.contribution ? fmt2s(r.contribution) : "0")); c.appendChild(cw); tr.appendChild(c);
      body.appendChild(tr);
    }
    const foot = el("tfoot"), fr = el("tr");
    const ftd = el("td", null, "Suma " + fmt2s(m.raw) + " → × 2 = " + fmt2s(m.raw * 2) + " → se redondea a " + signed(m.rescaled) + " (" + word(m.rescaled) + ")");
    ftd.colSpan = 7; ftd.style.textAlign = "right"; fr.appendChild(ftd); foot.appendChild(fr);
    t.append(head, body, foot); wrap.appendChild(t); sec.appendChild(wrap);
    hostModels.appendChild(sec);
  }
  document.getElementById("thesis").textContent = OP.thesis || "Sin texto.";

  /* Bias final: manual contra sistemático */
  (function () {
    const C = D.comparison, t = document.getElementById("tbl-cmp"), note = document.getElementById("cmp-note");
    const cols = [C.manual].concat(C.systematic ? [C.systematic] : []);
    const hd = el("thead"), h = el("tr");
    h.appendChild(el("th", null, ""));
    cols.forEach(c => h.appendChild(el("th", null, c.label)));
    hd.appendChild(h);
    const bd = el("tbody");
    const row = (name, f) => { const tr = el("tr"); tr.appendChild(el("td", null, name)); cols.forEach(c => tr.appendChild(f(c))); bd.appendChild(tr); };
    row("Capas", c => el("td", null, c.note));
    row("Índice del edge", c => el("td", null, fmt2s(c.score)));
    row("Bias final", c => { const td = el("td"); td.appendChild(el("b", null, c.bias)); return td; });
    row("Resultado", c => { const td = el("td", null, c.verdict === "hit" ? "✓ " + c.text : c.verdict === "miss" ? "✗ " + c.text : c.text); return td; });
    t.replaceChildren(hd, bd);
    note.textContent = C.reason || "Acierta si el bias apunta al lado que tocó primero (objetivo o invalidación); Choppy no opina; si no tocó nada en 48 h, un bias direccional falla.";
  })();

  /* P0: el del operador siempre; el sistemático solo con el análisis resuelto */
  const P0 = D.p0, OP0 = D.operator_p0;
  const STR = { Strong: "fuerte", Mid: "media", Weak: "débil" };
  const dirTxt = (d, s) => d > 0 ? "Long" + (s ? " · " + (STR[s] || s) : "") : d < 0 ? "Short" + (s ? " · " + (STR[s] || s) : "") : "No opina";
  const op0Dir = OP0.direction === "Long" ? 1 : OP0.direction === "Short" ? -1 : 0;
  const p0Tiles = [["Tu P0", OP0.direction ? (op0Dir ? dirTxt(op0Dir, OP0.strength) : "Neutral") : "Sin cargar", OP0.direction ? "lo que cargaste" : "la capa P0 no está cargada"]];
  const p0Frase = document.getElementById("p0-frase"), p0Note = document.getElementById("p0-note");
  if (P0.hidden) {
    p0Frase.textContent = "El P0 sistemático se muestra cuando el análisis se resuelva: cuando toque un nivel o pasen 48 horas" +
      (P0.hours_left != null ? " (faltan " + Math.ceil(P0.hours_left) + " h)." : ".");
    p0Tiles.push(["P0 sistemático (O2-v1.0)", "Oculto por ahora", "se ve con el análisis resuelto"]);
    document.getElementById("p0-tbl-wrap").hidden = true;
    p0Note.textContent = "Así no influye en tus próximas lecturas mientras se miden H1, H2 y H3.";
  } else if (P0.status !== "ok") {
    p0Frase.textContent = "El P0 sistemático no se pudo calcular para este análisis.";
    p0Tiles.push(["P0 sistemático (" + P0.model + ")", "No disponible", P0.text]);
    document.getElementById("p0-tbl-wrap").hidden = true;
    p0Note.hidden = true;
  } else {
    p0Frase.textContent = P0.direction === 0 ? "El P0 sistemático no opina en este análisis."
      : op0Dir === P0.direction ? "Tu P0 y el P0 sistemático coinciden en la dirección: " + dirTxt(P0.direction) + "."
      : "Tu P0 y el P0 sistemático leen distinto.";
    p0Tiles.push(["P0 sistemático (" + P0.model + ")", dirTxt(P0.direction, P0.strength), P0.source]);
    const t = document.getElementById("tbl-p0"), hd = el("thead"), h = el("tr");
    [["Temporalidad"], ["Estructura"], ["Lo que pasó en el tramo"], ["Tramo A → B", 1], ["Puntaje", 1]].forEach(x => h.appendChild(el("th", x[1] ? "n" : null, x[0])));
    hd.appendChild(h);
    const bd = el("tbody");
    for (const r of P0.rows) {
      const tr = el("tr");
      tr.appendChild(el("td", null, r.tf));
      tr.appendChild(el("td", null, r.state));
      tr.appendChild(el("td", null, r.situation));
      tr.appendChild(el("td", "n", r.leg ? fmt1.format(r.leg[0]) + " → " + fmt1.format(r.leg[1]) : "—"));
      tr.appendChild(el("td", "n", r.score == null ? "—" : signed(r.score)));
      bd.appendChild(tr);
    }
    t.replaceChildren(hd, bd);
    p0Note.textContent = "Se combina de la temporalidad más chica a la más grande: manda la primera con dirección; si las mayores la contradicen más de lo que la apoyan, no opina. Fuerza: fuerte si todas las mayores coinciden, media si algunas, débil si ninguna.";
  }
  const p0Host = document.getElementById("p0-tiles");
  for (const t of p0Tiles) { const d = el("div", "tile"); d.appendChild(el("span", "label", t[0])); d.appendChild(el("span", "value", t[1])); d.appendChild(el("span", "note", t[2])); p0Host.appendChild(d); }

  /* Velas */
  const TF_ORDER = Object.keys(D.charts);
  const chartArea = document.getElementById("chart-area");
  document.getElementById("velas-sub").textContent = TF_ORDER.length
    ? "Solo velas cerradas a las " + fmtTime(A.anchor_ms) + ", con las tres medias que lee el modelo."
    : "No hay velas de " + (A.symbol || A.asset || "este activo") + " en el banco para la hora del análisis.";
  if (!TF_ORDER.length) { chartArea.hidden = true; return; }
  const chart = document.getElementById("chart"), tip = document.getElementById("tip"), box = document.getElementById("chart-box");
  const after = document.getElementById("after");
  let current = TF_ORDER.indexOf("1H") >= 0 ? TF_ORDER.indexOf("1H") : 0, view = null, hover = null;
  const tabs = document.getElementById("tabs");
  TF_ORDER.forEach((tf, i) => {
    const b = el("button", null, tf); b.type = "button"; b.setAttribute("aria-pressed", String(i === current));
    b.addEventListener("click", () => { current = i; hover = null; [...tabs.children].forEach((x, j) => x.setAttribute("aria-pressed", String(j === current))); draw(); });
    tabs.appendChild(b);
  });
  after.addEventListener("change", () => { hover = null; draw(); });
  function niceTicks(lo, hi, count) {
    const span = hi - lo, step0 = span / count, mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= step0);
    const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(v); return out;
  }
  function draw() {
    const c = D.charts[TF_ORDER[current]], nb = c.n_before, hasAfter = c.bars.length > nb;
    document.getElementById("after-label").hidden = !hasAfter;
    const showAfter = hasAfter && after.checked;
    const last = showAfter ? c.bars.length - 1 : nb - 1;
    const idx = []; for (let i = 0; i <= last; i++) idx.push(i);
    const W = Math.max(300, box.clientWidth), H = chart.clientHeight || 360;
    const m = { l: 52, r: 120, t: 16, b: 26 }, pw = W - m.l - m.r, ph = H - m.t - m.b;
    let lo = Infinity, hi = -Infinity;
    for (const i of idx) { const b = c.bars[i]; lo = Math.min(lo, b[3], c.ema20[i], c.ema100[i], c.ema200[i]); hi = Math.max(hi, b[2], c.ema20[i], c.ema100[i], c.ema200[i]); }
    const levels = [["Validación", A.evp, "var(--ema200)"], ["Invalidación", A.si, "var(--ema100)"]].filter(l => l[1] != null);
    for (const l of levels) { lo = Math.min(lo, l[1]); hi = Math.max(hi, l[1]); }
    const pad = (hi - lo) * 0.05 || 1; lo -= pad; hi += pad;
    const step = pw / idx.length, X = k => m.l + step * (k + 0.5), Y = v => m.t + (hi - v) / (hi - lo) * ph;
    view = { c: c, idx: idx, X: X, step: step, m: m, W: W };
    chart.setAttribute("viewBox", "0 0 " + W + " " + H);
    chart.setAttribute("aria-label", "Velas de " + TF_ORDER[current] + " hasta la hora del análisis, con EMA 20, 100 y 200.");
    const g = [];
    for (const v of niceTicks(lo, hi, 5)) {
      g.push(svg("line", { x1: m.l, x2: m.l + pw, y1: Y(v), y2: Y(v), stroke: "var(--grid)", "stroke-width": 1 }));
      const t = svg("text", { x: m.l - 8, y: Y(v) + 4, "text-anchor": "end" }); t.textContent = fmt0.format(v); g.push(t);
    }
    g.push(svg("line", { x1: m.l, x2: m.l + pw, y1: m.t + ph, y2: m.t + ph, stroke: "var(--axis)", "stroke-width": 1 }));
    const anchorX = m.l + step * nb;
    if (showAfter) {
      g.push(svg("rect", { x: anchorX, y: m.t, width: m.l + pw - anchorX, height: ph, fill: "var(--future)" }));
      const t = svg("text", { x: anchorX + 6, y: m.t + 12 }); t.textContent = "Después del análisis"; g.push(t);
    }
    g.push(svg("line", { x1: anchorX, x2: anchorX, y1: m.t, y2: m.t + ph, stroke: "var(--axis)", "stroke-width": 1 }));
    const at = svg("text", { x: anchorX - 6, y: m.t + 12, "text-anchor": "end" }); at.textContent = fmtTime(A.anchor_ms); g.push(at);
    const every = Math.ceil(idx.length / Math.max(2, Math.min(5, Math.floor(pw / 120))));
    idx.forEach((i, k) => {
      if (k % every !== 0) return;
      const t = svg("text", { x: k === 0 ? m.l : X(k), y: H - 8, "text-anchor": k === 0 ? "start" : "middle" });
      t.textContent = c.minutes >= 1440 ? fmtDay(c.bars[i][0]) : fmtDate(c.bars[i][0]); g.push(t);
    });
    const bw = Math.max(1, Math.min(10, step * 0.62));
    idx.forEach((i, k) => {
      const b = c.bars[i], x = X(k), upBar = b[4] >= b[1];
      const grp = svg("g", i >= nb ? { opacity: 0.45 } : {});
      grp.appendChild(svg("line", { x1: x, x2: x, y1: Y(b[2]), y2: Y(b[3]), stroke: "var(--wick)", "stroke-width": 1 }));
      const top = Y(Math.max(b[1], b[4])), h = Math.max(1, Math.abs(Y(b[1]) - Y(b[4])));
      grp.appendChild(svg("rect", { x: x - bw / 2, y: top, width: bw, height: h, rx: Math.min(1.5, bw / 4),
        fill: upBar ? "var(--candle-up)" : "var(--candle-down)", stroke: upBar ? "var(--candle-up-edge)" : "none", "stroke-width": upBar ? 1 : 0 }));
      g.push(grp);
    });
    const lines = [["ema20", "var(--ema20)", "EMA 20"], ["ema100", "var(--ema100)", "EMA 100"], ["ema200", "var(--ema200)", "EMA 200"]];
    const ends = [];
    for (const [key, color, label] of lines) {
      const d = idx.map((i, k) => (k ? "L" : "M") + X(k).toFixed(1) + " " + Y(c[key][i]).toFixed(1)).join(" ");
      g.push(svg("path", { d: d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      ends.push({ y: Y(c[key][idx[idx.length - 1]]), label: label });
    }
    ends.sort((a, b) => a.y - b.y);
    ends.forEach((e, k) => {
      const prev = ends[k - 1], next = ends[k + 1];
      if ((prev && e.y - prev.y < 13) || (next && next.y - e.y < 13)) return;
      const t = svg("text", { x: m.l + pw + 6, y: e.y + 4, class: "elabel" }); t.textContent = e.label; g.push(t);
    });
    for (const l of levels) {
      g.push(svg("line", { x1: m.l, x2: m.l + pw, y1: Y(l[1]), y2: Y(l[1]), stroke: l[2], "stroke-width": 1.5, "stroke-dasharray": "6 4" }));
      const t = svg("text", { x: m.l + pw + 6, y: Y(l[1]) + 4, class: "elabel" }); t.textContent = l[0] + " " + fmt0.format(l[1]); g.push(t);
    }
    g.push(svg("line", { id: "cross", x1: 0, x2: 0, y1: m.t, y2: m.t + ph, stroke: "var(--muted)", "stroke-width": 1, visibility: "hidden" }));
    chart.replaceChildren.apply(chart, g);
    renderTable(c);
    if (hover !== null) showHover(hover);
  }
  function showHover(k) {
    if (!view) return;
    k = Math.max(0, Math.min(view.idx.length - 1, k)); hover = k;
    const c = view.c, i = view.idx[k], b = c.bars[i], x = view.X(k);
    const cross = document.getElementById("cross");
    cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
    tip.replaceChildren();
    tip.appendChild(el("div", "when", fmtDate(b[0]) + (i >= c.n_before ? " · después del análisis" : "")));
    tip.appendChild(el("div", "big", fmt1.format(b[4]) + " cierre"));
    tip.appendChild(el("div", null, "Ap. " + fmt1.format(b[1]) + " · Máx. " + fmt1.format(b[2]) + " · Mín. " + fmt1.format(b[3])));
    [["ema20", "var(--ema20)", "EMA 20"], ["ema100", "var(--ema100)", "EMA 100"], ["ema200", "var(--ema200)", "EMA 200"]].forEach(l => {
      const row = el("div", "row"), name = el("span"), key = el("i");
      key.style.background = l[1]; name.appendChild(key); name.appendChild(document.createTextNode(l[2]));
      row.appendChild(name); row.appendChild(el("b", null, fmt1.format(c[l[0]][i]))); tip.appendChild(row);
    });
    tip.hidden = false;
    const left = x * (box.clientWidth / view.W), tw = tip.offsetWidth;
    tip.style.left = (left + 14 + tw > box.clientWidth ? left - tw - 14 : left + 14) + "px";
    tip.style.top = "12px";
  }
  function hideHover() { hover = null; tip.hidden = true; const cross = document.getElementById("cross"); if (cross) cross.setAttribute("visibility", "hidden"); }
  chart.addEventListener("pointermove", e => { if (!view) return; const r = chart.getBoundingClientRect(); showHover(Math.floor(((e.clientX - r.left) * (view.W / r.width) - view.m.l) / view.step)); });
  chart.addEventListener("pointerleave", hideHover);
  chart.addEventListener("focus", () => showHover(D.charts[TF_ORDER[current]].n_before - 1));
  chart.addEventListener("blur", hideHover);
  chart.addEventListener("keydown", e => {
    const start = hover === null ? D.charts[TF_ORDER[current]].n_before - 1 : hover;
    if (e.key === "ArrowLeft") { showHover(start - 1); e.preventDefault(); }
    if (e.key === "ArrowRight") { showHover(start + 1); e.preventDefault(); }
  });
  function renderTable(c) {
    const t = document.getElementById("tbl-velas"), hd = el("thead"), h = el("tr");
    [["Vela"], ["Apertura", 1], ["Máximo", 1], ["Mínimo", 1], ["Cierre", 1], ["EMA 20", 1], ["EMA 100", 1], ["EMA 200", 1]].forEach(x => h.appendChild(el("th", x[1] ? "n" : null, x[0])));
    hd.appendChild(h);
    const bd = el("tbody");
    for (let i = Math.max(0, c.n_before - 12); i < c.n_before; i++) {
      const b = c.bars[i], tr = el("tr");
      tr.appendChild(el("td", null, fmtDate(b[0])));
      [b[1], b[2], b[3], b[4], c.ema20[i], c.ema100[i], c.ema200[i]].forEach(v => tr.appendChild(el("td", "n", fmt1.format(v))));
      bd.appendChild(tr);
    }
    t.replaceChildren(hd, bd);
  }
  draw();
  if (window.ResizeObserver) new ResizeObserver(() => draw()).observe(box); else window.addEventListener("resize", draw);
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
