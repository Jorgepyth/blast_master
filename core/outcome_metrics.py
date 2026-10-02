"""
core/outcome_metrics.py — criterios de acierto de blast_master (spec 002, RF-5, RF-17, RF-21).

La definición en palabras vive en `docs/criterios-de-acierto.md` (decidida por el usuario el 2026-09-27, N35 a N37).
Este módulo es la única implementación: la usan el reporte (`tools/resolution_report.py`) y cualquier cuaderno de
`jupyter/` que mida acierto.

- **S1** (principal): de los análisis con primer toque, la proporción que tocó primero el objetivo, sin límite de
  tiempo dentro del horizonte.
- **S4** (secundario): lo mismo, pero solo con los toques dentro de las 48 h desde el ancla; se informa cuántos quedan
  fuera.
- **Overlap**: otro análisis de la misma cuenta empezó después del ancla y antes del primer toque. Es solo una etiqueta:
  nunca saca a un análisis de S1 ni de S4 (N36).
- Los retroactivos quedan fuera de toda cifra por defecto, y se cuentan (R11, RF-17).

Funciones puras: reciben resultados ya calculados (`AnalysisOutcome`), sin DB ni velas.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence

from config.auto_resolution import ANCHOR_FALLBACK_MIN, S4_WINDOW_H
from core.candle_resolution import OUTCOME_CONFIRMED, OUTCOME_INVALIDATED

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
    cobertura del banco. Si es `None`, no se sabe y no se etiqueta.
    """
    analysis_id: str
    account: str
    anchor: datetime
    is_backdated: bool
    market_bias: Optional[str]
    outcome: str
    touch_time: Optional[datetime] = None
    overlap_limit: Optional[datetime] = None

    @property
    def hours_to_touch(self) -> Optional[float]:
        if self.touch_time is None:
            return None
        return (self.touch_time - self.anchor).total_seconds() / 3600


@dataclass(frozen=True)
class WinRate:
    """`wins` de `n`. `outside` son los toques que quedaron fuera de la ventana (solo S4); `excluded_backdated`, los
    retroactivos con toque que se dejaron fuera (RF-17)."""
    wins: int
    n: int
    outside: int
    excluded_backdated: int

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
                     include_backdated: bool = False, directional_only: bool = False) -> List[AnalysisOutcome]:
    """Los análisis que entran en la cifra (S1 con `window_h=None`, S4 con 48): para medir otra cosa sobre los mismos,
    por ejemplo el win rate manual del reporte (RF-6)."""
    return _classify(outcomes, window_h, include_backdated, directional_only)[0]


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
