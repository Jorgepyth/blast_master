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
  - parte 5 (T28): las reglas de Structural Resolution y Failure Reason, `propose_structural` (RF-8 a RF-8d;
    plan.md §3.5);
  - el chequeo del Mark Price (T31): `check_mark_price` y `check_mark_price_in_period` (RF-3, RF-3b; plan.md §3.7).

Convenciones: `time` es la hora de APERTURA de la vela en GT naive, igual que los CSV del banco; una vela
de la TF `tf` abarca `[time, time + LADDER_MINUTES[tf])`.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Iterator, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from config.auto_resolution import (
    EXPANSION_H,
    EXPANSION_R,
    MARK_PRICE_TOLERANCE,
    MAX_HORIZON,
    REASON_AMBIGUOUS,
    REASON_NO_HISTORY,
    REASON_NO_LEVELS,
    REASON_PENDING_CANDLES,
    REVERT_H,
    SWEEP_H,
    SWEEP_R,
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
    # N46: el toque lo confirmó el operador en su gráfico (excepción); `closest_price` es el precio del banco más
    # cercano al nivel, en la vela que pasa a ser la del toque.
    exception: bool = False
    closest_price: Optional[float] = None


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
    touched_level: Optional[str] = None,
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

    `touched_level` (N46) es el nivel que el operador vio tocado en su gráfico aunque el banco no llegue
    (`LEVEL_VALIDATION` o `LEVEL_INVALIDATION`): ver `_excepted_touch`.
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
    if touched_level is not None and touch.level != touched_level:
        touch = _excepted_touch(anchor, candles_by_timeframe, thesis, edge_validation_price, structural_invalidation,
                                touch, touched_level)
    mae = mfe = None
    if touch.outcome in (OUTCOME_CONFIRMED, OUTCOME_INVALIDATED):
        low, high = _extremes_through_touch(anchor, candles_by_timeframe, touch)
        mae, mfe = (low, high) if thesis == "long" else (high, low)
    return AnalysisResolution(touch.outcome, start.price, thesis, abs(start.price - structural_invalidation), touch,
                              mae, mfe)


def _excepted_touch(
    anchor: datetime,
    candles_by_timeframe: Mapping[str, Sequence[Candle]],
    thesis: str,
    evp: float,
    si: float,
    touch: FirstTouch,
    touched_level: str,
) -> FirstTouch:
    """
    N46: el toque que confirmó el operador. Es la vela del camino que más se acerca a `touched_level` (en un long, el
    `high` más alto para la validación y el `low` más bajo para la invalidación; en un short, al revés), antes del
    primer toque real o, si no lo hubo, hasta donde llegó el camino. En un empate vale la primera. Sin velas en ese
    tramo, el toque queda como estaba.
    """
    limit = touch.touch_time or touch.path_end
    validation = touched_level == LEVEL_VALIDATION
    level = evp if validation else si
    upward = (thesis == "long") == validation  # el nivel queda por encima del precio
    best = None
    for bar in forward_path(anchor, candles_by_timeframe):
        if limit is None or bar.time >= limit:
            break
        price = bar.high if upward else bar.low
        if best is None or abs(level - price) < abs(level - best[1]):
            best = (bar, price)
    if best is None:
        return touch
    bar, price = best
    opposite = "short" if thesis == "long" else "long"
    return FirstTouch(OUTCOME_CONFIRMED if validation else OUTCOME_INVALIDATED, touch_time=bar.time,
                      timeframe=bar.timeframe, direction=thesis if validation else opposite, level=touched_level,
                      path_end=bar.end, horizon_end=touch.horizon_end, exception=True, closest_price=price)


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


# Propuestas de Structural Resolution y Failure Reason (RF-8 a RF-8d). Son códigos neutros, como OUTCOME_*: pasarlos a
# los valores de `StructuralResolution` y `FailureReason` del wizard les toca a T30 y T40.
STRUCTURAL_REVERTED = "reverted"             # "Confirmed pero inmediatamente revertido" (N13)
STRUCTURAL_EXPANSION = "expansion"           # "Confirmed + expansión significativa" (N14, N20)
STRUCTURAL_MINIMAL = "minimal"               # "Confirmed pero mínima"
STRUCTURAL_NA = "n/a"                        # en Invalidated (RF-8b)
FAILURE_NA = "n/a"                           # en los tres Confirmed (RF-8)
FAILURE_LIQUIDITY_SWEEP = "liquidity_sweep"  # un Invalidated que barrió la invalidación (N15, N21)


@dataclass(frozen=True)
class StructuralProposal:
    """
    Propuesta de `Structural Resolution` y `Failure Reason` (RF-8 a RF-8d). Un campo en `None` no se propone.
    `missing_reason` es `pending_candles` cuando hubo toque pero el banco todavía no cubre la ventana que hace falta
    para decidir: 24 h después del toque del objetivo, o 48 h después del de la invalidación (aclaración de T28).
    """
    structural_resolution: Optional[str] = None
    failure_reason: Optional[str] = None
    missing_reason: Optional[str] = None


def propose_structural(
    resolution: AnalysisResolution,
    candles_by_timeframe: Mapping[str, Sequence[Candle]],
    edge_validation_price: float,
    structural_invalidation: float,
    mark_price: Optional[float],
) -> StructuralProposal:
    """
    RF-8 a RF-8d (plan.md §3.5). Las ventanas se recorren con el mismo camino de T25, desde la vela del toque:
    - Confirmed (RF-8): gana lo que pasa primero, recorriendo vela por vela desde la del toque (N45):
      1. `reverted`: después de la vela del toque y antes de `REVERT_H` horas desde ella, el precio vuelve al Mark
         Price (en un long, `low <= mark_price`). Sin Mark Price, al precio de partida (RF-8d). La vela del toque no
         cuenta: su extremo pudo ser anterior al toque. Si la vuelta y la expansión caen en la misma vela, gana
         `reverted`, porque no se sabe el orden;
      2. `expansion`: el precio va `EXPANSION_R` R o más allá del objetivo, desde la vela del toque (incluida) hasta lo
         primero que ocurra: `EXPANSION_H` horas o el toque de la invalidación, cuya vela no cuenta (N14, N20);
      3. `minimal`, si no pasa ninguna de las dos. Las tres con Failure Reason `n/a`. Lo que ya pasó es definitivo, así
         que solo falta (`pending_candles`) si el banco no cubre las ventanas y todavía no pasó nada;
    - Invalidated (RF-8b) → `n/a`, y `liquidity_sweep` si el precio toca el objetivo antes de `SWEEP_H` horas desde
      el toque de la invalidación, y lo más lejos que fue más allá de la invalidación en ese tramo, contando la vela
      del objetivo, es `SWEEP_R` R o menos (N15, N21). Si no, no se propone Failure Reason;
    - sin toque (open, ambiguous, pending, no_levels...) → no se propone nada.

    La etiqueta Overlap no cambia nada de esto (N52): un análisis con Overlap se propone según su toque.
    """
    if resolution.outcome == OUTCOME_CONFIRMED:
        reference = mark_price if mark_price is not None else resolution.start_price
        return _confirmed_proposal(resolution, candles_by_timeframe, edge_validation_price, structural_invalidation,
                                   reference)
    if resolution.outcome == OUTCOME_INVALIDATED:
        return _invalidated_proposal(resolution, candles_by_timeframe, edge_validation_price, structural_invalidation)
    return StructuralProposal()


def _confirmed_proposal(
    resolution: AnalysisResolution,
    candles_by_timeframe: Mapping[str, Sequence[Candle]],
    evp: float,
    si: float,
    reference: float,
) -> StructuralProposal:
    long = resolution.thesis_direction == "long"
    touch = resolution.first_touch
    revert_end = touch.touch_time + timedelta(hours=REVERT_H)
    expansion_end = touch.touch_time + timedelta(hours=EXPANSION_H)
    window_end = max(revert_end, expansion_end)  # hoy las dos son de 24 h, pero son reglas distintas (N13, N14)
    threshold = EXPANSION_R * resolution.r
    expanding, reached = True, touch.touch_time
    for bar in forward_path(touch.touch_time, candles_by_timeframe):
        if bar.time >= window_end:
            reached = window_end
            break
        reached = bar.end
        touch_candle = bar.time == touch.touch_time and bar.timeframe == touch.timeframe
        reverted = (bar.time < revert_end and not touch_candle
                    and (bar.low <= reference if long else bar.high >= reference))
        expanded = False
        if expanding and bar.time < expansion_end:
            if bar.low <= si if long else bar.high >= si:
                expanding = False
            else:
                expanded = (bar.high - evp if long else evp - bar.low) >= threshold
        # N45: gana lo que pasa primero. Si las dos cosas pasan en la misma vela no se sabe el orden: "revertido".
        if reverted:
            return StructuralProposal(STRUCTURAL_REVERTED, FAILURE_NA)
        if expanded:
            return StructuralProposal(STRUCTURAL_EXPANSION, FAILURE_NA)
    if reached < window_end:
        return StructuralProposal(missing_reason=REASON_PENDING_CANDLES)
    return StructuralProposal(STRUCTURAL_MINIMAL, FAILURE_NA)


def _invalidated_proposal(
    resolution: AnalysisResolution,
    candles_by_timeframe: Mapping[str, Sequence[Candle]],
    evp: float,
    si: float,
) -> StructuralProposal:
    long = resolution.thesis_direction == "long"
    touch = resolution.first_touch
    window_end = touch.touch_time + timedelta(hours=SWEEP_H)
    furthest, reached = si, touch.touch_time
    for bar in forward_path(touch.touch_time, candles_by_timeframe):
        if bar.time >= window_end:
            reached = window_end
            break
        reached = bar.end
        furthest = min(furthest, bar.low) if long else max(furthest, bar.high)
        if bar.high >= evp if long else bar.low <= evp:
            excess = si - furthest if long else furthest - si
            return StructuralProposal(STRUCTURAL_NA,
                                      FAILURE_LIQUIDITY_SWEEP if excess <= SWEEP_R * resolution.r else None)
    if reached < window_end:
        return StructuralProposal(STRUCTURAL_NA, None, REASON_PENDING_CANDLES)
    return StructuralProposal(STRUCTURAL_NA)


# --- Propuestas del Tactical Audit (T45, T46; RF-9, RF-10; N8, N12) -------------------------------------------------

# N8: el MAE/MFE táctico y `could_hit_tp` usan la TF más fina validada: 1M, si no 5M, si no 15M.
TRADE_LADDER = ("1M", "5M", "15M")
TRADE_EXCURSION_CAP = 10.0  # el máximo de los campos MAE y MFE del wizard (baseline §2.5)
REASON_NO_INTERVAL = "no_interval"  # RF-9b: la salida no es posterior a la entrada
REASON_ZERO_R = "zero_r"            # RF-9c: la entrada es igual al SL
REASON_INVALID_TP = "invalid_tp"    # RF-10d: el TP está del lado contrario de la tesis


@dataclass(frozen=True)
class TradeExcursion:
    """MAE y MFE de un trade en R (RF-9), con la TF usada; `reason` cuando no se proponen."""
    mae_r: Optional[float]
    mfe_r: Optional[float]
    timeframe: Optional[str]
    reason: Optional[str] = None


def _in_r(distance: float, r: float) -> float:
    return round(min(max(distance / r, 0.0), TRADE_EXCURSION_CAP), 2)


def trade_excursion(
    direction: str,
    entry_time: datetime,
    exit_time: datetime,
    entry_price: float,
    stop_loss: float,
    candles_by_timeframe: Mapping[str, Sequence[Candle]],
) -> TradeExcursion:
    """
    RF-9 (N8): el MAE y el MFE de una orden llenada, en R = |entrada − SL|. Usa la primera TF de 1M, 5M y 15M cuyas
    velas cubren todo el trade, y toma el máximo y el mínimo (mechas incluidas) de las velas que se solapan con
    [entrada, salida], también las de la entrada y la salida. Cada valor va de 0 a 10, a 2 decimales. Sin propuesta:
    `no_interval` (RF-9b), `zero_r` (RF-9c), y `no_history` o `pending_candles` si el banco no cubre el trade (RF-9d).
    """
    if exit_time <= entry_time:
        return TradeExcursion(None, None, None, REASON_NO_INTERVAL)
    r = abs(entry_price - stop_loss)
    if r == 0:
        return TradeExcursion(None, None, None, REASON_ZERO_R)
    trade_bars = {tf: candles_by_timeframe.get(tf) or () for tf in TRADE_LADDER}
    coverage = bank_coverage(trade_bars)
    for tf in TRADE_LADDER:
        span = coverage.by_timeframe.get(tf)
        if span is None or span.start > entry_time or span.end < exit_time:
            continue
        duration = timedelta(minutes=LADDER_MINUTES[tf])
        bars = [c for c in trade_bars[tf] if c.time < exit_time and c.time + duration > entry_time]
        high, low = max(c.high for c in bars), min(c.low for c in bars)
        if direction == "long":
            return TradeExcursion(_in_r(entry_price - low, r), _in_r(high - entry_price, r), tf)
        return TradeExcursion(_in_r(high - entry_price, r), _in_r(entry_price - low, r), tf)
    if coverage.start is not None and entry_time < coverage.start:
        return TradeExcursion(None, None, None, REASON_NO_HISTORY)
    return TradeExcursion(None, None, None, REASON_PENDING_CANDLES)


@dataclass(frozen=True)
class TpCheck:
    """`Could hit TP?` (RF-10): `answer` es "yes", "no" o `None` (sin propuesta, con `reason`); `timeframe`, la TF de
    la vela que decidió."""
    answer: Optional[str]
    timeframe: Optional[str]
    reason: Optional[str] = None


def could_hit_tp(
    direction: str,
    entry_time: datetime,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    candles_by_timeframe: Mapping[str, Sequence[Candle]],
    max_horizon: int = MAX_HORIZON,
) -> TpCheck:
    """
    RF-10 (N8, N12): recorre desde la entrada con el camino de T25, en 1M, 5M y 15M, hasta el primer toque del SL o
    el fin del horizonte (`max_horizon` velas de 1H). "yes" si antes tocó el TP; "no" si tocó el SL o llegó al fin del
    horizonte sin tocar el TP. Sin propuesta: `zero_r` (RF-9c), `invalid_tp` si el TP no está del lado de la tesis
    (RF-10d), `ambiguous` si una vela toca los dos (RF-10b), y `no_history` o `pending_candles` si faltan velas.
    """
    if entry_price == stop_loss:
        return TpCheck(None, None, REASON_ZERO_R)
    long = direction == "long"
    if (take_profit <= entry_price) if long else (take_profit >= entry_price):
        return TpCheck(None, None, REASON_INVALID_TP)
    trade_bars = {tf: candles_by_timeframe.get(tf) or () for tf in TRADE_LADDER}
    missing = anchor_missing_reason(entry_time, bank_coverage(trade_bars))
    if missing:
        return TpCheck(None, None, missing)
    end = horizon_end(entry_time, candles_by_timeframe.get("1H") or (), max_horizon)
    for bar in forward_path(entry_time, trade_bars):
        if end is not None and bar.time >= end:
            return TpCheck("no", None)
        tp_hit = bar.high >= take_profit if long else bar.low <= take_profit
        sl_hit = bar.low <= stop_loss if long else bar.high >= stop_loss
        if tp_hit and sl_hit:
            return TpCheck(None, bar.timeframe, REASON_AMBIGUOUS)
        if tp_hit or sl_hit:
            return TpCheck("yes" if tp_hit else "no", bar.timeframe)
    return TpCheck(None, None, REASON_PENDING_CANDLES)


# --- Chequeo del Mark Price (T31, RF-3, RF-3b, N16, N33; plan.md §3.7) ---------------------------------------------

# La escalera del chequeo: hasta 15M, que es la última y la única con tolerancia (N33).
MARK_PRICE_LADDER = ("1M", "5M", "15M")
# Motivo de un Mark Price que no se puede chequear porque ninguna vela contiene su hora (por ejemplo, con el mercado
# cerrado), aunque esté dentro del banco.
MARK_PRICE_NO_CANDLE = "no_candle"


@dataclass(frozen=True)
class MarkPriceCheck:
    """
    Resultado del chequeo del Mark Price. `fits` es `True` si cae en la vela (o el rango), `False` si queda afuera aun
    con la tolerancia (se avisa, nunca se bloquea, RF-3) y `None` si no se pudo chequear (`reason`). `timeframe` es la
    TF donde coincidió o la última que se miró; `distance`, cuánto queda afuera del rango sin tolerancia (0 si cae).
    """
    fits: Optional[bool]
    timeframe: Optional[str]
    distance: Optional[float]
    reason: Optional[str] = None


def _distance_outside(price: float, low: float, high: float) -> float:
    return low - price if price < low else price - high if price > high else 0.0


def _not_checkable(moment: datetime, candles_by_timeframe: Mapping[str, Sequence[Candle]]) -> MarkPriceCheck:
    reason = anchor_missing_reason(moment, bank_coverage(candles_by_timeframe)) or MARK_PRICE_NO_CANDLE
    return MarkPriceCheck(None, None, None, reason)


def check_mark_price(
    mark_price: float,
    mark_price_time: datetime,
    candles_by_timeframe: Mapping[str, Sequence[Candle]],
    tolerance: float = MARK_PRICE_TOLERANCE,
) -> MarkPriceCheck:
    """
    RF-3, N33: la vela que contiene `mark_price_time`, subiendo por 1M, 5M y 15M. Apenas `low <= mark_price <= high`,
    coincide en esa TF. En la última TF que tiene vela en esa hora (normalmente la de 15M) se acepta además una
    tolerancia de `tolerance` (0.1%) del precio (N16); si ni así cae, `fits=False`. Una TF sin vela en esa hora se
    saltea.
    """
    last = None
    for tf in MARK_PRICE_LADDER:
        duration = timedelta(minutes=LADDER_MINUTES[tf])
        candle = next((c for c in candles_by_timeframe.get(tf) or () if c.time <= mark_price_time < c.time + duration),
                      None)
        if candle is None:
            continue
        distance = _distance_outside(mark_price, candle.low, candle.high)
        if distance == 0.0:
            return MarkPriceCheck(True, tf, 0.0)
        last = (tf, distance)
    if last is None:
        return _not_checkable(mark_price_time, candles_by_timeframe)
    tf, distance = last
    return MarkPriceCheck(distance <= tolerance * mark_price, tf, distance)


def check_mark_price_in_period(
    mark_price: float,
    start: datetime,
    end: datetime,
    candles_by_timeframe: Mapping[str, Sequence[Candle]],
    tolerance: float = MARK_PRICE_TOLERANCE,
) -> MarkPriceCheck:
    """
    RF-3b, N33, para los análisis sin `mark_price_time` (el reporte): el rango de precios de las velas que se solapan
    con `[start, end)`, en 1M si las hay, si no en 5M, si no en 15M. Una vela que abre justo en `end` no entra: sus
    precios son posteriores. `fits=False` si el Mark Price queda afuera por más de `tolerance` del precio.
    """
    for tf in MARK_PRICE_LADDER:
        duration = timedelta(minutes=LADDER_MINUTES[tf])
        candles = [c for c in candles_by_timeframe.get(tf) or () if c.time < end and c.time + duration > start]
        if not candles:
            continue
        distance = _distance_outside(mark_price, min(c.low for c in candles), max(c.high for c in candles))
        return MarkPriceCheck(distance <= tolerance * mark_price, tf, distance)
    return _not_checkable(start, candles_by_timeframe)
