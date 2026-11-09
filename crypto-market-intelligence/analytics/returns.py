"""Return and risk metrics. Inputs are pandas Series indexed by daily DatetimeIndex.

Missing-data policy: prices are reindexed to the full daily calendar WITHOUT
filling, so a return is only computed between two consecutive observed days.
Returns that would span a gap are NaN and excluded from statistics.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def to_daily(close: pd.Series) -> pd.Series:
    s = close.copy()
    s.index = pd.DatetimeIndex(pd.to_datetime(s.index)).normalize()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    if s.empty:
        return s
    return s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="D"))


def simple_returns(close: pd.Series) -> pd.Series:
    """r_t = close_t / close_{t-1} - 1 (consecutive days only)."""
    daily = to_daily(close)
    return (daily / daily.shift(1) - 1).rename("return")


def cumulative_return(returns: pd.Series) -> float:
    r = returns.dropna()
    return float(np.prod(1 + r) - 1) if len(r) else float("nan")


def cumulative_index(returns: pd.Series, base: float = 100.0) -> pd.Series:
    """Compounded index; gap days contribute 0% (index held flat, documented)."""
    return base * (1 + returns.fillna(0)).cumprod()


def annualised_volatility(returns: pd.Series, factor: int = 365) -> float:
    r = returns.dropna()
    return float(r.std(ddof=1) * np.sqrt(factor)) if len(r) > 1 else float("nan")


def rolling_volatility(returns: pd.Series, window: int = 30, factor: int = 365) -> pd.Series:
    return returns.rolling(window, min_periods=int(window * 0.8)).std(ddof=1) * np.sqrt(factor)


def drawdown_series(values: pd.Series) -> pd.Series:
    v = values.dropna()
    return v / v.cummax() - 1


def max_drawdown(values: pd.Series) -> tuple[float, pd.Timestamp | None, pd.Timestamp | None]:
    """Largest peak-to-trough decline. Returns (mdd <= 0, peak_date, trough_date)."""
    dd = drawdown_series(values)
    if dd.empty:
        return float("nan"), None, None
    trough = dd.idxmin()
    peak = values.loc[:trough].dropna().idxmax()
    return float(dd.min()), peak, trough


def sharpe_ratio(returns: pd.Series, risk_free_annual: float = 0.0, factor: int = 365,
                 min_obs: int = 30) -> float | None:
    """Annualised Sharpe = mean(excess daily) / std(daily) x sqrt(factor).

    Returns None when the assumptions cannot be supported (too few observations
    or zero volatility) instead of an unreliable number."""
    r = returns.dropna()
    if len(r) < min_obs:
        return None
    sd = r.std(ddof=1)
    if not sd or np.isnan(sd):
        return None
    excess = r - risk_free_annual / factor
    return float(excess.mean() / sd * np.sqrt(factor))


def correlation_matrix(returns: pd.DataFrame, min_overlap: int = 60) -> pd.DataFrame:
    """Pearson correlation on pairwise-aligned returns (pairwise deletion of NaNs).
    Pairs with fewer than `min_overlap` common observations are NaN."""
    return returns.corr(method="pearson", min_periods=min_overlap)


def moving_average(close: pd.Series, window: int) -> pd.Series:
    return to_daily(close).rolling(window, min_periods=window).mean()


def period_return(close: pd.Series, end: pd.Timestamp, days: int | None = None,
                  start: pd.Timestamp | None = None) -> float:
    """Point-to-point return using the last observation on/before each boundary."""
    s = to_daily(close).dropna()
    s = s[s.index <= end]
    if s.empty:
        return float("nan")
    start = start if start is not None else end - pd.Timedelta(days=days)
    base = s[s.index <= start]
    if base.empty:
        return float("nan")
    return float(s.iloc[-1] / base.iloc[-1] - 1)


def herfindahl(weights: pd.Series) -> float:
    """Concentration (HHI) on weights summing to 1: 1/n (diversified) .. 1 (single asset)."""
    w = weights.dropna()
    w = w / w.sum() if w.sum() else w
    return float((w ** 2).sum())
