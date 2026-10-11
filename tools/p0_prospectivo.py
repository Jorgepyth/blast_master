"""
tools/p0_prospectivo.py — La medición prospectiva del P0 sistemático, opción 2 (`docs/p0-reglas-opcion2.md` §9 y sus
adendas).

Se escribió el 2026-10-11, antes de que la población tuviera un solo análisis. Fija de antemano cómo se calculan:
- los tres cortes de la decisión principal (63, 127 y 190 opiniones de O2-v1.0), contra el azar justo y contra B;
- H1 (66 casos) y H2 (29 casos): el P0 del operador cuando el sistema no opina, y cuando contradice su propia tesis;
- H3a y H3b (a las 190 opiniones): el edge del operador con el P0 sistemático, y con P0 y P2 sistemáticos.

Las decisiones de interpretación que el plan no detallaba están en la adenda "el script de la medición" del mismo doc.

Reglas:
- Solo lectura: las DBs en `mode=ro`, el banco y los dos registros (`p0_model_log.jsonl`, `p2_model_log.jsonl`) solo se
  leen. `--json` nunca escribe dentro del directorio de datos.
- Por defecto solo muestra los resultados de los cortes y de las hipótesis que ya llegaron a su número, con todos sus
  análisis resueltos. Las cifras acumuladas salen solo con `--descriptivo` (las miradas trimestrales, que no deciden).

Uso, desde la raíz del checkout principal:
    python tools/p0_prospectivo.py
    python tools/p0_prospectivo.py --descriptivo
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import numpy as np

import config.auto_resolution as cfg
from cli.main import determine_market_bias, get_dir_val, get_str_val
from config.auto_resolution import S4_WINDOW_H
from core.math_engine import calculate_edge_score
from core.stats_tests import mcnemar_exact, wilson_interval
from tools import p0_model_feedback as p0log
from tools import p0_opcion2 as o2
from tools import p0_sistematico as ps
from tools.candle_bank import bank_csv_path
from tools.p2_model_feedback import RETRY_STATUSES, STATUS_OK, latest_lines, read_log

# ---------------------------------------------------------------------------
# Lo que fija el plan (§9 y adendas). Cambiar un valor es una adenda nueva, nunca un arreglo.
# ---------------------------------------------------------------------------

POPULATION_START = datetime(2026, 10, 9, 17, 0)        # ancla posterior a esta hora (GT)
ACCOUNTS = {                                            # símbolo MT5 → (cuenta, DB)
    "XAUUSD": ("000", "flight_account_001_xauusd.db"),
    "BTCUSD": ("002", "flight_account_002_btcusdtp.db"),
    "US500": ("001", "flight_account_000_us500.db"),
}
MODEL = "O2-v1.0"
MODEL_HASH = "0265dd400d07"
P2_MODEL = "D600"
B_MODEL = "B"
CUTS: Tuple[Tuple[int, float], ...] = ((63, 0.0015), (127, 0.0182), (190, 0.0436))  # (opiniones, p unilateral <)
N_DRAWS = 100_000
DRAW_CHUNK = 10_000
SEED = 20261009
NO_SIRVE_MARGIN = 0.05                                  # "no sirve": Wilson superior < azar justo + 5 puntos
H1_N, H2_N, H_LIMIT = 66, 29, 0.50
H3_HOLM = (0.025, 0.05)

VERDICT_SIRVE = "sirve"
VERDICT_NO_SIRVE = "no sirve"
VERDICT_INSUFICIENTE = "insuficiente"
VERDICT_SIGUE = "sigue acumulando"


# ---------------------------------------------------------------------------
# Un análisis de la población, con todo lo que la medición necesita
# ---------------------------------------------------------------------------

@dataclass
class Case:
    trade_id: str
    symbol: str
    anchor: datetime
    start_price: float
    upper: float
    lower: float
    thesis: int                         # +1 si el objetivo (EVP) está arriba del precio de partida, −1 si abajo
    s4s: Optional[int]                  # S4 estricto: +1 tocó primero arriba en 48 h, −1 abajo, 0 nada en 48 h; None sin resolver
    s1: Optional[int] = None            # primer toque sin límite: +1/−1, None sin toque
    close_48h: Optional[float] = None   # cierre de la última vela 1H cerrada a las 48 h del ancla
    o2_dir: Optional[int] = None        # None: sin línea válida del modelo
    o2_strength: Optional[str] = None
    o2_source: str = "missing"          # "log", "recomputed" o "missing"
    o2_status: str = STATUS_OK          # sin modelo: el motivo; si se reintenta, el análisis todavía puede sumarse
    b_dir: Optional[int] = None
    op_p0: int = 0
    layers: Dict[str, Tuple[Optional[str], Optional[str]]] = field(default_factory=dict)  # P0..P4 → (dirección, fuerza)
    d600: Optional[float] = None        # p2_rescaled de D600 (−2 a 2)
    stored_dir: Optional[int] = None    # la dirección del edge guardado (calc_edge), para el chequeo de reconstrucción

    @property
    def resolved(self) -> bool:
        return self.s4s is not None

    @property
    def o2_pending(self) -> bool:
        """El modelo todavía no se pudo calcular, pero se va a poder (banco que no llega, reloj sin verificar)."""
        return self.o2_dir is None and self.o2_status in RETRY_STATUSES


def hit(direction: int, truth: Optional[int]) -> bool:
    """Gana la opinión cuyo lado se tocó primero dentro de las 48 h. Sin toque en 48 h (0) pierde cualquiera."""
    return direction != 0 and truth is not None and truth != 0 and direction == truth


def r_multiple(case: Case, direction: int) -> Optional[float]:
    """
    R de una decisión desde el precio de partida: se arriesga 1 hasta el nivel del lado contrario. Si toca primero su
    nivel en 48 h, gana la distancia a ese nivel dividida por el riesgo; si toca primero el contrario, −1; si en 48 h no
    toca ninguno, se cierra al cierre de la vela 1H de las 48 h.
    """
    if direction == 0 or case.s4s is None:
        return None
    sp = case.start_price
    target, stop = (case.upper, case.lower) if direction > 0 else (case.lower, case.upper)
    risk = abs(sp - stop)
    if risk <= 0:
        return None
    if case.s4s == direction:
        return abs(target - sp) / risk
    if case.s4s == -direction:
        return -1.0
    if case.close_48h is None:
        return None
    return direction * (case.close_48h - sp) / risk


# ---------------------------------------------------------------------------
# Estadística
# ---------------------------------------------------------------------------

def permutation_test(directions: Sequence[int], truths: Sequence[int], n_draws: int = N_DRAWS,
                     seed: int = SEED) -> Tuple[float, float]:
    """
    `(p unilateral, azar justo)`. Cada sorteo reparte al azar, entre los mismos análisis, la misma cantidad de Long y
    Short que dio el modelo. p = la fracción de sorteos que aciertan tanto o más que el modelo; el azar justo, el
    acierto promedio de los sorteos. Los sorteos van en bloques de `DRAW_CHUNK` con `default_rng(seed)`.
    """
    dirs = np.asarray(directions, dtype=np.int8)
    truth = np.asarray(truths, dtype=np.int8)
    if len(dirs) == 0:
        return float("nan"), float("nan")
    observed = int(((dirs == truth) & (truth != 0)).sum())
    rng = np.random.default_rng(seed)
    at_least, total_hits, done = 0, 0, 0
    while done < n_draws:
        size = min(DRAW_CHUNK, n_draws - done)
        perm = rng.permuted(np.tile(dirs, (size, 1)), axis=1)
        hits = ((perm == truth) & (truth != 0)).sum(axis=1)
        at_least += int((hits >= observed).sum())
        total_hits += int(hits.sum())
        done += size
    return at_least / n_draws, total_hits / (n_draws * len(dirs))


def sign_test_one_sided(better: int, worse: int) -> float:
    """P(al menos `better` de `better + worse` a favor) con una moneda: prueba de signos unilateral, sin empates."""
    n = better + worse
    if n == 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(better, n + 1)) / 2 ** n


def holm_two(p_a: float, p_b: float, alphas: Tuple[float, float] = H3_HOLM) -> Tuple[bool, bool]:
    """Holm sobre dos pruebas: la de p más chico contra 0.025; solo si pasa, la otra contra 0.05."""
    first_is_a = p_a <= p_b
    p_small, p_big = (p_a, p_b) if first_is_a else (p_b, p_a)
    small_ok = p_small < alphas[0]
    big_ok = small_ok and p_big < alphas[1]
    return (small_ok, big_ok) if first_is_a else (big_ok, small_ok)


def mean_r(cases: Sequence[Case], directions: Sequence[int]) -> Optional[float]:
    values = [r for r in (r_multiple(c, d) for c, d in zip(cases, directions)) if r is not None]
    return float(np.mean(values)) if values else None


def net(cases: Sequence[Case], directions: Sequence[int]) -> int:
    """Aciertos menos errores entre las opiniones; abstenerse vale 0."""
    return sum((1 if hit(d, c.s4s) else -1) for c, d in zip(cases, directions) if d != 0)


# ---------------------------------------------------------------------------
# Decisión principal: los tres cortes
# ---------------------------------------------------------------------------

def opinions(cases: Sequence[Case]) -> List[Case]:
    """Los análisis en que O2-v1.0 dio Long o Short, por orden de ancla."""
    return sorted((c for c in cases if c.o2_dir in (1, -1)), key=lambda c: c.anchor)


def first_n(selected: Sequence[Case], n: int, could_join: Sequence[Case] = ()) -> Tuple[Optional[List[Case]], str]:
    """
    Los primeros `n` (ya ordenados por ancla) y su estado: `ok`, `faltan N`, `sin resolver N` o `modelo sin calcular N`.
    `could_join` son los análisis que todavía pueden entrar a la selección (el modelo se reintenta): si alguno tiene
    ancla anterior al último elegido, el corte espera, para que el conjunto no cambie después de mirarlo.
    """
    if len(selected) < n:
        return None, f"faltan {n - len(selected)}"
    chosen = list(selected[:n])
    pending = sum(not c.resolved for c in chosen)
    if pending:
        return None, f"sin resolver {pending}"
    waiting = sum(c.anchor <= chosen[-1].anchor for c in could_join)
    if waiting:
        return None, f"modelo sin calcular {waiting}"
    return chosen, "ok"


def o2_waiting(cases: Sequence[Case]) -> List[Case]:
    return [c for c in cases if c.o2_pending]


def cut_verdict(p: float, alpha: float, net_model: int, net_b: int, wilson_hi: float, fair: float,
                last: bool) -> str:
    """"Sirve" en cualquier corte; "no sirve" e "insuficiente" solo en el último. En los otros, se sigue."""
    if p < alpha and net_model > net_b:
        return VERDICT_SIRVE
    if not last:
        return VERDICT_SIGUE
    if wilson_hi < fair + NO_SIRVE_MARGIN:
        return VERDICT_NO_SIRVE
    return VERDICT_INSUFICIENTE


def evaluate_cut(chosen: Sequence[Case], alpha: float, last: bool, n_draws: int = N_DRAWS) -> dict:
    dirs = [c.o2_dir for c in chosen]
    truths = [c.s4s for c in chosen]
    hits = sum(hit(d, t) for d, t in zip(dirs, truths))
    n = len(chosen)
    p, fair = permutation_test(dirs, truths, n_draws=n_draws)
    lo, hi = wilson_interval(hits, n)
    b_dirs = [c.b_dir or 0 for c in chosen]
    net_model, net_b = net(chosen, dirs), net(chosen, b_dirs)
    out = {
        "n": n, "hits": hits, "rate": hits / n, "wilson": [lo, hi], "fair_chance": fair, "p": p, "alpha": alpha,
        "net": net_model, "net_b": net_b, "b_opinions": sum(d != 0 for d in b_dirs), "r_mean": mean_r(chosen, dirs),
        "s1_hits": sum(hit(c.o2_dir, c.s1) for c in chosen), "s1_n": sum(c.s1 is not None for c in chosen),
        "verdict": cut_verdict(p, alpha, net_model, net_b, hi, fair, last),
    }
    for name, other in (("b", b_dirs), ("p0_operador", [c.op_p0 for c in chosen])):
        pairs = [(hit(d, c.s4s), hit(o, c.s4s)) for c, d, o in zip(chosen, dirs, other) if o != 0]
        only_model = sum(a and not b for a, b in pairs)
        only_other = sum(b and not a for a, b in pairs)
        out[f"mcnemar_{name}"] = {"pairs": len(pairs), "only_o2": only_model, f"only_{name}": only_other,
                                  "p": mcnemar_exact(only_model, only_other)}
    return out


# ---------------------------------------------------------------------------
# H1 y H2
# ---------------------------------------------------------------------------

def h1_cases(cases: Sequence[Case]) -> List[Case]:
    """El P0 del operador opina y el sistema no: O2-v1.0 tiene una línea válida con dirección 0."""
    return sorted((c for c in cases if c.op_p0 != 0 and c.o2_dir == 0), key=lambda c: c.anchor)


def h2_cases(cases: Sequence[Case]) -> List[Case]:
    """El P0 del operador opina en contra de la dirección de su tesis (el lado del EVP)."""
    return sorted((c for c in cases if c.op_p0 != 0 and c.op_p0 != c.thesis), key=lambda c: c.anchor)


def evaluate_h(chosen: Sequence[Case]) -> dict:
    hits = sum(hit(c.op_p0, c.s4s) for c in chosen)
    lo, hi = wilson_interval(hits, len(chosen))
    return {"n": len(chosen), "hits": hits, "rate": hits / len(chosen), "wilson": [lo, hi],
            "verdict": "confirmada" if hi < H_LIMIT else "no confirmada: la regla no se adopta"}


# ---------------------------------------------------------------------------
# H3a y H3b
# ---------------------------------------------------------------------------

def layer_value(direction: Optional[str], strength: Optional[str]) -> int:
    """Dirección × fuerza, como el wizard: Strong 2, Mid 1, el resto 0."""
    return get_dir_val(direction) * get_str_val(strength)


def edge_direction(case: Case, x0: Optional[float] = None, x2: Optional[float] = None) -> int:
    """La dirección del edge del operador (+1, −1 o 0 si queda Choppy), reemplazando x0 o x2 si se pasan."""
    xs = [layer_value(*case.layers.get(f"P{k}", (None, None))) for k in range(5)]
    if x0 is not None:
        xs[0] = x0
    if x2 is not None:
        xs[2] = x2
    bias = determine_market_bias(calculate_edge_score(*xs))
    return 1 if bias == "Bullish" else -1 if bias == "Bearish" else 0


def h3_directions(case: Case) -> Dict[str, int]:
    """Las tres versiones del edge: la real, H3a (P0 sistemático) y H3b (P0 y P2 sistemáticos; D600 vacío vale 0)."""
    x0 = case.o2_dir * get_str_val(case.o2_strength)
    return {"real": edge_direction(case),
            "h3a": edge_direction(case, x0=x0),
            "h3b": edge_direction(case, x0=x0, x2=case.d600 if case.d600 is not None else 0.0)}


def h3_span(cases: Sequence[Case], n_opinions: int = CUTS[-1][0]) -> Tuple[Optional[List[Case]], str]:
    """
    Los análisis de la población hasta el que trae la opinión número 190, inclusive, por orden de ancla. Los que no
    tienen una línea válida de O2-v1.0 quedan fuera (no se puede armar H3a ni H3b).
    """
    ops = opinions(cases)
    if len(ops) < n_opinions:
        return None, f"faltan {n_opinions - len(ops)} opiniones"
    limit = ops[n_opinions - 1].anchor
    waiting = sum(c.anchor <= limit for c in o2_waiting(cases))
    if waiting:
        return None, f"modelo sin calcular {waiting}"
    span = sorted((c for c in cases if c.anchor <= limit and c.o2_dir is not None), key=lambda c: c.anchor)
    pending = sum(not c.resolved for c in span)
    if pending:
        return None, f"sin resolver {pending}"
    return span, "ok"


def evaluate_h3(span: Sequence[Case], n_draws: int = N_DRAWS) -> dict:
    by_case = [h3_directions(c) for c in span]
    out = {"n_analyses": len(span)}
    scores = {}
    for key in ("real", "h3a", "h3b"):
        dirs = [d[key] for d in by_case]
        scores[key] = [0 if d == 0 else (1 if hit(d, c.s4s) else -1) for c, d in zip(span, dirs)]
        voting = [(c, d) for c, d in zip(span, dirs) if d != 0]
        hits = sum(hit(d, c.s4s) for c, d in voting)
        p, fair = permutation_test([d for _, d in voting], [c.s4s for c, _ in voting], n_draws=n_draws)
        out[key] = {"opinions": len(voting), "hits": hits, "rate": hits / len(voting) if voting else None,
                    "fair_chance": fair, "p_vs_fair_chance": p, "net": sum(scores[key]),
                    "r_mean": mean_r([c for c, _ in voting], [d for _, d in voting])}
    for key in ("h3a", "h3b"):
        better = sum(s > r for s, r in zip(scores[key], scores["real"]))
        worse = sum(s < r for s, r in zip(scores[key], scores["real"]))
        out[key].update({"better": better, "worse": worse, "p_sign": sign_test_one_sided(better, worse)})
    ok_a, ok_b = holm_two(out["h3a"]["p_sign"], out["h3b"]["p_sign"])
    out["h3a"]["verdict"] = "confirmada" if ok_a else "no confirmada: no se adopta"
    out["h3b"]["verdict"] = "confirmada" if ok_b else "no confirmada: no se adopta"
    return out


# ---------------------------------------------------------------------------
# Lectura de datos (solo lectura)
# ---------------------------------------------------------------------------

def valid_log_line(latest: Mapping[Tuple[str, str], object], trade_id: str, model: str,
                   model_hash: Optional[str] = None) -> Optional[dict]:
    """La línea vigente de (`trade_id`, `model`) si su estado es `ok` y, si se pide, si su huella es la congelada."""
    line = latest.get((trade_id, model))
    if line is None or line.data.get("status") != STATUS_OK:
        return None
    if model_hash is not None and line.data.get("model_hash") != model_hash:
        return None
    return line.data


def read_layers(db_path: str) -> Dict[str, Dict[str, Tuple[Optional[str], Optional[str]]]]:
    conn = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT trade_id, layer_name, direction, strength FROM analysis_layer").fetchall()
    finally:
        conn.close()
    out: Dict[str, Dict[str, Tuple[Optional[str], Optional[str]]]] = {}
    for trade_id, layer, direction, strength in rows:
        out.setdefault(trade_id, {})[layer] = (direction, strength)
    return out


def close_at(candles_1h: Sequence[ps.Candle], instant: datetime) -> Optional[float]:
    """El cierre de la última vela 1H cerrada en `instant`, o None si el banco todavía no llega."""
    times = [c.time + timedelta(hours=1) for c in candles_1h]
    k = bisect_right(times, instant) - 1
    if k < 0 or not times or times[-1] < instant:
        return None
    return candles_1h[k].close


# Sin S4 estricto todavía, pero se va a poder: el banco no llega a las 48 h o su reloj no se verificó. Cualquier otro
# motivo (una vela ambigua, sin niveles) no se resuelve nunca, y el análisis queda fuera, como en la evaluación.
TRUTH_WAITING = frozenset({cfg.REASON_PENDING_CANDLES, cfg.REASON_CLOCK_UNVERIFIED, cfg.REASON_CLOCK_MISALIGNED})


def load_cases(data_dir: str, bank_root: Optional[str] = None) -> Tuple[List[Case], Dict[str, dict]]:
    """Los análisis de la población con su verdad, sus modelos y las capas del operador; y conteos por símbolo."""
    bank_root = os.path.join(data_dir, "candle_bank") if bank_root is None else bank_root
    latest_p0 = latest_lines(read_log(os.path.join(data_dir, cfg.P0_MODEL_LOG_NAME)))
    latest_p2 = latest_lines(read_log(os.path.join(data_dir, cfg.P2_MODEL_LOG_NAME)))
    params = o2.MODELS_BY_NAME[MODEL]
    cases: List[Case] = []
    counts: Dict[str, dict] = {}
    for symbol, (_, db_name) in ACCOUNTS.items():
        db_path = os.path.join(data_dir, db_name)
        bank_dir = os.path.join(bank_root, symbol)
        if not os.path.exists(db_path) or not os.path.isdir(bank_dir):
            counts[symbol] = {"skipped": "sin DB o sin banco"}
            continue
        candles = {tf: ps.read_candles(bank_csv_path(bank_dir, tf)) for tf in ps.ALL_TFS
                   if os.path.exists(bank_csv_path(bank_dir, tf))}
        _, population, _, _ = ps.load_analyses(db_path, symbol, candles, POPULATION_START)
        counts[symbol] = {"population": 0, "truth_excluded": {}}
        if not population:
            continue
        ladder = {tf: candles[tf] for tf in ps.LADDER if candles.get(tf)}
        truths = {item.trade_id: ps.geometric_truth_at(item, ladder) for item in population}
        vectors = ps.truth_vectors(population, truths)
        layers = read_layers(db_path)
        market = ps.Market(symbol, candles)  # B con el código congelado de la evaluación (predictions_for)
        b_x0, _ = ps.predictions_for(population, market, [ps.SPECS_BY_NAME[B_MODEL]])
        for k, item in enumerate(population):
            code = truths[item.trade_id].code
            if not vectors["s4s_mask"][k] and code not in TRUTH_WAITING:
                excluded = counts[symbol]["truth_excluded"]
                excluded[code] = excluded.get(code, 0) + 1
                continue
            s4s = int(vectors["s4s"][k]) if vectors["s4s_mask"][k] else None
            s1 = int(vectors["s1"][k]) if vectors["s1_mask"][k] else None
            stored = None if item.bias is None else 1 if item.bias == "Bullish" else -1 if item.bias == "Bearish" else 0
            case = Case(item.trade_id, symbol, item.anchor, item.start_price, item.upper, item.lower,
                        1 if item.evp > item.start_price else -1, s4s, s1,
                        close_at(candles.get("1H", []), item.anchor + timedelta(hours=S4_WINDOW_H)),
                        b_dir=int(np.sign(b_x0[B_MODEL][k])), op_p0=item.operator_dir,
                        layers=layers.get(item.trade_id, {}), stored_dir=stored)
            line = valid_log_line(latest_p0, item.trade_id, MODEL, MODEL_HASH)
            if line is not None:
                case.o2_dir, case.o2_strength, case.o2_source = int(line["direction"]), line["strength"], "log"
            else:
                status, direction, strength, _ = p0log.model_p0(params, symbol, item.anchor, bank_root)
                case.o2_status = status
                if status == STATUS_OK:
                    case.o2_dir, case.o2_strength, case.o2_source = direction, strength, "recomputed"
            p2 = valid_log_line(latest_p2, item.trade_id, P2_MODEL)
            case.d600 = None if p2 is None or p2.get("p2_rescaled") is None else float(p2["p2_rescaled"])
            cases.append(case)
            counts[symbol]["population"] += 1
    return cases, counts


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------

def build_report(cases: Sequence[Case], counts: Mapping[str, dict], descriptive: bool = False,
                 n_draws: int = N_DRAWS) -> dict:
    ops = opinions(cases)
    waiting = o2_waiting(cases)
    report = {
        "population": {"total": len(cases), "by_symbol": dict(counts),
                       "o2_source": {s: sum(c.o2_source == s for c in cases) for s in ("log", "recomputed", "missing")},
                       "o2_pending": len(waiting),
                       "o2_opinions": len(ops), "resolved_opinions": sum(c.resolved for c in ops),
                       "edge_mismatches": sum(c.stored_dir is not None and c.stored_dir != edge_direction(c)
                                              for c in cases)},
        "cuts": [],
    }
    for k, (n, alpha) in enumerate(CUTS):
        chosen, status = first_n(ops, n, could_join=waiting)
        entry = {"cut": k + 1, "opinions": n, "alpha": alpha, "status": status}
        if chosen is not None:
            entry["result"] = evaluate_cut(chosen, alpha, last=k == len(CUTS) - 1, n_draws=n_draws)
        report["cuts"].append(entry)
        if chosen is not None and entry["result"]["verdict"] in (VERDICT_SIRVE, VERDICT_NO_SIRVE, VERDICT_INSUFICIENTE):
            break  # la decisión principal ya se tomó; los cortes siguientes no se miran
    selectors: Tuple[Tuple[str, Callable[[Sequence[Case]], List[Case]], int, Sequence[Case]], ...] = (
        ("h1", h1_cases, H1_N, waiting), ("h2", h2_cases, H2_N, ()))
    for name, selector, n, could_join in selectors:
        chosen, status = first_n(selector(cases), n, could_join=[c for c in could_join if c.op_p0 != 0])
        report[name] = {"cases": n, "status": status}
        if chosen is not None:
            report[name]["result"] = evaluate_h(chosen)
    span, status = h3_span(cases)
    report["h3"] = {"status": status}
    if span is not None:
        report["h3"]["result"] = evaluate_h3(span, n_draws=n_draws)
    if descriptive:
        report["descriptive"] = describe(cases)
    return report


def describe(cases: Sequence[Case]) -> dict:
    """Cifras acumuladas de todos los análisis resueltos, para las miradas trimestrales. No deciden nada."""
    done = [c for c in cases if c.resolved]
    methods = {
        "o2": lambda c: c.o2_dir or 0, "b": lambda c: c.b_dir or 0, "p0_operador": lambda c: c.op_p0,
        "tesis": lambda c: c.thesis, "edge_real": lambda c: edge_direction(c),
        "edge_h3a": lambda c: h3_directions(c)["h3a"] if c.o2_dir is not None else 0,
        "edge_h3b": lambda c: h3_directions(c)["h3b"] if c.o2_dir is not None else 0,
    }
    out = {}
    for symbol in [None] + sorted({c.symbol for c in done}):
        group = [c for c in done if symbol is None or c.symbol == symbol]
        rows = {}
        for name, fn in methods.items():
            voting = [(c, fn(c)) for c in group if fn(c) != 0]
            hits = sum(hit(d, c.s4s) for c, d in voting)
            rows[name] = {"opinions": len(voting), "hits": hits,
                          "rate": hits / len(voting) if voting else None,
                          "net": net([c for c, _ in voting], [d for _, d in voting]),
                          "r_mean": mean_r([c for c, _ in voting], [d for _, d in voting])}
        out[symbol or "todos"] = {"resolved": len(group), "methods": rows}
    return out


def _pct(x: Optional[float]) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.1%}"


def format_report(report: dict) -> str:
    pop = report["population"]
    lines = ["Medición prospectiva del P0 sistemático (opción 2, O2-v1.0)",
             f"Población: {pop['total']} análisis con ancla posterior a {POPULATION_START:%Y-%m-%d %H:%M} GT",
             "  " + ", ".join(f"{s}: {c.get('population', c.get('skipped'))}"
                              + (f" (fuera por la verdad: {c['truth_excluded']})" if c.get("truth_excluded") else "")
                              for s, c in pop["by_symbol"].items()),
             f"Opiniones de O2-v1.0: {pop['o2_opinions']} ({pop['resolved_opinions']} resueltas). Línea del modelo: "
             f"{pop['o2_source']['log']} del registro, {pop['o2_source']['recomputed']} recalculadas, "
             f"{pop['o2_source']['missing']} sin modelo ({pop['o2_pending']} se reintentan).",
             f"Chequeo: el edge recalculado difiere del guardado en {pop['edge_mismatches']} análisis.",
             "", "Decisión principal (cortes)"]
    for cut in report["cuts"]:
        head = f"  Corte {cut['cut']} ({cut['opinions']} opiniones, p < {cut['alpha']}): "
        if "result" not in cut:
            lines.append(head + cut["status"])
            continue
        r = cut["result"]
        lines.append(head + r["verdict"].upper())
        lines.append(f"    acierto {r['hits']}/{r['n']} = {_pct(r['rate'])} (IC 95% {_pct(r['wilson'][0])} a "
                     f"{_pct(r['wilson'][1])}); azar justo {_pct(r['fair_chance'])}; p = {r['p']:.4f}")
        lines.append(f"    neto {r['net']:+d} contra {r['net_b']:+d} de B ({r['b_opinions']} opiniones de B); "
                     f"R por decisión {r['r_mean']:+.2f}" if r["r_mean"] is not None else
                     f"    neto {r['net']:+d} contra {r['net_b']:+d} de B")
        for name in ("b", "p0_operador"):
            m = r[f"mcnemar_{name}"]
            lines.append(f"    McNemar contra {name}: {m['pairs']} pares, solo O2 {m['only_o2']}, "
                         f"solo {name} {m[f'only_{name}']}, p = {m['p']:.3f}")
        lines.append(f"    S1 (sin límite de tiempo): {r['s1_hits']}/{r['s1_n']}")
    lines.append("")
    for name, label in (("h1", "H1: tu P0 cuando el sistema no opina"), ("h2", "H2: tu P0 contra tu tesis")):
        h = report[name]
        if "result" not in h:
            lines.append(f"{label} ({h['cases']} casos): {h['status']}")
        else:
            r = h["result"]
            lines.append(f"{label}: {r['verdict'].upper()} — {r['hits']}/{r['n']} = {_pct(r['rate'])}, "
                         f"IC 95% hasta {_pct(r['wilson'][1])} (hace falta < 50%)")
    h3 = report["h3"]
    if "result" not in h3:
        lines.append(f"H3a y H3b (a las {CUTS[-1][0]} opiniones): {h3['status']}")
    else:
        r = h3["result"]
        lines.append(f"H3 sobre {r['n_analyses']} análisis; edge real {r['real']['hits']}/{r['real']['opinions']}, "
                     f"neto {r['real']['net']:+d}")
        for key in ("h3a", "h3b"):
            x = r[key]
            lines.append(f"  {key.upper()}: {x['verdict'].upper()} — mejor en {x['better']}, peor en {x['worse']}, "
                         f"p = {x['p_sign']:.4f}; acierto {x['hits']}/{x['opinions']}, neto {x['net']:+d}")
    if "descriptive" in report:
        lines += ["", "Cifras acumuladas (descriptivas: no deciden nada)"]
        for group, d in report["descriptive"].items():
            lines.append(f"  {group} ({d['resolved']} resueltos)")
            for name, m in d["methods"].items():
                r_txt = "—" if m["r_mean"] is None else f"{m['r_mean']:+.2f} R"
                lines.append(f"    {name:12s} {m['hits']:>3}/{m['opinions']:<3} {_pct(m['rate']):>6}  "
                             f"neto {m['net']:+d}  {r_txt}")
    return "\n".join(lines)


def _default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", default=cfg.ACCOUNTS_DATA_DIR)
    parser.add_argument("--descriptivo", action="store_true", help="también las cifras acumuladas (no deciden)")
    parser.add_argument("--json", help="guardar el informe en este archivo (fuera del directorio de datos)")
    args = parser.parse_args(argv)
    data_dir = os.path.abspath(args.data_dir)
    if args.json and os.path.abspath(args.json).startswith(data_dir + os.sep):
        parser.error("--json no puede escribir dentro del directorio de datos")
    cases, counts = load_cases(data_dir)
    report = build_report(cases, counts, descriptive=args.descriptivo)
    print(format_report(report))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, default=_default)
    return 0


if __name__ == "__main__":
    sys.exit(main())
