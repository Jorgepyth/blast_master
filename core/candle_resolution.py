"""
core/candle_resolution.py — resolvedor de análisis con velas (spec 002, RF-4 a RF-10).

Funciones puras, sin DB ni disco (plan.md decisión T5): reciben las velas del banco ya leídas, por TF, y
devuelven propuestas o un código de motivo de `config.auto_resolution`. Leer el banco y la DB le toca a
`tools/auto_resolution.py`.

Se construye por partes (tasks.md, Etapa 3):
  - parte 1 (T24): cobertura del banco por TF de la escalera, y los códigos `no_history` y
    `pending_candles` (RF-4c, RF-4g, N28; plan.md §3.1, casos límite);
  - parte 2 (T25): el camino hacia adelante de varias TF, `forward_path` (RF-4, N17, N18; plan.md §3.1);
  - parte 3 (T26): el primer toque sobre ese camino, `ambiguous`, el horizonte y `open`, `resolve_first_touch`
    (RF-4, RF-4b, RF-5, N19; plan.md §3.2);
  - parte 4 (T27): precio de partida, dirección de la tesis, `no_levels`, R y MAE/MFE estructurales, todo junto en
    `resolve_analysis` (RF-4, RF-4d; plan.md §3.4);
  - parte 5 (T28): las reglas de Structural Resolution y Failure Reason.

Convenciones: `time` es la hora de APERTURA de la vela en GT naive, igual que los CSV del banco; una vela
de la TF `tf` abarca `[time, time + LADDER_MINUTES[tf])`.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterator, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from config.auto_resolution import (
    MAX_HORIZON,
    REASON_AMBIGUOUS,
    REASON_NO_HISTORY,
    REASON_NO_LEVELS,
    REASON_PENDING_CANDLES,
)
from core.p2_ground_truth import LEVEL_VALIDATION, OhlcBar, first_touch_detail, infer_thesis_direction

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


class PathBar(NamedTuple):
    """Una vela del camino hacia adelante. `timeframe` da la precisión de la hora de un toque en ella (N19)."""
    timeframe: str
    time: datetime  # apertura
    end: datetime   # cierre: apertura + duración de la TF
    high: float
    low: float


def forward_path(anchor: datetime, bars_by_timeframe: Mapping[str, Sequence[OhlcBar]]) -> Iterator[PathBar]:
    """
    Las velas del banco desde `anchor` hacia adelante, en orden, sin huecos ni superposición (RF-4, N17, N18;
    plan.md §3.1). Es un generador: quien busca el primer toque corta ahí, sin armar el horizonte entero. El camino
    llega hasta el cierre de su última vela; si no hay ninguna (el ancla es anterior al banco o el banco todavía no
    llega), está vacío, y el motivo lo da `anchor_missing_reason`.

    En cada instante `t` (el ancla, y después el cierre de la vela anterior):
      1. La TF base es la más fina cuyo tramo cubre `t`. Si ninguna lo cubre, el camino termina.
      2. La siguiente vela es la que abre primero en `t` o después, entre la TF base y las más finas que ella. Si dos
         abren a la vez, gana la más fina. Por eso:
         - el camino arranca en la primera vela de 1M que abre en el ancla o después, y la que contiene el ancla queda
           fuera (N17). Sin 1M en esa fecha, arranca con la siguiente más fina;
         - se sube a una TF más fina en el borde de la vela de la más gruesa (N18), porque `t` es ese borde;
         - una TF más fina que todavía no cubre `t` pero abre antes que la próxima vela de la base se usa desde ahí,
           para no dejar ese tramo sin mirar (aclaración de T25 al paso 3 del plan).
      3. Se baja a una TF más gruesa (porque la actual se terminó en `t`) solo si `t` cae en el borde de su vela. Si su
         vela ya había empezado, usarla repetiría un tramo y saltarla dejaría un hueco: el camino se corta en `t`
         (plan.md §3.1, paso 5; el resultado será `pending_candles`).

    Un tramo en el que ninguna TF tiene velas (mercado cerrado) se cruza sin cambiar nada. Límite conocido: un hueco
    de datos dentro del tramo de una TF (velas que faltan en el banco aunque hubo mercado) se ve igual que un mercado
    cerrado, y también se cruza.
    """
    series = {tf: sorted(bars_by_timeframe[tf], key=lambda bar: bar.time)
              for tf in LADDER if bars_by_timeframe.get(tf)}
    opens = {tf: [bar.time for bar in bars] for tf, bars in series.items()}
    coverage = bank_coverage(series)

    def duration(tf: str) -> timedelta:
        return timedelta(minutes=LADDER_MINUTES[tf])

    t, current = anchor, None
    while True:
        covering = coverage.covering(t)
        if not covering:
            return
        base = covering[0]
        if current is not None and LADDER.index(base) > LADDER.index(current):
            j = bisect_right(opens[base], t) - 1
            if j >= 0 and opens[base][j] < t < opens[base][j] + duration(base):
                return
        best = None
        for tf in LADDER[: LADDER.index(base) + 1]:  # de la más fina a la base: en un empate gana la más fina
            if tf not in opens:
                continue
            i = bisect_left(opens[tf], t)
            if i < len(opens[tf]) and (best is None or opens[tf][i] < best[0]):
                best = (opens[tf][i], tf, i)
        if best is None:
            return
        _, tf, i = best
        bar = series[tf][i]
        end = bar.time + duration(tf)
        yield PathBar(tf, bar.time, end, bar.high, bar.low)
        t, current = end, tf


# Resultado del primer toque (RF-5). Son códigos neutros: pasarlos a los valores de `ResolutionType` del wizard
# ("Confirmed (A equal to B)", etc.) les toca a `tools/auto_resolution.py` y al pre-llenado (T30, T40).
OUTCOME_CONFIRMED = "confirmed"      # RF-5, punto 2: el primer nivel tocado es edge_validation_price
OUTCOME_INVALIDATED = "invalidated"  # RF-5, punto 3: el primer nivel tocado es structural_invalidation
OUTCOME_OPEN = "open"                # RF-5, punto 4: ningún toque en el horizonte, con el banco cubriéndolo entero


@dataclass(frozen=True)
class FirstTouch:
    """
    Resultado de `resolve_first_touch` (RF-4, RF-4b, RF-5, N19). `outcome` es `OUTCOME_CONFIRMED`,
    `OUTCOME_INVALIDATED`, `OUTCOME_OPEN`, o un código de motivo: `ambiguous`, `pending_candles` o `no_history`.
    Solo un toque tiene hora: `touch_time` es la apertura de la vela que tocó y `timeframe` su TF, que da la precisión
    (N19: ±1 min con 1M). `direction` y `level` son los de `first_touch_detail`. `path_end` dice hasta dónde se miró, y
    `horizon_end` dónde termina el horizonte (`None` si el banco todavía no llega).
    """
    outcome: str
    touch_time: Optional[datetime] = None
    timeframe: Optional[str] = None
    direction: Optional[str] = None
    level: Optional[str] = None
    path_end: Optional[datetime] = None
    horizon_end: Optional[datetime] = None


def horizon_end(anchor: datetime, one_hour_bars: Sequence[OhlcBar], max_horizon: int = MAX_HORIZON) -> Optional[datetime]:
    """
    Fin del horizonte (plan.md §3.1, paso 6, aclarado en T26): el cierre de la vela 1H número `max_horizon` que abre
    en el ancla o después. Así se miran `max_horizon` velas de 1H, igual que el backtest
    (`tools/p2_backtest.py:FORWARD_PATH_MAX_BARS`), y la vela de 1H que contiene el ancla no cuenta, igual que en el
    camino (N17). `None` si el banco todavía no tiene tantas.
    """
    opens = sorted(bar.time for bar in one_hour_bars if bar.time >= anchor)
    if len(opens) < max_horizon:
        return None
    return opens[max_horizon - 1] + timedelta(minutes=LADDER_MINUTES["1H"])


def resolve_first_touch(
    anchor: datetime,
    bars_by_timeframe: Mapping[str, Sequence[OhlcBar]],
    thesis_direction: str,
    edge_validation_price: float,
    structural_invalidation: float,
    max_horizon: int = MAX_HORIZON,
) -> FirstTouch:
    """
    Qué nivel toca primero el precio desde el ancla (RF-4, RF-4b, RF-5; plan.md §3.2), recorriendo `forward_path` vela
    por vela con la regla de toque de `first_touch_detail`, hasta el primer toque o hasta el fin del horizonte:

    - el ancla fuera del banco → `no_history` o `pending_candles` (`anchor_missing_reason`);
    - una vela que toca un solo nivel → `confirmed` o `invalidated`, con la apertura de esa vela como hora del toque
      y su TF como precisión (N19);
    - una vela que toca los dos → `ambiguous`, sin hora (RF-4b). El camino ya va en la TF más fina que tiene el banco
      para ese tramo (T25), así que no queda una TF más fina con la que mirar adentro: donde hay 1M, el camino ya va en
      1M (aclaración de T26 a los pasos 2 y 3 del plan);
    - ningún toque: `open` si el camino llegó al fin del horizonte, o `pending_candles` si el banco todavía no llega
      (`untouched_missing_reason`).

    `thesis_direction` es obligatoria: si los niveles caen del mismo lado del precio de partida, el análisis es
    `no_levels` y eso se decide antes de llamar (T27).
    """
    if thesis_direction is None:
        raise ValueError("thesis_direction is required: same-side levels are no_levels, decided before (T27)")
    missing = anchor_missing_reason(anchor, bank_coverage(bars_by_timeframe))
    if missing:
        return FirstTouch(missing)

    end = horizon_end(anchor, bars_by_timeframe.get("1H") or (), max_horizon)
    path_end = anchor
    for bar in forward_path(anchor, bars_by_timeframe):
        if end is not None and bar.time >= end:
            path_end = end
            break
        path_end = bar.end
        # first_touch_detail no usa el precio de entrada (su tercer argumento): se pasa 0.0.
        detail = first_touch_detail(thesis_direction, (bar,), 0.0, edge_validation_price, structural_invalidation)
        if detail.bar_index is None:
            continue
        if detail.ambiguous:
            return FirstTouch(REASON_AMBIGUOUS, path_end=path_end, horizon_end=end)
        outcome = OUTCOME_CONFIRMED if detail.level == LEVEL_VALIDATION else OUTCOME_INVALIDATED
        return FirstTouch(outcome, touch_time=bar.time, timeframe=bar.timeframe, direction=detail.direction,
                          level=detail.level, path_end=path_end, horizon_end=end)
    return FirstTouch(untouched_missing_reason(path_end, end) or OUTCOME_OPEN, path_end=path_end, horizon_end=end)


class Candle(NamedTuple):
    """
    Una vela del banco con apertura y cierre. `resolve_analysis` la necesita porque el precio de partida es un cierre
    (RF-4); el camino y el toque solo leen `time`, `high` y `low`, así que también sirven con ella.
    """
    time: datetime
    open: float
    high: float
    low: float
    close: float


class StartPrice(NamedTuple):
    """El precio de partida (RF-4): el cierre de una vela, de qué TF es y a qué hora cerró."""
    price: float
    timeframe: str
    close_time: datetime


def start_price(anchor: datetime, candles_by_timeframe: Mapping[str, Sequence[Candle]]) -> Optional[StartPrice]:
    """
    RF-4: el cierre de la última vela ya cerrada en el ancla (`time + duración <= ancla`, R3). Entre las TF de la
    escalera se toma la vela cerrada más reciente y, si dos cierran a la vez, la de la TF más fina: casi siempre es la
    de 1M. Si una TF más gruesa cerró después (por ejemplo, porque el 1M todavía no empezó), vale esa (aclaración de
    T27). `None` si en el ancla todavía no cerró ninguna vela.
    """
    best: Optional[StartPrice] = None
    for tf in LADDER:  # de la más fina a la más gruesa: en un empate queda la más fina
        duration = timedelta(minutes=LADDER_MINUTES[tf])
        closed = [candle for candle in candles_by_timeframe.get(tf) or () if candle.time + duration <= anchor]
        if not closed:
            continue
        last = max(closed, key=lambda candle: candle.time)
        if best is None or last.time + duration > best.close_time:
            best = StartPrice(last.close, tf, last.time + duration)
    return best


@dataclass(frozen=True)
class AnalysisResolution:
    """
    Lo que el resolvedor sabe de un análisis (RF-4, RF-4d, RF-5). `outcome` es el de `first_touch`, o un código de
    motivo cuando no se llegó a buscar el toque (`no_levels`, `no_history`, `pending_candles`). `r` es
    |precio de partida − structural_invalidation|. `structural_mae` y `structural_mfe` son precios: el más adverso y
    el más favorable a la tesis desde el ancla hasta la vela del toque inclusive (plan.md §3.4); solo existen si hubo
    toque.
    """
    outcome: str
    start_price: Optional[float] = None
    thesis_direction: Optional[str] = None
    r: Optional[float] = None
    first_touch: Optional[FirstTouch] = None
    structural_mae: Optional[float] = None
    structural_mfe: Optional[float] = None


def resolve_analysis(
    anchor: datetime,
    candles_by_timeframe: Mapping[str, Sequence[Candle]],
    edge_validation_price: Optional[float],
    structural_invalidation: Optional[float],
    max_horizon: int = MAX_HORIZON,
) -> AnalysisResolution:
    """
    Resuelve un análisis con las velas del banco (RF-4, RF-4d):
      1. sin objetivo o sin invalidación → `no_levels`;
      2. el ancla fuera del banco → `no_history` o `pending_candles`;
      3. precio de partida (`start_price`); si ninguna vela cerró todavía en el ancla → `no_history`;
      4. dirección de la tesis con `infer_thesis_direction`; si los dos niveles caen del mismo lado del precio de
         partida (o uno justo en él) → `no_levels`;
      5. primer toque (`resolve_first_touch`) y, si lo hubo, MAE y MFE estructurales: en un long, el `low` más bajo y
         el `high` más alto del camino hasta la vela del toque; en un short, al revés.
    """
    if edge_validation_price is None or structural_invalidation is None:
        return AnalysisResolution(REASON_NO_LEVELS)
    missing = anchor_missing_reason(anchor, bank_coverage(candles_by_timeframe))
    if missing:
        return AnalysisResolution(missing)
    start = start_price(anchor, candles_by_timeframe)
    if start is None:
        return AnalysisResolution(REASON_NO_HISTORY)
    thesis = infer_thesis_direction(start.price, edge_validation_price, structural_invalidation)
    if thesis is None:
        return AnalysisResolution(REASON_NO_LEVELS, start_price=start.price)

    touch = resolve_first_touch(anchor, candles_by_timeframe, thesis, edge_validation_price, structural_invalidation,
                                max_horizon=max_horizon)
    mae = mfe = None
    if touch.outcome in (OUTCOME_CONFIRMED, OUTCOME_INVALIDATED):
        low, high = _extremes_through_touch(anchor, candles_by_timeframe, touch)
        mae, mfe = (low, high) if thesis == "long" else (high, low)
    return AnalysisResolution(touch.outcome, start.price, thesis, abs(start.price - structural_invalidation), touch,
                              mae, mfe)


def _extremes_through_touch(
    anchor: datetime, candles_by_timeframe: Mapping[str, Sequence[Candle]], touch: FirstTouch
) -> Tuple[float, float]:
    """El `low` más bajo y el `high` más alto del camino, desde el ancla hasta la vela del toque inclusive."""
    low, high = float("inf"), float("-inf")
    for bar in forward_path(anchor, candles_by_timeframe):
        low, high = min(low, bar.low), max(high, bar.high)
        if bar.time == touch.touch_time and bar.timeframe == touch.timeframe:
            break
    return low, high
