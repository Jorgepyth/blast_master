"""
tools/p0_visor.py — visor de P0 sistemático: qué vio el motor en el ancla de un análisis, TF por TF.

Para el cuaderno `jupyter/p0_visor.ipynb`. Arma, para un análisis (por su short-id) y una TF:
- las velas alrededor del ancla (las posteriores al ancla se marcan como "futuro": el modelo no las ve);
- los fractales confirmados hasta el ancla, con su etiqueta (HH, LH, HL, LL) y los descartados;
- el estado, el tramo A→B y el nivel de invalidación;
- los niveles de Fibonacci y placebo del tramo, y sus eventos (entrada, barreras y resultado), conocidos en el ancla y,
  aparte, lo que pasó después;
- el puntaje de la TF (D8) y una explicación en palabras.

Solo lectura: las DBs con `mode=ro` (`read_analysis_rows`, `open_readonly_session`) y las velas de la foto o del banco,
sin escribir nada. Usa los mismos motores que la evaluación (`core/p0_structure.py`, `core/p0_fib.py`,
`tools/p0_sistematico.py`).
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from config.auto_resolution import RECOVERED_BACKDATED, S4_WINDOW_H
from core.candle_resolution import LADDER, start_price
from core.outcome_metrics import analysis_anchor
from core.p0_fib import (
    AMBIGUOUS,
    BARRIER_FRACTION,
    OPEN,
    P0_LEVELS,
    PENDING,
    PLACEBO_LEVELS,
    REJECTION,
    RUPTURE,
    LevelEvent,
    level_price,
    zone,
)
from core.p0_fib import detect_events
from core.p0_structure import (
    BEARISH,
    BOTTOM,
    BULLISH,
    INSUFFICIENT,
    RANGE,
    TOP,
    Leg,
    Pivot,
    alternating_sequence,
    find_pivots,
    state_at,
)
from core.p2_ground_truth import infer_thesis_direction
from tools import p0_sistematico as ps
from tools.auto_resolution import read_analysis_rows

STATE_ES = {BULLISH: "alcista (HH + HL)", BEARISH: "bajista (LH + LL)", RANGE: "rango",
            INSUFFICIENT: "historia insuficiente"}
OUTCOME_ES = {REJECTION: "rechazo", RUPTURE: "ruptura", AMBIGUOUS: "ambiguo", OPEN: "abierto", PENDING: "sin resultado"}
SITUATION_ES = {
    ps.SIT_INSUFFICIENT: "historia insuficiente: menos de dos techos y dos pisos confirmados",
    ps.SIT_RANGE: "rango: los techos y los pisos no forman una escalera",
    ps.SIT_NO_EVENT: "tendencia, sin un evento con resultado todavía",
    ps.SIT_RETR_REJECTION: "tendencia, y el último evento fue un rechazo en un retroceso (o en 1)",
    ps.SIT_RETR_RUPTURE: "tendencia, y el último evento fue una ruptura de un retroceso (o de 1)",
    ps.SIT_ZERO_REJECTION: "tendencia, y el último evento fue un rechazo en 0 (B)",
    ps.SIT_ZERO_RUPTURE: "tendencia, y el último evento fue una ruptura de 0 (B): el precio superó el extremo",
}


def level_name(r: float) -> str:
    return {0.0: "0 (B)", 1.0: "1 (A)"}.get(r, f"{r:g}")


# ---------------------------------------------------------------------------
# El análisis
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AnalysisInfo:
    trade_id: str
    symbol: str
    created_at: datetime
    anchor: datetime
    is_backdated: bool
    recovered: bool
    bias: Optional[str]
    evp: Optional[float]
    si: Optional[float]
    operator_direction: Optional[str]
    operator_strength: Optional[str]
    thesis: str
    labels: Dict[str, str]
    refs: Dict[str, str]

    @property
    def short_id(self) -> str:
        return self.trade_id[:8]


def find_analysis(db_path: str, symbol: str, short_id: str) -> AnalysisInfo:
    """El análisis cuyo id empieza con `short_id` (sin distinguir mayúsculas). Error si hay cero o más de uno."""
    key = short_id.strip().lower()
    if not key:
        raise ValueError("short_id vacío")
    rows = read_analysis_rows(db_path)
    matches = [r for r in rows if r.trade_id.lower().startswith(key)]
    if not matches:
        raise ValueError(f"no hay ningún análisis de {symbol} cuyo id empiece con '{short_id}'")
    if len(matches) > 1:
        raise ValueError(f"'{short_id}' coincide con {len(matches)} análisis: "
                         + ", ".join(r.trade_id[:12] for r in matches))
    row = matches[0]
    edges = ps.read_calc_edge(db_path)
    layers = ps.read_p0_layers(db_path)
    direction, strength, thesis = layers.get(row.trade_id, (None, None, None))
    labels, refs = ps.parse_thesis_labels(thesis or "")
    edge = edges.get(row.trade_id)
    return AnalysisInfo(
        row.trade_id, symbol, row.created_at,
        analysis_anchor(row.analysis_start_time, row.created_at, row.is_backdated), row.is_backdated,
        row.trade_id in RECOVERED_BACKDATED,
        ps.determine_market_bias(edge) if edge is not None else None,
        row.edge_validation_price, row.structural_invalidation, direction, strength, thesis or "", labels, refs)


# ---------------------------------------------------------------------------
# Lo que vio una TF en el ancla
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PivotView:
    kind: int
    index: int
    time: datetime
    price: float
    confirmed_at: datetime
    label: Optional[str]
    in_sequence: bool


@dataclass(frozen=True)
class EventView:
    r: float
    level: float
    zone_lo: float
    zone_hi: float
    entry_time: datetime
    entry_tf: str
    arrival: int                  # +1 llegó desde arriba, −1 desde abajo
    rejection_price: float
    rupture_price: float
    outcome_at_tau: str           # lo que se sabía en el ancla
    resolved_at_tau: Optional[datetime]
    outcome_final: Optional[str]  # lo que pasó después (solo para mirar, el modelo no lo vio)
    resolved_final: Optional[datetime]
    placebo: bool


@dataclass
class TfView:
    tf: str
    tau: datetime
    bars: pd.DataFrame            # time, open, high, low, close, future
    start: int                    # índice (en la serie de la TF) de la primera vela de `bars`
    last_closed: int              # índice de la última vela cerrada en τ
    n: int
    state: str
    leg: Optional[Leg]
    invalidation: Optional[float]
    pivots: List[PivotView]
    levels: Dict[str, Dict[float, Tuple[float, float, float]]]   # "p0"/"placebo" → r → (nivel, desde, hasta)
    events: Dict[str, List[EventView]]                            # "p0"/"placebo" → eventos
    reading: Dict[str, "ps.TfReading"]
    score_structure: int          # A
    score_fib: int                # B–H
    score_placebo: int            # gemelos
    letters: Dict[str, Pivot] = field(default_factory=dict)   # letra → pivote (los últimos 8 de la secuencia)
    chosen: bool = False          # True si el tramo lo eligió el usuario (opción 3)
    system_state: Optional[str] = None
    system_leg: Optional[Leg] = None
    system_scores: Tuple[int, int, int] = (0, 0, 0)          # A, B–H y gemelos, con el tramo del sistema

    @property
    def letter_of(self) -> Dict[Tuple[int, int], str]:
        return {(pv.kind, pv.index): letter for letter, pv in self.letters.items()}

    @property
    def tau_x(self) -> float:
        return self.last_closed - self.start + 0.5

    def x_of(self, t: Optional[datetime]) -> Optional[int]:
        """Posición en `bars` de la vela que contiene `t` (None si queda fuera)."""
        if t is None:
            return None
        times = list(self.bars.time)
        k = bisect_right(times, pd.Timestamp(t)) - 1
        return k if 0 <= k < len(times) else None


def _events_for_chosen_leg(market: "ps.Market", tf: str, leg: Leg, tau: Optional[datetime],
                           levels: Sequence[float]) -> Dict[float, LevelEvent]:
    """
    Eventos de un tramo elegido por el usuario: se lo toma como vigente desde la confirmación de B hasta τ (o hasta el
    final de la foto si τ es None), con el lado inicial del cierre de la vela de la TF que confirmó B.
    """
    found = detect_events(market.paths[tf], leg.a.price, leg.b.price, levels, market.close_at(tf, leg.b.confirmed_at),
                          start_time=leg.b.confirmed_at, tau=tau, ext_from_time=leg.b.confirmed_at,
                          half_width=market.half_width(tf, leg, levels), rupture_barrier=market.rupture_distance(leg))
    return {r: ev for r, ev in found.items() if ev is not None}


def _events_view(market: "ps.Market", tf: str, leg: Leg, tau: datetime, levels: Sequence[float],
                 placebo: bool, chosen: bool = False) -> List[EventView]:
    if chosen:
        known = _events_for_chosen_leg(market, tf, leg, tau, levels)
        final = _events_for_chosen_leg(market, tf, leg, None, levels)
    else:
        known = ps.tf_events_at(market, tf, leg, tau, levels, half_width=market.half_width(tf, leg, levels))
        final = ps.tf_events_at(market, tf, leg, None, levels, half_width=market.half_width(tf, leg, levels))
    d = BARRIER_FRACTION * leg.size
    d_rupture = market.rupture_fraction * leg.size
    out = []
    for r, ev in sorted(known.items(), key=lambda kv: kv[1].entry_index):
        lo, hi = zone(leg.a.price, leg.b.price, r)
        later = final.get(r)
        same = later is not None and later.entry_index == ev.entry_index
        out.append(EventView(
            r, ev.price, lo, hi, ev.entry_time, ev.entry_tf, ev.arrival, ev.price + ev.arrival * d,
            ev.price - ev.arrival * d_rupture, ev.outcome, ev.resolved_at,
            later.outcome if same else None, later.resolved_at if same else None, placebo))
    return out


STATE_CHOICES = {"alcista": BULLISH, "bajista": BEARISH, "rango": RANGE}
N_LETTERS = 8


def _is_letter(key: str) -> bool:
    return len(key) == 1 and key.isalpha()


def _pivots_at(tf: str, key: str, known: Sequence[Pivot]) -> List[Pivot]:
    """Los pivotes confirmados de la vela que nombra `key`: "AAAA-MM-DD HH:MM" (apertura) o "AAAA-MM-DD" (ese día)."""
    try:
        ts = pd.Timestamp(key)
    except ValueError:
        raise ValueError(f"{tf}: '{key}' no es una letra ni una fecha (AAAA-MM-DD o AAAA-MM-DD HH:MM)") from None
    if len(key) <= 10:
        return [p for p in known if pd.Timestamp(p.time).date() == ts.date()]
    return [p for p in known if pd.Timestamp(p.time) == ts]


def _resolve_pivot(tf: str, key: str, letters: Dict[str, Pivot], known: Sequence[Pivot],
                   kind: Optional[int]) -> Pivot:
    if _is_letter(key):
        if key not in letters:
            raise ValueError(f"{tf}: la letra '{key}' no existe; las disponibles son {', '.join(letters)}")
        return letters[key]
    at = _pivots_at(tf, key, known)
    found = [p for p in at if kind is None or p.kind == kind]
    if len(found) == 1:
        return found[0]
    if at and not found:
        word = "techo" if at[0].kind == TOP else "piso"
        raise ValueError(f"{tf}: en '{key}' hay un {word}, del mismo tipo que el otro extremo; A y B tienen que ser un "
                         "techo y un piso")
    if not found:
        ts = pd.Timestamp(key)
        near = sorted(known, key=lambda p: abs(pd.Timestamp(p.time) - ts))[:4]
        listed = "; ".join(f"{_fmt_t(p.time)} {'techo' if p.kind == TOP else 'piso'} {p.price:.2f}" for p in near)
        raise ValueError(f"{tf}: no hay un pivote confirmado en '{key}'. Los más cercanos: {listed}")
    listed = "; ".join(f"{_fmt_t(p.time)} {'techo' if p.kind == TOP else 'piso'} {p.price:.2f}" for p in found)
    raise ValueError(f"{tf}: '{key}' coincide con {len(found)} pivotes ({listed}); agregá la hora")


def _chosen_leg(tf: str, letters: Dict[str, Pivot], choice: Tuple[str, str],
                known: Sequence[Pivot] = ()) -> Leg:
    """
    El tramo A→B elegido. Cada extremo es una letra del gráfico o la fecha de la vela de un pivote confirmado (para
    pivotes más viejos que las 8 letras). Con una fecha que tiene un techo y un piso, se toma el tipo opuesto al otro
    extremo.
    """
    a_key, b_key = (str(x).strip().lower() for x in choice)
    # Primero el extremo que no es ambiguo (una letra, o una fecha con un solo pivote); el otro toma el tipo opuesto.
    b_first = not _is_letter(a_key) and (_is_letter(b_key) or len(_pivots_at(tf, b_key, known)) == 1) \
        and len(_pivots_at(tf, a_key, known)) != 1
    if b_first:
        b = _resolve_pivot(tf, b_key, letters, known, None)
        a = _resolve_pivot(tf, a_key, letters, known, -b.kind)
    else:
        a = _resolve_pivot(tf, a_key, letters, known, None)
        b = _resolve_pivot(tf, b_key, letters, known, -a.kind)
    if a.kind == b.kind:
        raise ValueError(f"{tf}: A ({a_key}) y B ({b_key}) tienen que ser un techo y un piso, no dos del mismo tipo")
    if a.index >= b.index:
        raise ValueError(f"{tf}: A ({a_key}) tiene que ser anterior a B ({b_key})")
    return Leg(tf, a, b)


def pivot_name(view: "TfView", pivot: Pivot) -> str:
    """La letra del pivote o, si es más viejo que las 8 letras, la fecha de su vela."""
    letter = view.letter_of.get((pivot.kind, pivot.index))
    return letter if letter else _fmt_t(pivot.time)


def tf_view(market: "ps.Market", tf: str, tau: datetime, window: int = 120, future: int = 20,
            choice: Optional[Tuple[str, str]] = None, state_choice: Optional[str] = None) -> TfView:
    """
    Todo lo que la TF `tf` sabía en τ, más `future` velas posteriores para mirar (marcadas como futuro).

    Opción 3: los últimos 8 pivotes de la secuencia llevan una letra (a = el más viejo). Con `choice = ("c", "f")` el
    tramo A→B es el que elige el usuario, y niveles, eventos y puntaje se recalculan con él; `state_choice` ("alcista",
    "bajista" o "rango") fija el estado, que por defecto sale de la orientación del tramo elegido. Lo que vio el
    sistema queda en `system_*` para compararlo.
    """
    bars = market.bars[tf]
    candles = market.candles[tf]
    k = bisect_right(bars.times, tau - bars.duration) - 1
    pt = state_at(market.timelines[tf], tau)
    known = [p for p in find_pivots(bars, market.n) if p.confirmed_at <= tau]
    seq = alternating_sequence(known)
    in_seq = {(e.pivot.kind, e.pivot.index): e.label for e in seq}
    letters = {chr(ord("a") + i): e.pivot for i, e in enumerate(seq[-N_LETTERS:])}
    system_reading = {ls: ps.read_tf(market, tf, tau, ls) for ls in ("p0", "placebo")}
    system_scores = (ps.tf_score(system_reading["p0"], False), ps.tf_score(system_reading["p0"], True),
                     ps.tf_score(system_reading["placebo"], True))
    leg, state, chosen = pt.leg, pt.state, False
    if choice is not None:
        leg = _chosen_leg(tf, letters, choice, known)
        if state_choice is not None and state_choice not in STATE_CHOICES:
            raise ValueError(f"{tf}: el estado tiene que ser uno de {list(STATE_CHOICES)}")
        state = STATE_CHOICES[state_choice] if state_choice else (BULLISH if leg.up else BEARISH)
        chosen = True
    start = k - window + 1
    if leg is not None:
        start = min(start, leg.a.index - 5)
    if letters:
        start = min(start, min(pv.index for pv in letters.values()) - 3)
    start = max(0, start)
    end = min(len(bars) - 1, k + future)
    rows = [{"time": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close, "future": i > k}
            for i, c in enumerate(candles[start:end + 1], start)]
    pivots = [PivotView(p.kind, p.index, p.time, p.price, p.confirmed_at, in_seq.get((p.kind, p.index)),
                        (p.kind, p.index) in in_seq) for p in known if p.index >= start]

    invalidation = None
    if chosen and state in (BULLISH, BEARISH):
        invalidation = leg.a.price  # con un tramo elegido, la referencia es su A
    elif state in (BULLISH, BEARISH) and seq:
        kind = BOTTOM if state == BULLISH else TOP
        invalidation = next(e.pivot.price for e in reversed(seq) if e.pivot.kind == kind)

    levels, events, reading = {}, {"p0": [], "placebo": []}, {}
    for level_set, rs in (("p0", P0_LEVELS), ("placebo", PLACEBO_LEVELS)):
        if chosen:
            known_ev = _events_for_chosen_leg(market, tf, leg, tau, rs)
            reading[level_set] = ps.reading_from_events(tf, state, leg, known_ev, level_set)
        else:
            reading[level_set] = system_reading[level_set]
        if leg is not None:
            levels[level_set] = {r: (level_price(leg.a.price, leg.b.price, r), *zone(leg.a.price, leg.b.price, r))
                                 for r in rs}
            events[level_set] = _events_view(market, tf, leg, tau, rs, level_set == "placebo", chosen)
        else:
            levels[level_set] = {}
    return TfView(tf, tau, pd.DataFrame(rows), start, k, market.n, state, leg, invalidation, pivots, levels,
                  events, reading, ps.tf_score(reading["p0"], False), ps.tf_score(reading["p0"], True),
                  ps.tf_score(reading["placebo"], True), letters, chosen, pt.state, pt.leg, system_scores)


def list_pivots(market: "ps.Market", tf: str, tau: datetime, last: int = 16) -> pd.DataFrame:
    """Los últimos `last` pivotes de la secuencia en τ, con su letra (si tiene) y la fecha para elegirlos en ELECCION."""
    known = [p for p in find_pivots(market.bars[tf], market.n) if p.confirmed_at <= tau]
    seq = alternating_sequence(known)
    first_letter = max(0, len(seq) - N_LETTERS)
    rows = [{"letra": chr(ord("a") + i - first_letter) if i >= first_letter else "",
             "fecha para ELECCION": pd.Timestamp(e.pivot.time).strftime("%Y-%m-%d %H:%M"),
             "tipo": "techo" if e.pivot.kind == TOP else "piso", "etiqueta": e.label or "", "precio": e.pivot.price}
            for i, e in enumerate(seq)]
    return pd.DataFrame(rows[-last:]).reset_index(drop=True)


def _fmt_t(t: Optional[datetime]) -> str:
    return "—" if t is None else pd.Timestamp(t).strftime("%Y-%m-%d %H:%M")


def explain_tf(view: TfView) -> str:
    """Lo que leyó la TF en el ancla, en palabras."""
    who = " (tu elección)" if view.chosen else ""
    lines = [f"**{view.tf}** · estado en el ancla{who}: **{STATE_ES[view.state]}**."]
    if view.chosen:
        sys_leg = view.system_leg
        sys_txt = "sin tramo" if sys_leg is None else f"{sys_leg.a.price:.2f} → {sys_leg.b.price:.2f}"
        lines.append(f"El sistema había leído {STATE_ES[view.system_state]}, tramo {sys_txt}, puntaje A "
                     f"{view.system_scores[0]:+d} y B–H {view.system_scores[1]:+d}.")
    leg = view.leg
    if leg is None:
        lines.append("No hay tramo: hacen falta al menos dos techos y dos pisos confirmados. Puntaje 0.")
        return "\n".join(f"- {x}" for x in lines)
    up = "sube" if leg.up else "baja"
    kind_a = "piso" if leg.a.kind == BOTTOM else "techo"
    kind_b = "techo" if leg.b.kind == TOP else "piso"
    la, lb = pivot_name(view, leg.a), pivot_name(view, leg.b)
    lines.append(f"Tramo A→B ({la}→{lb}): A = {leg.a.price:.2f} ({kind_a} de la vela {_fmt_t(leg.a.time)}) → "
                 f"B = {leg.b.price:.2f} ({kind_b} de la vela {_fmt_t(leg.b.time)}, confirmado al cierre "
                 f"{_fmt_t(leg.b.confirmed_at)}). El tramo {up} {leg.size:.2f}.")
    if view.invalidation is not None:
        side = "cierra debajo" if view.state == BULLISH else "cierra encima"
        lines.append(f"La tendencia se invalida si una vela de {view.tf} {side} de {view.invalidation:.2f}.")
    evs = view.events["p0"]
    if not evs:
        lines.append("Ningún nivel de Fibonacci del tramo fue tocado todavía en el ancla.")
    for ev in evs:
        arrival = "desde arriba (bajando)" if ev.arrival == 1 else "desde abajo (subiendo)"
        txt = (f"Nivel {level_name(ev.r)} = {ev.level:.2f}: el precio entró a su zona "
               f"[{ev.zone_lo:.2f}, {ev.zone_hi:.2f}] el {_fmt_t(ev.entry_time)}, {arrival}. Rechazo si vuelve a "
               f"{ev.rejection_price:.2f}; ruptura si llega a {ev.rupture_price:.2f}. ")
        if ev.outcome_at_tau in (REJECTION, RUPTURE, AMBIGUOUS):
            txt += f"En el ancla ya se sabía: **{OUTCOME_ES[ev.outcome_at_tau]}** ({_fmt_t(ev.resolved_at_tau)})."
        else:
            txt += "En el ancla todavía no tenía resultado"
            if ev.outcome_final in (REJECTION, RUPTURE, AMBIGUOUS, OPEN):
                txt += (f"; después fue {OUTCOME_ES[ev.outcome_final]} ({_fmt_t(ev.resolved_final)}), algo que el "
                        "modelo no vio")
            txt += "."
        lines.append(txt)
    lines.append(f"Situación de D8: {SITUATION_ES[view.reading['p0'].situation]}. Puntaje de esta TF: "
                 f"A (solo estructura) {view.score_structure:+d}; B–H (con Fibonacci) {view.score_fib:+d}; "
                 f"gemelos placebo {view.score_placebo:+d}.")
    return "\n".join(f"- {x}" for x in lines)


# ---------------------------------------------------------------------------
# Gráficos (matplotlib)
# ---------------------------------------------------------------------------

INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
SURFACE, FUTURE_SHADE = "#fcfcfb", "#f0efec"
UP, DOWN = "#2a78d6", "#e34948"          # par divergente: vela que sube / vela que baja
LEG_COLOR, EVENT_COLOR = "#4a3aa7", "#4a3aa7"
P0_COLOR = "#eb6834"


def _style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#c3c2b7")
    ax.tick_params(colors=INK2, labelsize=8)


def _candles(ax, view: TfView, x_from: int = 0, extra_prices: Sequence[float] = ()):
    """Velas desde la posición `x_from` (zoom), con el futuro sombreado y el eje y ajustado a lo visible."""
    from matplotlib.patches import Rectangle

    df = view.bars
    vis = df.iloc[x_from:]
    span = float(vis.high.max() - vis.low.min()) or 1.0
    for x, row in enumerate(df.itertuples(index=False)):
        if x < x_from:
            continue
        color = UP if row.close >= row.open else DOWN
        alpha = 0.35 if row.future else 1.0
        ax.vlines(x, row.low, row.high, color=color, linewidth=0.8, alpha=alpha, zorder=2)
        lo, hi = sorted((row.open, row.close))
        ax.add_patch(Rectangle((x - 0.32, lo), 0.64, max(hi - lo, span * 0.0015), facecolor=color, edgecolor=color,
                               linewidth=0.4, alpha=alpha, zorder=3))
    ax.axvspan(view.tau_x, len(df) - 0.5, color=FUTURE_SHADE, zorder=0)
    ax.axvline(view.tau_x, color=INK, linewidth=1.0, zorder=4)
    ax.text(view.tau_x, 0.99, " ancla: el modelo\n mira hasta acá", transform=ax.get_xaxis_transform(), fontsize=7,
            color=INK, va="top", ha="left", zorder=9)
    ax.set_xlim(x_from - 1, len(df))
    ticks = np.linspace(x_from, len(df) - 1, min(8, len(df) - x_from)).astype(int)
    fmt = "%Y-%m-%d" if view.tf in ("1W", "1D", "12H") else "%m-%d %H:%M"
    ax.set_xticks(ticks)
    ax.set_xticklabels([pd.Timestamp(df.time.iloc[t]).strftime(fmt) for t in ticks], rotation=0)
    prices = [float(vis.low.min()), float(vis.high.max()), *[p for p in extra_prices if p is not None]]
    lo, hi = min(prices), max(prices)
    pad = (hi - lo) * 0.08 or 1.0
    ax.set_ylim(lo - pad, hi + pad)


def _legend_below(ax, handles):
    ax.legend(handles=handles, fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.09), frameon=False,
              ncol=len(handles))


def plot_fractals(ax, view: TfView, info: Optional[AnalysisInfo] = None):
    """Velas, fractales confirmados en el ancla (con etiqueta), descartados, zigzag, tramo A→B e invalidación."""
    from matplotlib.lines import Line2D

    _style(ax)
    _candles(ax, view)
    df = view.bars
    pad = float(df.high.max() - df.low.min()) * 0.02
    seq = [p for p in view.pivots if p.in_sequence]
    if seq:
        ax.plot([p.index - view.start for p in seq], [p.price for p in seq], color=INK2, linewidth=1.0, zorder=5)
    for p in view.pivots:
        x = p.index - view.start
        top = p.kind == TOP
        y = p.price + pad if top else p.price - pad
        if p.in_sequence:
            ax.scatter([x], [y], marker="v" if top else "^", s=46, color=INK, zorder=6)
            ax.text(x, y + (pad if top else -pad), p.label or "1.º", fontsize=7, color=INK, ha="center",
                    va="bottom" if top else "top", zorder=6)
            xc = p.index + view.n - view.start
            ax.plot([x, xc], [p.price, p.price], color=MUTED, linewidth=0.8, linestyle=":", zorder=5)
        else:
            ax.scatter([x], [y], marker="v" if top else "^", s=40, facecolors="none", edgecolors=MUTED, zorder=6)
    for letter, pv in view.letters.items():
        x = pv.index - view.start
        if x < 0:
            continue
        top = pv.kind == TOP
        ax.annotate(letter, (x, pv.price), xytext=(9, 9 if top else -9), textcoords="offset points", fontsize=8,
                    fontweight="bold", color=SURFACE, ha="center", va="center", zorder=9,
                    bbox=dict(boxstyle="circle,pad=0.18", facecolor=INK, edgecolor="none"))
    if view.chosen and view.system_leg is not None:
        sa, sb = view.system_leg.a.index - view.start, view.system_leg.b.index - view.start
        ax.plot([sa, sb], [view.system_leg.a.price, view.system_leg.b.price], color=LEG_COLOR, linewidth=1.6,
                linestyle="--", zorder=7)
    if view.leg is not None:
        color = P0_COLOR if view.chosen else LEG_COLOR
        xa, xb = view.leg.a.index - view.start, view.leg.b.index - view.start
        ax.plot([xa, xb], [view.leg.a.price, view.leg.b.price], color=color, linewidth=2.6, zorder=7)
        for x, price, name in ((xa, view.leg.a.price, "A"), (xb, view.leg.b.price, "B")):
            ax.annotate(name, (x, price), xytext=(-14, 0), textcoords="offset points", fontsize=10,
                        fontweight="bold", color=color, va="center", zorder=8)
    if view.invalidation is not None:
        x0 = view.leg.b.index - view.start if view.leg is not None else 0
        ax.hlines(view.invalidation, x0, len(df) - 1, color=MUTED, linestyle="--", linewidth=1.0, zorder=4)
        word = "debajo" if view.state == BULLISH else "encima"
        ax.text(len(df) - 1, view.invalidation, f"invalida si cierra {word} de {view.invalidation:.2f} ",
                fontsize=7, color=INK2, ha="right", va="bottom")
    who = " (tu elección)" if view.chosen else ""
    ax.set_title(f"{view.tf} · Fractales y estructura · estado en el ancla{who}: {STATE_ES[view.state]}",
                 fontsize=10, color=INK, loc="left")
    handles = [Line2D([], [], marker="v", linestyle="", color=INK, label="techo confirmado (etiqueta HH/LH)"),
               Line2D([], [], marker="^", linestyle="", color=INK, label="piso confirmado (etiqueta HL/LL)"),
               Line2D([], [], marker="v", linestyle="", markerfacecolor="none", markeredgecolor=MUTED,
                      label="descartado (había otro más extremo)"),
               Line2D([], [], color=MUTED, linestyle=":", label=f"se confirma {view.n} velas después"),
               Line2D([], [], color=LEG_COLOR, linewidth=2.6, label="tramo A→B del sistema")]
    if view.chosen:
        handles[-1] = Line2D([], [], color=LEG_COLOR, linewidth=1.6, linestyle="--", label="tramo A→B del sistema")
        handles.append(Line2D([], [], color=P0_COLOR, linewidth=2.6, label="tu tramo A→B"))
    handles.append(Line2D([], [], marker="o", linestyle="", color=INK, label="letra: para elegir A→B"))
    _legend_below(ax, handles)


def plot_fibonacci(ax, view: TfView, info: Optional[AnalysisInfo] = None, show_placebo: bool = True):
    """Velas, zonas de los niveles de P0 del tramo, placebo, eventos con sus barreras y los niveles del operador."""
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    _style(ax)
    df = view.bars
    xmax = len(df) - 1
    title = f"{view.tf} · Fibonacci del tramo A→B" + (" (tu elección)" if view.chosen else "")
    if view.leg is None:
        _candles(ax, view)
        ax.text(0.5, 0.5, "Sin tramo: historia insuficiente", transform=ax.transAxes, ha="center", color=INK2)
        ax.set_title(title, fontsize=10, color=INK, loc="left")
        return
    # Zoom: desde unas velas antes de A hasta el final, con el eje y abarcando zonas, barreras y tus niveles.
    xa = max(0, view.leg.a.index - view.start)
    extra = [p for _c, lo, hi in view.levels["p0"].values() for p in (lo, hi)]
    extra += [p for ev in view.events["p0"] for p in (ev.rejection_price, ev.rupture_price)]
    if show_placebo:
        extra += [c for c, _lo, _hi in view.levels["placebo"].values()]
    if info is not None:
        extra += [info.evp, info.si]
    _candles(ax, view, x_from=max(0, xa - 8), extra_prices=extra)
    ax.plot([xa, view.leg.b.index - view.start], [view.leg.a.price, view.leg.b.price],
            color=P0_COLOR if view.chosen else LEG_COLOR, linewidth=1.6, alpha=0.8, zorder=5)
    for r, (center, lo, hi) in view.levels["p0"].items():
        ax.fill_between([xa, xmax], lo, hi, color=P0_COLOR, alpha=0.22, linewidth=0, zorder=1)
        ax.hlines(center, xa, xmax, color=P0_COLOR, linewidth=0.9, zorder=1)
        ax.text(xmax + 0.6, center, f"{level_name(r)}  {center:.2f}", fontsize=7, color=INK, va="center")
    if show_placebo:
        for r, (center, _lo, _hi) in view.levels["placebo"].items():
            ax.hlines(center, xa, xmax, color=MUTED, linewidth=0.7, linestyle="--", zorder=1)
            ax.text(xa, center, f"placebo {r:g} ", fontsize=6, color=MUTED, va="center", ha="right")
    if info is not None and info.evp is not None and info.si is not None:
        for price, name in ((info.evp, "tu objetivo (EVP)"), (info.si, "tu invalidación (SI)")):
            ax.hlines(price, view.tau_x, xmax, color=INK, linewidth=1.0, linestyle=(0, (1, 2)), zorder=4)
            ax.text(view.tau_x + 0.3, price, f" {name} {price:.2f}", fontsize=7, color=INK, va="bottom")
    for k, ev in enumerate(view.events["p0"]):
        xe = view.x_of(ev.entry_time)
        if xe is None:
            continue
        dy = 4 + 10 * k  # escalona las etiquetas: los eventos de una TF grande suelen caer en la misma vela
        ax.scatter([xe], [ev.level], s=40, color=EVENT_COLOR, zorder=8)
        arrow = "↓" if ev.arrival == 1 else "↑"
        ax.text(xe, ev.zone_hi if ev.arrival == 1 else ev.zone_lo, arrow, fontsize=11, color=EVENT_COLOR,
                ha="center", va="bottom" if ev.arrival == 1 else "top", zorder=8)
        known = ev.outcome_at_tau in (REJECTION, RUPTURE, AMBIGUOUS)
        x_end = view.x_of(ev.resolved_at_tau) if known else int(view.tau_x)
        x_end = xe if x_end is None else x_end
        for price in (ev.rejection_price, ev.rupture_price):
            ax.hlines(price, xe, max(x_end, xe + 1), color=EVENT_COLOR, linewidth=0.9, linestyle="--", zorder=7)
        if known:
            xr = view.x_of(ev.resolved_at_tau)
            yr = ev.rejection_price if ev.outcome_at_tau == REJECTION else ev.rupture_price
            ax.scatter([xr], [yr], marker="D", s=34, color=EVENT_COLOR, zorder=8)
            ax.annotate(f"{level_name(ev.r)}: {OUTCOME_ES[ev.outcome_at_tau]}", (xr, yr), xytext=(6, dy),
                        textcoords="offset points", fontsize=7, color=INK, zorder=9)
        else:
            ax.annotate(f"{level_name(ev.r)}: sin resultado en el ancla", (xe, ev.level), xytext=(6, -dy),
                        textcoords="offset points", fontsize=7, color=INK, zorder=9)
            xf = view.x_of(ev.resolved_final)
            if ev.outcome_final in (REJECTION, RUPTURE) and xf is not None:
                yf = ev.rejection_price if ev.outcome_final == REJECTION else ev.rupture_price
                ax.scatter([xf], [yf], marker="D", s=34, facecolors="none", edgecolors=EVENT_COLOR, zorder=8)
                ax.annotate(f"después: {OUTCOME_ES[ev.outcome_final]}", (xf, yf), xytext=(6, dy),
                            textcoords="offset points", fontsize=7, color=INK2, zorder=9)
    last = view.reading["p0"].last_event
    tail = "sin eventos" if last is None else f"último evento: {level_name(last.r)}"
    ax.set_title(f"{title} ({view.leg.a.price:.2f} → {view.leg.b.price:.2f}) · {tail} · puntaje con Fib "
                 f"{view.score_fib:+d}", fontsize=10, color=INK, loc="left")
    handles = [Patch(color=P0_COLOR, alpha=0.4, label="zona de un nivel de P0 (±2.5% del tramo)"),
               Line2D([], [], color=MUTED, linestyle="--", label="nivel placebo"),
               Line2D([], [], marker="o", linestyle="", color=EVENT_COLOR, label="entrada a la zona (↓ ↑ = llegada)"),
               Line2D([], [], color=EVENT_COLOR, linestyle="--", label="barreras: ±10% del tramo"),
               Line2D([], [], marker="D", linestyle="", color=EVENT_COLOR, label="resultado")]
    _legend_below(ax, handles)


# ---------------------------------------------------------------------------
# Resumen del análisis
# ---------------------------------------------------------------------------

def model_summary(market: "ps.Market", tau: datetime, views: Optional[Dict[str, TfView]] = None) -> pd.DataFrame:
    """P0 de cada modelo en τ y el puntaje de cada TF."""
    overrides = {(tf, ls): v.reading[ls] for tf, v in (views or {}).items() if v.chosen for ls in ("p0", "placebo")}
    out = ps.predict_at(market, tau, overrides=overrides or None)
    rows = []
    for spec in ps.ALL_SPECS:
        res = out[spec.name]
        x0 = res["x0"]
        rows.append({"modelo": spec.name, "descripción": spec.label,
                     **{f"puntaje {tf}": res["scores"].get(tf, "") for tf in ("1W", "1D", "12H", "4H", "1H")},
                     "P0 (x0)": x0, "dirección": "Long" if x0 > 0 else "Short" if x0 < 0 else "no opina",
                     "fuerza": {2: "Strong", 1: "Mid", 0: "—"}[abs(x0)]})
    return pd.DataFrame(rows)


def thesis_direction(info: AnalysisInfo, market: "ps.Market") -> Tuple[Optional[float], Optional[str]]:
    """Precio de partida en el ancla y la dirección de la tesis (`long` si el objetivo está arriba)."""
    ladder = {tf: market.candles[tf] for tf in LADDER if market.candles.get(tf)}
    sp = start_price(info.anchor, ladder)
    if sp is None or info.evp is None or info.si is None:
        return (None if sp is None else sp.price), None
    return sp.price, infer_thesis_direction(sp.price, info.evp, info.si)


def what_happened(info: AnalysisInfo, market: "ps.Market", predictions: Dict[str, int]) -> Dict[str, object]:
    """
    Lo que pasó después del ancla (el modelo no lo vio): qué nivel tocó primero el precio, con el resolvedor de la
    spec 002, y si cada predicción (+1 Long, −1 Short, 0 no opina) acertó en S1 y en S4 estricto (D12: Long acierta si
    toca primero el nivel de arriba).
    """
    sp, thesis = thesis_direction(info, market)
    if thesis is None:
        return {"motivo": "sin niveles, o los dos del mismo lado del precio de partida"}
    upper, lower = max(info.evp, info.si), min(info.evp, info.si)
    item = ps.AnalysisInput(info.trade_id, info.symbol, "", info.anchor, info.created_at, None, False, info.bias or "",
                            "", sp, upper, lower, info.evp, info.si, 0, None, "")
    ladder = {tf: market.candles[tf] for tf in LADDER if market.candles.get(tf)}
    truth = ps.geometric_truth_at(item, ladder)
    out: Dict[str, object] = {"precio de partida": sp, "nivel de arriba": upper, "nivel de abajo": lower,
                              "resultado del resolvedor": truth.code}
    if truth.upper_first is None:
        out["primer toque"] = "ninguno resuelto"
        return out
    hours = (truth.touch_time - info.anchor).total_seconds() / 3600
    out["primer toque"] = f"{'arriba' if truth.upper_first else 'abajo'} ({upper if truth.upper_first else lower:.2f})"
    out["hora del toque"] = truth.touch_time
    out["horas desde el ancla"] = round(hours, 1)
    for name, pred in predictions.items():
        if pred == 0:
            out[name] = "no opina"
            continue
        hit = (pred > 0) == truth.upper_first
        s4e = "acierta" if hit and hours <= S4_WINDOW_H else ("pierde (tarde)" if hit else "pierde")
        out[name] = f"{'Long' if pred > 0 else 'Short'}: S1 {'acierta' if hit else 'pierde'}, S4 estricto {s4e}"
    return out
