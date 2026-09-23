"""
tools/edge_evaluation.py — ¿el edge completo (P0-P4) aporta información real?

Responde tres preguntas pre-registradas (ver
~/.claude/plans/crea-un-plan-de-frolicking-kahn.md), con las reglas de
decisión fijadas ANTES de mirar resultados:

  1a. ¿El edge acierta la dirección más que el azar?          binomial exacto
  1b. ¿Le gana a "seguir la tendencia 1D" (point-in-time)?     McNemar exacto
  1c. ¿Acierta más cuanto mayor es |calc_edge|?               permutación
   2. ¿Hay pesos P0-P4 mejores que los actuales, FUERA de muestra?
   3. ¿El edge se traduce en R positivo?                      bootstrap

1a/1b/1c se corrigen juntas por Holm. Todo lo demás es exploratorio.

Por qué así (medido el 2026-09-22 sobre XAUUSD, reloj de velas corregido):
- "Siempre short" parece un rival fuerte pero sabe con retrospectiva que el
  período fue bajista: se reporta solo como referencia, nunca como test.
- El baseline justo (tendencia 1D en el anchor) acierta ~50%.
- Con decenas de apuestas casi nada es significativo: el reporte abre con
  potencia y proyección para decir, antes de los resultados, qué preguntas
  pueden responderse con la muestra actual.

Dos ground truths, que discrepan en ~30% de los trades ejecutados y no es un
bug: miden cosas distintas.
- GT-tesis (core/p2_ground_truth.py): ¿qué nivel ESTRUCTURAL tocó primero
  el precio? -> preguntas 1 y 2.
- GT-trade (signo de r_multiple): ¿el trade real ganó? -> pregunta 3.

Solo lectura: cada DB se abre con mode=ro (open_readonly_session). Aborta si
el chequeo de reloj de las velas falla en alguna cuenta
(calibrate_clock_offset), salvo --allow-clock-misalignment.
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import numpy as np
import pandas as pd
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from core.analytics_engine import filter_executed_trades, segment_kpis
from core.backtest_engine import evaluate_real_r_by_filter, run_icd_backtest
from core.stats_tests import (
    bootstrap_mean_ci,
    binomial_test_two_sided,
    holm_adjust,
    mcnemar_exact,
    min_detectable_gap,
    n_required_one_sample,
    permutation_corr_test,
    wilson_interval,
)
from tools.database import TacticalAudit
from tools.p2_backtest import (
    DEFAULT_DB_PATH,
    OHLC_DIR_ENV_VAR,
    ClockCalibration,
    ExclusionRecord,
    assemble_p2_systematic_rows,
    bias_to_direction,
    calibrate_clock_offset,
    load_ohlc_provider_from_csv,
    open_readonly_session,
)

LAYERS: Tuple[str, ...] = ("P0", "P1", "P2", "P3", "P4")
# Pesos de producción de core/math_engine.calculate_edge_score. Un test
# verifica que coincidan, para que un cambio allá no desincronice esto.
CURRENT_WEIGHTS: Tuple[float, ...] = (0.30, 0.25, 0.15, 0.10, 0.20)
EQUAL_WEIGHTS: Tuple[float, ...] = (0.20, 0.20, 0.20, 0.20, 0.20)
P2_ONLY_WEIGHTS: Tuple[float, ...] = (0.0, 0.0, 1.0, 0.0, 0.0)
# Umbral de determine_market_bias (cli/main.py): |edge| >= 0.26 es direccional.
BIAS_THRESHOLD = 0.26

ALPHA = 0.05
MOMENTUM_BARS = 20
WEIGHT_GRID_STEP = 0.05
WF_BLOCK = 10
OOS_MIN_IMPROVEMENT = 0.05
DEFAULT_SEED = 20260922
N_RESAMPLES = 10_000


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------

@dataclass
class EvalRow:
    account: str
    asset: Optional[str]
    trade_id: str
    anchor: datetime
    anchor_source: str               # "tactical_audit" | "mark_price"
    ref_price: float
    calc_edge: float                 # el persistido
    edge_bias: str                   # determine_market_bias(calc_edge)
    edge_dir: Optional[str]          # long / short / None (abstención)
    scores: Dict[str, int]
    thesis_dir: Optional[str]
    gt_thesis: Optional[str]
    gt_incompleto: bool
    baselines: Dict[str, Optional[str]] = field(default_factory=dict)
    order_filled: Optional[bool] = None
    r_multiple: Optional[float] = None
    trade_dir: Optional[str] = None  # signo de entry_price - stop_loss
    stop_slippage_r: Optional[float] = None

    @property
    def resolved(self) -> bool:
        return not self.gt_incompleto and self.gt_thesis is not None

    @property
    def edge_hit(self) -> Optional[bool]:
        if not self.resolved or self.edge_dir is None:
            return None
        return self.edge_dir == self.gt_thesis


@dataclass
class AccountData:
    label: str
    db_path: str
    ohlc_dir: Optional[str]
    rows: List[EvalRow] = field(default_factory=list)
    exclusions: List[ExclusionRecord] = field(default_factory=list)
    clock: Optional[ClockCalibration] = None
    skipped: Optional[str] = None


def point_in_time_baselines(provider: object, as_of: datetime) -> Dict[str, Optional[str]]:
    """
    Reglas ingenuas calculadas SOLO con velas anteriores al anchor (el
    provider aplica el candado estricto time < as_of):
      B1: precio vs EMA200 en 1D   (primario, pregunta 1b)
      B2: precio vs EMA200 en 4H
      B3: signo del retorno de las últimas MOMENTUM_BARS velas de 4H
    """
    out: Dict[str, Optional[str]] = {}
    for name, tf in (("B1", "1D"), ("B2", "4H")):
        snap = provider.get_indicator_snapshot(tf, as_of)
        if snap is None or snap.bars_available < 200 or snap.price == snap.ema200:
            out[name] = None
        else:
            out[name] = "long" if snap.price > snap.ema200 else "short"
    closes = provider.get_past_closes("4H", as_of, MOMENTUM_BARS + 1)
    if len(closes) < MOMENTUM_BARS + 1 or closes[-1] == closes[0]:
        out["B3"] = None
    else:
        out["B3"] = "long" if closes[-1] > closes[0] else "short"
    return out


def _tactical_columns(session: Session) -> set:
    return {c["name"] for c in inspect(session.get_bind()).get_columns("tactical_audit")}


def _anchor_execution(session: Session, trade_id: str, available: set):
    """
    La misma ejecución que usa resolve_anchor: la de entry_time más temprano.
    Pide columnas explícitas, y stop_slippage_r solo si existe en esta DB:
    las cuentas que el CLI no abrió desde el 2026-09-16 no la tienen, y acá
    no se migra nada (solo lectura).
    """
    cols = [TacticalAudit.order_filled, TacticalAudit.r_multiple,
            TacticalAudit.entry_price, TacticalAudit.stop_loss]
    if "stop_slippage_r" in available:
        cols.append(TacticalAudit.stop_slippage_r)
    return session.execute(
        select(*cols)
        .where(TacticalAudit.trade_id == trade_id, TacticalAudit.entry_time.isnot(None))
        .order_by(TacticalAudit.entry_time.asc())
        .limit(1)
    ).first()


def build_account_rows(
    session: Session,
    provider: object,
    account: str,
    include_no_execution: bool = True,
) -> Tuple[List[EvalRow], List[ExclusionRecord]]:
    p2_rows, exclusions = assemble_p2_systematic_rows(
        session, provider, include_no_execution=include_no_execution
    )
    available = _tactical_columns(session)
    rows: List[EvalRow] = []
    for r in p2_rows:
        row = EvalRow(
            account=account,
            asset=r.asset,
            trade_id=r.trade_id,
            anchor=r.timestamp_entry,
            anchor_source=r.timestamp_source,
            ref_price=r.entry_price,
            calc_edge=r.calc_edge_original,
            edge_bias=r.bias_predicho_original,
            edge_dir=bias_to_direction(r.bias_predicho_original),
            scores=dict(r.layer_scores),
            thesis_dir=r.thesis_direction,
            gt_thesis=r.ground_truth_direction,
            gt_incompleto=r.ground_truth_incompleto,
            baselines=point_in_time_baselines(provider, r.timestamp_entry),
        )
        if r.timestamp_source == "tactical_audit":
            ex = _anchor_execution(session, r.trade_id, available)
            if ex is not None:
                row.order_filled = ex.order_filled
                row.r_multiple = float(ex.r_multiple) if ex.r_multiple is not None else None
                slip = getattr(ex, "stop_slippage_r", None)
                row.stop_slippage_r = float(slip) if slip is not None else None
                if ex.entry_price is not None and ex.stop_loss is not None and ex.entry_price != ex.stop_loss:
                    row.trade_dir = "long" if ex.entry_price > ex.stop_loss else "short"
        rows.append(row)
    return rows, exclusions


# ---------------------------------------------------------------------------
# Pregunta 1
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    key: str
    question: str
    statistic: str
    p_value: Optional[float]
    direction_ok: bool
    p_holm: Optional[float] = None

    @property
    def signal(self) -> Optional[bool]:
        if self.p_value is None:
            return None
        p = self.p_holm if self.p_holm is not None else self.p_value
        return p < ALPHA and self.direction_ok


def edge_bets(rows: Sequence[EvalRow]) -> List[EvalRow]:
    return [r for r in rows if r.edge_hit is not None]


def test_1a_vs_chance(rows: Sequence[EvalRow]) -> TestResult:
    bets = edge_bets(rows)
    hits = sum(1 for r in bets if r.edge_hit)
    n = len(bets)
    p = binomial_test_two_sided(hits, n, 0.5) if n else None
    acc = hits / n if n else float("nan")
    return TestResult("1a", "¿Acierta más que el azar?",
                      f"{hits}/{n} = {acc * 100:.1f}%" if n else "sin apuestas",
                      p, n > 0 and acc > 0.5)


def discordant_pairs(rows: Sequence[EvalRow], baseline: str) -> Tuple[int, int, int]:
    """(solo acierta el edge, solo acierta el baseline, pares comparables)."""
    pairs = [r for r in edge_bets(rows) if r.baselines.get(baseline)]
    b = sum(1 for r in pairs if r.edge_hit and r.baselines[baseline] != r.gt_thesis)
    c = sum(1 for r in pairs if not r.edge_hit and r.baselines[baseline] == r.gt_thesis)
    return b, c, len(pairs)


def test_1b_vs_trend(rows: Sequence[EvalRow]) -> TestResult:
    b, c, n = discordant_pairs(rows, "B1")
    p = mcnemar_exact(b, c) if n else None
    return TestResult("1b", "¿Le gana a seguir la tendencia 1D?",
                      f"discordantes: edge {b} vs tendencia {c} (de {n} pares)",
                      p, b > c)


def test_1c_dose_response(rows: Sequence[EvalRow], seed: int, n_perm: int = N_RESAMPLES) -> TestResult:
    bets = edge_bets(rows)
    if len(bets) < 3:
        return TestResult("1c", "¿Acierta más con edge más fuerte?", "muestra insuficiente", None, False)
    x = [abs(r.calc_edge) for r in bets]
    y = [1.0 if r.edge_hit else 0.0 for r in bets]
    corr, p = permutation_corr_test(x, y, n_perm=n_perm, seed=seed)
    if math.isnan(corr):
        return TestResult("1c", "¿Acierta más con edge más fuerte?", "sin variación", None, False)
    return TestResult("1c", "¿Acierta más con edge más fuerte?",
                      f"r = {corr:+.3f} entre magnitud del edge y acierto (n={len(bets)})",
                      p, corr > 0)


def run_question_1(rows: Sequence[EvalRow], seed: int) -> List[TestResult]:
    tests = [test_1a_vs_chance(rows), test_1b_vs_trend(rows), test_1c_dose_response(rows, seed)]
    valid = [t for t in tests if t.p_value is not None]
    for t, adj in zip(valid, holm_adjust([t.p_value for t in valid])):
        t.p_holm = adj
    return tests


def hindsight_majority(rows: Sequence[EvalRow]) -> Optional[str]:
    c = Counter(r.gt_thesis for r in rows if r.resolved)
    return c.most_common(1)[0][0] if c else None


# ---------------------------------------------------------------------------
# Pregunta 2 — pesos fuera de muestra
# ---------------------------------------------------------------------------

def simplex_grid(step: float = WEIGHT_GRID_STEP, k: int = len(LAYERS)) -> np.ndarray:
    """Todas las combinaciones de k pesos >= 0, múltiplos de step, que suman 1."""
    units = round(1 / step)

    def compositions(total: int, parts: int):
        if parts == 1:
            yield (total,)
            return
        for first in range(total + 1):
            for rest in compositions(total - first, parts - 1):
                yield (first,) + rest

    return np.array(list(compositions(units, k)), dtype=float) / units


def grid_index(grid: np.ndarray, weights: Sequence[float]) -> int:
    hits = np.where(np.all(np.isclose(grid, np.asarray(weights)), axis=1))[0]
    if hits.size == 0:
        raise ValueError(f"{weights} no está en la grilla")
    return int(hits[0])


def weight_contributions(X: np.ndarray, y: np.ndarray, grid: np.ndarray,
                         threshold: float = BIAS_THRESHOLD) -> np.ndarray:
    """
    Matriz (trades x sets de pesos): +1 acierto, -1 fallo, 0 abstención.
    El edge se recalcula con la fórmula de calculate_edge_score
    (suma ponderada / 2) para cada set de pesos.
    """
    edges = X @ grid.T / 2.0
    direction = np.where(np.abs(edges) >= threshold - 1e-12, np.sign(edges), 0.0)
    return (direction * y[:, None]).astype(np.int8)


def select_best(train_net: np.ndarray, grid: np.ndarray, prior: Sequence[float]) -> int:
    """
    Máximo de aciertos-fallos en entrenamiento. Empates (muy frecuentes con
    decenas de trades) se resuelven hacia los pesos actuales: ante evidencia
    equivalente, no moverse.
    """
    dist = np.abs(grid - np.asarray(prior)).sum(axis=1)
    return int(np.argmax(train_net - 1e-6 * dist))


def walk_forward_folds(n: int, initial: int, block: int = WF_BLOCK) -> List[Tuple[range, range]]:
    """Ventana expansiva cronológica: entrena en [0, fin), evalúa en [fin, fin+block)."""
    folds = []
    end = initial
    while end < n:
        folds.append((range(0, end), range(end, min(end + block, n))))
        end += block
    return folds


def _score(col: np.ndarray) -> Tuple[int, int]:
    return int((col == 1).sum()), int((col == -1).sum())


@dataclass
class ArmScore:
    hits: int = 0
    misses: int = 0

    def add(self, col: np.ndarray) -> None:
        h, m = _score(col)
        self.hits += h
        self.misses += m

    @property
    def bets(self) -> int:
        return self.hits + self.misses

    @property
    def accuracy(self) -> Optional[float]:
        return self.hits / self.bets if self.bets else None


@dataclass
class WeightStudy:
    n: int
    in_sample_best: Tuple[float, ...]
    in_sample: Dict[str, ArmScore]
    wf_folds: int
    wf: Dict[str, ArmScore]
    wf_folds_best_beats_current: int
    loo: Dict[str, ArmScore]
    signal: Optional[bool]


def run_question_2(rows: Sequence[EvalRow]) -> Optional[WeightStudy]:
    data = sorted(
        (r for r in rows if r.resolved and all(r.scores.get(k) is not None for k in LAYERS)),
        key=lambda r: r.anchor,
    )
    n = len(data)
    if n < 2 * WF_BLOCK:
        return None
    X = np.array([[r.scores[k] for k in LAYERS] for r in data], dtype=float)
    y = np.array([1.0 if r.gt_thesis == "long" else -1.0 for r in data])
    grid = simplex_grid()
    C = weight_contributions(X, y, grid)
    ref = {"actuales": grid_index(grid, CURRENT_WEIGHTS),
           "iguales": grid_index(grid, EQUAL_WEIGHTS),
           "solo P2": grid_index(grid, P2_ONLY_WEIGHTS)}

    best_all = select_best(C.sum(axis=0), grid, CURRENT_WEIGHTS)
    in_sample = {"óptimo in-sample": ArmScore()}
    in_sample["óptimo in-sample"].add(C[:, best_all])
    for k, idx in ref.items():
        in_sample[k] = ArmScore()
        in_sample[k].add(C[:, idx])

    wf = {"óptimo": ArmScore(), **{k: ArmScore() for k in ref}}
    beats = 0
    folds = walk_forward_folds(n, initial=max(2 * WF_BLOCK, n // 2))
    for train, test in folds:
        best = select_best(C[list(train)].sum(axis=0), grid, CURRENT_WEIGHTS)
        test_rows = C[list(test)]
        wf["óptimo"].add(test_rows[:, best])
        for k, idx in ref.items():
            wf[k].add(test_rows[:, idx])
        b_acc = ArmScore(); b_acc.add(test_rows[:, best])
        c_acc = ArmScore(); c_acc.add(test_rows[:, ref["actuales"]])
        if b_acc.accuracy is not None and c_acc.accuracy is not None and b_acc.accuracy > c_acc.accuracy:
            beats += 1

    total = C.sum(axis=0)
    loo = {"óptimo": ArmScore(), **{k: ArmScore() for k in ref}}
    for i in range(n):
        best = select_best(total - C[i], grid, CURRENT_WEIGHTS)
        loo["óptimo"].add(C[i:i + 1, best])
        for k, idx in ref.items():
            loo[k].add(C[i:i + 1, idx])

    opt, cur = wf["óptimo"].accuracy, wf["actuales"].accuracy
    signal = None
    if opt is not None and cur is not None and folds:
        signal = (opt - cur) >= OOS_MIN_IMPROVEMENT and beats > len(folds) / 2
    return WeightStudy(n, tuple(float(w) for w in grid[best_all]), in_sample,
                       len(folds), wf, beats, loo, signal)


# ---------------------------------------------------------------------------
# Pregunta 3 — R real
# ---------------------------------------------------------------------------

def executed_frame(rows: Sequence[EvalRow]) -> pd.DataFrame:
    recs = [{
        "account": r.account, "trade_id": r.trade_id, "anchor": r.anchor,
        "order_filled": r.order_filled, "r_multiple": r.r_multiple,
        "calc_edge": r.calc_edge, "edge_bias": r.edge_bias,
        "abs_edge": abs(r.calc_edge), "thesis_dir": r.thesis_dir, "gt_thesis": r.gt_thesis,
        "gt_incompleto": r.gt_incompleto, "trade_dir": r.trade_dir,
        "stop_slippage_r": r.stop_slippage_r,
        **{f"{k.lower()}_score": r.scores.get(k) for k in LAYERS},
    } for r in rows if r.anchor_source == "tactical_audit" and r.order_filled is not None]
    df = pd.DataFrame(recs)
    if df.empty:
        return df
    return filter_executed_trades(df)


@dataclass
class MoneyStudy:
    n: int
    expectancy: float
    r_sum: float
    profit_factor: float
    wins: int
    ci: Tuple[float, float]
    signal: bool


def run_question_3(executed: pd.DataFrame, seed: int) -> Optional[MoneyStudy]:
    if executed.empty:
        return None
    r = executed["r_multiple"].astype(float).to_numpy()
    pos, neg = r[r > 0].sum(), r[r < 0].sum()
    ci = bootstrap_mean_ci(r, n_boot=N_RESAMPLES, seed=seed)
    return MoneyStudy(
        n=len(r), expectancy=float(r.mean()), r_sum=float(r.sum()),
        profit_factor=float(pos / abs(neg)) if neg < 0 else float("inf"),
        wins=int((r > 0).sum()), ci=ci, signal=ci[0] > 0,
    )


@dataclass
class ExecutionGap:
    confirmed_won: int = 0
    confirmed_lost: int = 0
    invalidated_won: int = 0
    invalidated_lost: int = 0
    direction_agree: int = 0
    direction_disagree: int = 0


def execution_gap(executed: pd.DataFrame) -> ExecutionGap:
    """
    Tabla tesis x trade. "Tesis confirmada" = el nivel de validación se tocó
    antes que el de invalidación. "Ganó" = r_multiple > 0. Además, acuerdo
    de dirección entre GT-tesis y GT-trade (la dirección hacia la que se
    movió el mercado según el resultado del trade real).
    """
    gap = ExecutionGap()
    for _, t in executed.iterrows():
        r = float(t["r_multiple"])
        if r == 0 or t["gt_incompleto"] or t["gt_thesis"] is None:
            continue
        confirmed = t["gt_thesis"] == t["thesis_dir"]
        won = r > 0
        if confirmed and won:
            gap.confirmed_won += 1
        elif confirmed:
            gap.confirmed_lost += 1
        elif won:
            gap.invalidated_won += 1
        else:
            gap.invalidated_lost += 1
        if t["trade_dir"]:
            moved = t["trade_dir"] if won else ("short" if t["trade_dir"] == "long" else "long")
            if moved == t["gt_thesis"]:
                gap.direction_agree += 1
            else:
                gap.direction_disagree += 1
    return gap


# ---------------------------------------------------------------------------
# Potencia y proyección
# ---------------------------------------------------------------------------

def months_spanned(anchors: Sequence[datetime]) -> float:
    if len(anchors) < 2:
        return 1.0
    return max((max(anchors) - min(anchors)).days / 30.44, 1.0)


@dataclass
class PowerLine:
    question: str
    n_now: int
    observed: str
    min_detectable: str
    n_needed: Optional[int]
    months_more: Optional[float]


def power_projection(rows: Sequence[EvalRow], executed: pd.DataFrame) -> List[PowerLine]:
    bets = edge_bets(rows)
    n = len(bets)
    per_account: Dict[str, List[EvalRow]] = {}
    for r in bets:
        per_account.setdefault(r.account, []).append(r)
    rate = sum(len(v) / months_spanned([r.anchor for r in v]) for v in per_account.values())

    def months(n_needed: Optional[int], n_now: int, per_month: float) -> Optional[float]:
        if n_needed is None or per_month <= 0:
            return None
        return max(0.0, (n_needed - n_now) / per_month)

    lines: List[PowerLine] = []
    if n:
        acc = sum(1 for r in bets if r.edge_hit) / n
        need = n_required_one_sample(0.5, acc) if acc > 0.5 else None
        lines.append(PowerLine("1a (vs azar)", n, f"{acc * 100:.1f}% vs 50%",
                               f"±{min_detectable_gap(n, max(acc, 0.5)) * 100:.0f} pts",
                               need, months(need, n, rate)))
        pairs = [r for r in bets if r.baselines.get("B1")]
        if pairs:
            e = sum(1 for r in pairs if r.edge_hit) / len(pairs)
            b = sum(1 for r in pairs if r.baselines["B1"] == r.gt_thesis) / len(pairs)
            need = n_required_one_sample(b, e) if e > b else None
            lines.append(PowerLine("1b (vs tendencia 1D)", len(pairs),
                                   f"{e * 100:.1f}% vs {b * 100:.1f}%",
                                   f"±{min_detectable_gap(len(pairs), max(e, b)) * 100:.0f} pts",
                                   need, months(need, len(pairs), rate)))
    if not executed.empty and len(executed) > 1:
        r = executed["r_multiple"].astype(float)
        sd, mean = float(r.std(ddof=1)), float(r.mean())
        need = math.ceil(((1.959964 + 0.841621) * sd / mean) ** 2) if mean > 0 and sd > 0 else None
        ex_rate = sum(
            len(g) / months_spanned(list(g["anchor"])) for _, g in executed.groupby("account")
        )
        lines.append(PowerLine("3 (expectancy > 0)", len(r), f"{mean:+.2f}R (sd {sd:.2f})",
                               f"±{2.8 * sd / math.sqrt(len(r)):.2f}R",
                               need, months(need, len(r), ex_rate)))
    return lines


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------

def _pct(x: Optional[float]) -> str:
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.1f}%"


def _p(p: Optional[float]) -> str:
    return "—" if p is None else ("<0.001" if p < 0.001 else f"{p:.3f}")


def _verdict(signal: Optional[bool]) -> str:
    return {True: "✅ señal", False: "— no concluyente", None: "sin datos"}[signal]


def _acc_line(rows: Sequence[EvalRow], pred) -> Tuple[int, int]:
    ok = n = 0
    for r in rows:
        d = pred(r)
        if d is None or not r.resolved:
            continue
        n += 1
        ok += d == r.gt_thesis
    return ok, n


def render_report(accounts: Sequence[AccountData], seed: int, generated_at: datetime) -> str:
    rows = [r for a in accounts for r in a.rows]
    executed = executed_frame(rows)
    q1 = run_question_1(rows, seed)
    q2 = run_question_2(rows)
    q3 = run_question_3(executed, seed)
    power = power_projection(rows, executed)
    L: List[str] = []
    A = L.append

    A("# Evaluación del edge completo (P0–P4)")
    A("")
    A(f"_Generado {generated_at:%Y-%m-%d %H:%M}. Semilla de remuestreo: `{seed}`. "
      "Solo lectura sobre las DBs de cuenta._")
    A("")
    A("Reglas de decisión fijadas antes de correr (plan `crea-un-plan-de-frolicking-kahn.md`). "
      "Las preguntas 1a/1b/1c se corrigen juntas por Holm; todo lo marcado exploratorio no "
      "entra en el veredicto.")
    A("")

    # --- Muestra ----------------------------------------------------------
    A("## Muestra")
    A("")
    A("| Cuenta | Activo | Reloj de velas | Con ejecución | Sin ejecución (`mark_price`) | Resueltos | Apuestas del edge | Con R real |")
    A("|---|---|---|---|---|---|---|---|")
    for a in accounts:
        if a.skipped:
            A(f"| {a.label} | — | — | — | — | — | — | **omitida:** {a.skipped} |")
            continue
        asset = next((r.asset for r in a.rows if r.asset), "—")
        clk = "—" if a.clock is None else ("OK" if a.clock.aligned else
                                           "sin calibrar" if a.clock.aligned is None else "❌")
        ex = sum(1 for r in a.rows if r.anchor_source == "tactical_audit")
        mk = sum(1 for r in a.rows if r.anchor_source == "mark_price")
        res = sum(1 for r in a.rows if r.resolved)
        bets = len(edge_bets(a.rows))
        rr = int((executed["account"] == a.label).sum()) if not executed.empty else 0
        A(f"| {a.label} | {asset} | {clk} | {ex} | {mk} | {res} | {bets} | {rr} |")
    tot_bets = len(edge_bets(rows))
    A(f"| **Total** | | | {sum(1 for r in rows if r.anchor_source == 'tactical_audit')} "
      f"| {sum(1 for r in rows if r.anchor_source == 'mark_price')} "
      f"| {sum(1 for r in rows if r.resolved)} | **{tot_bets}** | **{len(executed)}** |")
    A("")
    excl = Counter(e.reason for a in accounts for e in a.exclusions)
    if excl:
        A("Excluidos: " + ", ".join(f"`{k}` {v}" for k, v in excl.most_common()) + ".")
        A("")
    for a in accounts:
        if a.clock is not None and not a.skipped:
            A(f"- Reloj {a.label}: {a.clock.describe()}")
    A("")

    # --- Potencia ---------------------------------------------------------
    A("## Potencia: ¿qué puede responder esta muestra?")
    A("")
    A("Antes de mirar resultados: con pocas observaciones, un efecto real puede no verse. "
      "\"Brecha detectable\" es la diferencia mínima que esta muestra distingue del ruido "
      "(5% de significancia, 80% de potencia).")
    A("")
    A("| Pregunta | n actual | Observado | Brecha detectable | n necesario | Meses más (ritmo actual) |")
    A("|---|---|---|---|---|---|")
    for pl in power:
        mm = "—" if pl.months_more is None else ("ya alcanza" if pl.months_more == 0 else f"~{pl.months_more:.0f}")
        A(f"| {pl.question} | {pl.n_now} | {pl.observed} | {pl.min_detectable} "
          f"| {pl.n_needed if pl.n_needed is not None else '—'} | {mm} |")
    A("")
    A("\"—\" en n necesario: el efecto observado va en contra de la hipótesis (o es cero), así "
      "que más muestra no lo volvería significativo a favor.")
    A("")
    A("⚠️ **La proyección es optimista.** Supone que el efecto observado es el real, pero en "
      "muestras chicas el efecto observado tiende a estar inflado: si el verdadero es menor, "
      "harán falta más trades de los que dice la tabla. Tomarla como un piso, no como una promesa.")
    A("")

    # --- Veredicto --------------------------------------------------------
    A("## Veredicto pre-registrado")
    A("")
    A("| # | Pregunta | Estadístico | p | p (Holm) | Resultado |")
    A("|---|---|---|---|---|---|")
    for t in q1:
        A(f"| {t.key} | {t.question} | {t.statistic} | {_p(t.p_value)} | {_p(t.p_holm)} | {_verdict(t.signal)} |")
    if q2 is not None:
        opt, cur = q2.wf["óptimo"].accuracy, q2.wf["actuales"].accuracy
        A(f"| 2 | ¿Pesos mejores fuera de muestra? | OOS {_pct(opt)} vs actuales {_pct(cur)}; "
          f"gana en {q2.wf_folds_best_beats_current}/{q2.wf_folds} ventanas | — | — | {_verdict(q2.signal)} |")
    else:
        A("| 2 | ¿Pesos mejores fuera de muestra? | muestra insuficiente | — | — | sin datos |")
    if q3 is not None:
        A(f"| 3 | ¿R positivo? | expectancy {q3.expectancy:+.2f}R, IC95 [{q3.ci[0]:+.2f}, {q3.ci[1]:+.2f}] "
          f"(n={q3.n}) | — | — | {_verdict(q3.signal)} |")
    else:
        A("| 3 | ¿R positivo? | sin trades ejecutados | — | — | sin datos |")
    A("")
    A("\"No concluyente\" no es \"no funciona\": significa que la muestra no alcanza para "
      "distinguir el efecto del azar. Ver la tabla de potencia para cuánto falta.")
    A("")

    # --- Pregunta 1 -------------------------------------------------------
    A("## Pregunta 1 — Dirección: edge vs. azar y vs. reglas ingenuas")
    A("")
    A("Ground truth: GT-tesis (qué nivel estructural tocó primero el precio). Todos los "
      "baselines usan solo velas anteriores al anchor.")
    A("")
    majority = hindsight_majority(rows)
    preds = [
        ("Edge P0–P4", lambda r: r.edge_dir, "—"),
        ("B1 · precio vs EMA200 1D", lambda r: r.baselines.get("B1"), "**primario (1b)**"),
        ("B2 · precio vs EMA200 4H", lambda r: r.baselines.get("B2"), "exploratorio"),
        (f"B3 · momentum {MOMENTUM_BARS} velas 4H", lambda r: r.baselines.get("B3"), "exploratorio"),
        (f"B4 · siempre {majority}", lambda r: majority, "⚠️ retrospectiva, no es test"),
    ]
    A("| Regla | Rol | Puntería (todos los resueltos) | IC95 | En las apuestas del edge | McNemar vs edge |")
    A("|---|---|---|---|---|---|")
    bets = edge_bets(rows)
    for name, fn, role in preds:
        ok, n = _acc_line(rows, fn)
        lo, hi = wilson_interval(ok, n) if n else (float("nan"), float("nan"))
        ok2, n2 = _acc_line(bets, fn)
        if name.startswith("Edge"):
            mc = "—"
        else:
            b = sum(1 for r in bets if fn(r) and r.edge_hit and fn(r) != r.gt_thesis)
            c = sum(1 for r in bets if fn(r) and not r.edge_hit and fn(r) == r.gt_thesis)
            mc = f"{b} vs {c}, p={_p(mcnemar_exact(b, c))}"
        A(f"| {name} | {role} | {ok}/{n} = {_pct(ok / n if n else None)} "
          f"| [{_pct(lo)}, {_pct(hi)}] | {ok2}/{n2} = {_pct(ok2 / n2 if n2 else None)} | {mc} |")
    A("")
    A("McNemar compara solo los trades **discordantes**: donde las dos reglas coinciden, "
      "aciertan o fallan juntas y no dicen nada sobre cuál es mejor.")
    A("")

    A("### Dosis-respuesta: ¿acierta más con edge más fuerte?")
    A("")
    if len(bets) >= 3:
        df = pd.DataFrame({"abs_edge": [abs(r.calc_edge) for r in bets],
                           "hit": [1 if r.edge_hit else 0 for r in bets]})
        df["tercil"] = pd.qcut(df["abs_edge"], q=3, duplicates="drop")
        A("| Rango de \\|calc_edge\\| | Apuestas | Aciertos | Puntería | IC95 |")
        A("|---|---|---|---|---|")
        for rng, g in df.groupby("tercil", observed=True):
            k, n = int(g["hit"].sum()), len(g)
            lo, hi = wilson_interval(k, n)
            A(f"| {rng.left:.3f} – {rng.right:.3f} | {n} | {k} | {_pct(k / n)} | [{_pct(lo)}, {_pct(hi)}] |")
        A("")

    A("### Régimen: ¿acierta cuando va contra la tendencia mayor? (exploratorio)")
    A("")
    A("| Edge vs tendencia 1D | Apuestas | Puntería edge | Puntería tendencia |")
    A("|---|---|---|---|")
    for label, sel in (("A favor", lambda r: r.baselines.get("B1") == r.edge_dir),
                       ("En contra", lambda r: r.baselines.get("B1") not in (None, r.edge_dir))):
        g = [r for r in bets if sel(r)]
        e = sum(1 for r in g if r.edge_hit)
        t = sum(1 for r in g if r.baselines.get("B1") == r.gt_thesis)
        A(f"| {label} | {len(g)} | {_pct(e / len(g) if g else None)} | {_pct(t / len(g) if g else None)} |")
    A("")
    A("\"En contra\" es donde un seguidor de tendencia falla por construcción: si el edge "
      "tiene información propia, debería mostrarse acá.")
    A("")

    A("### Por activo (descriptivo)")
    A("")
    A("| Activo | Apuestas | Puntería | IC95 |")
    A("|---|---|---|---|")
    for asset in sorted({r.asset or "—" for r in bets}):
        g = [r for r in bets if (r.asset or "—") == asset]
        k = sum(1 for r in g if r.edge_hit)
        lo, hi = wilson_interval(k, len(g))
        A(f"| {asset} | {len(g)} | {_pct(k / len(g))} | [{_pct(lo)}, {_pct(hi)}] |")
    A("")

    # --- Pregunta 3 -------------------------------------------------------
    A("## Pregunta 3 — ¿Se traduce en R? (GT-trade)")
    A("")
    if q3 is None:
        A("Sin trades ejecutados con `r_multiple`.")
        A("")
    else:
        lo, hi = wilson_interval(q3.wins, q3.n)
        A(f"- Trades ejecutados con R real: **{q3.n}** (filtro `filter_executed_trades`: "
          "`order_filled` y `r_multiple` no nulo).")
        A(f"- Expectancy: **{q3.expectancy:+.3f}R** por trade; IC95 bootstrap "
          f"[{q3.ci[0]:+.3f}, {q3.ci[1]:+.3f}].")
        A(f"- R total: {q3.r_sum:+.2f} · profit factor: {q3.profit_factor:.2f} · "
          f"ganadores: {q3.wins}/{q3.n} = {_pct(q3.wins / q3.n)} (IC95 [{_pct(lo)}, {_pct(hi)}]).")
        A("")
        ex = executed.copy()
        ex["thesis_outcome"] = np.where(
            ex["gt_incompleto"] | ex["gt_thesis"].isna(), "sin resolver",
            np.where(ex["gt_thesis"] == ex["thesis_dir"], "tesis confirmada", "tesis invalidada"))
        ex["edge_tercil"] = pd.qcut(ex["abs_edge"], q=3, duplicates="drop").astype(str) if len(ex) >= 3 else "—"
        for col, title in (("edge_bias", "Por bias del edge"),
                           ("edge_tercil", "Por tercil de |calc_edge|"),
                           ("thesis_outcome", "Por resultado de la tesis")):
            seg = segment_kpis(ex, col, pnl_col="r_multiple", r_col="r_multiple")
            if seg.empty:
                continue
            A(f"**{title}** (exploratorio)")
            A("")
            A("| Grupo | Trades | Ganó | R promedio | R total | PF (R) |")
            A("|---|---|---|---|---|---|")
            for _, s in seg.iterrows():
                pf = "∞" if math.isinf(s["profit_factor_R"]) else f"{s['profit_factor_R']:.2f}"
                A(f"| {s[col]} | {int(s['trades'])} | {int(s['wins'])} | {s['r_avg']:+.2f} "
                  f"| {s['r_sum']:+.2f} | {pf} |")
            A("")

    # --- Pregunta 2 -------------------------------------------------------
    A("## Pregunta 2 — Pesos P0–P4 fuera de muestra")
    A("")
    if q2 is None:
        A("Muestra insuficiente para separar entrenamiento y validación.")
        A("")
    else:
        A(f"{len(simplex_grid())} combinaciones de pesos (paso {WEIGHT_GRID_STEP}, suman 1), umbral "
          f"de bias fijo en ±{BIAS_THRESHOLD}. Objetivo de selección: aciertos − fallos; empates "
          "resueltos hacia los pesos actuales. n = "
          f"{q2.n} trades resueltos con los 5 scores.")
        A("")
        A("| Pesos | In-sample | Walk-forward (OOS) | Leave-one-out (OOS) |")
        A("|---|---|---|---|")
        ins = q2.in_sample
        for key, label in (("óptimo", "Óptimo elegido"), ("actuales", "Actuales (0.30/0.25/0.15/0.10/0.20)"),
                           ("iguales", "Iguales (0.20 c/u)"), ("solo P2", "Solo P2")):
            i = ins["óptimo in-sample"] if key == "óptimo" else ins[key]
            w, lo_ = q2.wf[key], q2.loo[key]
            A(f"| {label} | {i.hits}/{i.bets} = {_pct(i.accuracy)} | {w.hits}/{w.bets} = {_pct(w.accuracy)} "
              f"| {lo_.hits}/{lo_.bets} = {_pct(lo_.accuracy)} |")
        A("")
        A(f"Óptimo in-sample: `{tuple(round(w, 2) for w in q2.in_sample_best)}` "
          f"(P0/P1/P2/P3/P4). **La columna in-sample está inflada por construcción**: elegir el "
          f"mejor de {len(simplex_grid())} sets sobre los mismos datos siempre se ve bien. Solo "
          "cuentan las columnas fuera de muestra.")
        A("")
        if not executed.empty:
            weights = {f"{k.lower()}_score": w for k, w in zip(LAYERS, CURRENT_WEIGHTS)}
            A("**Umbral de bias, medido en R real** (exploratorio; trades ejecutados, GT-trade). "
              "Qué habría pasado con solo los trades cuyo |edge| supera cada umbral.")
            A("")
            A("| Umbral | Trades | Ganó | R promedio | R total |")
            A("|---|---|---|---|---|")
            for thr in (0.0, 0.15, 0.20, 0.26, 0.30, 0.35, 0.40, 0.50):
                detail, _ = run_icd_backtest(executed, threshold=thr, score_cols=weights, weights=weights)
                real = evaluate_real_r_by_filter(detail, "proposed_signal")
                passing = detail[(detail["proposed_signal"] == 1) & detail["r_multiple"].notna()]
                n_pass = len(passing)
                wins = int((passing["r_multiple"] > 0).sum())
                avg = real["r_multiple_avg_filtro"]
                tot = real["r_multiple_sum_filtro"]
                label = "todos" if thr == 0 else f"{thr:.2f}"
                A(f"| {label} | {n_pass} | {wins}/{n_pass} | "
                  f"{'—' if pd.isna(avg) else f'{avg:+.2f}R'} | {'—' if pd.isna(tot) else f'{tot:+.2f}R'} |")
            A("")

    # --- Brecha de ejecución ---------------------------------------------
    A("## Brecha de ejecución: tesis vs. trade")
    A("")
    gap = execution_gap(executed) if not executed.empty else ExecutionGap()
    A("| | Trade ganó | Trade perdió |")
    A("|---|---|---|")
    A(f"| **Tesis confirmada** | {gap.confirmed_won} | {gap.confirmed_lost} |")
    A(f"| **Tesis invalidada** | {gap.invalidated_won} | {gap.invalidated_lost} |")
    A("")
    tot = gap.direction_agree + gap.direction_disagree
    A(f"GT-tesis y GT-trade coinciden en la dirección del mercado en **{gap.direction_agree}/{tot}** "
      "trades. Las discrepancias son de ejecución, no de dirección: "
      "*tesis confirmada / trade perdió* suele ser un stop táctico tocado antes de que la tesis "
      "se desarrolle; *tesis invalidada / trade ganó*, un take profit más cerca que los niveles "
      "estructurales. `stop_slippage_r` (disponible desde 2026-09-16) permitirá separar el primer "
      "caso cuando haya muestra.")
    A("")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_account(spec: str) -> Tuple[str, str]:
    if "=" not in spec:
        raise argparse.ArgumentTypeError(f"--account espera DB_PATH=OHLC_DIR, recibió {spec!r}")
    db, ohlc = spec.split("=", 1)
    return db, ohlc


def load_account(db_path: str, ohlc_dir: str, include_no_execution: bool) -> AccountData:
    label = os.path.splitext(os.path.basename(db_path))[0].replace("flight_account_", "")
    acc = AccountData(label=label, db_path=db_path, ohlc_dir=ohlc_dir)
    if not os.path.isdir(ohlc_dir):
        acc.skipped = f"no existe el directorio de velas `{ohlc_dir}`"
        return acc
    provider = load_ohlc_provider_from_csv(ohlc_dir)
    session = open_readonly_session(db_path)
    try:
        acc.clock = calibrate_clock_offset(session, provider)
        acc.rows, acc.exclusions = build_account_rows(session, provider, label, include_no_execution)
    finally:
        session.close()
    return acc


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluación del edge completo P0-P4.")
    parser.add_argument("--account", action="append", type=parse_account, default=None,
                        metavar="DB_PATH=OHLC_DIR",
                        help="Cuenta a incluir (repetible). Default: la cuenta XAUUSD con "
                             f"${OHLC_DIR_ENV_VAR}.")
    parser.add_argument("--include-no-execution", action=argparse.BooleanOptionalAction, default=True,
                        help="Incluir análisis sin ejecución usando mark_price (default: sí).")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", default=os.path.join(ROOT_DIR, "jupyter", "edge_evaluation_report.md"))
    parser.add_argument("--allow-clock-misalignment", action="store_true",
                        help="Generar el reporte aunque el reloj de velas no cuadre. Solo diagnóstico.")
    args = parser.parse_args(argv)

    specs = args.account
    if not specs:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT_DIR, ".env"))
        specs = [(DEFAULT_DB_PATH, os.getenv(OHLC_DIR_ENV_VAR, ""))]

    accounts = [load_account(db, ohlc, args.include_no_execution) for db, ohlc in specs]
    for a in accounts:
        if a.skipped:
            print(f"[{a.label}] omitida: {a.skipped}", file=sys.stderr)
        elif a.clock is not None:
            print(f"[{a.label}] reloj: {a.clock.describe()}")
    bad = [a.label for a in accounts if a.clock is not None and a.clock.aligned is False]
    if bad and not args.allow_clock_misalignment:
        print(f"ABORTADO: reloj de velas desalineado en {', '.join(bad)}. Re-exportar con el "
              "offset de servidor correcto.", file=sys.stderr)
        return 2

    report = render_report(accounts, args.seed, datetime.now())
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(report)
    print(f"Reporte escrito en {args.out}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
