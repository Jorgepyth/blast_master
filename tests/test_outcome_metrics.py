"""
T29 (spec 002): `core/outcome_metrics.py`, las métricas de acierto S1 y S4 y la etiqueta Overlap (RF-5, RF-17, RF-21;
N11, N22, N35, N36; plan.md §3.3 y §3.10; docs/criterios-de-acierto.md). Funciones puras: reciben resultados del
resolvedor, no saben de DBs ni de velas.
"""
import dataclasses
from datetime import datetime, timedelta

import pytest

from core.candle_resolution import OUTCOME_CONFIRMED, OUTCOME_INVALIDATED, OUTCOME_OPEN
from config.auto_resolution import REASON_AMBIGUOUS, REASON_NO_LEVELS, REASON_PENDING_CANDLES
from core.outcome_metrics import (
    DIRECTIONAL_BIASES,
    AnalysisOutcome,
    OverlapLabel,
    WinRate,
    analysis_anchor,
    counted_outcomes,
    overlap_labels,
    s1,
    s4,
    s4_strict,
)

T0 = datetime(2026, 8, 1, 10, 0)


def _outcome(analysis_id, anchor_h, outcome=OUTCOME_CONFIRMED, touch_h=None, account="XAU", backdated=False,
             bias="Bullish", limit_h=None):
    anchor = T0 + timedelta(hours=anchor_h)
    touch = anchor + timedelta(hours=touch_h) if touch_h is not None else None
    limit = anchor + timedelta(hours=limit_h) if limit_h is not None else touch
    return AnalysisOutcome(analysis_id, account, anchor, backdated, bias, outcome, touch, limit)


# --- ancla (docs/criterios-de-acierto.md, "Definiciones base"; N4, N5) ------------------------------------------

def test_the_anchor_is_the_start_time_or_created_at_minus_20_min_or_the_typed_time_of_a_backdated_one():
    created = datetime(2026, 8, 20, 6, 32)
    assert analysis_anchor(datetime(2026, 8, 20, 6, 5), created, False) == datetime(2026, 8, 20, 6, 5)
    assert analysis_anchor(None, created, False) == datetime(2026, 8, 20, 6, 12)
    assert analysis_anchor(None, created, True) == created


# --- S1 y S4 (N35; plan.md §3.10) -------------------------------------------------------------------------------

def test_s1_is_the_share_of_first_touches_that_hit_the_target_with_no_time_limit():
    outcomes = [
        _outcome("a", 0, OUTCOME_CONFIRMED, 5),
        _outcome("b", 1, OUTCOME_INVALIDATED, 7),
        _outcome("c", 2, OUTCOME_CONFIRMED, 176.9),        # tocó a los 7 días: cuenta igual (el ejemplo d62ab4d1)
        _outcome("d", 3, REASON_AMBIGUOUS),                 # ni ganado ni perdido
        _outcome("e", 4, OUTCOME_OPEN),                     # sin toque en el horizonte: no cuenta
        _outcome("f", 5, REASON_PENDING_CANDLES),
    ]
    assert s1(outcomes) == WinRate(wins=2, n=3, outside=0, excluded_backdated=0)
    assert s1(outcomes).rate == pytest.approx(2 / 3)


def test_s4_counts_only_touches_within_48h_and_says_how_many_were_left_out():
    outcomes = [
        _outcome("a", 0, OUTCOME_CONFIRMED, 47.9),
        _outcome("b", 1, OUTCOME_CONFIRMED, 48.0),          # justo 48 h: entra
        _outcome("c", 2, OUTCOME_INVALIDATED, 48.1),        # fuera
        _outcome("d", 3, OUTCOME_CONFIRMED, 176.9),         # fuera
    ]
    assert s4(outcomes) == WinRate(wins=2, n=2, outside=2, excluded_backdated=0)


def test_backdated_analyses_are_left_out_by_default_and_counted():
    outcomes = [_outcome("a", 0, OUTCOME_CONFIRMED, 5), _outcome("b", 1, OUTCOME_CONFIRMED, 5, backdated=True),
                _outcome("c", 2, OUTCOME_INVALIDATED, 5, backdated=True), _outcome("d", 3, OUTCOME_OPEN, backdated=True)]
    assert s1(outcomes) == WinRate(wins=1, n=1, outside=0, excluded_backdated=2)  # el abierto no habría contado igual
    assert s1(outcomes, include_backdated=True) == WinRate(wins=2, n=3, outside=0, excluded_backdated=0)


def test_the_directional_cut_keeps_only_bullish_and_bearish():
    outcomes = [_outcome("a", 0, OUTCOME_CONFIRMED, 5, bias="Bullish"),
                _outcome("b", 1, OUTCOME_INVALIDATED, 5, bias="Bearish"),
                _outcome("c", 2, OUTCOME_CONFIRMED, 5, bias="Choppy / Neutral")]
    assert DIRECTIONAL_BIASES == ("Bullish", "Bearish")
    assert s1(outcomes, directional_only=True) == WinRate(wins=1, n=2, outside=0, excluded_backdated=0)
    assert s4(outcomes, directional_only=True).n == 2


def test_a_win_rate_without_analyses_has_no_rate():
    assert s1([]) == WinRate(0, 0, 0, 0) and s1([]).rate is None


# --- Overlap: solo una etiqueta (N11, N22, N36; plan.md §3.3) ---------------------------------------------------

def test_overlap_is_another_analysis_of_the_same_account_started_before_the_first_touch():
    outcomes = [
        _outcome("A", 0, OUTCOME_CONFIRMED, 18.3),
        _outcome("B", 12.1, OUTCOME_CONFIRMED, 2),          # empezó a las 12.1 h, antes del toque de A (18.3 h)
        _outcome("C", 15, OUTCOME_CONFIRMED, 2),            # también, pero después: el B de A es el primero
        _outcome("X", 1, OUTCOME_CONFIRMED, 50, account="BTC"),  # otra cuenta: no cuenta
    ]
    labels = overlap_labels(outcomes)
    assert labels == {"A": OverlapLabel("A", "B", T0 + timedelta(hours=12.1), pytest.approx(12.1))}


def test_a_backdated_analysis_counts_as_b_with_its_typed_time():
    outcomes = [_outcome("A", 0, OUTCOME_INVALIDATED, 30), _outcome("B", 24, OUTCOME_CONFIRMED, 1, backdated=True)]
    assert overlap_labels(outcomes)["A"].b_id == "B"


@pytest.mark.parametrize("b_anchor_h", [0, 18.3, 20])
def test_an_analysis_started_at_the_same_time_or_at_or_after_the_touch_is_not_an_overlap(b_anchor_h):
    outcomes = [_outcome("A", 0, OUTCOME_CONFIRMED, 18.3), _outcome("B", b_anchor_h, OUTCOME_CONFIRMED, 1)]
    assert "A" not in overlap_labels(outcomes)


def test_without_a_touch_the_limit_is_the_end_of_the_bank_and_an_unknown_limit_gives_no_label():
    pending = _outcome("A", 0, REASON_PENDING_CANDLES, limit_h=40)   # el banco llega hasta las 40 h
    assert overlap_labels([pending, _outcome("B", 30, OUTCOME_CONFIRMED, 1)])["A"].b_id == "B"
    unknown = _outcome("A", 0, REASON_PENDING_CANDLES)                # sin límite conocido
    assert overlap_labels([unknown, _outcome("B", 30, OUTCOME_CONFIRMED, 1)]) == {}


def test_overlap_never_takes_an_analysis_out_of_s1_or_s4():
    outcomes = [_outcome("A", 0, OUTCOME_CONFIRMED, 18.3), _outcome("B", 12.1, OUTCOME_INVALIDATED, 2)]
    assert "A" in overlap_labels(outcomes)
    assert s1(outcomes) == WinRate(wins=1, n=2, outside=0, excluded_backdated=0)
    assert s4(outcomes) == WinRate(wins=1, n=2, outside=0, excluded_backdated=0)


# --- Los 21 candidatos de specs/002-auto-resolucion-velas/analisis-overlap-2d.md ------------------------------------

# (cuenta, ID A, creado A, retroactivo, market bias A, ID B, h A→B, primer toque, h A→toque), copiados de la tabla.
CANDIDATES_21 = [
    ("XAU", "386ea615", "2026-06-08 06:51", False, "Choppy / Neutral", "7b6e9c10", 24.1, "INVALIDATION", 26.2),
    ("XAU", "00e2e31b", "2026-06-23 07:24", False, "Bearish", "9fb9e581", 0.9, "VALIDATION", 23.2),
    ("XAU", "d55f09b2", "2026-06-26 05:26", False, "Bullish", "478d3315", 73.1, "INVALIDATION", 74.9),
    ("XAU", "f24b9653", "2026-07-09 05:30", True, "Bearish", "7ae2bfe8", 24.0, "VALIDATION", 98.8),
    ("XAU", "85d8f20c", "2026-07-26 16:58", False, "Bullish", "2237dc83", 12.6, "INVALIDATION", 15.9),
    ("XAU", "12e03eed", "2026-08-02 18:31", False, "Choppy / Neutral", "d6318133", 11.1, "INVALIDATION", 13.1),
    ("XAU", "68ff0f26", "2026-08-09 18:02", False, "Choppy / Neutral", "211a3584", 11.5, "INVALIDATION", 24.3),
    ("XAU", "8812cccd", "2026-08-11 05:35", False, "Choppy / Neutral", "9b0f4eef", 24.6, "VALIDATION", 61.7),
    ("XAU", "65c7627b", "2026-08-17 06:32", False, "Choppy / Neutral", "fdb60a42", 23.8, "VALIDATION", 48.8),
    ("XAU", "fdb60a42", "2026-08-18 06:20", False, "Bearish", "285714b9", 24.1, "INVALIDATION", 25.0),
    ("XAU", "cf43465b", "2026-08-20 06:32", False, "Bullish", "0df23244", 12.1, "VALIDATION", 18.3),
    ("XAU", "47947184", "2026-08-24 06:14", False, "Bullish", "fe37ea56", 36.7, "INVALIDATION", 51.1),
    ("XAU", "cbac26ff", "2026-08-27 06:22", False, "Choppy / Neutral", "464efa51", 12.1, "VALIDATION", 26.0),
    ("XAU", "4aa4b9a3", "2026-08-30 16:35", False, "Choppy / Neutral", "21e49870", 13.7, "VALIDATION", 34.2),
    ("BTC", "5971536e", "2026-08-05 06:58", False, "Bullish", "6793c370", 22.6, "VALIDATION", 46.6),
    ("BTC", "22bb5efc", "2026-08-12 07:00", False, "Choppy / Neutral", "4fbd26c0", 23.7, "INVALIDATION", 27.8),
    ("BTC", "4fbd26c0", "2026-08-13 06:39", False, "Choppy / Neutral", "b0c8c373", 96.0, "INVALIDATION", 101.9),
    ("BTC", "ecf1153a", "2026-08-18 07:05", False, "Bullish", "57c1dd0c", 23.7, "VALIDATION", 25.2),
    ("BTC", "c73353ad", "2026-08-25 19:12", False, "Choppy / Neutral", "3565cf47", 11.1, "INVALIDATION", 177.9),
    ("BTC", "d62ab4d1", "2026-08-27 06:57", False, "Bullish", "ad5f44a6", 11.6, "VALIDATION", 176.9),
    ("BTC", "4eb0c637", "2026-08-30 16:48", False, "Bearish", "c77654ac", 49.9, "INVALIDATION", 88.3),
]


def _fixture_21():
    """Los 21 A con su toque, y cada B que no es también un A, empezado h A→B después de su A y resuelto al minuto."""
    outcomes, a_ids = {}, {row[1] for row in CANDIDATES_21}
    for account, a_id, created, backdated, bias, b_id, h_ab, first, h_touch in CANDIDATES_21:
        anchor = analysis_anchor(None, datetime.strptime(created, "%Y-%m-%d %H:%M"), backdated)
        touch = anchor + timedelta(hours=h_touch)
        outcome = OUTCOME_CONFIRMED if first == "VALIDATION" else OUTCOME_INVALIDATED
        outcomes[a_id] = AnalysisOutcome(a_id, account, anchor, backdated, bias, outcome, touch, touch)
        if b_id not in a_ids:
            b_anchor = anchor + timedelta(hours=h_ab)
            outcomes[b_id] = AnalysisOutcome(b_id, account, b_anchor, False, "Bullish", OUTCOME_CONFIRMED,
                                             b_anchor + timedelta(minutes=1), b_anchor + timedelta(minutes=1))
    return list(outcomes.values())


def test_the_21_overlap_candidates_of_the_analysis_are_reproduced():
    labels = overlap_labels(_fixture_21())

    assert set(labels) == {row[1] for row in CANDIDATES_21}
    for account, a_id, created, backdated, bias, b_id, h_ab, first, h_touch in CANDIDATES_21:
        assert labels[a_id].b_id == b_id
        assert labels[a_id].hours_a_to_b == pytest.approx(h_ab, abs=0.06)  # la tabla redondea a 0.1 h


def test_the_48h_counts_of_the_21_candidates_are_reproduced():
    a_rows = [o for o in _fixture_21() if o.analysis_id in {row[1] for row in CANDIDATES_21}]
    # La tabla, "48 h desde A": VALIDATION 6, INVALIDATION 6 y sin toque 9 (el retroactivo incluido).
    assert s4(a_rows, include_backdated=True) == WinRate(wins=6, n=12, outside=9, excluded_backdated=0)
    assert s4(a_rows) == WinRate(wins=6, n=12, outside=8, excluded_backdated=1)
    assert s1(a_rows) == WinRate(wins=9, n=20, outside=0, excluded_backdated=1)


# --- S4 estricto (N51: la cifra principal desde el 2026-10-03) ----------------------------------------------------

def _pending(analysis_id, anchor_h, observed_h, **kwargs):
    """Sin toque todavía: el banco llega `observed_h` horas después del ancla."""
    outcome = _outcome(analysis_id, anchor_h, REASON_PENDING_CANDLES, **kwargs)
    return dataclasses.replace(outcome, path_end=outcome.anchor + timedelta(hours=observed_h))


STRICT_CASES = [
    _outcome("win", 0, OUTCOME_CONFIRMED, 48.0),          # el objetivo justo a las 48 h: gana
    _outcome("loss", 1, OUTCOME_INVALIDATED, 3),           # la invalidación dentro de 48 h: pierde
    _outcome("late_win", 2, OUTCOME_CONFIRMED, 99),        # el objetivo a las 99 h: pierde (tarde)
    _outcome("late_loss", 3, OUTCOME_INVALIDATED, 75),     # la invalidación a las 75 h: pierde (tarde)
    _outcome("open", 4, OUTCOME_OPEN),                     # sin toque en todo el horizonte: pierde
    _pending("quiet", 5, observed_h=48),                   # sin toque y 48 h de velas: pierde
    _pending("young", 6, observed_h=47.9),                 # sin toque y menos de 48 h de velas: todavía no cuenta
    _outcome("amb", 7, REASON_AMBIGUOUS),                  # no cuenta, como en S1
    _outcome("nolev", 8, REASON_NO_LEVELS),                # sin resultado: no cuenta
]


def test_strict_s4_wins_only_with_the_target_within_48h_and_leaves_nothing_out():
    rate = s4_strict(STRICT_CASES)
    assert rate == WinRate(wins=1, n=6, outside=0, excluded_backdated=0, late=4)
    assert [o.analysis_id for o in counted_outcomes(STRICT_CASES, strict=True)] == [
        "win", "loss", "late_win", "late_loss", "open", "quiet"]


def test_strict_s4_has_the_wins_of_s4_and_the_late_ones_of_s1_as_losses():
    outcomes = [_outcome("a", 0, OUTCOME_CONFIRMED, 10), _outcome("b", 1, OUTCOME_INVALIDATED, 10),
                _outcome("c", 2, OUTCOME_CONFIRMED, 60), _outcome("d", 3, OUTCOME_INVALIDATED, 60)]
    assert (s4(outcomes).wins, s4(outcomes).n) == (1, 2)       # S4 deja fuera c y d: 50%
    assert (s1(outcomes).wins, s1(outcomes).n) == (2, 4)       # S1 cuenta c como ganado: 50%
    assert s4_strict(outcomes) == WinRate(wins=1, n=4, outside=0, excluded_backdated=0, late=2)  # 25%


def test_strict_s4_leaves_backdated_out_by_default_counts_them_and_keeps_the_directional_cut():
    outcomes = [_outcome("a", 0, OUTCOME_CONFIRMED, 5), _outcome("b", 1, OUTCOME_CONFIRMED, 5, backdated=True),
                _outcome("c", 2, OUTCOME_INVALIDATED, 5, bias="Choppy / Neutral"),
                _outcome("d", 3, REASON_AMBIGUOUS, backdated=True)]   # sin resultado: ni se cuenta como excluido
    assert s4_strict(outcomes) == WinRate(wins=1, n=2, outside=0, excluded_backdated=1)
    assert s4_strict(outcomes, include_backdated=True) == WinRate(wins=2, n=3, outside=0, excluded_backdated=0)
    assert s4_strict(outcomes, directional_only=True) == WinRate(wins=1, n=1, outside=0, excluded_backdated=1)
