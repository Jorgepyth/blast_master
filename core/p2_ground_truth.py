"""
core/p2_ground_truth.py — P2_systematic: ground truth geométrico (Opción B).

Funciones puras (sin acceso a DB, sin efectos secundarios), mismo espíritu
que core/math_engine.py y core/backtest_engine.py. Derivan la dirección de
la tesis original (long/short) comparando entry_price (tactical_audit)
contra edge_validation_price/structural_invalidation (unified_department,
tools/database.py:120-121 — ambos confirmados PRE-ejecución, capturados en
flow_new_analysis antes de bias_a, cli/main.py:2593-2594), y determinan qué
nivel tocó primero recorriendo velas hacia adelante desde el anchor
timestamp.

Decisión de esta sesión (Opción B): NO se usa bias_a/real_bias_b/
specific_bias_compliance como ground truth. Esos campos viven en un
vocabulario StructuralBias de 7 valores (BOS, CHOCH, Choppy-Bullish/Bearish
Range Rotation, No_Bias(Choppy), Validated Range Expansion, Trend Reversal)
sin traducción a {Bullish, Bearish, Choppy/Neutral} en ningún lugar del
código (ver jupyter/p2_systematic_bias_field_verification.md). El ground
truth aquí es puramente geométrico: qué nivel de precio tocó primero.
"""
from __future__ import annotations

from typing import NamedTuple, Optional, Sequence, Tuple


class OhlcBar(NamedTuple):
    """Una vela OHLC mínima — solo los campos que el touch-check necesita."""
    time: object  # datetime-like; solo necesita ser comparable/ordenable
    high: float
    low: float


def infer_thesis_direction(
    entry_price: float,
    edge_validation_price: float,
    structural_invalidation: float,
) -> Optional[str]:
    """
    "long" si edge_validation_price (objetivo) está arriba de entry_price y
    structural_invalidation (invalidación) está abajo; "short" si es al
    revés. None si ambos niveles caen del mismo lado de entry_price, o
    alguno es igual a entry_price — anomalía de datos, no un caso de
    negocio válido (no se adivina una dirección en ese caso).
    """
    if edge_validation_price > entry_price and structural_invalidation < entry_price:
        return "long"
    if edge_validation_price < entry_price and structural_invalidation > entry_price:
        return "short"
    return None


def first_touch_direction(
    thesis_direction: Optional[str],
    ohlc_path_forward: Sequence[OhlcBar],
    entry_price: float,
    edge_validation_price: float,
    structural_invalidation: float,
) -> Tuple[Optional[str], bool]:
    """
    Recorre ohlc_path_forward en orden cronológico (se asume que ya viene
    sin la vela de entrada — responsabilidad del caller) y determina qué
    nivel tocó primero:
      - Toca primero edge_validation_price -> devuelve thesis_direction
        (la tesis se confirmó).
      - Toca primero structural_invalidation -> devuelve la dirección
        opuesta a thesis_direction (la tesis se invalidó).
      - Ninguno se toca dentro del path disponible, el path viene vacío, o
        thesis_direction es None -> (None, True) (incompleto).

    "Tocar" = high >= nivel para el nivel que está arriba de entry_price, o
    low <= nivel para el que está abajo — sin asumir de antemano cuál de
    los dos (validation/invalidation) es el de arriba, ya que depende de
    thesis_direction.

    Supuesto no cubierto por la especificación original, decidido aquí de
    forma conservadora (señalado en jupyter/p2_systematic_task_plan.md,
    no aprobado en silencio): si una misma vela toca ambos niveles
    (high >= superior Y low <= inferior en la misma barra), no hay forma de
    saber cuál ocurrió primero sin datos intrabar más finos — se reporta
    como incompleto en vez de adivinar un orden.
    """
    if thesis_direction is None:
        return None, True

    opposite = "short" if thesis_direction == "long" else "long"

    if edge_validation_price > structural_invalidation:
        upper_level, lower_level = edge_validation_price, structural_invalidation
    else:
        upper_level, lower_level = structural_invalidation, edge_validation_price

    for bar in ohlc_path_forward:
        touched_upper = bar.high >= upper_level
        touched_lower = bar.low <= lower_level

        if touched_upper and touched_lower:
            return None, True

        if touched_upper:
            return (thesis_direction if upper_level == edge_validation_price else opposite), False

        if touched_lower:
            return (thesis_direction if lower_level == edge_validation_price else opposite), False

    return None, True


LEVEL_VALIDATION = "validation"      # tocó edge_validation_price: la tesis se confirmó
LEVEL_INVALIDATION = "invalidation"  # tocó structural_invalidation: la tesis se invalidó


class FirstTouchDetail(NamedTuple):
    """
    Resultado de `first_touch_detail`, en el orden del plan de la spec 002
    (§3.2, paso 5): (dirección, incompleto, índice, nivel, ambiguo).

      - direction, incomplete: exactamente lo que devuelve
        `first_touch_direction` con los mismos argumentos.
      - bar_index: posición, en el camino recibido, de la vela que tocó (o de
        la vela ambigua). None si no hubo toque.
      - level: LEVEL_VALIDATION o LEVEL_INVALIDATION. None si no hubo toque o
        si la vela fue ambigua.
      - ambiguous: la vela `bar_index` tocó los dos niveles (RF-4b).
    """
    direction: Optional[str]
    incomplete: bool
    bar_index: Optional[int]
    level: Optional[str]
    ambiguous: bool


def first_touch_detail(
    thesis_direction: Optional[str],
    ohlc_path_forward: Sequence[OhlcBar],
    entry_price: float,
    edge_validation_price: float,
    structural_invalidation: float,
) -> FirstTouchDetail:
    """
    Spec 002 (RF-4, RF-4b, RF-4f): misma firma y misma regla de toque que
    `first_touch_direction`, pero dice también en qué vela fue el toque y qué
    nivel tocó. Con eso el resolvedor saca la hora del toque (la apertura de
    esa vela, N19) y distingue una tesis confirmada de una invalidada. Si la
    primera vela que toca algo toca los dos niveles, devuelve su índice marcado
    como ambiguo, para que el llamador la recorra con una TF más fina (plan
    §3.2); acá no se adivina un orden.

    `first_touch_direction` no cambia: sus llamadores actuales quedan como
    están (RF-4f). El recorrido está repetido a propósito, y
    `tests/test_p2_first_touch_detail.py` exige, con caminos al azar, que los
    dos primeros campos coincidan siempre con `first_touch_direction`.
    """
    if thesis_direction is None:
        return FirstTouchDetail(None, True, None, None, False)

    opposite = "short" if thesis_direction == "long" else "long"

    if edge_validation_price > structural_invalidation:
        upper_level, lower_level = edge_validation_price, structural_invalidation
    else:
        upper_level, lower_level = structural_invalidation, edge_validation_price

    for index, bar in enumerate(ohlc_path_forward):
        touched_upper = bar.high >= upper_level
        touched_lower = bar.low <= lower_level

        if touched_upper and touched_lower:
            return FirstTouchDetail(None, True, index, None, True)

        if touched_upper:
            touched_level = upper_level
        elif touched_lower:
            touched_level = lower_level
        else:
            continue

        if touched_level == edge_validation_price:
            return FirstTouchDetail(thesis_direction, False, index, LEVEL_VALIDATION, False)
        return FirstTouchDetail(opposite, False, index, LEVEL_INVALIDATION, False)

    return FirstTouchDetail(None, True, None, None, False)
