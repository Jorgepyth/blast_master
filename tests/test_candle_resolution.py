"""
T24 (spec 002): `core/candle_resolution.py`, parte 1. Cobertura del banco por TF de la escalera y los códigos
`no_history` y `pending_candles` (RF-4c, RF-4g, N28; plan.md §3.1, casos límite). Velas sintéticas, sin DB ni disco.
"""
from datetime import datetime, timedelta

import pytest

from config.auto_resolution import (
    MAX_HORIZON,
    REASON_AMBIGUOUS,
    REASON_NO_HISTORY,
    REASON_NO_LEVELS,
    REASON_PENDING_CANDLES,
    RESOLUTION_TIME_SOURCE_OPEN,
)
from core.candle_resolution import (
    LADDER,
    LADDER_MINUTES,
    OUTCOME_CONFIRMED,
    OUTCOME_INVALIDATED,
    OUTCOME_OPEN,
    FAILURE_LIQUIDITY_SWEEP,
    FAILURE_NA,
    STRUCTURAL_EXPANSION,
    STRUCTURAL_MINIMAL,
    STRUCTURAL_NA,
    STRUCTURAL_REVERTED,
    MARK_PRICE_NO_CANDLE,
    AnalysisResolution,
    BankCoverage,
    Candle,
    FirstTouch,
    MarkPriceCheck,
    StartPrice,
    StructuralProposal,
    TimeframeCoverage,
    anchor_missing_reason,
    bank_coverage,
    check_mark_price,
    check_mark_price_in_period,
    forward_path,
    horizon_end,
    propose_structural,
    resolve_analysis,
    resolve_first_touch,
    start_price,
    untouched_missing_reason,
)
from core.p2_ground_truth import LEVEL_INVALIDATION, LEVEL_VALIDATION, OhlcBar, first_touch_detail

T0 = datetime(2026, 6, 1, 0, 0)


def _bars(timeframe, start, n, high=101.0, low=99.0):
    step = timedelta(minutes=LADDER_MINUTES[timeframe])
    return [OhlcBar(time=start + i * step, high=high, low=low) for i in range(n)]


# --- escalera ----------------------------------------------------------------------

def test_the_ladder_goes_from_the_finest_to_the_coarsest_timeframe():
    assert LADDER == ("1M", "5M", "15M", "30M", "1H")  # plan.md §3.1, paso 1


def test_ladder_durations_agree_with_the_backtest_table():
    from tools.p2_backtest import TIMEFRAME_MINUTES
    assert LADDER_MINUTES == {tf: TIMEFRAME_MINUTES[tf] for tf in LADDER}


# --- cobertura por TF -----------------------------------------------------------------

def test_coverage_of_a_timeframe_runs_from_its_first_open_to_the_close_of_its_last_candle():
    coverage = bank_coverage({"15M": _bars("15M", T0, 4)})
    assert coverage.by_timeframe == {"15M": TimeframeCoverage("15M", start=T0, end=T0 + timedelta(hours=1))}


def test_coverage_ignores_timeframes_outside_the_ladder_and_empty_ones():
    coverage = bank_coverage({
        "4H": _bars("1H", T0, 3),  # 4H no está en la escalera: el resolvedor no la usa
        "5M": [],
        "1H": _bars("1H", T0, 2),
    })
    assert list(coverage.by_timeframe) == ["1H"]


def test_coverage_does_not_depend_on_the_order_of_the_candles():
    bars = _bars("1M", T0, 10)
    assert bank_coverage({"1M": list(reversed(bars))}) == bank_coverage({"1M": bars})


def test_the_bank_starts_at_its_oldest_candle_and_ends_at_its_newest_close_across_timeframes():
    coverage = bank_coverage({
        "1M": _bars("1M", T0 + timedelta(days=30), 60),   # el 1M empieza más tarde (poco historial en MT5)
        "1H": _bars("1H", T0, 24 * 31),                     # el 1H viene de antes y llega más lejos
    })
    assert coverage.start == T0
    assert coverage.end == T0 + timedelta(days=31)
    assert BankCoverage({}).start is None and BankCoverage({}).end is None


def test_covering_lists_the_timeframes_that_cover_an_instant_from_the_finest():
    coverage = bank_coverage({
        "1H": _bars("1H", T0, 48),
        "1M": _bars("1M", T0 + timedelta(hours=10), 120),  # cubre de las 10:00 a las 12:00
        "15M": _bars("15M", T0, 96),
    })
    assert coverage.covering(T0 + timedelta(hours=11)) == ["1M", "15M", "1H"]
    assert coverage.covering(T0 + timedelta(hours=10)) == ["1M", "15M", "1H"]  # la apertura cuenta
    assert coverage.covering(T0 + timedelta(hours=12)) == ["15M", "1H"]          # el cierre del último 1M no
    assert coverage.covering(T0 + timedelta(hours=30)) == ["1H"]
    assert coverage.covering(T0 + timedelta(hours=48)) == []


# --- no_history y pending_candles en el ancla (RF-4g, RF-4c) ---------------------------------

def test_an_anchor_before_the_oldest_candle_in_every_timeframe_is_no_history():
    coverage = bank_coverage({"1M": _bars("1M", T0, 60), "1H": _bars("1H", T0, 24)})
    assert anchor_missing_reason(T0 - timedelta(minutes=1), coverage) == REASON_NO_HISTORY


def test_an_anchor_before_the_1m_history_but_inside_the_1h_one_is_not_no_history():
    coverage = bank_coverage({
        "1M": _bars("1M", T0 + timedelta(days=30), 60),
        "1H": _bars("1H", T0, 24 * 31),
    })
    assert anchor_missing_reason(T0 + timedelta(days=1), coverage) is None
    assert anchor_missing_reason(T0, coverage) is None  # justo en la apertura de la vela más vieja


def test_an_anchor_the_bank_has_not_reached_yet_is_pending_candles():
    # Un análisis de hoy, con el banco exportado por última vez ayer: se resuelve con el próximo export.
    coverage = bank_coverage({"1H": _bars("1H", T0, 24)})
    assert anchor_missing_reason(T0 + timedelta(hours=24), coverage) == REASON_PENDING_CANDLES  # justo al final
    assert anchor_missing_reason(T0 + timedelta(days=5), coverage) == REASON_PENDING_CANDLES


def test_a_bank_with_no_candles_in_the_ladder_is_pending_candles():
    # Temporal: el próximo export trae las velas (el reloj sin verificar se chequea antes, RF-4e).
    assert anchor_missing_reason(T0, bank_coverage({})) == REASON_PENDING_CANDLES
    assert anchor_missing_reason(T0, bank_coverage({"1D": _bars("1H", T0, 3)})) == REASON_PENDING_CANDLES


# --- pending_candles sin toque (RF-4c) -----------------------------------------------------------

def test_without_a_touch_the_result_is_pending_until_the_path_reaches_the_end_of_the_horizon():
    horizon_end = T0 + timedelta(days=90)
    assert untouched_missing_reason(T0 + timedelta(days=3), horizon_end=None) == REASON_PENDING_CANDLES
    assert untouched_missing_reason(T0 + timedelta(days=3), horizon_end=horizon_end) == REASON_PENDING_CANDLES
    assert untouched_missing_reason(horizon_end, horizon_end=horizon_end) is None             # horizonte cumplido
    assert untouched_missing_reason(horizon_end + timedelta(hours=1), horizon_end=horizon_end) is None


def test_a_bank_that_ends_before_the_touch_is_pending_candles():
    """El caso del "Hecho cuando" de T24: el precio toca la validación el día 5, pero el banco llega solo hasta el
    día 3. Recorriendo lo que hay no aparece ningún toque, y el banco todavía no llega al fin del horizonte:
    `pending_candles`, no `open` (baseline H12)."""
    market = _bars("1H", T0, 24 * 7)
    touch_at = 24 * 5
    market[touch_at] = OhlcBar(time=market[touch_at].time, high=111.0, low=100.0)
    bank = {"1H": market[:24 * 3]}

    coverage = bank_coverage(bank)
    assert anchor_missing_reason(T0, coverage) is None
    detail = first_touch_detail("long", bank["1H"], 100.0, 110.0, 90.0)
    assert detail.bar_index is None  # en el banco no hay toque

    assert untouched_missing_reason(coverage.end, horizon_end=None) == REASON_PENDING_CANDLES
    # Con todo el mercado, el mismo recorrido sí encuentra el toque del día 5.
    assert first_touch_detail("long", market, 100.0, 110.0, 90.0).bar_index == touch_at


@pytest.mark.parametrize("timeframe", LADDER)
def test_every_ladder_timeframe_has_a_duration(timeframe):
    assert LADDER_MINUTES[timeframe] > 0


# --- camino hacia adelante de varias TF (T25, RF-4, N17, N18; plan.md §3.1) -----------------------------

def _path(anchor, bank):
    return list(forward_path(anchor, bank))


def _assert_contiguous(path):
    """Sin huecos ni velas repetidas: cada vela abre justo donde cerró la anterior."""
    for prev, nxt in zip(path, path[1:]):
        assert nxt.time == prev.end, (prev, nxt)


def test_the_path_goes_1m_then_5m_then_1h_without_gaps_or_repeated_candles():
    anchor = T0 + timedelta(hours=10, seconds=30)                    # 10:00:30
    bank = {
        "1M": list(reversed(_bars("1M", T0 + timedelta(hours=9), 70))),  # 09:00 a 10:09, cierra 10:10 (desordenadas a propósito)
        "5M": _bars("5M", T0 + timedelta(hours=9), 24),                 # 09:00 a 10:55, cierra 11:00
        "1H": _bars("1H", T0 + timedelta(hours=8), 7),                  # 08:00 a 14:00, cierra 15:00
    }

    path = _path(anchor, bank)

    assert [b.timeframe for b in path] == ["1M"] * 9 + ["5M"] * 10 + ["1H"] * 4
    assert path[0].time == T0 + timedelta(hours=10, minutes=1)
    assert path[-1].end == T0 + timedelta(hours=15)
    _assert_contiguous(path)
    assert len({b.time for b in path}) == len(path)


def test_the_1m_candle_that_contains_the_anchor_is_left_out():
    bank = {"1M": _bars("1M", T0, 120)}
    assert _path(T0 + timedelta(minutes=10, seconds=30), bank)[0].time == T0 + timedelta(minutes=11)
    # Una vela que abre justo en el ancla sí entra (N17: "que abre en el ancla o después").
    assert _path(T0 + timedelta(minutes=10), bank)[0].time == T0 + timedelta(minutes=10)


def test_without_1m_on_the_anchor_date_it_starts_with_the_next_finest_and_moves_up_on_a_coarse_boundary():
    # N17 y N18: el 1M empieza el día 2 a las 00:07. El día 1 se recorre con 15M, y se sube a 1M recién en el borde de
    # la vela de 15M siguiente (00:15). El 1M termina justo en un borde de 15M (01:15) y se vuelve a 15M.
    bank = {"15M": _bars("15M", T0, 4 * 48), "1M": _bars("1M", T0 + timedelta(days=1, minutes=7), 68)}

    path = _path(T0 + timedelta(hours=23, minutes=20), bank)

    assert (path[0].timeframe, path[0].time) == ("15M", T0 + timedelta(hours=23, minutes=30))
    first_1m = next(i for i, b in enumerate(path) if b.timeframe == "1M")
    assert path[first_1m].time == T0 + timedelta(days=1, minutes=15)
    assert [b.timeframe for b in path[first_1m:first_1m + 61]] == ["1M"] * 60 + ["15M"]
    assert path[-1].end == T0 + timedelta(days=2)
    _assert_contiguous(path)


def test_moving_up_to_a_finer_timeframe_waits_for_the_boundary_of_the_coarse_candle():
    # N18: el 1M empieza a las 09:07, a mitad de la vela de 15M de las 09:00. Se usa esa vela entera y el 1M desde las 09:15.
    bank = {"15M": _bars("15M", T0 + timedelta(hours=8), 12), "1M": _bars("1M", T0 + timedelta(hours=9, minutes=7), 50)}

    path = _path(T0 + timedelta(hours=8, minutes=50), bank)

    assert [(b.timeframe, f"{b.time:%H:%M}") for b in path[:3]] == [("15M", "09:00"), ("1M", "09:15"), ("1M", "09:16")]
    _assert_contiguous(path)


def test_moving_down_to_a_coarser_timeframe_off_its_boundary_cuts_the_path():
    # Plan §3.1, paso 5: el 1M termina a las 10:07 y la vela de 5M de las 10:05 ya empezó. Usarla repetiría 10:05-10:07
    # y saltarla dejaría 10:07-10:10 sin mirar: el camino se corta (el resultado será pending_candles).
    bank = {"1M": _bars("1M", T0 + timedelta(hours=10), 7), "5M": _bars("5M", T0 + timedelta(hours=10), 12)}

    path = _path(T0 + timedelta(hours=10), bank)

    assert [b.timeframe for b in path] == ["1M"] * 7
    assert path[-1].end == T0 + timedelta(hours=10, minutes=7)


def test_a_finer_timeframe_that_starts_before_the_next_coarse_candle_is_used_from_its_start():
    # La vela de 1H que contiene el ancla queda fuera. El 15M arranca a las 08:30, antes del 1H de las 09:00:
    # se usa desde ahí, para no dejar 08:30-09:00 sin mirar.
    bank = {"1H": _bars("1H", T0, 24), "15M": _bars("15M", T0 + timedelta(hours=8, minutes=30), 20)}

    path = _path(T0 + timedelta(hours=8, minutes=20), bank)

    assert (path[0].timeframe, path[0].time) == ("15M", T0 + timedelta(hours=8, minutes=30))
    _assert_contiguous(path)


def test_on_a_tie_the_finer_timeframe_wins():
    # El 1M empieza justo a las 09:00, cuando abre la vela de 15M siguiente al ancla: las dos abren a la vez y se usa la
    # más fina (por ejemplo, al reabrir el mercado después de un fin de semana).
    bank = {"15M": _bars("15M", T0, 48), "1M": _bars("1M", T0 + timedelta(hours=9), 30)}

    path = _path(T0 + timedelta(hours=8, minutes=50), bank)

    assert (path[0].timeframe, path[0].time) == ("1M", T0 + timedelta(hours=9))
    _assert_contiguous(path)


def test_a_market_closed_gap_is_crossed_without_changing_timeframe():
    # Fin de semana: ninguna TF tiene velas. El camino sigue cuando el mercado reabre, sin superponer nada.
    friday = _bars("1M", T0, 60)
    sunday = _bars("1M", T0 + timedelta(days=2), 60)

    path = _path(T0, {"1M": friday + sunday})

    assert len(path) == 120 and {b.timeframe for b in path} == {"1M"}
    assert path[60].time == T0 + timedelta(days=2)
    assert all(nxt.time >= prev.end for prev, nxt in zip(path, path[1:]))


def test_an_anchor_outside_the_bank_gives_an_empty_path():
    bank = {"1H": _bars("1H", T0, 24)}
    assert _path(T0 - timedelta(hours=1), bank) == []    # anterior al banco: no_history (T24)
    assert _path(T0 + timedelta(days=1), bank) == []     # el banco todavía no llega: pending_candles (T24)
    assert _path(T0, {}) == []


def test_path_candles_feed_first_touch_detail_and_keep_their_timeframe():
    # El primer toque se busca directo sobre el camino (T26): la hora del toque es la apertura de esa vela (N19).
    bars = _bars("1M", T0, 30)
    bars[12] = OhlcBar(time=bars[12].time, high=111.0, low=100.0)

    path = _path(T0 + timedelta(seconds=30), {"1M": bars})
    detail = first_touch_detail("long", path, 100.0, 110.0, 90.0)

    assert (path[detail.bar_index].timeframe, path[detail.bar_index].time) == ("1M", T0 + timedelta(minutes=12))


def test_the_path_is_produced_lazily():
    # El resolvedor corta en el primer toque; no hace falta armar los ~130 mil minutos de un horizonte entero.
    import types
    assert isinstance(forward_path(T0, {"1M": _bars("1M", T0, 5)}), types.GeneratorType)


# --- primer toque, ambiguous, horizonte y open (T26, RF-4, RF-4b, RF-5, RF-5b, N19; plan.md §3.1 y §3.2) -------

EVP, SI = 110.0, 90.0   # tesis long: validación arriba, invalidación abajo


def _touching(bars, index, high=None, low=None):
    """Copia de `bars` con la vela `index` estirada hasta `high` y/o `low`."""
    bars = list(bars)
    bar = bars[index]
    bars[index] = OhlcBar(time=bar.time, high=high if high is not None else bar.high,
                          low=low if low is not None else bar.low)
    return bars


def _resolve(bank, anchor=T0 + timedelta(seconds=30), max_horizon=MAX_HORIZON, thesis="long"):
    return resolve_first_touch(anchor, bank, thesis, EVP, SI, max_horizon=max_horizon)


def test_confirmed_when_validation_is_touched_first_with_the_open_of_that_candle_and_its_timeframe():
    bank = {"1M": _touching(_bars("1M", T0, 60), 12, high=110.5)}

    result = _resolve(bank)

    assert result == FirstTouch(OUTCOME_CONFIRMED, touch_time=T0 + timedelta(minutes=12), timeframe="1M",
                                direction="long", level=LEVEL_VALIDATION, path_end=T0 + timedelta(minutes=13),
                                horizon_end=None)


def test_invalidated_when_the_structural_invalidation_is_touched_first():
    bars = _touching(_touching(_bars("1M", T0, 60), 7, low=89.0), 20, high=111.0)  # invalida a las 00:07, valida después

    result = _resolve({"1M": bars})

    assert (result.outcome, result.touch_time, result.direction, result.level) == (
        OUTCOME_INVALIDATED, T0 + timedelta(minutes=7), "short", LEVEL_INVALIDATION)


def test_a_15m_candle_that_touches_both_levels_is_resolved_by_the_1m_of_that_stretch():
    # El 15M solo diría "las dos en la vela de las 10:30", pero hay 1M desde las 10:30: el camino ya va en 1M ahí
    # (T25) y el 1M dice cuál fue primero.
    fifteen = _touching(_bars("15M", T0, 96), 42, high=111.0, low=89.0)                # 10:30, doble
    one = _touching(_touching(_bars("1M", T0 + timedelta(hours=10, minutes=30), 120), 3, high=110.2), 11, low=89.5)

    result = _resolve({"15M": fifteen, "1M": one}, anchor=T0 + timedelta(hours=10, minutes=5))

    assert (result.outcome, result.touch_time, result.timeframe) == (
        OUTCOME_CONFIRMED, T0 + timedelta(hours=10, minutes=33), "1M")
    # Sin ese 1M, la vela doble de 15M es lo más fino que hay: no se adivina el orden.
    assert _resolve({"15M": fifteen}, anchor=T0 + timedelta(hours=10, minutes=5)).outcome == REASON_AMBIGUOUS


def test_a_1m_candle_that_touches_both_levels_is_ambiguous_with_no_time_proposed():
    bank = {"1M": _touching(_bars("1M", T0, 60), 15, high=111.0, low=89.0)}

    result = _resolve(bank)

    assert (result.outcome, result.touch_time, result.timeframe, result.level) == (REASON_AMBIGUOUS, None, None, None)
    assert result.path_end == T0 + timedelta(minutes=16)


def test_no_touch_within_the_horizon_is_open_and_a_later_touch_does_not_count():
    # Horizonte de 24 velas de 1H para el test. El ancla (00:00:30) está dentro de la vela de las 00:00, que no cuenta:
    # la 1.ª es la de la 01:00 y la 24.ª la de las 24:00, que cierra a la 01:00 del día siguiente.
    bars = _touching(_bars("1H", T0, 40), 25, high=111.0)  # toca en la vela que abre justo cuando termina el horizonte

    result = _resolve({"1H": bars}, max_horizon=24)

    assert result == FirstTouch(OUTCOME_OPEN, path_end=T0 + timedelta(hours=25), horizon_end=T0 + timedelta(hours=25))


def test_a_market_close_right_before_the_end_of_the_horizon_still_reaches_it():
    # El horizonte (2 velas de 1H) termina a las 03:00, pero el mercado cerró a las 02:30 y reabre a las 05:00: el
    # camino de 1M llega hasta las 02:30 y la vela siguiente ya está pasado el horizonte. Es `open`, no `pending`.
    one_hour = _bars("1H", T0, 3) + _bars("1H", T0 + timedelta(hours=5), 5)
    one_minute = _bars("1M", T0, 150) + _bars("1M", T0 + timedelta(hours=5), 60)

    result = _resolve({"1H": one_hour, "1M": one_minute}, max_horizon=2)

    assert result == FirstTouch(OUTCOME_OPEN, path_end=T0 + timedelta(hours=3), horizon_end=T0 + timedelta(hours=3))


def test_a_touch_in_the_last_candle_of_the_horizon_still_counts():
    bars = _touching(_bars("1H", T0, 40), 24, high=111.0)  # la 24.ª vela desde el ancla

    assert _resolve({"1H": bars}, max_horizon=24).outcome == OUTCOME_CONFIRMED


def test_without_a_touch_a_bank_that_ends_before_the_horizon_is_pending():
    result = _resolve({"1H": _bars("1H", T0, 10)}, max_horizon=24)

    assert result == FirstTouch(REASON_PENDING_CANDLES, path_end=T0 + timedelta(hours=10), horizon_end=None)


def test_an_anchor_before_the_bank_is_no_history_and_after_it_pending():
    bank = {"1M": _bars("1M", T0, 60)}
    assert _resolve(bank, anchor=T0 - timedelta(minutes=5)) == FirstTouch(REASON_NO_HISTORY)
    assert _resolve(bank, anchor=T0 + timedelta(hours=2)) == FirstTouch(REASON_PENDING_CANDLES)


def test_horizon_end_is_the_close_of_the_nth_1h_candle_opening_at_or_after_the_anchor():
    bars = _bars("1H", T0, 30)
    assert horizon_end(T0 + timedelta(minutes=20), bars, 24) == T0 + timedelta(hours=25)  # la de las 00:00 no cuenta
    assert horizon_end(T0, bars, 24) == T0 + timedelta(hours=24)                           # abre en el ancla: cuenta
    assert horizon_end(T0 + timedelta(hours=7), bars, 24) is None                          # todavía no hay 24
    assert horizon_end(T0, list(reversed(bars)), 24) == T0 + timedelta(hours=24)


def test_the_default_horizon_is_the_backtest_one():
    from tools.p2_backtest import FORWARD_PATH_MAX_BARS
    assert MAX_HORIZON == FORWARD_PATH_MAX_BARS == 2160


def test_a_missing_thesis_is_refused():
    # Niveles del mismo lado (no_levels) lo decide T27 antes de llamar.
    with pytest.raises(ValueError):
        _resolve({"1M": _bars("1M", T0, 5)}, thesis=None)


def test_the_open_outcome_is_the_same_word_as_the_resolution_time_source():
    assert OUTCOME_OPEN == RESOLUTION_TIME_SOURCE_OPEN
    assert (OUTCOME_CONFIRMED, OUTCOME_INVALIDATED) == ("confirmed", "invalidated")


# --- precio de partida, tesis, no_levels, R y MAE/MFE estructurales (T27, RF-4, RF-4d; plan.md §3.4) -----------

def _candles(timeframe, start, n, special=None):
    """Velas planas (open=close=100, high=101, low=99); `special` = {índice: (high, low, close)} para las que importan."""
    step = timedelta(minutes=LADDER_MINUTES[timeframe])
    special = special or {}
    out = []
    for i in range(n):
        high, low, close = special.get(i, (101.0, 99.0, 100.0))
        out.append(Candle(time=start + i * step, open=100.0, high=high, low=low, close=close))
    return out


ANCHOR = T0 + timedelta(minutes=10, seconds=30)  # 00:10:30: la última vela cerrada es la de 00:09, el camino arranca 00:11


def test_long_confirmed_with_start_price_r_and_hand_checked_mae_and_mfe():
    # Partida 100 (cierre de la vela de 00:09). Camino: 00:11 baja a 97.5, 00:13 sube a 104, 00:15 toca 110.5.
    # Después del toque, 00:17 baja a 80: no cuenta, la resolución ya ocurrió.
    bank = {"1M": _candles("1M", T0, 30, {11: (101.0, 97.5, 100.0), 13: (104.0, 99.0, 100.0),
                                           15: (110.5, 99.0, 100.0), 17: (101.0, 80.0, 100.0)})}

    result = resolve_analysis(ANCHOR, bank, 110.0, 90.0)

    assert (result.outcome, result.start_price, result.thesis_direction, result.r) == (OUTCOME_CONFIRMED, 100.0, "long", 10.0)
    assert (result.structural_mae, result.structural_mfe) == (97.5, 110.5)  # long: MAE = el mínimo, MFE = el máximo
    assert result.first_touch.touch_time == T0 + timedelta(minutes=15)


def test_short_confirmed_mae_is_the_highest_price_and_mfe_the_lowest():
    # Validación abajo (90), invalidación arriba (110): tesis short. 00:11 sube a 102.5, 00:12 baja a 95, 00:14 toca 89.5.
    bank = {"1M": _candles("1M", T0, 30, {11: (102.5, 99.0, 100.0), 12: (101.0, 95.0, 100.0),
                                           14: (101.0, 89.5, 100.0)})}

    result = resolve_analysis(ANCHOR, bank, 90.0, 110.0)

    assert (result.outcome, result.thesis_direction, result.r) == (OUTCOME_CONFIRMED, "short", 10.0)
    assert (result.structural_mae, result.structural_mfe) == (102.5, 89.5)


def test_long_invalidated_mae_reaches_the_invalidation():
    bank = {"1M": _candles("1M", T0, 30, {12: (103.0, 99.0, 100.0), 14: (101.0, 89.0, 100.0)})}

    result = resolve_analysis(ANCHOR, bank, 120.0, 90.0)  # objetivo más lejos que la invalidación: R es |100 - 90|

    assert (result.outcome, result.structural_mae, result.structural_mfe) == (OUTCOME_INVALIDATED, 89.0, 103.0)
    assert result.r == 10.0


@pytest.mark.parametrize("evp, si", [(105.0, 108.0), (92.0, 95.0), (100.0, 90.0), (None, 90.0), (110.0, None)])
def test_missing_levels_or_both_on_the_same_side_of_the_start_price_is_no_levels(evp, si):
    # RF-4d: los dos arriba, los dos abajo, uno justo en el precio de partida, o alguno vacío.
    result = resolve_analysis(ANCHOR, {"1M": _candles("1M", T0, 30)}, evp, si)
    assert (result.outcome, result.first_touch, result.structural_mae, result.r) == (REASON_NO_LEVELS, None, None, None)


def test_without_a_touch_there_is_no_structural_mae_nor_mfe():
    open_bank = {"1H": _candles("1H", T0, 40)}
    result = resolve_analysis(T0 + timedelta(minutes=90), open_bank, 110.0, 90.0, max_horizon=24)
    assert (result.outcome, result.structural_mae, result.structural_mfe) == (OUTCOME_OPEN, None, None)
    assert result.r == 10.0  # el R sí se conoce

    ambiguous_bank = {"1M": _candles("1M", T0, 30, {13: (111.0, 89.0, 100.0)})}
    assert resolve_analysis(ANCHOR, ambiguous_bank, 110.0, 90.0).structural_mae is None


def test_an_anchor_outside_the_bank_keeps_its_reason():
    bank = {"1M": _candles("1M", T0, 30)}
    assert resolve_analysis(T0 - timedelta(minutes=1), bank, 110.0, 90.0) == AnalysisResolution(REASON_NO_HISTORY)
    assert resolve_analysis(T0 + timedelta(hours=1), bank, 110.0, 90.0) == AnalysisResolution(REASON_PENDING_CANDLES)


def test_start_price_is_the_close_of_the_last_closed_candle_at_the_anchor():
    bank = {"1M": _candles("1M", T0, 30, {9: (101.0, 99.0, 100.7)}), "5M": _candles("5M", T0, 6, {1: (101.0, 99.0, 99.3)})}
    # A las 00:10:30 la de 1M de 00:10 todavía no cerró: vale la de 00:09 (cierra 00:10), más reciente que la de 5M de 00:05.
    assert start_price(ANCHOR, bank) == StartPrice(100.7, "1M", T0 + timedelta(minutes=10))
    # Justo a las 00:10, la de 00:09 de 1M y la de 00:05 de 5M cierran a la vez: gana la más fina.
    assert start_price(T0 + timedelta(minutes=10), bank) == StartPrice(100.7, "1M", T0 + timedelta(minutes=10))


def test_start_price_uses_a_coarser_candle_when_it_closed_more_recently():
    # El 1M empieza a las 00:10: a las 00:10:30 no tiene ninguna vela cerrada. La de 5M de 00:05 sí (cierra 00:10).
    bank = {"1M": _candles("1M", T0 + timedelta(minutes=10), 20), "5M": _candles("5M", T0, 6, {1: (101.0, 99.0, 99.3)})}
    assert start_price(ANCHOR, bank) == StartPrice(99.3, "5M", T0 + timedelta(minutes=10))


def test_start_price_after_a_closed_market_is_the_last_close_before_it():
    friday = _candles("1M", T0, 60, {59: (101.0, 99.0, 100.4)})
    sunday = _candles("1M", T0 + timedelta(days=2), 60)
    assert start_price(T0 + timedelta(days=1), {"1M": friday + sunday}) == StartPrice(100.4, "1M", T0 + timedelta(hours=1))


def test_without_any_closed_candle_at_the_anchor_there_is_no_start_price():
    bank = {"1M": _candles("1M", T0, 30)}
    assert start_price(T0 + timedelta(seconds=30), bank) is None
    assert resolve_analysis(T0 + timedelta(seconds=30), bank, 110.0, 90.0) == AnalysisResolution(REASON_NO_HISTORY)


def test_candles_feed_the_path_and_the_first_touch_like_any_ohlc_bar():
    bank = {"1M": _candles("1M", T0, 30, {15: (110.5, 99.0, 100.0)})}
    path = _path(ANCHOR, bank)
    assert path[0].time == T0 + timedelta(minutes=11)
    assert resolve_first_touch(ANCHOR, bank, "long", 110.0, 90.0).touch_time == T0 + timedelta(minutes=15)


# --- Structural Resolution y Failure Reason (T28, RF-8 a RF-8d; plan.md §3.5) -----------------------------------
# Escenario long: partida 100 (cierre de 00:09), objetivo 110, invalidación 90, R = 10, Mark Price 101. El toque es la
# vela de 1M de las 00:15. `specials` cambia las velas de después del toque: {minutos desde el toque: (high, low)}.

def _scenario(kind, specials=None, minutes_after=1500):
    specials = specials or {}
    rows = []
    for i in range(16 + minutes_after):
        offset = i - 15
        if offset < 0:
            high, low = 101.0, 99.0
        elif offset == 0:
            high, low = (110.5, 99.0) if kind == "confirmed" else (101.0, 89.5)
        else:
            base = 105.0 if kind == "confirmed" else 95.0
            high, low = specials.get(offset, (base + 0.5, base - 0.5))
        mid = (high + low) / 2
        rows.append(Candle(T0 + timedelta(minutes=i), mid, high, low, mid))
    return {"1M": rows}


def _propose(bank, mark_price=101.0, evp=110.0, si=90.0):
    resolution = resolve_analysis(ANCHOR, bank, evp, si)
    return resolution, propose_structural(resolution, bank, evp, si, mark_price)


def _mirror(bank):
    """El mismo escenario visto como short: cada precio p pasa a 200 - p (el máximo y el mínimo se intercambian)."""
    return {tf: [Candle(c.time, 200 - c.open, 200 - c.low, 200 - c.high, 200 - c.close) for c in candles]
            for tf, candles in bank.items()}


@pytest.mark.parametrize("offset, expected", [(23 * 60 + 59, STRUCTURAL_REVERTED), (24 * 60 + 1, STRUCTURAL_MINIMAL)])
def test_confirmed_is_reverted_only_if_price_returns_to_the_mark_price_within_24h(offset, expected):
    bank = _scenario("confirmed", {offset: (105.5, 100.9)})  # baja a 100.9, por debajo del Mark Price (101)
    assert _propose(bank)[1] == StructuralProposal(expected, FAILURE_NA)


@pytest.mark.parametrize("offset, high, expected", [
    (600, 115.0, STRUCTURAL_EXPANSION),   # 0.50R más allá del objetivo
    (600, 114.9, STRUCTURAL_MINIMAL),     # 0.49R
    (1445, 120.0, STRUCTURAL_MINIMAL),    # mucho más, pero pasadas las 24 h
])
def test_confirmed_expansion_needs_half_r_beyond_the_target_within_24h(offset, high, expected):
    bank = _scenario("confirmed", {offset: (high, 104.5)})
    assert _propose(bank)[1] == StructuralProposal(expected, FAILURE_NA)


def test_the_expansion_window_and_the_revert_window_have_their_own_lengths(monkeypatch):
    # REVERT_H y EXPANSION_H son reglas distintas (N13, N14), aunque hoy valgan las dos 24 h.
    import core.candle_resolution as resolution_module
    monkeypatch.setattr(resolution_module, "EXPANSION_H", 12)
    assert _propose(_scenario("confirmed", {13 * 60: (120.0, 104.5)}))[1].structural_resolution == STRUCTURAL_MINIMAL
    monkeypatch.setattr(resolution_module, "EXPANSION_H", 30)
    later = _scenario("confirmed", {26 * 60: (120.0, 104.5)}, minutes_after=1900)
    assert _propose(later)[1].structural_resolution == STRUCTURAL_EXPANSION
    # Con la ventana de expansión más larga, la vuelta al Mark Price sigue mirándose solo en sus 24 h.
    late_revert = _scenario("confirmed", {25 * 60: (105.5, 100.9)}, minutes_after=1900)
    assert _propose(late_revert)[1].structural_resolution == STRUCTURAL_MINIMAL


# N45 (decidido por el usuario el 2026-10-03): gana lo que pasa primero; en la misma vela, "revertido".
@pytest.mark.parametrize("specials, expected", [
    ({60: (120.0, 104.5), 300: (105.5, 100.9)}, STRUCTURAL_EXPANSION),  # se expande y después vuelve
    ({60: (105.5, 100.9), 300: (120.0, 104.5)}, STRUCTURAL_REVERTED),   # vuelve y después se expande
    ({60: (120.0, 100.9)}, STRUCTURAL_REVERTED),                         # las dos en la misma vela
])
def test_whichever_happens_first_wins(specials, expected):
    bank = _scenario("confirmed", specials)
    assert _propose(bank)[1] == StructuralProposal(expected, FAILURE_NA)
    assert _propose(_mirror(bank), mark_price=99.0, evp=90.0, si=110.0)[1] == StructuralProposal(expected, FAILURE_NA)


def test_an_expansion_before_the_end_of_the_candles_is_final():
    # Con menos de 24 h de velas: una expansión ya ocurrida no la cambia una vuelta posterior (N45), así que no es
    # pending_candles. Sin expansión ni vuelta todavía, sí lo es.
    assert _propose(_scenario("confirmed", {60: (120.0, 104.5)}, minutes_after=600))[1] == StructuralProposal(
        STRUCTURAL_EXPANSION, FAILURE_NA)


def test_the_expansion_window_closes_at_the_invalidation_touch():
    # N20: se mide hasta lo primero que ocurra, 24 h o el toque de la invalidación. El Mark Price va por debajo de la
    # invalidación (artificial) para que ese toque no cuente como "revertido".
    bank = _scenario("confirmed", {60: (105.5, 89.0), 120: (120.0, 104.5)})
    assert _propose(bank, mark_price=85.0)[1].structural_resolution == STRUCTURAL_MINIMAL
    assert _propose(_scenario("confirmed", {120: (120.0, 104.5)}), mark_price=85.0)[1].structural_resolution == (
        STRUCTURAL_EXPANSION)


def test_without_mark_price_the_revert_is_measured_against_the_start_price():
    bank = _scenario("confirmed", {300: (105.5, 100.5)})  # llega al Mark Price (101) pero no a la partida (100)
    assert _propose(bank, mark_price=101.0)[1].structural_resolution == STRUCTURAL_REVERTED
    assert _propose(bank, mark_price=None)[1].structural_resolution == STRUCTURAL_MINIMAL


def test_the_touch_candle_itself_does_not_count_as_a_revert():
    # La vela del toque va de 99 a 110.5: su mínimo pudo ser antes del toque, así que no prueba una vuelta.
    assert _propose(_scenario("confirmed"))[1].structural_resolution == STRUCTURAL_MINIMAL


def test_confirmed_with_less_than_24h_of_candles_after_the_touch_is_pending_unless_it_already_reverted():
    assert _propose(_scenario("confirmed", minutes_after=600))[1] == StructuralProposal(
        missing_reason=REASON_PENDING_CANDLES)
    already = _scenario("confirmed", {300: (105.5, 100.9)}, minutes_after=600)
    assert _propose(already)[1].structural_resolution == STRUCTURAL_REVERTED


@pytest.mark.parametrize("low, expected", [(80.0, FAILURE_LIQUIDITY_SWEEP), (79.9, None)])
def test_invalidated_is_a_liquidity_sweep_only_with_an_excess_of_at_most_1r(low, expected):
    bank = _scenario("invalidated", {60: (95.5, low), 600: (110.5, 104.5)}, minutes_after=3000)
    resolution, proposal = _propose(bank)
    assert resolution.outcome == OUTCOME_INVALIDATED
    assert proposal == StructuralProposal(STRUCTURAL_NA, expected)  # exceso 1.0R barre; 1.01R no


@pytest.mark.parametrize("hours, expected", [(47, FAILURE_LIQUIDITY_SWEEP), (49, None)])
def test_invalidated_is_a_liquidity_sweep_only_if_the_target_is_touched_within_48h(hours, expected):
    bank = _scenario("invalidated", {hours * 60: (110.5, 104.5)}, minutes_after=3000)
    assert _propose(bank)[1] == StructuralProposal(STRUCTURAL_NA, expected)


def test_the_candle_that_touches_the_target_also_counts_for_the_sweep_excess():
    # No se sabe si su mínimo fue antes o después del toque del objetivo: cuenta, para no proponer un sweep de más.
    bank = _scenario("invalidated", {600: (110.5, 79.0)}, minutes_after=3000)
    assert _propose(bank)[1] == StructuralProposal(STRUCTURAL_NA, None)


def test_invalidated_with_less_than_48h_of_candles_and_no_target_yet_is_pending():
    assert _propose(_scenario("invalidated", minutes_after=600))[1] == StructuralProposal(
        STRUCTURAL_NA, None, REASON_PENDING_CANDLES)


@pytest.mark.parametrize("kind, specials, mark_price", [
    ("confirmed", {23 * 60 + 59: (105.5, 100.9)}, 101.0),
    ("confirmed", {600: (115.0, 104.5)}, 101.0),
    ("confirmed", {600: (114.9, 104.5)}, None),
    ("invalidated", {60: (95.5, 80.0), 600: (110.5, 104.5)}, 101.0),
    ("invalidated", {60: (95.5, 79.9), 600: (110.5, 104.5)}, 101.0),
])
def test_a_short_gets_the_same_proposal_as_the_mirrored_long(kind, specials, mark_price):
    bank = _scenario(kind, specials, minutes_after=3000)
    long_result = _propose(bank, mark_price)[1]
    short_mark = None if mark_price is None else 200 - mark_price
    short_resolution, short_result = _propose(_mirror(bank), short_mark, evp=90.0, si=110.0)
    assert short_resolution.thesis_direction == "short"
    assert short_result == long_result


def test_propose_structural_no_longer_knows_about_overlap():
    """N52: el Overlap es solo una etiqueta; la propuesta sale del toque y ya no hay código `overlap`."""
    import inspect
    import core.candle_resolution as candle_resolution
    assert "overlap" not in inspect.signature(propose_structural).parameters
    assert not hasattr(candle_resolution, "FAILURE_OVERLAP")


def test_without_a_touch_nothing_is_proposed():
    ambiguous = {"1M": _candles("1M", T0, 30, {13: (111.0, 89.0, 100.0)})}
    pending = {"1M": _candles("1M", T0, 30)}
    for bank in (ambiguous, pending):
        assert _propose(bank)[1] == StructuralProposal()
    assert _propose(pending, evp=105.0, si=108.0)[1] == StructuralProposal()  # no_levels


# --- chequeo del Mark Price (T31, RF-3, RF-3b, N16, N33; plan.md §3.7) ---------------------------------------------

MP_TIME = T0 + timedelta(hours=10, minutes=7, seconds=30)
MP_BANK = {
    "1M": [Candle(T0 + timedelta(hours=10, minutes=7), 100.1, 100.2, 100.0, 100.1)],
    "5M": [Candle(T0 + timedelta(hours=10, minutes=5), 100.0, 100.5, 99.8, 100.2)],
    "15M": [Candle(T0 + timedelta(hours=10), 100.0, 101.0, 99.5, 100.5)],
}


@pytest.mark.parametrize("mark_price, expected", [
    (100.1, MarkPriceCheck(True, "1M", 0.0)),               # cae en la vela de 1M de las 10:07
    (100.4, MarkPriceCheck(True, "5M", 0.0)),               # recién en la de 5M
    (100.25, MarkPriceCheck(True, "5M", 0.0)),              # en 1M solo con la tolerancia: la tolerancia es de la última
    (100.8, MarkPriceCheck(True, "15M", 0.0)),              # recién en la de 15M
    (101.05, MarkPriceCheck(True, "15M", pytest.approx(0.05))),   # afuera por 0.05, dentro del 0.1% (0.101)
    (101.2, MarkPriceCheck(False, "15M", pytest.approx(0.2))),    # afuera por 0.2: advertencia
    (99.3, MarkPriceCheck(False, "15M", pytest.approx(0.2))),     # también por abajo
])
def test_the_mark_price_is_looked_for_in_1m_then_5m_then_15m_with_01pct_only_in_15m(mark_price, expected):
    assert check_mark_price(mark_price, MP_TIME, MP_BANK) == expected


def test_without_1m_at_that_time_it_starts_in_5m():
    bank = {"5M": MP_BANK["5M"], "15M": MP_BANK["15M"]}
    assert check_mark_price(100.1, MP_TIME, bank) == MarkPriceCheck(True, "5M", 0.0)


def test_without_15m_at_that_time_the_tolerance_applies_to_the_last_timeframe_with_a_candle():
    bank = {"1M": MP_BANK["1M"], "5M": MP_BANK["5M"]}
    assert check_mark_price(100.55, MP_TIME, bank) == MarkPriceCheck(True, "5M", pytest.approx(0.05))
    assert check_mark_price(100.7, MP_TIME, bank) == MarkPriceCheck(False, "5M", pytest.approx(0.2))


def test_a_time_exactly_on_a_candle_close_belongs_to_the_next_candle():
    # A las 10:08 en punto la vela de 1M de las 10:07 ya cerró: no la contiene. En el banco no hay de 10:08, así que vale 5M.
    assert check_mark_price(100.1, T0 + timedelta(hours=10, minutes=8), MP_BANK) == MarkPriceCheck(True, "5M", 0.0)


def test_a_time_the_bank_does_not_cover_cannot_be_checked():
    assert check_mark_price(100.1, MP_TIME - timedelta(days=1), MP_BANK) == MarkPriceCheck(
        None, None, None, REASON_NO_HISTORY)
    assert check_mark_price(100.1, MP_TIME + timedelta(days=1), MP_BANK) == MarkPriceCheck(
        None, None, None, REASON_PENDING_CANDLES)
    # Dentro del banco pero sin ninguna vela que contenga esa hora (por ejemplo, con el mercado cerrado).
    gap = {"1M": _candles("1M", T0, 5) + _candles("1M", T0 + timedelta(hours=2), 5)}
    assert check_mark_price(100.1, T0 + timedelta(hours=1), gap) == MarkPriceCheck(None, None, None,
                                                                                  MARK_PRICE_NO_CANDLE)


def test_without_mark_price_time_the_range_of_the_20_minutes_before_saving_is_used():
    # RF-3b: el rango de precios de [created_at - 20 min, created_at] en la TF más fina que lo cubre.
    one_minute = [Candle(T0 + timedelta(minutes=i), 100.0, 100.0 + i / 10, 100.0 - i / 10, 100.0) for i in range(40)]
    created_at = T0 + timedelta(minutes=30)
    # Velas de 00:10 a 00:29: el mínimo es 97.1 y el máximo 102.9. La de 00:30 abre justo en created_at y no entra:
    # sus precios son posteriores al guardado.
    assert check_mark_price_in_period(102.5, created_at - timedelta(minutes=20), created_at,
                                      {"1M": one_minute}) == MarkPriceCheck(True, "1M", 0.0)
    assert check_mark_price_in_period(104.0, created_at - timedelta(minutes=20), created_at,
                                      {"1M": one_minute}) == MarkPriceCheck(False, "1M", pytest.approx(1.1))
    # Sin 1M en ese período, se usa la de 15M.
    fifteen = [Candle(T0, 100.0, 101.0, 99.0, 100.0), Candle(T0 + timedelta(minutes=15), 100.0, 102.0, 98.5, 100.0),
               Candle(T0 + timedelta(minutes=30), 100.0, 100.5, 99.5, 100.0)]
    assert check_mark_price_in_period(101.8, created_at - timedelta(minutes=20), created_at,
                                      {"15M": fifteen}) == MarkPriceCheck(True, "15M", 0.0)
    assert check_mark_price_in_period(100.0, T0 + timedelta(days=3), T0 + timedelta(days=3, minutes=20),
                                      {"15M": fifteen}).reason == REASON_PENDING_CANDLES
    # Con 1M y 15M a la vez, manda la 1M aunque la de 15M tenga un rango más ancho.
    assert check_mark_price_in_period(103.5, created_at - timedelta(minutes=20), created_at,
                                      {"1M": one_minute, "15M": [Candle(T0, 100.0, 104.0, 96.0, 100.0),
                                                                 Candle(T0 + timedelta(minutes=15), 100.0, 104.0,
                                                                        96.0, 100.0)]}).fits is False


# --- Excepciones de toque (T30b, N46) ------------------------------------------------------------------------------
# El operador confirma en su gráfico que un nivel se tocó aunque el banco (el feed del broker) no llegue: el toque pasa
# a la vela de máximo acercamiento a ese nivel, antes del primer toque real o hasta donde llega el camino.

def _near_miss_bank(specials, n=60):
    return {"1M": _candles("1M", T0, n, specials)}


def test_an_exception_moves_the_touch_to_the_closest_candle_before_the_real_first_touch():
    # 00:20 llega a 109.9 (no toca 110); 00:25 vuelve a 109.9 (empate: vale la primera); 00:40 toca la invalidación.
    bank = _near_miss_bank({20: (109.9, 99.0, 100.0), 25: (109.9, 99.0, 100.0), 40: (101.0, 89.5, 100.0)})
    assert resolve_analysis(ANCHOR, bank, 110.0, 90.0).outcome == OUTCOME_INVALIDATED

    resolution = resolve_analysis(ANCHOR, bank, 110.0, 90.0, touched_level=LEVEL_VALIDATION)

    touch = resolution.first_touch
    assert resolution.outcome == OUTCOME_CONFIRMED
    assert (touch.touch_time, touch.timeframe, touch.level, touch.direction) == (
        T0 + timedelta(minutes=20), "1M", LEVEL_VALIDATION, "long")
    assert (touch.exception, touch.closest_price, touch.path_end) == (True, 109.9, T0 + timedelta(minutes=21))
    assert (resolution.structural_mae, resolution.structural_mfe) == (99.0, 109.9)  # hasta la vela de la excepción
    mirrored = resolve_analysis(ANCHOR, _mirror(bank), 90.0, 110.0, touched_level=LEVEL_VALIDATION)
    assert (mirrored.outcome, mirrored.first_touch.direction, mirrored.first_touch.closest_price) == (
        OUTCOME_CONFIRMED, "short", 200 - 109.9)


def test_an_exception_without_any_real_touch_uses_the_whole_path():
    bank = _near_miss_bank({20: (109.9, 99.0, 100.0)}, n=30)  # el banco termina sin toques: pending_candles
    assert resolve_analysis(ANCHOR, bank, 110.0, 90.0).outcome == REASON_PENDING_CANDLES
    resolution = resolve_analysis(ANCHOR, bank, 110.0, 90.0, touched_level=LEVEL_VALIDATION)
    assert resolution.outcome == OUTCOME_CONFIRMED and resolution.first_touch.touch_time == T0 + timedelta(minutes=20)


def test_an_exception_can_also_confirm_the_invalidation():
    bank = _near_miss_bank({20: (101.0, 90.2, 100.0), 40: (110.5, 99.0, 100.0)})
    resolution = resolve_analysis(ANCHOR, bank, 110.0, 90.0, touched_level=LEVEL_INVALIDATION)
    assert resolution.outcome == OUTCOME_INVALIDATED
    assert (resolution.first_touch.direction, resolution.first_touch.closest_price) == ("short", 90.2)


def test_an_exception_for_the_level_the_bank_already_touches_changes_nothing():
    bank = _near_miss_bank({20: (110.5, 99.0, 100.0)})
    assert resolve_analysis(ANCHOR, bank, 110.0, 90.0, touched_level=LEVEL_VALIDATION) == resolve_analysis(
        ANCHOR, bank, 110.0, 90.0)


def test_an_exception_without_candles_after_the_anchor_changes_nothing():
    bank = _near_miss_bank({}, n=5)
    late = T0 + timedelta(hours=2)  # el banco termina antes del ancla
    assert resolve_analysis(late, bank, 110.0, 90.0, touched_level=LEVEL_VALIDATION) == resolve_analysis(
        late, bank, 110.0, 90.0)


def test_the_candle_of_the_real_touch_is_not_a_candidate_for_the_exception():
    # La vela de las 00:40 toca la invalidación y además llega a 109.95: dentro de esa vela no se sabe el orden, así
    # que la excepción queda en la de las 00:20 (109.9), anterior al toque real.
    bank = _near_miss_bank({20: (109.9, 99.0, 100.0), 40: (109.95, 89.5, 100.0)})
    resolution = resolve_analysis(ANCHOR, bank, 110.0, 90.0, touched_level=LEVEL_VALIDATION)
    assert resolution.first_touch.touch_time == T0 + timedelta(minutes=20)
