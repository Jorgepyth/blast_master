"""
core/p0_fib.py — niveles de Fibonacci, zonas, camino y eventos de P0 sistemático.

Pre-registro `jupyter/p0_sistematico/preregistro.md` v1.1, §7 (D1, D2, D6, D7); RF-4.

- **Niveles (D1):** `L_r = B − r · (B − A)`; 0 = B (fin del impulso), 1 = A. P0: 0, 0.382, 0.5, 0.618, 1.
- **Placebo (D2):** 0.300, 0.441, 0.559 (retrocesos) y 0.10, 0.90 (extremos), emparejados en orden (T4).
- **Zona (D6):** `[L_r − δ·|B−A|, L_r + δ·|B−A|]`, δ = 0.025.
- **Camino (v1.1):** la escalera 1M→1H de `forward_path` donde cubre; donde no, la TF más fina del banco entre 4H, 12H,
  1D y 1W que cubre el instante y no es más gruesa que la del tramo, usando cada vela entera.
- **Evento (D6):** la primera entrada a la zona viniendo de afuera, mientras el tramo es el vigente y antes de que el
  precio supere B ("extendido"); máximo uno por nivel.
- **Resultado (D7):** barreras a `D = 0.10·|B−A|` desde `L_r`. Rechazo: se aleja D en sentido contrario a la llegada;
  ruptura: avanza D en el sentido de la llegada. En la vela de entrada solo cuenta la ruptura. Una vela que toca las
  dos: ambiguo. Sin barrera en 2160 h de velas recorridas desde la entrada: abierto; sin velas suficientes: pendiente.

Funciones puras, sin I/O.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, Mapping, Optional, Sequence

import numpy as np

from config.auto_resolution import MAX_HORIZON
from core.candle_resolution import LADDER, bank_coverage, forward_path

P0_LEVELS = (0.0, 0.382, 0.5, 0.618, 1.0)
PLACEBO_LEVELS = (0.10, 0.300, 0.441, 0.559, 0.90)
PLACEBO_OF = {0.382: 0.300, 0.5: 0.441, 0.618: 0.559, 0.0: 0.10, 1.0: 0.90}
RETRACEMENT_P0 = (0.382, 0.5, 0.618)
EXTREME_P0 = (0.0, 1.0)
RETRACEMENT_PLACEBO = (0.300, 0.441, 0.559)
EXTREME_PLACEBO = (0.10, 0.90)
# El "0" de cada juego (el nivel en B) para el puntaje de D8; los demás son "un retroceso o 1".
ZERO_LEVEL = {"p0": 0.0, "placebo": 0.10}

ZONE_DELTA = 0.025
BARRIER_FRACTION = 0.10
HORIZON_HOURS = float(MAX_HORIZON)  # 2160 velas de 1H = 2160 h de velas recorridas (v1.1)

REJECTION = "rejection"
RUPTURE = "rupture"
AMBIGUOUS = "ambiguous"
OPEN = "open"
PENDING = "pending"

TF_MINUTES = {"1M": 1, "5M": 5, "15M": 15, "30M": 30, "1H": 60, "4H": 240, "12H": 720, "1D": 1440, "1W": 10080}
TF_ORDER = ("1M", "5M", "15M", "30M", "1H", "4H", "12H", "1D", "1W")
COARSE_TFS = ("4H", "12H", "1D", "1W")


def level_price(a: float, b: float, r: float) -> float:
    return b - r * (b - a)


def zone(a: float, b: float, r: float, half_width: Optional[float] = None) -> tuple:
    center = level_price(a, b, r)
    hw = ZONE_DELTA * abs(b - a) if half_width is None else half_width
    return center - hw, center + hw


@dataclass(frozen=True)
class Path:
    """Velas del camino, contiguas y en orden: TF, apertura, cierre, máximo, mínimo y horas acumuladas."""
    tf: np.ndarray
    time: np.ndarray   # datetime64[ns]
    end: np.ndarray    # datetime64[ns]
    high: np.ndarray
    low: np.ndarray
    cum_hours: np.ndarray

    @classmethod
    def from_bars(cls, tfs, times, ends, highs, lows) -> "Path":
        time = np.asarray(times, dtype="datetime64[ns]")
        end = np.asarray(ends, dtype="datetime64[ns]")
        hours = (end - time) / np.timedelta64(1, "h")
        return cls(np.asarray(tfs, dtype=object), time, end, np.asarray(highs, float), np.asarray(lows, float),
                   np.cumsum(hours) if len(hours) else np.zeros(0))

    def __len__(self) -> int:
        return len(self.time)

    def first_at_or_after(self, t: datetime) -> int:
        return int(np.searchsorted(self.time, np.datetime64(t, "ns"), side="left"))

    def count_closed(self, tau: datetime) -> int:
        """Cuántas velas del camino cerraron en τ (`end <= τ`)."""
        return int(np.searchsorted(self.end, np.datetime64(tau, "ns"), side="right"))

    def when(self, i: int) -> datetime:
        return self.time[i].astype("datetime64[us]").astype(datetime)

    def until(self, tau: datetime) -> "Path":
        k = self.count_closed(tau)
        return Path(self.tf[:k], self.time[:k], self.end[:k], self.high[:k], self.low[:k], self.cum_hours[:k])


def _duration(tf: str) -> timedelta:
    return timedelta(minutes=TF_MINUTES[tf])


def ladder_path(bars_by_tf: Mapping[str, Sequence]) -> Path:
    """El camino de la escalera (1M→1H) desde su primera vela hasta donde llega, con `forward_path`."""
    ladder = {tf: bars_by_tf[tf] for tf in LADDER if bars_by_tf.get(tf)}
    if not ladder:
        return Path.from_bars([], [], [], [], [])
    start = min(min(bar.time for bar in bars) for bars in ladder.values())
    rows = [(pb.timeframe, pb.time, pb.end, pb.high, pb.low) for pb in forward_path(start, ladder)]
    return Path.from_bars(*zip(*rows)) if rows else Path.from_bars([], [], [], [], [])


def build_event_path(bars_by_tf: Mapping[str, Sequence], max_tf: str, ladder: Optional[Path] = None) -> Path:
    """
    El camino de los eventos de un tramo de la TF `max_tf` (v1.1): la escalera donde cubre el instante (el tramo de
    `forward_path` desde ese instante, que es un sufijo del camino de la escalera) y, donde no, la siguiente vela entera
    de la TF más fina entre 4H, 12H, 1D y 1W que cubre el instante y no es más gruesa que `max_tf`.
    """
    if TF_ORDER.index(max_tf) < TF_ORDER.index("1H"):
        raise ValueError(f"max_tf must be 1H or coarser, got {max_tf}")
    ladder = ladder_path(bars_by_tf) if ladder is None else ladder
    lad_cov = bank_coverage({tf: bars_by_tf[tf] for tf in LADDER if bars_by_tf.get(tf)})
    coarse = [tf for tf in COARSE_TFS if TF_ORDER.index(tf) <= TF_ORDER.index(max_tf) and bars_by_tf.get(tf)]
    series = {tf: sorted(bars_by_tf[tf], key=lambda bar: bar.time) for tf in coarse}
    opens = {tf: [bar.time for bar in series[tf]] for tf in coarse}
    cov = {tf: (opens[tf][0], opens[tf][-1] + _duration(tf)) for tf in coarse}

    starts = [opens[tf][0] for tf in coarse]
    if len(ladder):
        starts.append(ladder.when(0))
    if not starts:
        return Path.from_bars([], [], [], [], [])
    t = min(starts)
    tfs, times, ends, highs, lows = [], [], [], [], []
    while True:
        if len(ladder) and lad_cov.covering(t):
            i = ladder.first_at_or_after(t)
            if i >= len(ladder):
                break
            tfs.extend(ladder.tf[i:])
            times.extend(ladder.time[i:])
            ends.extend(ladder.end[i:])
            highs.extend(ladder.high[i:])
            lows.extend(ladder.low[i:])
            t = ladder.end[-1].astype("datetime64[us]").astype(datetime)
            continue
        tf = next((tf for tf in coarse if cov[tf][0] <= t < cov[tf][1]), None)
        if tf is None:
            break
        k = bisect_left(opens[tf], t)
        if k >= len(opens[tf]):
            break
        bar = series[tf][k]
        end = bar.time + _duration(tf)
        tfs.append(tf)
        times.append(np.datetime64(bar.time, "ns"))
        ends.append(np.datetime64(end, "ns"))
        highs.append(bar.high)
        lows.append(bar.low)
        t = end
    return Path.from_bars(tfs, times, ends, highs, lows)


def _first_true(mask: Callable[[int, int], np.ndarray], lo: int, hi: int, chunk: int = 2048) -> Optional[int]:
    """Primer índice en [lo, hi) donde `mask` es verdadera, buscando por bloques crecientes."""
    i, size = lo, chunk
    while i < hi:
        j = min(hi, i + size)
        m = mask(i, j)
        if m.size:
            k = int(np.argmax(m))
            if m[k]:
                return i + k
        i, size = j, size * 4
    return None


@dataclass(frozen=True)
class LevelEvent:
    """Un evento (D6, D7). `arrival` es +1 si llegó desde arriba y −1 si llegó desde abajo."""
    r: float
    price: float
    entry_index: int
    entry_time: datetime
    entry_tf: str
    arrival: int
    outcome: str
    resolved_index: Optional[int]
    resolved_at: Optional[datetime]
    coarse: bool


def detect_events(
    path: Path,
    a: float,
    b: float,
    levels: Sequence[float],
    ref_close: float,
    start_time: datetime,
    stop_time: Optional[datetime] = None,
    tau: Optional[datetime] = None,
    ext_from_time: Optional[datetime] = None,
    half_width: Optional[Mapping[float, float]] = None,
    barrier: Optional[float] = None,
    horizon_hours: float = HORIZON_HOURS,
    coarse_tfs: Sequence[str] = COARSE_TFS,
    rupture_barrier: Optional[float] = None,
) -> Dict[float, Optional[LevelEvent]]:
    """
    Los eventos de un tramo A→B en un intervalo en que fue el vigente (D6, D7).

    - `start_time`: inicio del intervalo (la confirmación de B o el cambio de estado). Solo velas que abren ahí o
      después.
    - `ref_close`: el cierre de la vela de la TF del tramo que cerró en `start_time`; da el lado inicial.
    - `stop_time`: fin del intervalo; una entrada cuenta si su vela abre antes.
    - `tau`: corte para el puntaje (solo velas cerradas en τ). Un resultado no conocido en τ queda `pending`.
    - `ext_from_time`: desde cuándo se mira si el precio superó B (la confirmación de B); por defecto, `start_time`.
    - `half_width`: semiancho de zona por nivel (sensibilidad ATR14); por defecto δ·|B−A|.
    - `barrier`: distancia de la barrera de rechazo (y de ruptura, si no se da `rupture_barrier`); por defecto
      0.10·|B−A|.
    - `rupture_barrier`: distancia de la barrera de ruptura, si se quiere distinta de la de rechazo (exploratorio,
      pedido del usuario del 2026-10-08). Por defecto, la misma que `barrier`: el pre-registro no cambia.
    """
    up = b > a
    size = abs(b - a)
    d = BARRIER_FRACTION * size if barrier is None else barrier
    d_rupture = d if rupture_barrier is None else rupture_barrier
    n = len(path)
    limit = n if tau is None else path.count_closed(tau)
    i0 = path.first_at_or_after(start_time)
    stop = limit if stop_time is None else min(limit, path.first_at_or_after(stop_time))
    e0 = path.first_at_or_after(ext_from_time or start_time)
    if up:
        ext = _first_true(lambda i, j: path.high[i:j] > b, e0, limit)
    else:
        ext = _first_true(lambda i, j: path.low[i:j] < b, e0, limit)
    entry_hi = stop if ext is None else min(stop, ext + 1)

    out: Dict[float, Optional[LevelEvent]] = {}
    for r in levels:
        center = level_price(a, b, r)
        hw = ZONE_DELTA * size if half_width is None else half_width[r]
        lo, hi = center - hw, center + hw
        out[r] = None
        if i0 >= entry_hi:
            continue
        if lo <= ref_close <= hi:
            k = _first_true(lambda i, j: (path.low[i:j] > hi) | (path.high[i:j] < lo), i0, entry_hi)
            if k is None:
                continue
            side, search = (1 if path.low[k] > hi else -1), k + 1
        else:
            side, search = (1 if ref_close > hi else -1), i0
        e = _first_true(lambda i, j: (path.low[i:j] <= hi) & (path.high[i:j] >= lo), search, entry_hi)
        if e is None:
            continue
        if e > search:
            side = 1 if path.low[e - 1] > hi else -1
        out[r] = _resolve(path, r, center, e, side, d, limit, horizon_hours, coarse_tfs, d_rupture)
    return out


def _resolve(path: Path, r: float, center: float, e: int, side: int, d: float, limit: int, horizon_hours: float,
             coarse_tfs: Sequence[str], d_rupture: Optional[float] = None) -> LevelEvent:
    rejection, rupture = center + side * d, center - side * (d if d_rupture is None else d_rupture)

    def hit_rejection(i, j):
        return path.high[i:j] >= rejection if side == 1 else path.low[i:j] <= rejection

    def hit_rupture(i, j):
        return path.low[i:j] <= rupture if side == 1 else path.high[i:j] >= rupture

    entry_time = path.when(e)
    base = path.cum_hours[e - 1] if e > 0 else 0.0
    h_end = int(np.searchsorted(path.cum_hours, base + horizon_hours, side="left"))  # última vela del horizonte
    outcome, resolved = None, None
    if e < limit and hit_rupture(e, e + 1)[0]:
        outcome, resolved = RUPTURE, e
    else:
        hi = min(limit, h_end + 1)
        j = _first_true(lambda i, k: hit_rejection(i, k) | hit_rupture(i, k), e + 1, hi)
        if j is not None:
            rej, rup = hit_rejection(j, j + 1)[0], hit_rupture(j, j + 1)[0]
            outcome = AMBIGUOUS if (rej and rup) else (REJECTION if rej else RUPTURE)
            resolved = j
        elif h_end < limit:
            outcome = OPEN
        else:
            outcome = PENDING
    last = resolved if resolved is not None else min(h_end, limit - 1)
    coarse = bool(np.isin(path.tf[e:last + 1], list(coarse_tfs)).any()) if last >= e else bool(
        path.tf[e] in coarse_tfs)
    return LevelEvent(r, center, e, entry_time, str(path.tf[e]), side, outcome, resolved,
                      path.when(resolved) if resolved is not None else None, coarse)
