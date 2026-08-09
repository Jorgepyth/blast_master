"""
core/plotting_engine.py — Visualización integrada (Fase 5).

Puerto de `plot_trading_stats` (`notion_api_analysis/notion_to_csv/
analysis_tools.py:468-657`) al esquema y convención de blast_master. Mismo
espíritu de "función pura" que el resto de `core/`: reciben DataFrames ya
construidos por el caller (notebook), cero acceso a DB.

Desviación deliberada respecto a la referencia: la referencia dibuja y llama
`plt.show()` dentro de cada función (`-> None`, intestable). Acá cada función
crea la(s) `Figure` con `plt.subplots()` y las **retorna sin cerrarlas ni
mostrarlas explícitamente**. Esto no cambia la experiencia en el notebook:
con `%matplotlib inline` activo, cualquier figura creada y no cerrada en una
celda se auto-renderiza al terminar la celda, se llame o no a `plt.show()`.
Y permite testear inspeccionando `fig.axes[0].lines`/`.patches`/`.images`
sin comparar píxeles.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

_GRID_COLOR = "0.85"
_GRID_COLOR_MINOR = "0.92"


def _apply_grid(ax: plt.Axes) -> None:
    ax.set_axisbelow(True)
    ax.grid(True, which="major", color=_GRID_COLOR, linewidth=0.8)
    ax.grid(True, which="minor", color=_GRID_COLOR_MINOR, linewidth=0.6)


def _set_trade_xaxis(ax: plt.Axes, n: int, max_xticks: int = 25) -> None:
    """Eje X de series por trade (1..n), con densidad de ticks adaptativa."""
    n = int(max(n, 1))
    ax.set_xlim(1, n)
    major_step = max(1, int(np.ceil(n / max(1, max_xticks))))
    ax.xaxis.set_major_locator(mticker.MultipleLocator(major_step))
    ax.xaxis.set_minor_locator(mticker.MultipleLocator(1))
    if major_step == 1 and n > 15:
        for lbl in ax.get_xticklabels(which="major"):
            lbl.set_rotation(45)
            lbl.set_ha("right")
    _apply_grid(ax)


def plot_equity_curves(
    curve: pd.DataFrame,
    equity_col: str = "equity",
    drawdown_col: str = "drawdown",
    label_suffix: str = "($)",
    palette: Optional[Sequence] = None,
) -> Tuple[plt.Figure, plt.Figure]:
    """
    Equity curve y drawdown como series por trade (1..n). Llamar dos veces
    desde el notebook: una con equity_col="equity"/drawdown_col="drawdown"
    (neto en $), otra con "equity_r"/"drawdown_r" (en R).
    """
    n_trades = len(curve)
    x_trades = np.arange(1, n_trades + 1)
    color = palette[0] if palette else None

    fig_equity, ax_eq = plt.subplots()
    ax_eq.plot(x_trades, curve[equity_col].to_numpy(dtype=float), color=color)
    ax_eq.set_title(f"Equity Curve {label_suffix}")
    ax_eq.set_xlabel("Trade # (orden cronológico)")
    ax_eq.set_ylabel(f"Equity {label_suffix}")
    _set_trade_xaxis(ax_eq, n_trades)
    fig_equity.tight_layout()

    fig_dd, ax_dd = plt.subplots()
    ax_dd.plot(x_trades, curve[drawdown_col].to_numpy(dtype=float), color=color)
    ax_dd.set_title(f"Drawdown {label_suffix}")
    ax_dd.set_xlabel("Trade # (orden cronológico)")
    ax_dd.set_ylabel(f"Drawdown {label_suffix}")
    _set_trade_xaxis(ax_dd, n_trades)
    fig_dd.tight_layout()

    return fig_equity, fig_dd


def plot_return_distribution(values, title: str, xlabel: str) -> plt.Figure:
    """
    Histograma (bins="auto", density=True) con curva Normal teórica
    superpuesta (mu/sd muestrales, fórmula manual). Si sd==0 (todos los
    valores iguales) no se agrega la curva, mismo guard que la referencia.
    """
    x = pd.to_numeric(pd.Series(values), errors="coerce").dropna().to_numpy(dtype=float)
    mu = float(np.mean(x)) if x.size else 0.0
    sd = float(np.std(x, ddof=1)) if x.size > 1 else 0.0

    fig, ax = plt.subplots()
    ax.hist(x, bins="auto", density=True, alpha=0.8)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Density")
    ax.xaxis.set_minor_locator(mticker.AutoMinorLocator())
    ax.yaxis.set_minor_locator(mticker.AutoMinorLocator())
    _apply_grid(ax)

    if sd > 0:
        xs = np.linspace(x.min(), x.max(), 200)
        pdf = (1.0 / (sd * np.sqrt(2 * np.pi))) * np.exp(-0.5 * ((xs - mu) / sd) ** 2)
        ax.plot(xs, pdf)

    fig.tight_layout()
    return fig


def plot_mfe_vs_mae(
    df: pd.DataFrame,
    pnl_col: str = "pnl_and_cost",
    mfe_col: str = "mfe_favorable",
    mae_col: str = "mae_adverse",
    palette: Optional[Sequence] = None,
) -> Optional[plt.Figure]:
    """
    Scatter MFE vs |MAE|, wins (o) vs losses (x) según pnl_col. Devuelve None
    si faltan columnas — caso válido (no todo dataset trae MFE/MAE), no error.
    """
    if mfe_col not in df.columns or mae_col not in df.columns:
        return None

    d = df.copy()
    mfe = pd.to_numeric(d[mfe_col], errors="coerce")
    mae = pd.to_numeric(d[mae_col], errors="coerce").abs()
    wins_mask = d[pnl_col] > 0
    losses_mask = d[pnl_col] < 0

    win_color = palette[2] if palette and len(palette) > 2 else None
    loss_color = palette[1] if palette and len(palette) > 1 else None

    fig, ax = plt.subplots()
    ax.scatter(mae[wins_mask], mfe[wins_mask], marker="o", label="Wins", color=win_color)
    ax.scatter(mae[losses_mask], mfe[losses_mask], marker="x", label="Losses", color=loss_color)
    ax.set_title("MFE vs MAE (en R)")
    ax.set_xlabel("MAE abs (R)")
    ax.set_ylabel("MFE (R)")
    ax.xaxis.set_minor_locator(mticker.AutoMinorLocator())
    ax.yaxis.set_minor_locator(mticker.AutoMinorLocator())
    _apply_grid(ax)
    ax.legend()
    fig.tight_layout()
    return fig


def _show_heatmap(mat: pd.DataFrame, title: str, seg_rows: str, seg_cols: str) -> plt.Figure:
    fig, ax = plt.subplots()
    arr = mat.to_numpy(dtype=float)
    ax.imshow(arr, aspect="auto")

    ax.set_title(title)
    ax.set_xlabel(seg_cols)
    ax.set_ylabel(seg_rows)
    ax.set_xticks(np.arange(mat.shape[1]))
    ax.set_yticks(np.arange(mat.shape[0]))
    ax.set_xticklabels(mat.columns.astype(str), rotation=45, ha="right")
    ax.set_yticklabels(mat.index.astype(str))

    ax.set_xticks(np.arange(-0.5, mat.shape[1], 1), minor=True)
    ax.set_yticks(np.arange(-0.5, mat.shape[0], 1), minor=True)
    ax.grid(which="minor", color=_GRID_COLOR, linewidth=0.8)
    ax.tick_params(which="minor", bottom=False, left=False)

    finite = arr[np.isfinite(arr)]
    is_count_like = finite.size > 0 and np.all(np.mod(finite, 1) == 0)
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            val = mat.iloc[i, j]
            if np.isfinite(val):
                txt = f"{int(val)}" if is_count_like else f"{val:.2f}"
                ax.text(j, i, txt, ha="center", va="center")

    fig.tight_layout()
    return fig


def plot_segment_heatmaps(
    df: pd.DataFrame,
    seg_rows: str = "market_state",
    seg_cols: str = "setup_type",
    r_col: str = "r_multiple",
) -> Optional[Tuple[plt.Figure, plt.Figure]]:
    """
    Heatmaps de conteo y de R promedio por (seg_rows x seg_cols). Devuelve
    None si faltan columnas — caso válido, no error.
    """
    if seg_rows not in df.columns or seg_cols not in df.columns:
        return None

    pivot_count = pd.pivot_table(
        df, index=seg_rows, columns=seg_cols, values=r_col, aggfunc="count", fill_value=0
    )
    pivot_mean_r = pd.pivot_table(
        df, index=seg_rows, columns=seg_cols, values=r_col, aggfunc="mean"
    )

    fig_count = _show_heatmap(pivot_count, f"Heatmap Conteo: {seg_rows} x {seg_cols}", seg_rows, seg_cols)
    fig_avg_r = _show_heatmap(pivot_mean_r, f"Heatmap Avg R: {seg_rows} x {seg_cols}", seg_rows, seg_cols)
    return fig_count, fig_avg_r
