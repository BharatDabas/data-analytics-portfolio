"""ABC (annual consumption value) and XYZ (demand variability) classification."""
from __future__ import annotations

import numpy as np
import pandas as pd


def abc_classify(value: pd.Series, a_share: float = 0.80, b_share: float = 0.95) -> pd.Series:
    """Pareto classification on annual consumption value.

    Items are ranked by value (descending). An item belongs to A while the cumulative
    share of the items ranked *above* it is below a_share - so the item that crosses the
    80% line is still A - then B up to b_share, then C. Zero/negative totals => all C.
    """
    value = value.fillna(0).clip(lower=0)
    total = value.sum()
    if total <= 0:
        return pd.Series("C", index=value.index)
    ordered = value.sort_values(ascending=False, kind="mergesort")
    prev_share = (ordered.cumsum() - ordered) / total
    cls = np.where(prev_share < a_share - 1e-12, "A", np.where(prev_share < b_share - 1e-12, "B", "C"))
    cls = np.where(ordered.to_numpy() <= 0, "C", cls)
    return pd.Series(cls, index=ordered.index).reindex(value.index)


def coefficient_of_variation(series: pd.Series) -> float:
    """Sample std / mean of a demand series; NaN when mean is zero (no demand)."""
    mean = float(series.mean())
    if len(series) < 2 or mean <= 0:
        return float("nan")
    return float(series.std(ddof=1)) / mean


def xyz_classify(cv: pd.Series, x_cv: float = 0.5, y_cv: float = 1.0) -> pd.Series:
    """X: stable (cv <= x_cv), Y: variable (<= y_cv), Z: erratic or undefined (no demand / too little history)."""
    return pd.Series(np.where(cv <= x_cv, "X", np.where(cv <= y_cv, "Y", "Z")), index=cv.index).where(cv.notna(), "Z")
