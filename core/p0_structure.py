"""
core/p0_structure.py — motor de estructura de P0 sistemático (fractales de precio, secuencia alterna, estado y tramo).

Pre-registro `jupyter/p0_sistematico/preregistro.md` v1.1, §6 (D3, D4, D5); RF-1 a RF-3; INV-3 e INV-4.

- **Fractal (D3):** superior en t si `H_t >= max(H_{t-n..t-1})` y `H_t > max(H_{t+1..t+n})`; inferior, simétrico con
  mínimos. Se conoce al **cierre** de la vela t+n (R8, INV-4).
- **Secuencia alterna (D4):** dos techos seguidos conservan el más alto (con empate, el anterior); dos pisos, el más
  bajo. Cada techo se etiqueta contra el techo anterior de la secuencia (HH/LH) y cada piso contra el piso anterior
  (HL/LL); un empate exacto hereda la etiqueta del anterior.
- **Estado:** HH + HL = alcista; LH + LL = bajista; cualquier otra combinación = rango. Menos de dos techos y dos pisos,
  o un último pivote sin etiqueta = historia insuficiente (E1).
- **Invalidación:** alcista, cuando una vela cierra debajo del último HL; bajista, cuando cierra encima del último LH
  (con `wick_invalidation=True`, la mecha: sensibilidad T11). Desde ahí, rango hasta que se confirmen un techo nuevo y
  un piso nuevo.
- **Tramo A→B (D5):** alcista → B = último techo, A = el piso anterior; bajista → B = último piso, A = el techo anterior;
  rango → B = último pivote, A = el pivote opuesto anterior.

Funciones puras, sin I/O. "Fractal" es solo el fractal de precio de n velas (R14).
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence

import numpy as np

TOP = 1
BOTTOM = -1

BULLISH = "bullish"
BEARISH = "bearish"
RANGE = "range"
INSUFFICIENT = "insufficient_history"
TREND_STATES = (BULLISH, BEARISH)


@dataclass(frozen=True)
class Bars:
    """Velas de una TF, ordenadas por apertura. `times` son aperturas; una vela cierra en `time + duration`."""
    times: tuple
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    duration: timedelta

    @classmethod
    def from_lists(cls, times: Sequence[datetime], high, low, close, duration: timedelta) -> "Bars":
        order = sorted(range(len(times)), key=lambda i: times[i])
        return cls(tuple(times[i] for i in order), np.asarray(high, float)[order], np.asarray(low, float)[order],
                   np.asarray(close, float)[order], duration)

    def __len__(self) -> int:
        return len(self.times)

    def close_time(self, i: int) -> datetime:
        return self.times[i] + self.duration

    def closed(self, tau: datetime) -> "Bars":
        """Las velas ya cerradas en τ (`time + duración <= τ`, INV-3)."""
        k = bisect_right(self.times, tau - self.duration)
        return Bars(self.times[:k], self.high[:k], self.low[:k], self.close[:k], self.duration)


@dataclass(frozen=True)
class Pivot:
    kind: int               # TOP o BOTTOM
    index: int              # vela t
    time: datetime          # apertura de la vela t
    price: float            # H_t o L_t
    confirmed_at: datetime  # cierre de la vela t+n


def find_pivots(bars: Bars, n: int = 2) -> List[Pivot]:
    """Fractales de n velas (D3), en orden de vela. Una vela puede dar un techo y un piso a la vez."""
    h, lo = bars.high, bars.low
    out: List[Pivot] = []
    for t in range(n, len(bars) - n):
        if h[t] >= h[t - n:t].max() and h[t] > h[t + 1:t + n + 1].max():
            out.append(Pivot(TOP, t, bars.times[t], float(h[t]), bars.close_time(t + n)))
        if lo[t] <= lo[t - n:t].min() and lo[t] < lo[t + 1:t + n + 1].min():
            out.append(Pivot(BOTTOM, t, bars.times[t], float(lo[t]), bars.close_time(t + n)))
    return out


@dataclass(frozen=True)
class SeqEntry:
    pivot: Pivot
    label: Optional[str]  # HH, LH, HL, LL o None (sin pivote anterior del mismo tipo)


def _label(new: Pivot, previous: Optional[SeqEntry]) -> Optional[str]:
    if previous is None:
        return None
    if new.price == previous.pivot.price:
        return previous.label  # empate exacto: no cambia la etiqueta
    if new.kind == TOP:
        return "HH" if new.price > previous.pivot.price else "LH"
    return "HL" if new.price > previous.pivot.price else "LL"


def _previous_of_kind(seq: Sequence[SeqEntry], kind: int, before: int) -> Optional[SeqEntry]:
    for j in range(before - 1, -1, -1):
        if seq[j].pivot.kind == kind:
            return seq[j]
    return None


def _push(seq: List[SeqEntry], pivot: Pivot) -> bool:
    """Agrega un pivote a la secuencia alterna. Devuelve True si entró (agregado o reemplazo)."""
    if seq and seq[-1].pivot.kind == pivot.kind:
        last = seq[-1].pivot
        more_extreme = pivot.price > last.price if pivot.kind == TOP else pivot.price < last.price
        if not more_extreme:
            return False  # dos del mismo tipo seguidos: se queda el más extremo; con empate, el anterior
        seq[-1] = SeqEntry(pivot, _label(pivot, _previous_of_kind(seq, pivot.kind, len(seq) - 1)))
        return True
    seq.append(SeqEntry(pivot, _label(pivot, _previous_of_kind(seq, pivot.kind, len(seq)))))
    return True


def _order_same_bar(pivots: Sequence[Pivot], last_kind: Optional[int]) -> List[Pivot]:
    """Techo y piso en la misma vela: entra primero el del tipo opuesto al último pivote de la secuencia."""
    if len(pivots) < 2 or last_kind is None:
        return list(pivots)
    return sorted(pivots, key=lambda p: 0 if p.kind != last_kind else 1)


def alternating_sequence(pivots: Sequence[Pivot]) -> List[SeqEntry]:
    """La secuencia alterna con etiquetas, a partir de pivotes en orden de confirmación."""
    seq: List[SeqEntry] = []
    i = 0
    while i < len(pivots):
        group = [p for p in pivots[i:] if p.index == pivots[i].index]
        for p in _order_same_bar(group, seq[-1].pivot.kind if seq else None):
            _push(seq, p)
        i += len(group)
    return seq


@dataclass(frozen=True)
class Leg:
    """Un tramo A→B (D5). Su identidad es (TF, pivote A, pivote B)."""
    tf: str
    a: Pivot
    b: Pivot

    @property
    def up(self) -> bool:
        return self.b.price > self.a.price

    @property
    def size(self) -> float:
        return abs(self.b.price - self.a.price)

    @property
    def key(self) -> tuple:
        return (self.tf, self.a.kind, self.a.index, self.b.kind, self.b.index)


@dataclass(frozen=True)
class StructurePoint:
    """Desde `at` (el cierre de una vela de la TF), el estado y el tramo de la TF valen esto."""
    at: datetime
    state: str
    leg: Optional[Leg]


def _state(seq: Sequence[SeqEntry]) -> str:
    tops = [e for e in seq if e.pivot.kind == TOP]
    bottoms = [e for e in seq if e.pivot.kind == BOTTOM]
    if len(tops) < 2 or len(bottoms) < 2:
        return INSUFFICIENT
    top, bottom = tops[-1].label, bottoms[-1].label
    if top is None or bottom is None:
        return INSUFFICIENT
    if top == "HH" and bottom == "HL":
        return BULLISH
    if top == "LH" and bottom == "LL":
        return BEARISH
    return RANGE


def _leg(seq: Sequence[SeqEntry], state: str, tf: str) -> Optional[Leg]:
    if state == INSUFFICIENT:
        return None
    if state in TREND_STATES:
        kind = TOP if state == BULLISH else BOTTOM
        j = max(k for k, e in enumerate(seq) if e.pivot.kind == kind)
    else:
        j = len(seq) - 1
    if j < 1:
        return None
    return Leg(tf, seq[j - 1].pivot, seq[j].pivot)


def structure_timeline(bars: Bars, n: int = 2, tf: str = "", wick_invalidation: bool = False) -> List[StructurePoint]:
    """
    El estado y el tramo de la TF a lo largo del tiempo: un punto en cada cierre de vela en que cambian. En cada cierre,
    primero se confirman los pivotes de la vela t−n y después se mira la invalidación con esa misma vela.
    """
    by_confirmation = {}
    for p in find_pivots(bars, n):
        by_confirmation.setdefault(p.index + n, []).append(p)

    seq: List[SeqEntry] = []
    invalidated = False
    new_top = new_bottom = False
    points: List[StructurePoint] = []
    for i in range(len(bars)):
        at = bars.close_time(i)
        group = by_confirmation.get(i, [])
        for p in _order_same_bar(group, seq[-1].pivot.kind if seq else None):
            if _push(seq, p) and invalidated:
                new_top |= p.kind == TOP
                new_bottom |= p.kind == BOTTOM
        if invalidated and new_top and new_bottom:
            invalidated = False
        state = RANGE if invalidated and _state(seq) != INSUFFICIENT else _state(seq)
        if state in TREND_STATES:
            if state == BULLISH:
                level = max(k for k, e in enumerate(seq) if e.pivot.kind == BOTTOM)
                price = bars.low[i] if wick_invalidation else bars.close[i]
                broken = price < seq[level].pivot.price
            else:
                level = max(k for k, e in enumerate(seq) if e.pivot.kind == TOP)
                price = bars.high[i] if wick_invalidation else bars.close[i]
                broken = price > seq[level].pivot.price
            if broken:
                invalidated, new_top, new_bottom = True, False, False
                state = RANGE
        leg = _leg(seq, state, tf)
        if not points or points[-1].state != state or points[-1].leg != leg:
            points.append(StructurePoint(at, state, leg))
    return points


_BEFORE_ANY = StructurePoint(datetime.min, INSUFFICIENT, None)


def state_at(timeline: Sequence[StructurePoint], tau: datetime) -> StructurePoint:
    """El punto vigente en τ (el último con `at <= τ`); antes del primero, historia insuficiente."""
    k = bisect_right([p.at for p in timeline], tau)
    return timeline[k - 1] if k else _BEFORE_ANY


def leg_intervals(timeline: Sequence[StructurePoint], until: Optional[datetime] = None) -> dict:
    """
    Para cada tramo (por su identidad), los intervalos [desde, hasta) en que fue el tramo vigente de la TF, junto con el
    estado de cada intervalo. `hasta` es None si sigue vigente al final de la serie (o en `until`).
    """
    out: dict = {}
    for k, p in enumerate(timeline):
        if until is not None and p.at > until:
            break
        if p.leg is None:
            continue
        end = timeline[k + 1].at if k + 1 < len(timeline) else None
        if end is not None and until is not None and end > until:
            end = None
        out.setdefault(p.leg.key, {"leg": p.leg, "intervals": []})["intervals"].append((p.at, end, p.state))
    return out
