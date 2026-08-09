import numpy as np
import pandas as pd
import pytest

from core.analytics_engine import (
    _max_consecutive_runs,
    _safe_std,
    _sharpe_per_trade,
    _sortino_per_trade,
    _skew_kurtosis,
    _ulcer_index,
    compute_trade_kpis_extended,
    filter_executed_trades,
    segment_kpis,
)


def test_sharpe_per_trade_known_series():
    assert _sharpe_per_trade(np.array([1, 2, 3, 4, 5])) == pytest.approx(1.8973665961010275)


def test_sortino_per_trade_known_series():
    assert _sortino_per_trade(np.array([2, -1, 3, -2, 1])) == pytest.approx(0.8485281374238569)


def test_ulcer_index_known_equity_curve():
    assert _ulcer_index(np.array([100, 110, 105, 90, 95])) == pytest.approx(0.10365231137264891)


def test_skew_kurtosis_known_series():
    skew, kurt = _skew_kurtosis(np.array([1, 2, 3, 4, 10]))
    assert skew == pytest.approx(1.1384199576606164)
    assert kurt == pytest.approx(-0.2120000000000002)


def test_max_consecutive_runs():
    flags = pd.Series([True, True, False, True, True, True, False, False])
    assert _max_consecutive_runs(flags) == (3, 2)


def test_safe_std_single_element_returns_nan():
    assert np.isnan(_safe_std(np.array([5.0])))


def test_filter_executed_trades_uses_r_multiple_not_r_r():
    # Fase 0 regression guard: r_r having a value must NOT count as "executed"
    # if r_multiple (the realized outcome) is missing.
    df = pd.DataFrame({
        "compliance": ["Edge_valid", "Edge_valid", "No_edge"],
        "r_r": [1.5, np.nan, 1.0],
        "r_multiple": [np.nan, 0.8, 0.8],
    })
    out = filter_executed_trades(df)
    assert len(out) == 1
    assert out.iloc[0]["compliance"] == "Edge_valid"
    assert out.iloc[0]["r_multiple"] == 0.8


def test_filter_executed_trades_missing_columns_raise():
    with pytest.raises(KeyError):
        filter_executed_trades(pd.DataFrame({"compliance": ["Edge_valid"]}))


def test_segment_kpis_tiny_synthetic_df():
    df = pd.DataFrame({
        "grp": ["X", "X", "Y", "Y"],
        "pnl_and_cost": [10.0, -5.0, 20.0, -10.0],
        "r_multiple": [1.0, -0.5, 2.0, -1.0],
    })
    out = segment_kpis(df, "grp")
    assert list(out["grp"]) == ["Y", "X"]  # sorted by r_avg desc

    y = out[out["grp"] == "Y"].iloc[0]
    assert y["trades"] == 2 and y["wins"] == 1 and y["losses"] == 1
    assert y["win_rate"] == pytest.approx(0.5)
    assert y["pnl_sum"] == pytest.approx(10.0)
    assert y["r_sum"] == pytest.approx(1.0)
    assert y["r_avg"] == pytest.approx(0.5)
    assert y["profit_factor_$"] == pytest.approx(2.0)
    assert y["profit_factor_R"] == pytest.approx(2.0)

    x = out[out["grp"] == "X"].iloc[0]
    assert x["pnl_sum"] == pytest.approx(5.0)
    assert x["r_avg"] == pytest.approx(0.25)


def test_segment_kpis_missing_group_col_returns_empty():
    df = pd.DataFrame({"pnl_and_cost": [1.0], "r_multiple": [1.0]})
    assert segment_kpis(df, "nonexistent_col").empty


def test_segment_kpis_empty_dataframe_does_not_raise():
    df = pd.DataFrame({"grp": pd.Series(dtype="object"), "pnl_and_cost": pd.Series(dtype="float"), "r_multiple": pd.Series(dtype="float")})
    assert segment_kpis(df, "grp").empty


def _synthetic_trades_df():
    return pd.DataFrame([
        dict(compliance="Edge_valid", entry_time="2024-01-01 09:00", exit_time="2024-01-01 09:30",
             pnl_and_cost=50.0, r_multiple=1.0, mfe_favorable=1.2, mae_adverse=0.3,
             tier_setup="A", market_state="Trend", session="London", exit_type="manual", setup_type="breakout"),
        dict(compliance="Edge_valid", entry_time="2024-01-01 10:00", exit_time="2024-01-01 10:40",
             pnl_and_cost=80.0, r_multiple=1.6, mfe_favorable=2.0, mae_adverse=0.2,
             tier_setup="A", market_state="Trend", session="London", exit_type="manual", setup_type="breakout"),
        dict(compliance="Invalid_edge", entry_time="2024-01-01 11:00", exit_time="2024-01-01 11:50",
             pnl_and_cost=-30.0, r_multiple=-0.6, mfe_favorable=0.4, mae_adverse=0.8,
             tier_setup="A", market_state="Trend", session="NY", exit_type="stop order", setup_type="breakout"),
        dict(compliance="Edge_valid", entry_time="2024-01-01 12:00", exit_time="2024-01-01 12:20",
             pnl_and_cost=30.0, r_multiple=0.6, mfe_favorable=0.9, mae_adverse=0.1,
             tier_setup="B", market_state="Range", session="NY", exit_type="manual", setup_type="range_reversion"),
        dict(compliance="Invalid_edge", entry_time="2024-01-01 13:00", exit_time="2024-01-01 13:30",
             pnl_and_cost=-20.0, r_multiple=-0.4, mfe_favorable=0.3, mae_adverse=0.5,
             tier_setup="B", market_state="Range", session="NY", exit_type="stop order", setup_type="range_reversion"),
        # Trade abierto / sin resultado realizado: debe excluirse por el filtro por defecto.
        dict(compliance="Edge_valid", entry_time="2024-01-01 14:00", exit_time=None,
             pnl_and_cost=np.nan, r_multiple=np.nan, mfe_favorable=np.nan, mae_adverse=np.nan,
             tier_setup="A", market_state="Trend", session="London", exit_type=None, setup_type="breakout"),
    ])


def test_compute_trade_kpis_extended_end_to_end():
    df = _synthetic_trades_df()
    kpis, curve, segments = compute_trade_kpis_extended(df, segment_cols=["tier_setup", "market_state"])

    assert kpis["trades"] == 5  # el trade abierto (r_multiple NaN) queda excluido
    assert kpis["wins"] == 3 and kpis["losses"] == 2 and kpis["breakeven"] == 0
    assert kpis["win_rate"] == pytest.approx(0.6)

    assert kpis["pnl_total_net"] == pytest.approx(110.0)
    assert kpis["avg_win_net"] == pytest.approx(53.333333333333336)
    assert kpis["avg_loss_net"] == pytest.approx(-25.0)
    assert kpis["payoff"] == pytest.approx(2.1333333333333333)
    assert kpis["profit_factor_$"] == pytest.approx(3.2)
    assert kpis["profit_factor_R"] == pytest.approx(3.2)

    assert kpis["r_total"] == pytest.approx(2.2)
    assert kpis["expectancy_R_per_trade"] == pytest.approx(0.44)

    assert kpis["max_drawdown_$"] == pytest.approx(-30.0)
    assert kpis["max_drawdown_R"] == pytest.approx(-0.6)
    assert kpis["max_win_streak"] == 2
    assert kpis["max_loss_streak"] == 1

    assert kpis["duration_median_min"] == pytest.approx(30.0)
    assert kpis["duration_mean_min"] == pytest.approx(34.0)

    assert kpis["sharpe_R_per_trade"] == pytest.approx(0.4722726698152011)
    assert kpis["sortino_R_per_trade"] == pytest.approx(3.11126983722081)
    assert kpis["ulcer_index_pct_equity"] == pytest.approx(0.12403473458920845)
    assert kpis["calmar_like_$"] == pytest.approx(3.6666666666666665)
    assert kpis["calmar_like_R"] == pytest.approx(3.6666666666666665)

    assert kpis["mfe_stats"]["mfe_mean_R_winners"] == pytest.approx(1.366666666666667)
    assert kpis["mfe_stats"]["mfe_mean_R_losers"] == pytest.approx(0.35)
    assert kpis["mae_stats"]["mae_mean_R_abs_winners"] == pytest.approx(0.2)
    assert kpis["mae_stats"]["mae_mean_R_abs_losers"] == pytest.approx(0.65)

    assert len(curve) == 5
    assert curve["equity"].iloc[-1] == pytest.approx(110.0)
    assert curve["equity_r"].iloc[-1] == pytest.approx(2.2)

    tier = segments["tier_setup"].set_index("tier_setup")
    assert tier.loc["A", "trades"] == 3 and tier.loc["A", "wins"] == 2
    assert tier.loc["A", "r_avg"] == pytest.approx(0.6666666666666666)
    assert tier.loc["B", "trades"] == 2 and tier.loc["B", "losses"] == 1
    assert tier.loc["B", "r_avg"] == pytest.approx(0.1)

    # market_state replica la misma partición que tier_setup en este dataset sintético
    market = segments["market_state"].set_index("market_state")
    assert market.loc["Trend", "trades"] == tier.loc["A", "trades"]
    assert market.loc["Range", "trades"] == tier.loc["B", "trades"]


def test_zero_trades_profit_factor_is_nan_not_inf():
    df = _synthetic_trades_df()
    df_empty = df[df["r_multiple"].isna()].copy()  # solo queda el trade abierto
    kpis, curve, segments = compute_trade_kpis_extended(df_empty, segment_cols=["tier_setup"])

    assert kpis["trades"] == 0
    assert np.isnan(kpis["profit_factor_$"])
    assert np.isnan(kpis["profit_factor_R"])
    assert np.isnan(kpis["payoff"])
    assert kpis["max_win_streak"] == 0
    assert kpis["max_loss_streak"] == 0


def test_segment_cols_missing_columns_are_skipped():
    # Shape típico de df_master (sin session/exit_type/setup_type)
    df = _synthetic_trades_df().drop(columns=["session", "exit_type", "setup_type"])
    kpis, curve, segments = compute_trade_kpis_extended(df)  # segment_cols por defecto
    assert set(segments.keys()) == {"tier_setup", "market_state"}
