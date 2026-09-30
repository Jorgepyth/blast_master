"""
core/candle_resolution.py — resolvedor de análisis con velas (spec 002, RF-4 a RF-10).

Funciones puras, sin DB ni disco (plan.md decisión T5): reciben las velas del banco ya leídas, por TF, y
devuelven propuestas o un código de motivo de `config.auto_resolution`. Leer el banco y la DB le toca a
`tools/auto_resolution.py`.

Se construye por partes (tasks.md, Etapa 3):
  - parte 1 (T24): cobertura del banco por TF de la escalera, y los códigos `no_history` y
    `pending_candles` (RF-4c, RF-4g, N28; plan.md §3.1, casos límite);
  - partes 2 a 5 (T25 a T28): el camino de varias TF, el primer toque, MAE/MFE, R y las reglas de
    Structural Resolution.

Convenciones: `time` es la hora de APERTURA de la vela en GT naive, igual que los CSV del banco; una vela
de la TF `tf` abarca `[time, time + LADDER_MINUTES[tf])`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Mapping, Optional, Sequence

from config.auto_resolution import REASON_NO_HISTORY, REASON_PENDING_CANDLES
from core.p2_ground_truth import OhlcBar

# Escalera del camino hacia adelante, de la TF más fina a la más gruesa (plan.md §3.1, paso 1; N17, N18).
# 4H y las más lentas no están: son para los modelos de P2, no para la hora del toque.
LADDER = ("1M", "5M", "15M", "30M", "1H")

# Duración de cada TF de la escalera, en minutos. Es la misma tabla que `tools.p2_backtest.TIMEFRAME_MINUTES`
# (un test lo exige), repetida acá para que `core/` no dependa de `tools/`.
LADDER_MINUTES: Dict[str, int] = {"1M": 1, "5M": 5, "15M": 15, "30M": 30, "1H": 60}


@dataclass(frozen=True)
class TimeframeCoverage:
    """Tramo que cubre una TF del banco: de la apertura de su vela más vieja al cierre de la más nueva."""
    timeframe: str
    start: datetime
    end: datetime

    def covers(self, instant: datetime) -> bool:
        return self.start <= instant < self.end


@dataclass(frozen=True)
class BankCoverage:
    """Cobertura del banco de un símbolo en cada TF de la escalera que tiene velas."""
    by_timeframe: Dict[str, TimeframeCoverage]

    @property
    def start(self) -> Optional[datetime]:
        """La apertura de la vela más vieja del banco, en cualquier TF de la escalera."""
        return min((c.start for c in self.by_timeframe.values()), default=None)

    @property
    def end(self) -> Optional[datetime]:
        """El cierre de la vela más nueva del banco, en cualquier TF de la escalera."""
        return max((c.end for c in self.by_timeframe.values()), default=None)

    def covering(self, instant: datetime) -> List[str]:
        """Las TF cuyo tramo cubre `instant`, de la más fina a la más gruesa."""
        return [tf for tf in LADDER if tf in self.by_timeframe and self.by_timeframe[tf].covers(instant)]


def bank_coverage(bars_by_timeframe: Mapping[str, Sequence[OhlcBar]]) -> BankCoverage:
    """
    Cobertura de cada TF de la escalera presente en `bars_by_timeframe`. Las TF fuera de la escalera y las
    que no tienen velas no cuentan. No hace falta que las velas vengan ordenadas.
    """
    by_timeframe: Dict[str, TimeframeCoverage] = {}
    for tf in LADDER:
        bars = bars_by_timeframe.get(tf) or ()
        if not bars:
            continue
        times = [bar.time for bar in bars]
        last_close = max(times) + timedelta(minutes=LADDER_MINUTES[tf])
        by_timeframe[tf] = TimeframeCoverage(tf, start=min(times), end=last_close)
    return BankCoverage(by_timeframe)


def anchor_missing_reason(anchor: datetime, coverage: BankCoverage) -> Optional[str]:
    """
    ¿Se puede empezar a resolver desde `anchor`? (plan.md §3.1, casos límite; N28)

    - `no_history` (RF-4g): el ancla es anterior a la vela más vieja del banco en todas las TF de la
      escalera. No se arregla con otro export: MT5 ya no entrega esas velas.
    - `pending_candles` (RF-4c): el banco todavía no llega hasta el ancla (su última vela cierra en el ancla o
      antes), o no tiene velas de la escalera. Es temporal: se resuelve con el próximo export.
    - `None`: alguna TF cubre el ancla, o el ancla cae entre la vela más vieja y la más nueva del banco.
    """
    if coverage.start is None:
        return REASON_PENDING_CANDLES
    if anchor < coverage.start:
        return REASON_NO_HISTORY
    if anchor >= coverage.end:
        return REASON_PENDING_CANDLES
    return None


def untouched_missing_reason(path_end: datetime, horizon_end: Optional[datetime]) -> Optional[str]:
    """
    Para un recorrido que no tocó ningún nivel (RF-4c; baseline H12):

    - `pending_candles` si el camino se cortó antes del fin del horizonte, o si el banco todavía no tiene
      velas hasta el fin del horizonte (`horizon_end` es `None`). No es `Open`: el toque puede estar en velas
      que el banco todavía no tiene.
    - `None` si el camino llegó al fin del horizonte sin toque: el resultado es definitivo, y quien llama
      lo marca como `Open` (T26).

    `path_end` es hasta dónde llegó el camino (el cierre de su última vela); `horizon_end`, la apertura de la
    vela 1H número `MAX_HORIZON` contada desde el ancla (plan.md §3.1, paso 6).
    """
    if horizon_end is None or path_end < horizon_end:
        return REASON_PENDING_CANDLES
    return None
