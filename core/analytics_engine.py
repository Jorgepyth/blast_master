"""
core/analytics_engine.py — Motor de KPIs de trading (Fase 1).

Funciones puras: reciben un DataFrame ya construido por el caller (notebook o
un futuro comando de CLI) y devuelven KPIs/DataFrames. Cero acceso a base de
datos aquí — mismo espíritu que core/math_engine.py, que tampoco toca la DB.

Regla de Fase 0 (obligatoria en todo este módulo): usar `r_multiple` (el
R-multiple REALIZADO, calculado desde closing_price) para cualquier métrica de
resultado — expectancy, win rate, Sharpe, Sortino, etc. NUNCA usar `r_r` (el
Risk:Reward PLANEADO hacia el take-profit) para eso: `r_r` es un objetivo, no
un resultado, y mezclar ambos fue la causa raíz de los "20 registros
corruptos" detectados en la auditoría cruzada de esta sesión.

Explícitamente fuera de alcance de este módulo (decisión deliberada, no
descuido — no agregar sin decidirlo primero):
  - pretty-printing / consola: es presentación, no cómputo.
  - graficación (equity curve, histogramas, heatmaps): Fase 5 del roadmap.
  - analisis_edge() / analizar_correlaciones() / MFE_MAE_analysis(): Fase 2
    (validación del edge predictivo), no KPIs de ejecución.
  - lectura de CSV (get_latest_csv_path): blast_master lee de SQLite, no CSV.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# =========================
# Helpers privados (matemática pura, sin nombres de columna)
# =========================

def _win_rate_margin_of_error_95(win_rate: float, n: int) -> float:
    """
    Margen de error del win_rate a 95% de confianza (error estándar de
    proporción: 1.96 * sqrt(p*(1-p)/n)). NaN si n==0 o win_rate es NaN —
    no hay manera de reportar confianza sin muestra.
    """
    if not n or pd.isna(win_rate):
        return np.nan
    return float(1.96 * np.sqrt(win_rate * (1 - win_rate) / n))


def _max_consecutive_runs(flags: pd.Series) -> Tuple[int, int]:
    """flags: Series booleana donde True=win. Retorna (max_win_streak, max_loss_streak)."""
    max_w = max_l = 0
    cur_w = cur_l = 0
    for f in flags.fillna(False).astype(bool).to_numpy():
        if f:
            cur_w += 1
            cur_l = 0
        else:
            cur_l += 1
            cur_w = 0
        max_w = max(max_w, cur_w)
        max_l = max(max_l, cur_l)
    return max_w, max_l


def _safe_std(x) -> float:
    x = np.asarray(x, dtype=float)
    if x.size < 2:
        return np.nan
    s = np.std(x, ddof=1)
    return float(s) if np.isfinite(s) and s > 0 else np.nan


def _sharpe_per_trade(x) -> float:
    """Sharpe por trade (no anualizado): mean / std."""
    x = np.asarray(x, dtype=float)
    mu = float(np.mean(x)) if x.size else np.nan
    s = _safe_std(x)
    if not np.isfinite(mu) or not np.isfinite(s) or s == 0:
        return np.nan
    return mu / s


def _sortino_per_trade(x, mar: float = 0.0) -> float:
    """Sortino por trade: (mean - MAR) / downside_std."""
    x = np.asarray(x, dtype=float)
    if x.size == 0:
        return np.nan
    mu = float(np.mean(x))
    downside = x[x < mar]
    s_down = _safe_std(downside)
    if not np.isfinite(mu) or not np.isfinite(s_down) or s_down == 0:
        return np.nan
    return (mu - mar) / s_down


def _ulcer_index(equity) -> float:
    """sqrt(mean(drawdown_pct^2)), drawdown medido desde el peak acumulado."""
    eq = np.asarray(equity, dtype=float)
    if eq.size == 0:
        return np.nan
    peak = np.maximum.accumulate(eq)
    dd_pct = np.zeros_like(eq)
    nonzero = peak != 0
    dd_pct[nonzero] = (eq[nonzero] - peak[nonzero]) / peak[nonzero]
    dd_pct[~nonzero] = 0.0
    return float(np.sqrt(np.mean(dd_pct ** 2)))


def _skew_kurtosis(x) -> Tuple[float, float]:
    """Skewness y kurtosis excess (vía z-scores), suficiente para diagnosticar sesgo/colas."""
    x = np.asarray(x, dtype=float)
    if x.size < 3:
        return np.nan, np.nan
    mu = np.mean(x)
    s = np.std(x, ddof=0)
    if s == 0:
        return np.nan, np.nan
    z = (x - mu) / s
    skew = float(np.mean(z ** 3))
    kurt_excess = float(np.mean(z ** 4) - 3.0)
    return skew, kurt_excess


# =========================
# Filtro de trades ejecutados (regla de Fase 0)
# =========================

def filter_executed_trades(
    df: pd.DataFrame,
    r_multiple_col: str = "r_multiple",
    order_filled_col: str = "order_filled",
) -> pd.DataFrame:
    """
    Filas con resultado realizado: order_filled == True Y r_multiple no nulo.

    Fase 0: usa r_multiple (R realizado), NUNCA r_r (R:R planeado) — r_r no dice
    nada sobre qué pasó realmente con el trade.

    Gate binario deliberado (decisión del usuario, ago-2026): ya no distingue
    calidad de ejecución — el antiguo par Edge_valid/Invalid_edge de
    tactical_audit.compliance (campo retirado junto con
    unified_department.trade_status) — solo si la orden se llenó.
    """
    if order_filled_col not in df.columns:
        raise KeyError(f"Falta la columna requerida: '{order_filled_col}'")
    if r_multiple_col not in df.columns:
        raise KeyError(f"Falta la columna requerida: '{r_multiple_col}'")

    mask = df[order_filled_col].fillna(True).astype(bool) & df[r_multiple_col].notna()
    return df[mask].copy()


# =========================
# KPIs por segmento
# =========================

def segment_kpis(
    df: pd.DataFrame,
    group_col: str,
    pnl_col: str = "pnl_and_cost",
    r_col: str = "r_multiple",
) -> pd.DataFrame:
    """
    Tabla de KPIs por categoría: trades, wins, losses, win_rate, pnl_sum,
    pnl_avg, r_sum, r_avg, profit_factor_$, profit_factor_R.
    """
    if group_col not in df.columns or df.empty:
        return pd.DataFrame()

    d = df.copy()
    d[pnl_col] = pd.to_numeric(d[pnl_col], errors="coerce").fillna(0.0)
    d[r_col] = pd.to_numeric(d[r_col], errors="coerce").fillna(0.0)

    def pf_money(g: pd.DataFrame) -> float:
        gp = g.loc[g[pnl_col] > 0, pnl_col].sum()
        gl = g.loc[g[pnl_col] < 0, pnl_col].sum()
        return float(gp / abs(gl)) if gl != 0 else np.inf

    def pf_r(g: pd.DataFrame) -> float:
        gp = g.loc[g[r_col] > 0, r_col].sum()
        gl = g.loc[g[r_col] < 0, r_col].sum()
        return float(gp / abs(gl)) if gl != 0 else np.inf

    grp = d.groupby(group_col, dropna=False)

    out = pd.DataFrame({
        "trades": grp.size(),
        "wins": grp.apply(lambda g: int((g[pnl_col] > 0).sum()), include_groups=False),
        "losses": grp.apply(lambda g: int((g[pnl_col] < 0).sum()), include_groups=False),
        "win_rate": grp.apply(lambda g: float((g[pnl_col] > 0).mean()), include_groups=False),
        "win_rate_moe_95": grp.apply(
            lambda g: _win_rate_margin_of_error_95(float((g[pnl_col] > 0).mean()), len(g)),
            include_groups=False,
        ),
        "pnl_sum": grp[pnl_col].sum(),
        "pnl_avg": grp[pnl_col].mean(),
        "r_sum": grp[r_col].sum(),
        "r_avg": grp[r_col].mean(),
        "profit_factor_$": grp.apply(pf_money, include_groups=False),
        "profit_factor_R": grp.apply(pf_r, include_groups=False),
    }).reset_index()

    out = out.sort_values(["r_avg", "pnl_sum"], ascending=[False, False]).reset_index(drop=True)
    return out


# =========================
# KPIs principales (extendidos) + curva de equity
# =========================

def compute_trade_kpis_extended(
    df: pd.DataFrame,
    pnl_col: str = "pnl_and_cost",
    r_col: str = "r_multiple",
    entry_col: str = "entry_time",
    exit_col: str = "exit_time",
    mfe_col: str = "mfe_favorable",
    mae_col: str = "mae_adverse",
    order_filled_col: str = "order_filled",
    segment_cols: Optional[List[str]] = None,
    sort_chronologically: bool = True,
    filter_executed: bool = True,
) -> Tuple[Dict[str, Any], pd.DataFrame, Dict[str, pd.DataFrame]]:
    """
    KPIs principales (neto de costos) + KPIs extendidos + tabla por segmentos.

    Devuelve: (kpis: dict, curve: DataFrame con equity/drawdown, segments: dict[str, DataFrame]).

    Por defecto filtra a trades ejecutados (filter_executed_trades) si existe
    la columna order_filled — usa r_multiple, no r_r (ver módulo docstring).
    """
    d = df.copy()

    for c in (pnl_col, r_col):
        if c not in d.columns:
            raise KeyError(f"Falta la columna requerida: '{c}'")

    if filter_executed and order_filled_col in d.columns:
        d = filter_executed_trades(d, r_col, order_filled_col)

    d[pnl_col] = pd.to_numeric(d[pnl_col], errors="coerce").fillna(0.0)
    d[r_col] = pd.to_numeric(d[r_col], errors="coerce").fillna(0.0)

    if entry_col in d.columns:
        d[entry_col] = pd.to_datetime(d[entry_col], errors="coerce")
    if exit_col in d.columns:
        d[exit_col] = pd.to_datetime(d[exit_col], errors="coerce")

    if sort_chronologically and entry_col in d.columns and d[entry_col].notna().any():
        d = d.sort_values(entry_col).reset_index(drop=True)

    n = len(d)
    pnl = d[pnl_col].to_numpy(dtype=float)
    r = d[r_col].to_numpy(dtype=float)

    wins_mask = pnl > 0
    losses_mask = pnl < 0
    be_mask = pnl == 0

    wins = int(wins_mask.sum())
    losses = int(losses_mask.sum())
    breakeven = int(be_mask.sum())
    win_rate = wins / n if n else np.nan

    pnl_total = float(pnl.sum()) if n else np.nan
    pnl_mean = float(np.mean(pnl)) if n else np.nan
    pnl_median = float(np.median(pnl)) if n else np.nan
    best_trade = float(np.max(pnl)) if n else np.nan
    worst_trade = float(np.min(pnl)) if n else np.nan

    gross_profit = float(pnl[wins_mask].sum()) if n else 0.0
    gross_loss = float(pnl[losses_mask].sum()) if n else 0.0

    avg_win = float(np.mean(pnl[wins_mask])) if wins else np.nan
    avg_loss = float(np.mean(pnl[losses_mask])) if losses else np.nan
    if n == 0:
        payoff = np.nan
        profit_factor = np.nan
    else:
        payoff = (avg_win / abs(avg_loss)) if (wins and losses and avg_loss != 0) else np.nan
        profit_factor = (gross_profit / abs(gross_loss)) if (losses and gross_loss != 0) else np.inf

    r_total = float(r.sum()) if n else np.nan
    expectancy_r = float(np.mean(r)) if n else np.nan

    gp_r = float(r[r > 0].sum()) if n else 0.0
    gl_r = float(r[r < 0].sum()) if n else 0.0
    if n == 0:
        profit_factor_r = np.nan
    else:
        profit_factor_r = (gp_r / abs(gl_r)) if gl_r != 0 else np.inf

    equity = np.cumsum(pnl)
    peak = np.maximum.accumulate(equity) if n else equity
    dd = equity - peak
    max_dd = float(dd.min()) if n else np.nan

    equity_r = np.cumsum(r)
    peak_r = np.maximum.accumulate(equity_r) if n else equity_r
    dd_r = equity_r - peak_r
    max_dd_r = float(dd_r.min()) if n else np.nan

    max_win_streak, max_loss_streak = _max_consecutive_runs(pd.Series(wins_mask))

    duration_median_min = duration_mean_min = np.nan
    duration_min = np.full(n, np.nan, dtype=float)
    if entry_col in d.columns and exit_col in d.columns:
        dur = (d[exit_col] - d[entry_col]).dt.total_seconds() / 60.0
        if dur.notna().any():
            duration_min = dur.to_numpy(dtype=float)
            duration_median_min = float(np.nanmedian(duration_min))
            duration_mean_min = float(np.nanmean(duration_min))

    sharpe_r = _sharpe_per_trade(r)
    sortino_r = _sortino_per_trade(r, mar=0.0)

    ulcer_pct = _ulcer_index(equity)
    ulcer_pct_r = _ulcer_index(equity_r)

    calmar_like_money = (pnl_total / abs(max_dd)) if (np.isfinite(max_dd) and max_dd != 0) else np.nan
    calmar_like_r = (r_total / abs(max_dd_r)) if (np.isfinite(max_dd_r) and max_dd_r != 0) else np.nan

    skew_pnl, kurt_pnl = _skew_kurtosis(pnl)
    skew_r, kurt_r = _skew_kurtosis(r)

    mfe_stats = mae_stats = None
    if mfe_col in d.columns:
        mfe = pd.to_numeric(d[mfe_col], errors="coerce")
        mfe_stats = {
            "mfe_mean_R": float(mfe.mean()),
            "mfe_std_R": float(mfe.std(ddof=1)),
            "mfe_mean_R_winners": float(mfe[wins_mask].mean()) if wins else np.nan,
            "mfe_mean_R_losers": float(mfe[losses_mask].mean()) if losses else np.nan,
        }
    if mae_col in d.columns:
        mae = pd.to_numeric(d[mae_col], errors="coerce")
        mae_abs = mae.abs()
        mae_stats = {
            "mae_mean_R_abs": float(mae_abs.mean()),
            "mae_std_R_abs": float(mae_abs.std(ddof=1)),
            "mae_mean_R_abs_winners": float(mae_abs[wins_mask].mean()) if wins else np.nan,
            "mae_mean_R_abs_losers": float(mae_abs[losses_mask].mean()) if losses else np.nan,
        }

    curve = pd.DataFrame({
        entry_col: d[entry_col] if entry_col in d.columns else pd.NaT,
        "pnl_net": pnl,
        "equity": equity,
        "drawdown": dd,
        "r": r,
        "equity_r": equity_r,
        "drawdown_r": dd_r,
        "duration_min": duration_min,
    })

    kpis: Dict[str, Any] = {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": win_rate,
        "win_rate_moe_95": _win_rate_margin_of_error_95(win_rate, n),

        "pnl_total_net": pnl_total,
        "pnl_avg_net": pnl_mean,
        "pnl_median_net": pnl_median,
        "best_trade_net": best_trade,
        "worst_trade_net": worst_trade,

        "avg_win_net": avg_win,
        "avg_loss_net": avg_loss,
        "payoff": payoff,

        "profit_factor_$": profit_factor,
        "profit_factor_R": profit_factor_r,

        "r_total": r_total,
        "expectancy_R_per_trade": expectancy_r,

        "max_drawdown_$": max_dd,
        "max_drawdown_R": max_dd_r,

        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,

        "duration_median_min": duration_median_min,
        "duration_mean_min": duration_mean_min,

        "sharpe_R_per_trade": sharpe_r,
        "sortino_R_per_trade": sortino_r,
        "ulcer_index_pct_equity": ulcer_pct,
        "ulcer_index_pct_equity_R": ulcer_pct_r,
        "calmar_like_$": calmar_like_money,
        "calmar_like_R": calmar_like_r,
        "skew_pnl": skew_pnl,
        "kurtosis_excess_pnl": kurt_pnl,
        "skew_R": skew_r,
        "kurtosis_excess_R": kurt_r,

        "mfe_stats": mfe_stats,
        "mae_stats": mae_stats,
    }

    if segment_cols is None:
        segment_cols = ["tier_setup", "market_state", "session", "exit_type", "setup_type"]

    segments: Dict[str, pd.DataFrame] = {}
    for col in segment_cols:
        if col in d.columns:
            segments[col] = segment_kpis(d, group_col=col, pnl_col=pnl_col, r_col=r_col)

    return kpis, curve, segments
