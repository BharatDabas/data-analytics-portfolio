"""Baseline weekly demand forecasting with rolling-origin backtesting.

Candidate methods are deliberately simple, transparent baselines suited to short,
noisy and partly intermittent SKU/location series:
  * moving_average  - mean of the last 8 weeks
  * ses             - simple exponential smoothing (alpha = 0.3)
  * seasonal_naive  - same week last year (needs >= 52 weeks of history, else MA)
  * croston_sba     - Croston with Syntetos-Boylan bias correction, for intermittent demand
The best method per series is selected on backtest WAPE (MAE when WAPE is undefined).
Prediction intervals are +/- z x RMSE of the selected method's backtest errors.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def moving_average(history: np.ndarray, h: int, window: int = 8) -> np.ndarray:
    return np.full(h, float(np.mean(history[-window:])) if len(history) else 0.0)


def ses(history: np.ndarray, h: int, alpha: float = 0.3) -> np.ndarray:
    if not len(history):
        return np.zeros(h)
    level = float(history[0])
    for x in history[1:]:
        level = alpha * float(x) + (1 - alpha) * level
    return np.full(h, level)


def seasonal_naive(history: np.ndarray, h: int, season: int = 52) -> np.ndarray:
    if len(history) < season:
        return moving_average(history, h)
    idx = [len(history) - season + (i % season) for i in range(h)]
    return history[idx].astype(float)


def croston_sba(history: np.ndarray, h: int, alpha: float = 0.1) -> np.ndarray:
    nz = np.flatnonzero(history > 0)
    if len(nz) == 0:
        return np.zeros(h)
    size, interval, last = float(history[nz[0]]), float(nz[0] + 1), nz[0]
    for i in nz[1:]:
        size = alpha * float(history[i]) + (1 - alpha) * size
        interval = alpha * float(i - last) + (1 - alpha) * interval
        last = i
    return np.full(h, (1 - alpha / 2) * size / interval)


METHODS = {"moving_average": moving_average, "ses": ses, "seasonal_naive": seasonal_naive,
           "croston_sba": croston_sba}


def wape(actual, forecast) -> float:
    """Sum |error| / sum |actual|. Undefined (NaN) when total actual demand is zero."""
    a, f = np.asarray(actual, float), np.asarray(forecast, float)
    denom = np.abs(a).sum()
    return float(np.abs(f - a).sum() / denom) if denom > 0 else float("nan")


def mae(actual, forecast) -> float:
    a, f = np.asarray(actual, float), np.asarray(forecast, float)
    return float(np.abs(f - a).mean()) if len(a) else float("nan")


def bias(actual, forecast) -> float:
    """(sum forecast - sum actual) / sum actual; positive = over-forecast. NaN when actual is zero."""
    a, f = np.asarray(actual, float), np.asarray(forecast, float)
    s = a.sum()
    return float((f.sum() - s) / s) if s > 0 else float("nan")


def demand_pattern(history: np.ndarray) -> str:
    """Syntetos-Boylan style label using share of zero weeks."""
    if len(history) == 0 or history.sum() == 0:
        return "No demand"
    zero_share = float((history == 0).mean())
    return "Intermittent" if zero_share >= 0.3 else "Regular"


def backtest(history: np.ndarray, origins: int, horizon: int) -> pd.DataFrame:
    """Rolling-origin evaluation. Origin k uses history[:n - horizon - k] and forecasts the next `horizon` weeks."""
    rows = []
    n = len(history)
    for k in range(origins):
        cut = n - horizon - k
        if cut < 8:
            break
        train, test = history[:cut], history[cut:cut + horizon]
        for name, fn in METHODS.items():
            fc = fn(train, horizon)
            for step in range(horizon):
                rows.append((k, cut, cut + step, step + 1, name, float(fc[step]), float(test[step])))
    return pd.DataFrame(rows, columns=["origin_k", "origin_idx", "target_idx", "horizon", "method",
                                       "forecast", "actual"])


def select_method(bt: pd.DataFrame) -> tuple[str, pd.DataFrame]:
    """Score each method on backtest; choose lowest WAPE, falling back to MAE when WAPE is undefined."""
    scores = bt.groupby("method").apply(
        lambda g: pd.Series({"wape": wape(g["actual"], g["forecast"]), "mae": mae(g["actual"], g["forecast"]),
                             "bias": bias(g["actual"], g["forecast"]), "actual_qty": g["actual"].sum()}),
        include_groups=False).reset_index()
    key = scores["wape"] if scores["wape"].notna().any() else scores["mae"]
    best = str(scores.loc[key.fillna(np.inf).idxmin(), "method"])
    scores["selected"] = scores["method"] == best
    return best, scores


def forecast_series(history: np.ndarray, horizon: int, origins: int, bt_horizon: int
                    ) -> tuple[str, pd.DataFrame, pd.DataFrame, np.ndarray, float]:
    """Returns (method, backtest rows, method scores, point forecast, rmse)."""
    bt = backtest(history, origins, bt_horizon)
    if bt.empty:
        point = moving_average(history, horizon)
        return "moving_average", bt, pd.DataFrame(), point, float("nan")
    best, scores = select_method(bt)
    sel = bt[bt["method"] == best]
    rmse = float(np.sqrt(np.mean((sel["forecast"] - sel["actual"]) ** 2)))
    point = METHODS[best](history, horizon)
    return best, bt, scores, point, rmse
