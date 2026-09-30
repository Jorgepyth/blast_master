"""
tests/test_p2_bank_v2.py — P2 banco v2 (tools/p2_bank_v2.py, jupyter/p2_banco_v2.ipynb).

Datos sintéticos solamente: nada de este archivo lee las DBs reales ni el banco de
velas. Los tres tests que pide el pre-registro para el 2H
(jupyter/p2_banco_v2/preregistro.md, §6) son:
  (a) test_no_2h_bar_used_contains_data_at_or_after_the_anchor_*
  (b) test_resampled_2h_is_the_exact_aggregation_of_its_1h_bars
  (c) test_dst_week_*
"""
import sys
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from tools.database import AnalysisLayer, Base, TacticalAudit, UnifiedDepartment
from core.stats_tests import mcnemar_exact, net_score, permutation_max_net_test
from tools.p2_backtest import (
    MODEL_A,
    MODEL_D,
    MODEL_E,
    CsvOHLCProvider,
    ModelSpec,
    TFIndicatorSnapshot,
    calibrate_clock_offset,
    compute_score_p2_sistematico,
    rescale_p2_sistematico,
)
from tools import p2_bank_v2 as v2

# El exportador importa MetaTrader5 al cargarse (Windows-only): mismo stand-in que
# tests/test_export_p2_ohlc.py, para poder comparar las dos implementaciones de DST.
if "MetaTrader5" not in sys.modules:
    _fake_mt5 = MagicMock()
    for _i, _name in enumerate(("W1", "D1", "H12", "H4", "H1", "M30", "M15", "M5", "M1", "H2"), start=1):
        setattr(_fake_mt5, f"TIMEFRAME_{_name}", _i)
    sys.modules["MetaTrader5"] = _fake_mt5

from windows_export import export_p2_ohlc as exporter  # noqa: E402


# ---------------------------------------------------------------------------
# Ayudas
# ---------------------------------------------------------------------------

def _bars(start, periods, minutes, seed=0, base=1000.0):
    """Velas con precios únicos: cada vela tiene su propio rango."""
    rng = np.random.default_rng(seed)
    times = pd.date_range(start, periods=periods, freq=f"{minutes}min")
    opens = base + np.cumsum(rng.normal(0, 1, periods))
    closes = opens + rng.normal(0, 1, periods)
    highs = np.maximum(opens, closes) + rng.uniform(0.1, 1.0, periods)
    lows = np.minimum(opens, closes) - rng.uniform(0.1, 1.0, periods)
    return pd.DataFrame({"time": times, "open": opens, "high": highs, "low": lows, "close": closes})


def _server_hours_to_gt(server_labels, base_offset=2, rule="us"):
    """Lo que hace el exportador: hora del servidor -> UTC (offset por vela) -> GT naive."""
    epochs = pd.Series([int(pd.Timestamp(t, tz="UTC").timestamp()) for t in server_labels])
    utc, ambiguous = exporter.server_time_to_utc_dst(epochs, base_offset, rule)
    gt = exporter.utc_to_gt_naive(utc)
    return gt[~ambiguous.to_numpy()].reset_index(drop=True), pd.Series(pd.to_datetime(server_labels))[~ambiguous.to_numpy()].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

def test_2x_models_use_d_rules_on_the_five_short_timeframes_and_sum_to_one():
    assert [m.name for m in v2.MODELS_2X] == ["2A", "2B", "2C", "2D", "2E"]
    for m in v2.MODELS_2X:
        assert m.timeframes == ("1D", "2H", "1H", "30M", "5M")
        assert m.ema_chain == MODEL_D.ema_chain and m.use_di is MODEL_D.use_di
        assert sum(m.weights.values()) == pytest.approx(1.0, abs=1e-12)
    assert v2.MODELS_2X[1].weights == {"1D": 0.10, "2H": 0.25, "1H": 0.25, "30M": 0.20, "5M": 0.20}
    assert len({tuple(m.weights.values()) for m in v2.MODELS_2X}) == 5  # R3: ningún peso repetido


def test_g_and_d_eq_controls():
    assert v2.MODEL_G.timeframes == MODEL_E.timeframes and v2.MODEL_G.use_di and v2.MODEL_G.ema_chain == (20, 100, 200)
    assert v2.MODEL_G.weights == {"1W": 0.10, "1D": 0.10, "12H": 0.10, "4H": 0.10, "1H": 0.20, "30M": 0.20, "15M": 0.20}
    assert v2.MODEL_DEQ.timeframes == MODEL_D.timeframes
    assert set(v2.MODEL_DEQ.weights.values()) == {1 / 6}


# ---------------------------------------------------------------------------
# Proveedor
# ---------------------------------------------------------------------------

def test_bank_provider_gives_the_same_as_csv_provider_on_existing_timeframes(tmp_path):
    for tf, minutes in (("1H", 60), ("15M", 15)):
        _bars("2026-06-01", 1200, minutes, seed=minutes).to_csv(tmp_path / f"{tf}.csv", index=False)
    old, new = CsvOHLCProvider(str(tmp_path)), v2.BankProvider(str(tmp_path))
    for anchor in (datetime(2026, 6, 20, 7, 40), datetime(2026, 6, 25, 8, 0), datetime(2026, 6, 26, 13, 5)):
        for tf in ("1H", "15M"):
            pd.testing.assert_frame_equal(old.closed_bars(tf, anchor), new.closed_bars(tf, anchor))
            assert old.bar_containing(tf, anchor) == new.bar_containing(tf, anchor)
            assert old.get_indicator_snapshot(tf, anchor) == new.get_indicator_snapshot(tf, anchor)
            assert new.n_closed(tf, anchor) == len(old.closed_bars(tf, anchor))


def test_bank_provider_2h_uses_120_minute_bars(tmp_path):
    prov = v2.BankProvider(str(tmp_path), {"2H": _bars("2026-06-01 01:00", 50, 120)})
    assert prov.has_timeframe("2H") and not prov.has_timeframe("1H")
    anchor = datetime(2026, 6, 1, 6, 30)  # abiertas 01, 03, 05: cerraron 03:00 y 05:00; la de 05 cierra 07:00
    assert list(prov.closed_bars("2H", anchor)["time"]) == [pd.Timestamp("2026-06-01 01:00"), pd.Timestamp("2026-06-01 03:00")]
    assert prov.n_closed("2H", anchor) == 2
    assert prov.bar_containing("2H", anchor) is not None


def _perturb_after(df, anchor, minutes):
    """Cambia todo lo que una vela que no cerró en el ancla podría filtrar."""
    out = df.copy()
    late = out["time"] + pd.Timedelta(minutes=minutes) > pd.Timestamp(anchor)
    out.loc[late, ["open", "high", "low", "close"]] *= 3.0
    return out


@pytest.mark.parametrize("anchor", [datetime(2026, 7, 20, 6, 40), datetime(2026, 7, 20, 7, 0),
                                    datetime(2026, 7, 21, 8, 15)])
def test_no_2h_bar_used_contains_data_at_or_after_the_anchor_native(tmp_path, anchor):
    """(a) con 2H nativo inyectado: la vela en formación y las futuras no cambian el snapshot."""
    native = _bars("2026-05-01 01:00", 1200, 120, seed=3)
    clean = v2.BankProvider(str(tmp_path), {"2H": native})
    dirty = v2.BankProvider(str(tmp_path), {"2H": _perturb_after(native, anchor, 120)})
    assert clean.get_indicator_snapshot("2H", anchor) == dirty.get_indicator_snapshot("2H", anchor)
    used = clean.closed_bars("2H", anchor)
    assert (used["time"] + pd.Timedelta(minutes=120) <= pd.Timestamp(anchor)).all()


@pytest.mark.parametrize("anchor", [datetime(2026, 7, 20, 6, 40), datetime(2026, 7, 20, 7, 0),
                                    datetime(2026, 7, 21, 8, 15)])
def test_no_2h_bar_used_contains_data_at_or_after_the_anchor_resampled(tmp_path, anchor):
    """(a) con 2H remuestreado desde 1H: una 1H posterior al ancla no llega al snapshot."""
    h1 = _bars("2026-05-01 00:00", 2400, 60, seed=4)
    r_clean = v2.resample_1h_to_2h(h1, "us")
    r_dirty = v2.resample_1h_to_2h(_perturb_after(h1, anchor, 60), "us")
    clean = v2.BankProvider(str(tmp_path), {"2H": r_clean})
    dirty = v2.BankProvider(str(tmp_path), {"2H": r_dirty})
    assert clean.get_indicator_snapshot("2H", anchor) == dirty.get_indicator_snapshot("2H", anchor)
    used = clean.closed_bars("2H", anchor)
    assert (used["time"] + pd.Timedelta(minutes=120) <= pd.Timestamp(anchor)).all()


# ---------------------------------------------------------------------------
# Remuestreo y hora del servidor
# ---------------------------------------------------------------------------

def test_resampled_2h_is_the_exact_aggregation_of_its_1h_bars():
    """(b) cada 2H = sus dos 1H: apertura de la primera, máximo, mínimo, cierre de la segunda."""
    h1 = _bars("2026-07-06 00:00", 24 * 10, 60, seed=5)   # julio: servidor UTC+3, GT = servidor - 9 h
    r2 = v2.resample_1h_to_2h(h1, "us")
    assert (r2["n_bars"] == 2).sum() >= len(r2) - 2   # solo los dos bordes pueden quedar con una
    for _, bar in r2[r2["n_bars"] == 2].iterrows():
        pair = h1[(h1["time"] >= bar["time"]) & (h1["time"] < bar["time"] + pd.Timedelta(hours=2))]
        assert len(pair) == 2
        assert bar["open"] == pair["open"].iloc[0] and bar["close"] == pair["close"].iloc[1]
        assert bar["high"] == pair["high"].max() and bar["low"] == pair["low"].min()
    # En verano, la hora par del servidor es impar en GT (servidor = GT + 9 h).
    assert set(r2["time"].dt.hour % 2) == {1}


def test_resampled_2h_keeps_a_lone_1h_as_its_own_bar():
    # Verano: servidor = GT + 9 h, así que los tramos abren en hora impar GT. La 1H de las
    # 02:00 GT queda sola en su tramo (01:00-03:00, sin la de la 01:00); 03:00 y 04:00 forman el siguiente.
    h1 = _bars("2026-07-06 02:00", 3, 60, seed=6)
    r2 = v2.resample_1h_to_2h(h1, "us")
    assert list(r2["n_bars"]) == [1, 2]
    assert r2["open"].iloc[0] == h1["open"].iloc[0] and r2["close"].iloc[0] == h1["close"].iloc[0]


@pytest.mark.parametrize("rule", ["us", "eu"])
def test_server_is_dst_matches_the_exporter_every_hour_of_2026_and_2027(rule):
    hours = pd.Series(pd.date_range("2026-01-01", "2027-12-31 23:00", freq="h"))
    exporter_dst, exporter_ambiguous = exporter.dst_masks(hours, rule)
    mine = v2.server_is_dst(hours, rule)
    assert (mine[~exporter_ambiguous] == exporter_dst[~exporter_ambiguous]).all()
    assert (v2.server_is_ambiguous(hours, rule) == exporter_ambiguous).all()
    for year in (2026, 2027):
        assert v2.dst_transition_dates(year, rule) == exporter.dst_transition_dates(year, rule)


@pytest.mark.parametrize("rule", ["us", "eu"])
def test_gt_to_server_inverts_the_exporter_conversion_every_hour_of_2026(rule):
    server = [str(t) for t in pd.date_range("2026-01-01", "2026-12-31 23:00", freq="h")]
    gt, kept_server = _server_hours_to_gt(server, base_offset=2, rule=rule)
    pd.testing.assert_series_equal(v2.gt_to_server(gt, rule), kept_server, check_names=False)


@pytest.mark.parametrize("transition", ["2026-03-08", "2026-11-01"])
def test_dst_week_2h_boundaries_stay_on_even_server_hours(transition):
    """(c) semana del cambio de horario (regla us), mercado 24/7 como BTC."""
    day = pd.Timestamp(transition)
    server = [str(t) for t in pd.date_range(day - pd.Timedelta(days=3), day + pd.Timedelta(days=4), freq="h")]
    gt, kept_server = _server_hours_to_gt(server, base_offset=2, rule="us")
    h1 = _bars(gt.iloc[0], len(gt), 60, seed=7)
    h1["time"] = gt.to_numpy()
    r2 = v2.resample_1h_to_2h(h1, "us")

    server_open = v2.gt_to_server(r2["time"], "us")
    assert (server_open.dt.hour % 2 == 0).all() and (server_open.dt.minute == 0).all()
    assert (r2["n_bars"] <= 2).all()
    # El tramo que empieza en la hora del cambio no se arma: el exportador descarta esa vela nativa.
    assert not v2.server_is_ambiguous(server_open, "us").any()
    # Ningún tramo mezcla horas de los dos lados del cambio: sus 1H tienen el mismo offset.
    shift = kept_server.reset_index(drop=True) - pd.Series(gt).reset_index(drop=True)
    h1_bucket = v2.gt_to_server(h1["time"], "us").dt.floor("2h")
    mixed = pd.DataFrame({"bucket": h1_bucket, "shift": shift}).groupby("bucket")["shift"].nunique()
    assert (mixed == 1).all()
    # Antes del cambio de primavera (invierno, servidor = GT + 8 h) la frontera es par en GT;
    # después (verano, + 9 h), impar. En otoño, al revés.
    before = r2[r2["time"] < day - pd.Timedelta(days=1)]["time"].dt.hour % 2
    after = r2[r2["time"] > day + pd.Timedelta(days=1)]["time"].dt.hour % 2
    if transition.endswith("03-08"):
        assert set(before) == {0} and set(after) == {1}
    else:
        assert set(before) == {1} and set(after) == {0}


def test_aggregating_resampled_2h_to_4h_reproduces_4h_built_straight_from_1h():
    h1 = _bars("2026-07-06 00:00", 24 * 15, 60, seed=8)
    via_2h = v2.aggregate_server_aligned(v2.resample_1h_to_2h(h1, "us"), 4, "us")
    direct = v2.aggregate_server_aligned(h1, 4, "us")
    res = v2.compare_series(via_2h, direct)
    assert res["pct_fronteras_alineadas"] == 1.0 and res["pct_ohlc_identico"] == 1.0


def test_compare_series_detects_a_one_hour_shift():
    a = _bars("2026-07-06 01:00", 100, 120, seed=9)
    shifted = a.assign(time=a["time"] + pd.Timedelta(hours=1))
    assert v2.compare_series(a, a)["pct_ohlc_identico"] == 1.0
    assert v2.compare_series(a, shifted)["comunes"] == 0


# ---------------------------------------------------------------------------
# Velas repetidas del banco (hallazgo 2026-09-29)
# ---------------------------------------------------------------------------

def test_drop_relabeled_duplicates_keeps_the_copy_on_the_server_boundary():
    # 1D de invierno: el servidor abre a las 00:00 = 16:00 GT (UTC+2). La copia de las
    # 15:00 viene del offset único +3 y sobra. En verano la de las 15:00 es la buena.
    rows = [
        ("2026-01-14 15:00", 10.0), ("2026-01-14 16:00", 10.0),   # par repetido de invierno
        ("2026-01-15 15:00", 11.0), ("2026-01-15 16:00", 11.0),
        ("2026-07-14 15:00", 12.0), ("2026-07-15 15:00", 13.0),   # verano, sin repetir
        ("2026-07-16 15:00", 14.0), ("2026-07-16 16:00", 15.0),   # a 1 h pero distinto: no se toca
    ]
    df = pd.DataFrame({"time": pd.to_datetime([t for t, _ in rows]),
                       "open": [p for _, p in rows], "high": [p + 1 for _, p in rows],
                       "low": [p - 1 for _, p in rows], "close": [p for _, p in rows]})
    clean, stats = v2.drop_relabeled_duplicates(df, "1D", "us")
    assert stats == {"pares": 2, "sin_resolver": 0}
    assert list(clean["time"].dt.strftime("%m-%d %H")) == ["01-14 16", "01-15 16", "07-14 15", "07-15 15",
                                                           "07-16 15", "07-16 16"]


def test_on_server_boundary_for_weekly_bars_is_sunday_midnight():
    # 1W en verano: domingo 00:00 servidor = sábado 15:00 GT.
    assert v2.on_server_boundary(pd.Series(pd.to_datetime(["2026-07-18 15:00", "2026-07-18 16:00"])), "1W", "us").tolist() == [True, False]


# ---------------------------------------------------------------------------
# Huecos y cobertura
# ---------------------------------------------------------------------------

def test_missing_trading_day_runs():
    ref = pd.DataFrame({"time": pd.to_datetime([f"2026-07-{d:02d} 10:00" for d in (6, 7, 8, 9, 10, 13, 14)])})
    bars = pd.DataFrame({"time": pd.to_datetime(["2026-07-06 09:00", "2026-07-08 09:00", "2026-07-14 09:00"])})
    runs = v2.missing_trading_day_runs(bars, ref, datetime(2026, 7, 6), datetime(2026, 7, 14))
    assert runs == [(date(2026, 7, 7), date(2026, 7, 7), 1), (date(2026, 7, 9), date(2026, 7, 13), 3)]


def test_first_time_with_closed_is_the_close_of_the_nth_bar():
    df = _bars("2026-07-01", 900, 120)
    assert v2.first_time_with_closed(df, "2H", 800) == df["time"].iloc[799] + pd.Timedelta(hours=2)
    assert v2.first_time_with_closed(df.head(10), "2H", 800) is None


# ---------------------------------------------------------------------------
# Alcance y reloj
# ---------------------------------------------------------------------------

def _rec(**kw):
    base = dict(account="XAUUSD", trade_id="t", asset="XAUUSD", created_at=datetime(2026, 7, 20, 7, 0),
                is_backdated=False, evp=1100.0, si=900.0, calc_edge=0.5, market_bias="Bullish", p2_disc=1)
    base.update(kw)
    return v2.AnalysisRecord(**base)


def test_scope_filters_apply_in_order_and_anchor_is_created_at_minus_20(tmp_path):
    m1 = _bars("2026-07-20 00:00", 24 * 60, 1, seed=10)
    m1.loc[:, ["open", "high", "low", "close"]] = 1000.0
    prov = v2.BankProvider(str(tmp_path), {"1M": m1})
    assert v2.scope_analysis(_rec(evp=None), prov).status == v2.EXCL_MISSING_LEVELS
    assert v2.scope_analysis(_rec(is_backdated=True), prov).status == v2.EXCL_BACKDATED
    assert v2.scope_analysis(_rec(created_at=datetime(2026, 7, 19, 7, 0)), prov).status == v2.EXCL_NO_HISTORY
    assert v2.scope_analysis(_rec(evp=950.0, si=900.0), prov).status == v2.EXCL_LEVELS_SAME_SIDE
    ok = v2.scope_analysis(_rec(), prov)
    assert ok.status == v2.IN_SCOPE and ok.anchor == datetime(2026, 7, 20, 6, 40)
    assert ok.start_tf == "1M" and ok.start_price == 1000.0 and ok.thesis == "long"
    assert (ok.bias_original, ok.segment) == ("Bullish", v2.SEG_DIRECTIONAL)
    assert v2.scope_analysis(_rec(calc_edge=0.1), prov).segment == v2.SEG_CHOPPY


def test_starting_price_falls_back_to_the_next_finest_timeframe(tmp_path):
    m5 = _bars("2026-07-19 00:00", 12 * 48, 5, seed=11)
    m1 = _bars("2026-07-20 06:00", 30, 1, seed=12)   # 1M empieza después del ancla de abajo
    prov = v2.BankProvider(str(tmp_path), {"1M": m1, "5M": m5})
    price, tf = v2.starting_price(prov, datetime(2026, 7, 20, 5, 0))
    assert tf == "5M" and price == float(m5[m5["time"] + pd.Timedelta(minutes=5) <= "2026-07-20 05:00"]["close"].iloc[-1])
    assert v2.starting_price(prov, datetime(2026, 7, 20, 6, 10))[1] == "1M"


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _analysis(session, tid, **kw):
    campos = dict(id=tid, state="READY_FOR_NOTION", asset="XAUUSD", market_bias="Bullish", calc_edge=0.5,
                  p4_hierarchy="x", p1_timeframe="15M", p1_type="x", nodes_l1=1, nodes_l2=1,
                  tactical_classification="x", long_prob=0.5, short_prob=0.5, no_trade_prob=0.0)
    campos.update(kw)
    session.add(UnifiedDepartment(**campos))


def test_clock_check_tf_gives_the_same_as_calibrate_clock_offset(tmp_path, session):
    m15 = _bars("2026-06-01", 24 * 4 * 6, 15, seed=13)
    m15.to_csv(tmp_path / "15M.csv", index=False)
    for i in range(8):
        bar = m15.iloc[20 + i * 30]
        _analysis(session, f"f{i}")
        session.add(TacticalAudit(trade_id=f"f{i}", entry_time=bar["time"] + timedelta(minutes=5),
                                  entry_price=(bar["high"] + bar["low"]) / 2, order_filled=True))
    for i in range(5):
        bar = m15.iloc[300 + i * 40]
        _analysis(session, f"m{i}", created_at=bar["time"].to_pydatetime() + timedelta(minutes=3),
                  mark_price=float(bar["low"]) + 0.01)
    _analysis(session, "retro", created_at=datetime(2026, 6, 3), mark_price=1.0, is_backdated=True)
    session.commit()

    ref = calibrate_clock_offset(session, CsvOHLCProvider(str(tmp_path)))
    mine = v2.clock_check_tf(v2.reference_entries(session), v2.BankProvider(str(tmp_path)), "15M")
    assert mine.rate_by_offset == ref.rate_by_offset
    assert (mine.best_offset, mine.aligned, mine.n_entries) == (ref.best_offset, ref.aligned, ref.n_entries)


def test_load_analyses_reads_p2_from_the_layer_table(session):
    _analysis(session, "a1", edge_validation_price=1100, structural_invalidation=900, created_at=datetime(2026, 7, 1))
    _analysis(session, "a2", created_at=datetime(2026, 7, 2))
    session.add(AnalysisLayer(trade_id="a1", department="efficiency", layer_name="P2", score=-2))
    session.add(AnalysisLayer(trade_id="a1", department="efficiency", layer_name="P0", score=1))
    session.commit()
    recs = v2.load_analyses(session, "XAUUSD")
    assert [r.trade_id for r in recs] == ["a1", "a2"]
    assert recs[0].p2_disc == -2 and recs[1].p2_disc is None
    assert recs[0].evp == 1100.0 and recs[1].evp is None
    assert recs[0].layers == {"P2": -2, "P0": 1} and recs[1].layers == {}


def test_edge_with_p2_swaps_only_p2_in_the_production_formula():
    from core.math_engine import calculate_edge_score
    rec = _rec(layers={"P0": 2, "P1": -1, "P2": 1, "P3": 0, "P4": 2})
    assert v2.edge_with_p2(rec, 1) == calculate_edge_score(2, -1, 1, 0, 2)
    assert v2.edge_with_p2(rec, -2) == calculate_edge_score(2, -1, -2, 0, 2)
    assert v2.edge_with_p2(rec, None) is None
    assert v2.edge_with_p2(_rec(layers={"P0": 2, "P2": 1}), 1) is None   # faltan capas


# ---------------------------------------------------------------------------
# Primer toque S1 / S4
# ---------------------------------------------------------------------------

def _flat(start, periods, minutes, price=100.0):
    """Velas planas: no tocan ningún nivel hasta que un test les ponga una mecha."""
    times = pd.date_range(start, periods=periods, freq=f"{minutes}min")
    return pd.DataFrame({"time": times, "open": price, "high": price + 0.1, "low": price - 0.1, "close": price})


def _spike(df, when, high=None, low=None):
    out = df.copy()
    row = out["time"] == pd.Timestamp(when)
    assert row.sum() == 1, when
    if high is not None:
        out.loc[row, "high"] = high
    if low is not None:
        out.loc[row, "low"] = low
    return out


def _prov(tmp_path, **frames):
    return v2.BankProvider(str(tmp_path), {tf.replace("m", "M").replace("h", "H"): df for tf, df in frames.items()})


ANCHOR = datetime(2026, 7, 20, 6, 40)


def test_first_touch_resolves_to_the_level_touched_first_and_names_it(tmp_path):
    m1 = _spike(_spike(_flat("2026-07-20", 24 * 60, 1), "2026-07-20 08:15", high=111.0), "2026-07-20 09:00", low=89.0)
    prov = _prov(tmp_path, **{"1M": m1, "1H": _flat("2026-07-20", 24, 60)})
    long_thesis = v2.first_touch_s1(prov, ANCHOR, evp=110.0, si=90.0)
    assert (long_thesis.state, long_thesis.direction, long_thesis.level) == (v2.S1_RESOLVED, 1, "validation")
    assert long_thesis.touch_time == pd.Timestamp("2026-07-20 08:15") and long_thesis.touch_tf == "1M"
    assert long_thesis.hours == pytest.approx(95 / 60)
    short_thesis = v2.first_touch_s1(prov, ANCHOR, evp=90.0, si=110.0)   # mismos niveles, tesis al revés
    assert (short_thesis.direction, short_thesis.level) == (1, "invalidation")


def test_first_touch_ignores_the_bar_open_before_the_anchor_and_counts_the_one_opening_at_it(tmp_path):
    anchor = datetime(2026, 7, 20, 6, 40, 30)   # la vela 1M de las 06:40 está en formación
    forming = _spike(_flat("2026-07-20", 24 * 60, 1), "2026-07-20 06:40", high=111.0)
    prov = _prov(tmp_path, **{"1M": _spike(forming, "2026-07-20 07:00", low=89.0), "1H": _flat("2026-07-20", 24, 60)})
    ft = v2.first_touch_s1(prov, anchor, 110.0, 90.0)
    assert (ft.direction, ft.touch_time) == (-1, pd.Timestamp("2026-07-20 07:00"))   # la mecha de las 06:40 no cuenta

    at_anchor = _spike(_flat("2026-07-20", 24 * 60, 1), "2026-07-20 06:40", high=111.0)
    ft2 = v2.first_touch_s1(_prov(tmp_path, **{"1M": at_anchor, "1H": _flat("2026-07-20", 24, 60)}), ANCHOR, 110.0, 90.0)
    assert (ft2.direction, ft2.touch_time, ft2.hours) == (1, pd.Timestamp("2026-07-20 06:40"), 0.0)


def test_first_touch_is_ambiguous_when_one_bar_touches_both_levels(tmp_path):
    m1 = _spike(_flat("2026-07-20", 24 * 60, 1), "2026-07-20 08:00", high=111.0, low=89.0)
    ft = v2.first_touch_s1(_prov(tmp_path, **{"1M": m1, "1H": _flat("2026-07-20", 24, 60)}), ANCHOR, 110.0, 90.0)
    assert ft.state == v2.S1_AMBIGUOUS and ft.direction is None
    assert not ft.within_s4


def test_first_touch_uses_the_finest_timeframe_and_switches_at_the_coarse_bar_edge(tmp_path):
    h1 = _flat("2026-07-20", 24, 60)
    m1 = _flat("2026-07-20 02:30", 600, 1)       # el 1M empieza a mitad de la vela 1H de las 02:00
    anchor = datetime(2026, 7, 20, 1, 10)

    # Toque a las 03:20: ya se recorre en 1M (la 1H de las 03:00 no se usa).
    prov = _prov(tmp_path, **{"1M": _spike(m1, "2026-07-20 03:20", high=111.0), "1H": h1})
    ft = v2.first_touch_s1(prov, anchor, 110.0, 90.0)
    assert (ft.touch_tf, ft.touch_time) == ("1M", pd.Timestamp("2026-07-20 03:20"))

    # Toque solo visible en la vela 1H de las 02:00 (abrió antes de que empiece el 1M): se usa entera.
    prov = _prov(tmp_path, **{"1M": m1, "1H": _spike(h1, "2026-07-20 02:00", low=89.0)})
    ft = v2.first_touch_s1(prov, anchor, 110.0, 90.0)
    assert (ft.touch_tf, ft.touch_time, ft.direction) == ("1H", pd.Timestamp("2026-07-20 02:00"), -1)

    # Sin superposición: el 1M de 02:30 a 02:59 queda dentro de la vela 1H de las 02:00 y no se vuelve a mirar.
    prov = _prov(tmp_path, **{"1M": _spike(m1, "2026-07-20 02:45", high=111.0), "1H": h1})
    assert v2.first_touch_s1(prov, anchor, 110.0, 90.0).state != v2.S1_RESOLVED

    # La vela 1H de la 01:00 abrió antes del ancla: su mecha no cuenta.
    prov = _prov(tmp_path, **{"1M": m1, "1H": _spike(h1, "2026-07-20 01:00", high=111.0)})
    assert v2.first_touch_s1(prov, anchor, 110.0, 90.0).state != v2.S1_RESOLVED


def test_first_touch_open_when_the_horizon_ends_and_pending_when_candles_run_out(tmp_path):
    m1 = _spike(_flat("2026-07-20", 24 * 60, 1), "2026-07-20 12:30", high=111.0)
    prov = _prov(tmp_path, **{"1M": m1, "1H": _flat("2026-07-20", 24, 60)})
    # Horizonte de 3 velas 1H desde las 06:40: las de 07, 08 y 09; termina a las 10:00, antes del toque.
    ft = v2.first_touch_s1(prov, ANCHOR, 110.0, 90.0, horizon_bars=3)
    assert ft.state == v2.S1_OPEN and ft.path_end == pd.Timestamp("2026-07-20 10:00")
    # Con horizonte largo el toque de las 12:30 entra.
    assert v2.first_touch_s1(prov, ANCHOR, 110.0, 90.0, horizon_bars=10).state == v2.S1_RESOLVED
    # Sin toque y con menos velas que el horizonte: pending (no es open).
    flat = _prov(tmp_path, **{"1M": _flat("2026-07-20", 24 * 60, 1), "1H": _flat("2026-07-20", 24, 60)})
    ft = v2.first_touch_s1(flat, ANCHOR, 110.0, 90.0)
    assert ft.state == v2.S1_PENDING and ft.path_end == pd.Timestamp("2026-07-21 00:00")


def test_s4_counts_only_touches_within_48_hours(tmp_path):
    m1 = _flat("2026-07-20", 4 * 24 * 60, 1)
    h1 = _flat("2026-07-20", 4 * 24, 60)
    inside = v2.first_touch_s1(_prov(tmp_path, **{"1M": _spike(m1, "2026-07-22 06:40", high=111.0), "1H": h1}), ANCHOR, 110.0, 90.0)
    outside = v2.first_touch_s1(_prov(tmp_path, **{"1M": _spike(m1, "2026-07-22 06:41", high=111.0), "1H": h1}), ANCHOR, 110.0, 90.0)
    assert inside.hours == 48.0 and inside.within_s4
    assert outside.state == v2.S1_RESOLVED and not outside.within_s4


def test_overlap_label_picks_the_first_later_analysis_before_the_touch():
    a, end = datetime(2026, 7, 20, 6, 40), datetime(2026, 7, 21, 6, 40)
    others = [("antes", datetime(2026, 7, 19)), ("b2", datetime(2026, 7, 20, 20)), ("b1", datetime(2026, 7, 20, 9)),
              ("después", datetime(2026, 7, 22))]
    assert v2.overlap_label(a, end, others) == ("b1", datetime(2026, 7, 20, 9))
    assert v2.overlap_label(a, datetime(2026, 7, 20, 8), others) is None


def test_evaluate_account_scopes_resolves_and_labels_overlap(tmp_path):
    m1 = _spike(_flat("2026-07-20", 3 * 24 * 60, 1), "2026-07-21 10:00", high=111.0)
    prov = _prov(tmp_path, **{"1M": m1, "1H": _flat("2026-07-20", 3 * 24, 60)})
    recs = [_rec(trade_id="a", created_at=datetime(2026, 7, 20, 7, 0), evp=110.0, si=90.0),
            _rec(trade_id="b", created_at=datetime(2026, 7, 20, 15, 0), evp=110.0, si=90.0),
            _rec(trade_id="retro", created_at=datetime(2026, 7, 20, 12, 0), is_backdated=True)]
    rows = {r.scoped.rec.trade_id: r for r in v2.evaluate_account(recs, prov, with_snapshots=False)}
    assert rows["a"].resolved and rows["a"].touch.direction == 1
    assert rows["a"].overlap == ("retro", datetime(2026, 7, 20, 12, 0))   # el retroactivo ancla en su created_at
    assert rows["b"].overlap is None                                      # nadie empieza entre b y su toque
    assert not rows["retro"].in_scope and rows["retro"].touch is None


# ---------------------------------------------------------------------------
# Predicciones y grilla
# ---------------------------------------------------------------------------

def _snap(signal, bars=1000):
    """Snapshot cuyo aporte con las reglas de D (EMA 20/100/200 + DI) es `signal`."""
    adx = {1.0: 30.0, 0.5: 22.0, 0.0: 10.0}[abs(signal)]
    if signal > 0 or signal == 0:
        return TFIndicatorSnapshot(price=4, ema20=3, ema200=1, adx14=adx, bars_available=bars, ema100=2,
                                   plus_di=30, minus_di=10)
    return TFIndicatorSnapshot(price=1, ema20=2, ema200=4, adx14=adx, bars_available=bars, ema100=3,
                               plus_di=10, minus_di=30)


def test_tf_signal_reproduces_the_per_timeframe_term_of_the_model():
    for signal in (-1.0, -0.5, 0.0, 0.5, 1.0):
        assert v2.tf_signal(_snap(signal), (20, 100, 200), True) == signal
    contra = TFIndicatorSnapshot(price=4, ema20=3, ema200=1, adx14=30, bars_available=1000, ema100=2, plus_di=10, minus_di=30)
    assert v2.tf_signal(contra, (20, 100, 200), True) == 0      # el DI contradice a las EMAs
    assert v2.tf_signal(contra, (20, 200), False) == 1.0        # sin filtro DI (modelo A) sí aporta
    assert v2.tf_signal(None, (20, 100, 200), True) is None


def test_predict_model_is_the_sign_of_the_rescaled_score_and_flags_missing_coverage():
    snaps = {"1D": _snap(1.0), "2H": _snap(1.0), "1H": _snap(0.5), "30M": _snap(0.0), "5M": _snap(-1.0)}
    m = v2.MODELS_2X[0]
    pred = v2.predict_model(snaps, m)
    score, _ = compute_score_p2_sistematico(snaps, m)
    assert pred.score == score and pred.rescaled == rescale_p2_sistematico(score)
    assert pred.pred == 1 and pred.covered and pred.missing == ()
    assert pred.signals == {"1D": 1.0, "2H": 1.0, "1H": 0.5, "30M": 0.0, "5M": -1.0}

    short = dict(snaps, **{"2H": _snap(1.0, bars=799)})
    gone = v2.predict_model(short, m)
    assert (gone.pred, gone.covered, gone.missing) == (0, False, ("2H",))
    assert v2.predict_model({k: v for k, v in snaps.items() if k != "5M"}, m).missing == ("5M",)


def test_grid_predictions_match_the_original_functions_exactly_including_ties():
    grid = v2.weight_grid()
    assert grid.shape == (10626, 5)
    levels = (-1.0, -0.5, 0.0, 0.5, 1.0)
    patterns = [(a, b, c, d, e) for a in levels for b in levels for c in levels for d in levels for e in levels]
    snapshot_list = [dict(zip(v2.TIMEFRAMES_2X, (_snap(x) for x in pat))) for pat in patterns]
    contrib, covered = v2.contribution_matrix(snapshot_list)
    assert covered.all() and contrib.shape == (3125, 5)
    rng = np.random.default_rng(0)
    rows = sorted(set(rng.choice(len(grid), size=60, replace=False))
                  | {v2.grid_index(grid, w) for w in v2.WEIGHTS_2X.values()})
    fast = v2.grid_predictions(contrib, covered, grid[rows])
    ties = 0
    for col, gi in enumerate(rows):
        model = v2.model_2x("g", grid[gi])
        for i, snaps in enumerate(snapshot_list):
            score, _ = compute_score_p2_sistematico(snaps, model)
            assert fast[i, col] == v2.sign_of(rescale_p2_sistematico(score)), (grid[gi], patterns[i])
            ties += abs(abs(score) - 0.25) < 1e-9
    assert ties > 1000   # el caso delicado (score justo en el umbral) está bien cubierto


def test_grid_weights_of_the_fixed_models_are_the_same_floats_as_their_literals():
    grid = v2.weight_grid()
    for name, w in v2.WEIGHTS_2X.items():
        assert tuple(grid[v2.grid_index(grid, w)]) == tuple(w), name


def test_contribution_matrix_marks_uncovered_analyses_and_the_grid_does_not_opine_there():
    ok = dict(zip(v2.TIMEFRAMES_2X, (_snap(1.0) for _ in range(5))))
    short = dict(ok, **{"5M": _snap(1.0, bars=10)})
    missing = {k: v for k, v in ok.items() if k != "2H"}
    contrib, covered = v2.contribution_matrix([ok, short, missing])
    assert covered.tolist() == [True, False, False]
    grid = v2.weight_grid()
    pred = v2.grid_predictions(contrib, covered, grid)
    assert (pred[0] == 1).all() and (pred[1:] == 0).all()
    # Una TF con peso 0 igual exige cobertura: la combinación "todo en 1D" tampoco opina sin 5M.
    only_1d = v2.grid_index(grid, (1.0, 0.0, 0.0, 0.0, 0.0))
    assert pred[1, only_1d] == 0


def test_grid_neighbors_move_one_step_between_two_timeframes():
    inner = v2.grid_neighbors((0.2, 0.2, 0.2, 0.2, 0.2))
    assert len(inner) == 20 and len(set(inner)) == 20
    assert all(abs(sum(w) - 1) < 1e-12 and min(w) >= 0 for w in inner)
    assert all(abs(sum(abs(a - 0.2) for a in w) - 0.10) < 1e-12 for w in inner)
    corner = v2.grid_neighbors((1.0, 0.0, 0.0, 0.0, 0.0))
    assert len(corner) == 4   # solo se puede bajar la TF que tiene peso
    grid = v2.weight_grid()
    assert all(v2.grid_index(grid, w) >= 0 for w in inner + corner)


# ---------------------------------------------------------------------------
# Estadística y etiquetas
# ---------------------------------------------------------------------------

def test_predictor_stats_counts_only_opinions_inside_the_mask():
    pred = np.array([1, -1, 0, 1, 1, -1])
    truth = np.array([1, 1, 1, -1, 1, -1])
    st = v2.predictor_stats(pred, truth)
    assert (st.hits, st.bets, st.total, st.net) == (3, 5, 6, 1)
    assert st.accuracy == 0.6 and st.opina == pytest.approx(5 / 6) and st.insufficient
    part = v2.predictor_stats(pred, truth, np.array([True, True, True, False, False, False]))
    assert (part.hits, part.bets, part.total) == (1, 2, 3)


def test_paired_compare_uses_only_analyses_where_both_opine():
    truth = np.array([1] * 10)
    x = np.array([1, 1, 1, 1, -1, 0, 1, 1, 0, -1])
    y = np.array([1, -1, -1, 1, 1, 1, 0, -1, 0, -1])
    pr = v2.paired_compare(x, y, truth)
    assert (pr.n, pr.b, pr.c, pr.n_disc) == (7, 3, 1, 4)
    assert pr.net_x - pr.net_y == 2 * (pr.b - pr.c)
    assert pr.p == mcnemar_exact(3, 1) and pr.d == pytest.approx(2 / 7)
    assert v2.pair_counts(x, y) == (7, 4)   # la MDD no necesita la verdad
    assert pr.ci[0] < pr.d < pr.ci[1]
    same = v2.paired_compare(x, x, truth)
    assert (same.b, same.c, same.p, same.ci) == (0, 0, 1.0, (0.0, 0.0))


def _paired(n, b, c):
    """Par con n pares, b a favor de X y c a favor de Y (el resto, los dos aciertan)."""
    truth = np.ones(n, dtype=int)
    x, y = np.ones(n, dtype=int), np.ones(n, dtype=int)
    y[:b] = -1
    x[b:b + c] = -1
    return v2.paired_compare(x, y, truth)


def test_r7_labels_against_d():
    assert v2.label_vs_d(_paired(19, 10, 0), 0.001) == v2.LABEL_NO_CONCLUYENTE      # menos de 20 pares
    assert v2.label_vs_d(_paired(40, 12, 1), 0.01) == v2.LABEL_SUPERA
    assert v2.label_vs_d(_paired(40, 12, 1), 0.20) == v2.LABEL_NO_DISTINGUIBLE      # falta el p de 8.1
    assert v2.label_vs_d(_paired(40, 8, 3), 0.01) == v2.LABEL_NO_DISTINGUIBLE       # McNemar no alcanza
    assert v2.label_vs_d(_paired(40, 1, 12), 0.50) == v2.LABEL_INFERIOR
    assert v2.label_vs_d(_paired(40, 5, 5), 0.01) == v2.LABEL_NO_DISTINGUIBLE       # empate: se queda D


def test_labels_against_p2_disc_add_equivalente_when_the_interval_contains_zero():
    assert v2.label_vs_disc(_paired(40, 6, 5), 0.5) == v2.LABEL_EQUIVALENTE
    assert v2.label_vs_disc(_paired(40, 0, 0), 0.5) == v2.LABEL_EQUIVALENTE
    assert v2.label_vs_disc(_paired(40, 12, 1), 0.01) == v2.LABEL_SUPERA
    assert v2.label_vs_disc(_paired(40, 12, 1), 0.30) == v2.LABEL_NO_DISTINGUIBLE   # significativo pero sin 8.1
    assert v2.label_vs_disc(_paired(40, 1, 12), 0.30) == v2.LABEL_INFERIOR
    assert v2.label_vs_disc(_paired(10, 5, 0), 0.01) == v2.LABEL_NO_CONCLUYENTE


def test_single_comparison_label_needs_no_family_p():
    assert v2.label_single_comparison(_paired(40, 12, 1)) == v2.LABEL_SUPERA
    assert v2.label_single_comparison(_paired(40, 1, 12)) == v2.LABEL_INFERIOR
    assert v2.label_single_comparison(_paired(40, 6, 5)) == v2.LABEL_NO_DISTINGUIBLE
    assert v2.label_single_comparison(_paired(15, 10, 0)) == v2.LABEL_NO_CONCLUYENTE


def test_family_permutation_gives_each_model_its_own_adjusted_p():
    rng = np.random.default_rng(1)
    truth = rng.choice([-1, 1], size=60)
    good = truth.copy(); good[:8] *= -1
    noise = rng.choice([-1, 0, 1], size=60)
    preds = {"bueno": good, "ruido": noise, "callado": np.zeros(60, dtype=int)}
    nets, p_adj, null = v2.family_permutation(preds, truth, n_perm=2000, seed=7)
    observed, p_max, _ = permutation_max_net_test(np.vstack(list(preds.values())), truth, n_perm=2000, seed=7)
    assert nets["bueno"] == observed == int(net_score(good, truth)[0])
    assert p_adj["bueno"] == p_max and p_adj["bueno"] < 0.01
    assert p_adj["ruido"] > p_adj["bueno"] and p_adj["callado"] >= p_adj["ruido"]
    assert len(null) == 2000


def test_best_of_breaks_ties_by_hits_then_by_name():
    truth = np.array([1, 1, 1, 1, 1, 1])
    preds = {"2A": np.array([1, 1, 0, 0, 0, 0]),      # neto 2, 2 aciertos
             "2B": np.array([1, 1, 1, -1, 0, 0]),     # neto 2, 3 aciertos
             "2C": np.array([1, 1, 1, -1, 0, 0]),     # igual que 2B
             "2D": np.array([1, 0, 0, 0, 0, 0]), "2E": np.array([-1, 0, 0, 0, 0, 0])}
    assert v2.best_of(preds, truth) == "2B"


def test_mdd_and_percentile_helpers():
    assert v2.mdd_pair(40, 0) is None and v2.mdd_pair(0, 0) is None
    assert v2.mdd_pair(40, 4) is None                     # con 4 discordantes ni un 4-0 se detecta
    assert v2.mdd_pair(40, 40) == pytest.approx(2 * v2.mdd_predictor(40))
    assert v2.mid_rank_percentile(np.array([1, 2, 2, 3]), 2) == 50.0
    assert v2.mid_rank_percentile(np.array([1, 2, 2, 3]), 3) == 87.5
    assert v2.two_proportions_p(10, 20, 10, 20) == 1.0 and v2.two_proportions_p(18, 20, 5, 20) < 0.001


# ---------------------------------------------------------------------------
# 2F
# ---------------------------------------------------------------------------

def _toy_grid():
    grid = np.array([[1.0, 0.0], [0.5, 0.5], [0.0, 1.0]])
    return grid, (0.5, 0.5)


def test_run_2f_predicts_each_test_analysis_with_weights_chosen_from_earlier_ones_only():
    grid, prior = _toy_grid()
    n = 60
    truth = np.ones(n, dtype=int)
    pred = np.zeros((n, 3), dtype=np.int8)
    pred[:, 0] = 1                     # la combinación 0 acierta siempre...
    pred[30:, 0] = -1                  # ...hasta la mitad; después falla
    pred[30:, 2] = 1                   # la 2 no opina al principio y acierta después
    res = v2.run_2f(pred, truth, grid, prior)

    assert res.in_sample_net.tolist() == [0, 0, 30] and res.final_idx == 2
    assert [f["train"] for f in res.folds] == [(0, 30), (0, 40), (0, 50)]
    assert [f["test"] for f in res.folds] == [(30, 40), (40, 50), (50, 60)]
    assert not res.wf_mask[:30].any() and res.wf_mask[30:].all()
    # Primer fold: con los 30 primeros gana la 0, que después falla. El resultado es el de fuera de muestra.
    assert res.folds[0]["idx"] == 0 and (res.wf_pred[30:40] == -1).all()
    assert res.folds[2]["idx"] == 2 and (res.wf_pred[50:] == 1).all()
    assert (res.wf_pred[:30] == 0).all()
    assert res.loo_idx.tolist() == [2] * n


def test_run_2f_ties_go_to_the_prior_and_leave_one_out_excludes_the_left_out_analysis():
    grid, prior = _toy_grid()
    truth = np.ones(40, dtype=int)
    pred = np.zeros((40, 3), dtype=np.int8)
    res = v2.run_2f(pred, truth, grid, prior)
    assert res.final_idx == 1 and all(f["idx"] == 1 for f in res.folds)   # todo empatado: queda el prior

    pred[7, 0] = 1                      # un solo análisis hace ganar a la combinación 0
    res = v2.run_2f(pred, truth, grid, prior)
    assert res.final_idx == 0
    assert res.loo_idx[7] == 1 and res.loo_pred[7] == 0      # sin ese análisis, vuelve al prior
    assert res.loo_idx[8] == 0


def test_permutation_2f_is_reproducible_and_separates_signal_from_noise():
    grid, prior = _toy_grid()
    rng = np.random.default_rng(3)
    n = 80
    truth = rng.choice([-1, 1], size=n)
    pred = np.zeros((n, 3), dtype=np.int8)
    pred[:, 0] = truth                                    # una combinación que sabe la verdad
    pred[:, 2] = rng.choice([-1, 1], size=n)
    every = np.ones(n, dtype=bool)
    half = np.arange(n) % 2 == 0
    out = v2.permutation_2f(pred, truth, grid, prior, {"todos": every, "pares": half}, n_perm=300, seed=5)
    again = v2.permutation_2f(pred, truth, grid, prior, {"todos": every, "pares": half}, n_perm=300, seed=5)
    assert out == again
    res = v2.run_2f(pred, truth, grid, prior)
    assert out["todos"][0] == int((res.wf_pred * truth)[res.wf_mask].sum()) == int(res.wf_mask.sum())
    assert out["pares"][0] == int((res.wf_pred * truth)[res.wf_mask & half].sum())
    assert out["todos"][1] < 0.01

    noise = np.zeros((n, 3), dtype=np.int8)
    noise[:, 0] = rng.choice([-1, 1], size=n)
    noise[:, 2] = rng.choice([-1, 1], size=n)
    assert v2.permutation_2f(noise, truth, grid, prior, {"todos": every}, n_perm=300, seed=5)["todos"][1] > 0.05


def test_random_moments_are_half_past_the_hour_inside_the_window_and_reproducible(tmp_path):
    m1 = _bars("2026-07-19", 12 * 24 * 60, 1, seed=20)
    h1 = _bars("2026-07-19", 12 * 24, 60, seed=21)
    prov = _prov(tmp_path, **{"1M": m1, "1H": h1})
    args = (prov, datetime(2026, 7, 21), datetime(2026, 7, 28), 5.0, [], 11)
    df = v2.random_moments(*args, n=40, hours=(5, 8))
    assert len(df) == 28                                   # 7 días x 4 horas: menos candidatas que 40
    assert set(df["momento"].dt.minute) == {30} and set(df["momento"].dt.hour) <= {5, 6, 7, 8}
    assert df["momento"].min() == pd.Timestamp("2026-07-21 05:30") and df["momento"].max() == pd.Timestamp("2026-07-27 08:30")
    assert df["momento"].is_monotonic_increasing
    pd.testing.assert_frame_equal(df, v2.random_moments(*args, n=40, hours=(5, 8)))
    assert len(v2.random_moments(*args, n=40)) == 40


# ---------------------------------------------------------------------------
# Fuente del 2H (F2b a F2d)
# ---------------------------------------------------------------------------

def _h2_setup(tmp_path, days=120, start="2026-04-01 00:00"):
    h1 = _bars(start, days * 24, 60, seed=30)     # verano, mercado 24/7
    prov = v2.BankProvider(str(tmp_path), {"1H": h1, "4H": v2.aggregate_server_aligned(h1, 4, "us")[v2.OHLC_COLUMNS]})
    resampled = v2.resample_1h_to_2h(h1, "us")[v2.OHLC_COLUMNS]
    # Referencias de reloj: el punto medio de una vela 1H, a los 20 minutos de su apertura.
    entries = [(h1["time"].iloc[i].to_pydatetime() + timedelta(minutes=20),
                float((h1["high"].iloc[i] + h1["low"].iloc[i]) / 2)) for i in range(200, 2000, 90)]
    return prov, resampled, entries


def test_native_2h_is_used_when_it_passes_the_three_conditions(tmp_path):
    prov, resampled, entries = _h2_setup(tmp_path)
    anchor_first, anchor_last = datetime(2026, 7, 10, 6, 40), datetime(2026, 7, 28, 6, 40)
    h2 = v2.decide_h2_source(prov, resampled.copy(), resampled, entries, anchor_first, anchor_first, anchor_last, "us")
    assert h2.source == v2.H2_NATIVE and h2.reasons == []
    assert h2.clock.aligned is True and h2.closed_at_first >= 800 and h2.gap_runs == []
    assert h2.f2d["pct_ohlc_identico"] == 1.0 and h2.control_4h["pct_ohlc_identico"] >= 0.99


def test_short_or_gappy_native_2h_falls_back_to_the_resample(tmp_path):
    prov, resampled, entries = _h2_setup(tmp_path)
    anchor_first, anchor_last = datetime(2026, 7, 10, 6, 40), datetime(2026, 7, 28, 6, 40)
    short = resampled[resampled["time"] >= "2026-06-20"].reset_index(drop=True)     # menos de 800 velas antes del ancla
    h2 = v2.decide_h2_source(prov, short, resampled, entries, anchor_first, anchor_first, anchor_last, "us")
    assert h2.source == v2.H2_RESAMPLED and any("F2b.2" in r for r in h2.reasons)

    one_day = resampled[resampled["time"].dt.strftime("%m-%d") != "07-15"].reset_index(drop=True)   # 1 día: tolerado
    assert v2.decide_h2_source(prov, one_day, resampled, entries, anchor_first, anchor_first, anchor_last, "us").source == v2.H2_NATIVE

    assert v2.decide_h2_source(prov, None, resampled, entries, anchor_first, anchor_first, anchor_last, "us").source == v2.H2_RESAMPLED


def test_two_missing_trading_days_in_a_row_reject_the_native_2h(tmp_path):
    # Serie larga (230 días): 2 días faltantes son menos del 1% de las velas, así que F2d no salta
    # y decide F2b.3.
    prov, resampled, entries = _h2_setup(tmp_path, days=230, start="2026-03-10 00:00")
    anchor_first, anchor_last = datetime(2026, 7, 10, 6, 40), datetime(2026, 7, 28, 6, 40)
    holes = resampled[~resampled["time"].dt.strftime("%m-%d").isin(["07-15", "07-16"])].reset_index(drop=True)
    h2 = v2.decide_h2_source(prov, holes, resampled, entries, anchor_first, anchor_first, anchor_last, "us")
    assert h2.source == v2.H2_RESAMPLED and any("F2b.3" in r for r in h2.reasons)
    assert h2.gap_runs == [(date(2026, 7, 15), date(2026, 7, 16), 2)]


def test_a_native_2h_missing_more_than_one_percent_of_the_bars_stops_instead_of_falling_back(tmp_path):
    # Pre-registro §6, F2d: si las dos series coinciden en menos del 99%, se reporta antes de seguir.
    prov, resampled, entries = _h2_setup(tmp_path)
    anchor = datetime(2026, 7, 10, 6, 40)
    holes = resampled[~resampled["time"].dt.strftime("%m-%d").isin(["07-15", "07-16"])].reset_index(drop=True)
    with pytest.raises(v2.StopCondition, match="F2d"):
        v2.decide_h2_source(prov, holes, resampled, entries, anchor, anchor, datetime(2026, 7, 28), "us")


def test_a_native_2h_that_disagrees_with_the_resample_stops_everything(tmp_path):
    prov, resampled, entries = _h2_setup(tmp_path)
    anchor = datetime(2026, 7, 10, 6, 40)
    shifted = resampled.assign(time=resampled["time"] + pd.Timedelta(hours=1))
    with pytest.raises(v2.StopCondition, match="F2d"):
        v2.decide_h2_source(prov, shifted, resampled, entries, anchor, anchor, datetime(2026, 7, 28), "us")


def test_prepare_provider_drops_repeated_bars_and_adds_the_chosen_2h(tmp_path):
    prov, resampled, entries = _h2_setup(tmp_path)
    # 1D con un par repetido de invierno (15:00 sobra, 16:00 es la frontera del servidor) y una vela de verano.
    d1 = pd.DataFrame({"time": pd.to_datetime(["2026-01-14 15:00", "2026-01-14 16:00", "2026-07-14 15:00"]),
                       "open": [10.0, 10.0, 12.0], "high": [11.0, 11.0, 13.0], "low": [9.0, 9.0, 11.0],
                       "close": [10.5, 10.5, 12.5]})
    prov.add_frame("1D", d1)
    m1 = _flat("2026-07-10", 3 * 24 * 60, 1)
    prov.add_frame("1M", m1)
    recs = [_rec(trade_id="a", created_at=datetime(2026, 7, 11, 7, 0), evp=110.0, si=90.0)]

    dedup, h2 = v2.prepare_provider(prov, recs, entries, resampled.copy(), "us")

    assert dedup["1D"] == {"pares": 1, "sin_resolver": 0, "antes": 3, "después": 2}
    assert list(prov.frame("1D")["time"].dt.hour) == [16, 15]
    assert "12H" not in dedup                       # una TF que el proveedor no tiene se saltea
    # Sin 1W, D no mide ningún análisis: el nativo no puede probar que tiene historia suficiente (F2b.2).
    assert h2.source == v2.H2_RESAMPLED and any("F2b.2" in r for r in h2.reasons)
    assert prov.has_timeframe("2H") and len(prov.frame("2H")) == len(resampled)
    assert v2.h2_loss_share({"XAUUSD": prov}, {"XAUUSD": v2.evaluate_account(recs, prov, with_snapshots=False)}) == (0, 0)


def test_prepare_provider_stops_when_no_analysis_is_in_scope(tmp_path):
    prov, resampled, entries = _h2_setup(tmp_path)
    with pytest.raises(v2.StopCondition):
        v2.prepare_provider(prov, [_rec(evp=None)], entries, None, "us")


def test_fast_selection_picks_exactly_what_select_best_picks():
    from tools.edge_evaluation import select_best
    grid = v2.weight_grid()
    prior = v2.WEIGHTS_2X["2A"]
    penalty = v2._prior_penalty(grid, prior)
    rng = np.random.default_rng(9)
    for _ in range(200):
        net = rng.integers(-6, 7, size=len(grid))          # rango chico: muchos empates
        assert v2._select_best_fast(net, penalty) == select_best(net, grid, prior)
    flat = np.zeros(len(grid), dtype=np.int64)
    assert v2._select_best_fast(flat, penalty) == select_best(flat, grid, prior) == v2.grid_index(grid, prior)


# ---------------------------------------------------------------------------
# 2F usando solo resultados ya conocidos (control de fuga de información)
# ---------------------------------------------------------------------------

def _hourly_anchors(n):
    return [datetime(2026, 7, 1) + timedelta(hours=i) for i in range(n)]


def test_known_outcomes_control_equals_the_walk_forward_when_every_outcome_was_already_known():
    grid, prior = _toy_grid()
    rng = np.random.default_rng(4)
    n = 60
    truth = rng.choice([-1, 1], size=n)
    pred = rng.choice([-1, 0, 1], size=(n, 3)).astype(np.int8)
    anchors = _hourly_anchors(n)
    touches = [a + timedelta(minutes=10) for a in anchors]      # todo se resuelve antes del análisis siguiente
    res = v2.run_2f(pred, truth, grid, prior)
    ctl = v2.run_2f_known_outcomes(pred, truth, grid, prior, anchors, touches)
    assert (ctl.tests_with_unknown, ctl.unknown_uses) == (0, 0)
    assert (ctl.mask == res.wf_mask).all() and (ctl.pred == res.wf_pred).all()
    assert ctl.net == int((res.wf_pred * truth)[res.wf_mask].sum()) and ctl.p is None


def test_known_outcomes_control_ignores_a_training_outcome_that_was_not_known_yet():
    grid, prior = _toy_grid()
    n = 40
    truth = np.ones(n, dtype=int)
    pred = np.zeros((n, 3), dtype=np.int8)
    pred[19, 0] = 1          # el único análisis que hace ganar a la combinación 0...
    pred[20:30, 0] = 1
    anchors = _hourly_anchors(n)
    touches = [a + timedelta(minutes=10) for a in anchors]
    touches[19] = anchors[24] + timedelta(minutes=30)   # ...se resolvió después de empezar los análisis 20 a 24

    res = v2.run_2f(pred, truth, grid, prior)
    assert (res.wf_pred[20:30] == 1).all()              # el walk-forward normal ya usa ese resultado

    ctl = v2.run_2f_known_outcomes(pred, truth, grid, prior, anchors, touches)
    assert (ctl.pred[20:25] == 0).all()                 # sin ese resultado, queda el prior, que no opina
    assert (ctl.pred[25:30] == 1).all()                 # desde el 25 ya se conocía
    assert (ctl.tests_with_unknown, ctl.unknown_uses) == (5, 5)
    assert ctl.net == 5


def test_known_outcomes_control_permutation_is_reproducible():
    grid, prior = _toy_grid()
    rng = np.random.default_rng(6)
    n = 70
    truth = rng.choice([-1, 1], size=n)
    pred = np.zeros((n, 3), dtype=np.int8)
    pred[:, 0] = truth
    pred[:, 2] = rng.choice([-1, 1], size=n)
    anchors = _hourly_anchors(n)
    touches = [a + timedelta(hours=3) for a in anchors]          # cada resultado tarda 3 análisis en conocerse
    a = v2.run_2f_known_outcomes(pred, truth, grid, prior, anchors, touches, n_perm=200, seed=8)
    b = v2.run_2f_known_outcomes(pred, truth, grid, prior, anchors, touches, n_perm=200, seed=8)
    assert a.p == b.p and 0 < a.p < 0.02 and a.tests_with_unknown > 0
    assert a.net == int(a.mask.sum())                            # la combinación que sabe la verdad gana igual
