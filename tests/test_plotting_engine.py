import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from core.plotting_engine import (
    plot_equity_curves,
    plot_mfe_vs_mae,
    plot_return_distribution,
    plot_segment_heatmaps,
)


@pytest.fixture(autouse=True)
def _close_all_figures():
    yield
    plt.close("all")


def test_plot_equity_curves_plots_exact_series():
    curve = pd.DataFrame({
        "equity": [1.0, 3.0, 2.0, 5.0],
        "drawdown": [0.0, 0.0, -1.0, 0.0],
    })
    fig_equity, fig_dd = plot_equity_curves(curve)

    ax_eq = fig_equity.axes[0]
    assert ax_eq.lines[0].get_ydata().tolist() == [1.0, 3.0, 2.0, 5.0]
    assert ax_eq.lines[0].get_xdata().tolist() == [1, 2, 3, 4]

    ax_dd = fig_dd.axes[0]
    assert ax_dd.lines[0].get_ydata().tolist() == [0.0, 0.0, -1.0, 0.0]


def test_plot_equity_curves_respects_r_columns_and_suffix():
    curve = pd.DataFrame({
        "equity": [1.0, 2.0],
        "drawdown": [0.0, 0.0],
        "equity_r": [0.5, 1.5],
        "drawdown_r": [0.0, -0.2],
    })
    fig_equity, fig_dd = plot_equity_curves(curve, equity_col="equity_r", drawdown_col="drawdown_r", label_suffix="(R)")
    assert fig_equity.axes[0].lines[0].get_ydata().tolist() == [0.5, 1.5]
    assert fig_dd.axes[0].lines[0].get_ydata().tolist() == [0.0, -0.2]
    assert "(R)" in fig_equity.axes[0].get_title()


def test_plot_return_distribution_adds_normal_curve_when_std_positive():
    fig = plot_return_distribution([1.0, 2.0, 3.0, 4.0, 5.0], "titulo", "xlabel")
    ax = fig.axes[0]
    assert len(ax.patches) > 0  # barras del histograma
    assert len(ax.lines) == 1  # curva normal superpuesta


def test_plot_return_distribution_skips_normal_curve_when_std_zero():
    fig = plot_return_distribution([2.0, 2.0, 2.0], "titulo", "xlabel")
    ax = fig.axes[0]
    assert len(ax.lines) == 0  # sin sd no hay curva normal, y no crashea


def test_plot_mfe_vs_mae_splits_wins_and_losses():
    df = pd.DataFrame({
        "pnl_and_cost": [10.0, -5.0, 3.0],
        "mfe_favorable": [2.0, 1.0, 0.5],
        "mae_adverse": [-0.5, -1.5, -0.2],
    })
    fig = plot_mfe_vs_mae(df)
    ax = fig.axes[0]
    wins_scatter, losses_scatter = ax.collections
    assert wins_scatter.get_offsets().data.tolist() == [[0.5, 2.0], [0.2, 0.5]]
    assert losses_scatter.get_offsets().data.tolist() == [[1.5, 1.0]]


def test_plot_mfe_vs_mae_returns_none_when_columns_missing():
    df = pd.DataFrame({"pnl_and_cost": [1.0, -1.0]})
    assert plot_mfe_vs_mae(df) is None


def test_plot_segment_heatmaps_counts_and_avg_r():
    df = pd.DataFrame({
        "market_state": ["Range", "Range", "Trend", "Trend", "Trend"],
        "setup_type": ["A", "A", "B", "B", "A"],
        "r_multiple": [1.0, 3.0, -2.0, 2.0, 4.0],
    })
    result = plot_segment_heatmaps(df)
    assert result is not None
    fig_count, fig_avg_r = result

    count_arr = fig_count.axes[0].images[0].get_array().data
    # index=market_state (Range, Trend), columns=setup_type (A, B)
    assert count_arr.tolist() == [[2.0, 0.0], [1.0, 2.0]]

    avg_arr = fig_avg_r.axes[0].images[0].get_array().data
    assert avg_arr[0][0] == pytest.approx(2.0)  # Range/A: mean(1,3)
    assert np.isnan(avg_arr[0][1])  # Range/B: sin datos
    assert avg_arr[1][0] == pytest.approx(4.0)  # Trend/A
    assert avg_arr[1][1] == pytest.approx(0.0)  # Trend/B: mean(-2,2)


def test_plot_segment_heatmaps_returns_none_when_columns_missing():
    df = pd.DataFrame({"r_multiple": [1.0, 2.0]})
    assert plot_segment_heatmaps(df) is None
