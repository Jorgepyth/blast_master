"""
core/outcome_metrics.py — criterios de acierto de blast_master (spec 002, RF-5, RF-17, RF-21).

La definición en palabras vive en `docs/criterios-de-acierto.md` (decidida por el usuario el 2026-09-27, N35 a N37).
Este módulo es la única implementación: la usan el reporte (`tools/resolution_report.py`) y cualquier cuaderno de
`jupyter/` que mida acierto.

- **S4 estricto** (la cifra principal desde el 2026-10-03, N51): gana el análisis que tocó primero el objetivo dentro de
  las 48 h desde el ancla; todo lo demás con resultado conocido a 48 h pierde, también los toques tardíos y los que no
  tocaron nada en 48 h. No deja a nadie fuera.
- **S1**: de los análisis con primer toque, la proporción que tocó primero el objetivo, sin límite de tiempo dentro del
  horizonte.
- **S4**: lo mismo que S1, pero solo con los toques dentro de las 48 h desde el ancla; se informa cuántos quedan fuera.
- **Overlap**: otro análisis de la misma cuenta empezó después del ancla y antes del primer toque. Es solo una etiqueta:
  nunca saca a un análisis de S1 ni de S4 (N36).
- Los retroactivos quedan fuera de toda cifra por defecto, y se cuentan (R11, RF-17). Los 7 recuperados después del
  DROP del 2026-07-27 cuentan (N50): el servicio los marca como no retroactivos (`tools/auto_resolution.py`).

Funciones puras: reciben resultados ya calculados (`AnalysisOutcome`), sin DB ni velas.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

from config.auto_resolution import ANCHOR_FALLBACK_MIN, REASON_PENDING_CANDLES, S4_WINDOW_H
from core.candle_resolution import OUTCOME_CONFIRMED, OUTCOME_INVALIDATED, OUTCOME_OPEN

# El corte "direccionales" de los reportes: los análisis Bullish o Bearish, sin los "Choppy / Neutral".
DIRECTIONAL_BIASES = ("Bullish", "Bearish")

_TOUCHED = (OUTCOME_CONFIRMED, OUTCOME_INVALIDATED)


def analysis_anchor(analysis_start_time: Optional[datetime], created_at: datetime, is_backdated: bool) -> datetime:
    """
    El ancla de un análisis (N4, N5): `analysis_start_time` si existe; si no, `created_at` en un retroactivo (ya es la
    hora tipeada) y `created_at − ANCHOR_FALLBACK_MIN` minutos en los demás.
    """
    if analysis_start_time is not None:
        return analysis_start_time
    if is_backdated:
        return created_at
    return created_at - timedelta(minutes=ANCHOR_FALLBACK_MIN)


@dataclass(frozen=True)
class AnalysisOutcome:
    """
    Lo que las métricas necesitan de un análisis. `outcome` es el código del resolvedor (`confirmed`, `invalidated`,
    `open`, o un motivo). `touch_time` es la hora del primer toque cuando lo hubo. `overlap_limit` es hasta cuándo un
    análisis nuevo de la cuenta lo vuelve Overlap: el primer toque (o la vela ambigua) o, sin toque, el fin de la
    cobertura del banco. Si es `None`, no se sabe y no se etiqueta. `path_end` es hasta dónde se miraron las velas sin
    un toque (lo usa S4 estricto para saber si ya pasaron las 48 h).
    """
    analysis_id: str
    account: str
    anchor: datetime
    is_backdated: bool
    market_bias: Optional[str]
    outcome: str
    touch_time: Optional[datetime] = None
    overlap_limit: Optional[datetime] = None
    path_end: Optional[datetime] = None

    @property
    def hours_to_touch(self) -> Optional[float]:
        if self.touch_time is None:
            return None
        return (self.touch_time - self.anchor).total_seconds() / 3600


@dataclass(frozen=True)
class WinRate:
    """`wins` de `n`. `outside` son los toques que quedaron fuera de la ventana (solo S4); `excluded_backdated`, los
    retroactivos con resultado que se dejaron fuera (RF-17); `late`, las pérdidas de S4 estricto por no tocar el objetivo
    dentro de la ventana (tocaron algo después, o nada)."""
    wins: int
    n: int
    outside: int
    excluded_backdated: int
    late: int = 0

    @property
    def rate(self) -> Optional[float]:
        return self.wins / self.n if self.n else None


def _classify(
    outcomes: Sequence[AnalysisOutcome],
    window_h: Optional[float],
    include_backdated: bool,
    directional_only: bool,
):
    counted, outside, excluded = [], 0, 0
    for item in outcomes:
        if item.outcome not in _TOUCHED:
            continue
        if directional_only and item.market_bias not in DIRECTIONAL_BIASES:
            continue
        if item.is_backdated and not include_backdated:
            excluded += 1
            continue
        if window_h is not None and item.hours_to_touch > window_h:
            outside += 1
            continue
        counted.append(item)
    return counted, outside, excluded


def _win_rate(
    outcomes: Sequence[AnalysisOutcome],
    window_h: Optional[float],
    include_backdated: bool,
    directional_only: bool,
) -> WinRate:
    counted, outside, excluded = _classify(outcomes, window_h, include_backdated, directional_only)
    wins = sum(item.outcome == OUTCOME_CONFIRMED for item in counted)
    return WinRate(wins, len(counted), outside, excluded)


def counted_outcomes(outcomes: Sequence[AnalysisOutcome], window_h: Optional[float] = None,
                     include_backdated: bool = False, directional_only: bool = False,
                     strict: bool = False) -> List[AnalysisOutcome]:
    """Los análisis que entran en la cifra (S1 con `window_h=None`, S4 con 48, S4 estricto con `strict=True`): para
    medir otra cosa sobre los mismos, por ejemplo el win rate manual del reporte (RF-6)."""
    if strict:
        return _classify_strict(outcomes, S4_WINDOW_H if window_h is None else window_h, include_backdated,
                                directional_only)[0]
    return _classify(outcomes, window_h, include_backdated, directional_only)[0]


_WIN, _LOSS, _LATE = "win", "loss", "late"


def _strict_result(item: AnalysisOutcome, window_h: float) -> Optional[str]:
    """S4 estricto (N51): `win`, `loss`, `late` (pierde por no tocar el objetivo a tiempo) o `None` si no cuenta."""
    if item.outcome in _TOUCHED:
        if item.hours_to_touch <= window_h:
            return _WIN if item.outcome == OUTCOME_CONFIRMED else _LOSS
        return _LATE
    if item.outcome == OUTCOME_OPEN:
        return _LATE  # sin toque en todo el horizonte, que es mucho más largo que la ventana
    if (item.outcome == REASON_PENDING_CANDLES and item.path_end is not None
            and item.path_end - item.anchor >= timedelta(hours=window_h)):
        return _LATE  # sin toque todavía, pero las velas ya cubren la ventana entera
    return None  # ambiguo, sin velas suficientes o sin resultado (sin niveles, sin reloj, sin símbolo)


def _classify_strict(
    outcomes: Sequence[AnalysisOutcome],
    window_h: float,
    include_backdated: bool,
    directional_only: bool,
):
    counted, wins, late, excluded = [], 0, 0, 0
    for item in outcomes:
        if directional_only and item.market_bias not in DIRECTIONAL_BIASES:
            continue
        result = _strict_result(item, window_h)
        if result is None:
            continue
        if item.is_backdated and not include_backdated:
            excluded += 1
            continue
        counted.append(item)
        wins += result == _WIN
        late += result == _LATE
    return counted, wins, late, excluded


def s4_strict(outcomes: Sequence[AnalysisOutcome], include_backdated: bool = False, directional_only: bool = False,
              window_h: float = S4_WINDOW_H) -> WinRate:
    """
    S4 estricto (N51), la cifra principal: gana el que tocó primero el objetivo dentro de `window_h` horas del ancla.
    Pierde el que tocó primero la invalidación dentro de la ventana, y también (`late`) el que tocó algo después o no
    tocó nada con las velas cubriendo la ventana entera. No cuentan los ambiguos, los que todavía no tienen la ventana
    cubierta y los que no tienen resultado. A diferencia de S4, no deja a nadie fuera (`outside` es siempre 0).
    """
    counted, wins, late, excluded = _classify_strict(outcomes, window_h, include_backdated, directional_only)
    return WinRate(wins, len(counted), 0, excluded, late)


def s1(outcomes: Sequence[AnalysisOutcome], include_backdated: bool = False,
       directional_only: bool = False) -> WinRate:
    """S1 (N35): los análisis con primer toque, sin límite de tiempo. Los ambiguos, abiertos y pendientes no cuentan."""
    return _win_rate(outcomes, None, include_backdated, directional_only)


def s4(outcomes: Sequence[AnalysisOutcome], include_backdated: bool = False, directional_only: bool = False,
       window_h: float = S4_WINDOW_H) -> WinRate:
    """S4 (N35): como S1, pero solo los toques a `window_h` horas o menos del ancla; los demás van en `outside`."""
    return _win_rate(outcomes, window_h, include_backdated, directional_only)


@dataclass(frozen=True)
class OverlapLabel:
    """A es Overlap por B: el primer análisis de la cuenta que empezó después de A y antes de su límite (N11)."""
    analysis_id: str
    b_id: str
    b_anchor: datetime
    hours_a_to_b: float


def overlap_labels(outcomes: Sequence[AnalysisOutcome]) -> Dict[str, OverlapLabel]:
    """
    La etiqueta Overlap de cada análisis que la tenga (N11, N22, N36; plan.md §3.3). B es cualquier otro análisis de la
    misma cuenta, retroactivos incluidos (con su hora tipeada como ancla), con `ancla(A) < ancla(B) < límite(A)`; si hay
    varios, el primero. La etiqueta no cambia el primer toque ni las cifras de acierto.
    """
    by_account: Dict[str, list] = {}
    for item in outcomes:
        by_account.setdefault(item.account, []).append(item)
    labels: Dict[str, OverlapLabel] = {}
    for item in outcomes:
        if item.overlap_limit is None:
            continue
        later = [other for other in by_account[item.account]
                 if other.analysis_id != item.analysis_id and item.anchor < other.anchor < item.overlap_limit]
        if later:
            first = min(later, key=lambda other: other.anchor)
            hours = (first.anchor - item.anchor).total_seconds() / 3600
            labels[item.analysis_id] = OverlapLabel(item.analysis_id, first.analysis_id, first.anchor, hours)
    return labels
