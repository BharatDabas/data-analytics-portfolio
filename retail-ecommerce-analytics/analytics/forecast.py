"""Weekly category sales forecasting with backtesting.

Method (deliberately simple and explainable):
  1. Build a complete weekly series (Monday weeks; partial first/last weeks dropped).
  2. Backtest two candidate models on the last `holdout` weeks:
       - seasonal naive: same week last year x recent year-over-year growth
       - damped-trend exponential smoothing (Holt, statsmodels)
     and keep the model with the lowest WAPE per series.
  3. Refit the chosen model on the full history and forecast `horizon` weeks.
  4. Prediction interval = forecast +/- z x RMSE of the backtest errors (empirical,
     assumes errors are roughly stable and symmetric; widening with horizon is not modelled).
"""
from __future__ import annotations

import warnings
from datetime import date

import numpy as np
import pandas as pd

SEASON = 52


def mae(actual, forecast) -> float:
    a, f = np.asarray(actual, float), np.asarray(forecast, float)
    return float(np.mean(np.abs(a - f))) if a.size else float("nan")


def wape(actual, forecast) -> float:
    """Weighted absolute percentage error = sum|A-F| / sum|A|. NaN when actuals sum to zero."""
    a, f = np.asarray(actual, float), np.asarray(forecast, float)
    den = np.abs(a).sum()
    return float(np.abs(a - f).sum() / den) if den > 0 else float("nan")


def bias(actual, forecast) -> float:
    """Signed bias = sum(F-A) / sum(A); positive = over-forecasting."""
    a, f = np.asarray(actual, float), np.asarray(forecast, float)
    den = a.sum()
    return float((f - a).sum() / den) if den > 0 else float("nan")


def weekly_series(df: pd.DataFrame, date_col: str, value_col: str, as_of: date, group_col: str | None = None) -> pd.DataFrame:
    """Complete weekly totals (zero-filled: no sales in a week is a real zero, not missing data)."""
    d = df.copy()
    d["week"] = pd.to_datetime(d[date_col]).dt.to_period("W-SUN").dt.start_time
    keys = ["week"] + ([group_col] if group_col else [])
    s = d.groupby(keys)[value_col].sum()
    first_full = pd.to_datetime(d[date_col]).min().to_period("W-SUN").start_time + pd.Timedelta(weeks=1)
    last_full = pd.Period(as_of, "W-SUN").start_time - (pd.Timedelta(0) if pd.Timestamp(as_of).dayofweek == 6
                                                        else pd.Timedelta(weeks=1))
    weeks = pd.date_range(first_full, last_full, freq="7D")
    if group_col:
        idx = pd.MultiIndex.from_product([weeks, sorted(d[group_col].dropna().unique())], names=keys)
        return s.reindex(idx, fill_value=0.0).reset_index()
    return s.reindex(weeks, fill_value=0.0).rename_axis("week").reset_index()


def seasonal_naive(history: np.ndarray, horizon: int) -> np.ndarray:
    h = np.asarray(history, float)
    if h.size < SEASON + 13:
        return np.repeat(h[-4:].mean() if h.size else 0.0, horizon)
    recent, year_ago = h[-13:].sum(), h[-SEASON - 13:-SEASON].sum()
    growth = recent / year_ago if year_ago > 0 else 1.0
    growth = float(np.clip(growth, 0.5, 2.0))
    base = np.array([h[-SEASON + (i % SEASON)] for i in range(horizon)])
    return base * growth


def damped_holt(history: np.ndarray, horizon: int) -> np.ndarray:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    h = np.asarray(history, float)
    if h.size < 10:
        return np.repeat(h.mean() if h.size else 0.0, horizon)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = ExponentialSmoothing(h, trend="add", damped_trend=True, initialization_method="estimated").fit()
    return np.clip(fit.forecast(horizon), 0, None)


MODELS = {"Seasonal naive × YoY": seasonal_naive, "Damped Holt (ETS)": damped_holt}


def backtest_and_forecast(series: pd.DataFrame, value_col: str, holdout: int = 12, horizon: int = 12,
                          z: float = 1.96) -> tuple[pd.DataFrame, dict]:
    """series: complete weekly frame with columns [week, value_col].
    Returns (frame with actual/backtest/forecast/interval columns, metrics dict)."""
    s = series.sort_values("week").reset_index(drop=True)
    y = s[value_col].to_numpy(float)
    if y.size <= holdout + 8:
        raise ValueError("not enough history to backtest")
    train, test = y[:-holdout], y[-holdout:]
    scores = {}
    for name, fn in MODELS.items():
        pred = fn(train, holdout)
        scores[name] = {"pred": pred, "wape": wape(test, pred), "mae": mae(test, pred), "bias": bias(test, pred)}
    best = min(scores, key=lambda k: (np.inf if np.isnan(scores[k]["wape"]) else scores[k]["wape"]))
    bt = scores[best]["pred"]
    rmse = float(np.sqrt(np.mean((test - bt) ** 2)))
    fc = MODELS[best](y, horizon)
    future_weeks = pd.date_range(s["week"].iloc[-1] + pd.Timedelta(weeks=1), periods=horizon, freq="7D")
    hist = s[["week"]].assign(actual=y, backtest=np.nan)
    hist.loc[hist.index[-holdout:], "backtest"] = bt
    hist["lower"] = (hist["backtest"] - z * rmse).clip(lower=0)
    hist["upper"] = hist["backtest"] + z * rmse
    fut = pd.DataFrame({"week": future_weeks, "actual": np.nan, "backtest": np.nan, "forecast": fc,
                        "lower": np.clip(fc - z * rmse, 0, None), "upper": fc + z * rmse})
    out = pd.concat([hist.assign(forecast=np.nan), fut], ignore_index=True)
    out["outside_interval"] = out["backtest"].notna() & ((out["actual"] < out["lower"]) | (out["actual"] > out["upper"]))
    metrics = {"model": best, "wape": scores[best]["wape"], "mae": scores[best]["mae"], "bias": scores[best]["bias"],
               "rmse": rmse, "holdout_weeks": holdout,
               "candidates": {k: {m: v[m] for m in ("wape", "mae", "bias")} for k, v in scores.items()}}
    return out, metrics
