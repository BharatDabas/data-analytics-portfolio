"""First-purchase cohort retention and repeat-purchase timing."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


def cohort_matrix(orders: pd.DataFrame, as_of: date, max_periods: int = 12) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Monthly first-purchase cohorts.

    Returns (active_customers, retention_rate) indexed by cohort month with columns 0..max_periods.
    Period k = calendar months since the cohort month. A customer is 'retained' in period k if they
    placed at least one eligible order in that month. Periods that have not fully elapsed by the
    as-of date are NaN (not zero) so incomplete months do not look like churn.
    """
    o = orders[~orders["is_cancelled"]].copy()
    if o.empty:
        empty = pd.DataFrame(columns=range(max_periods + 1))
        return empty, empty
    o["month"] = pd.to_datetime(o["order_date"]).dt.to_period("M")
    first = o.groupby("customer_id")["month"].min().rename("cohort")
    o = o.join(first, on="customer_id")
    o["k"] = (o["month"] - o["cohort"]).apply(lambda x: x.n)
    o = o[o["k"] <= max_periods]
    active = (o.groupby(["cohort", "k"])["customer_id"].nunique().unstack("k")
              .reindex(columns=range(max_periods + 1)).fillna(0))
    rate = active.div(active[0], axis=0)
    last_complete = pd.Period(as_of, "M") - (0 if pd.Timestamp(as_of).is_month_end else 1)
    elapsed = pd.DataFrame({k: [c + k <= last_complete for c in rate.index] for k in rate.columns}, index=rate.index)
    rate, active = rate.where(elapsed), active.where(elapsed)
    rate.index = rate.index.to_timestamp()
    active.index = active.index.to_timestamp()
    return active, rate


def retention_curve(rate: pd.DataFrame, cohort_sizes: pd.Series | None = None) -> pd.Series:
    """Customer-weighted average retention by period across cohorts that have reached that period."""
    if rate.empty:
        return pd.Series(dtype=float)
    if cohort_sizes is None:
        return rate.mean(axis=0, skipna=True)
    w = cohort_sizes.reindex(rate.index)
    num = rate.mul(w, axis=0).sum(axis=0, skipna=True)
    den = rate.notna().mul(w, axis=0).sum(axis=0)
    return (num / den).where(den > 0)


def days_between_orders(orders: pd.DataFrame) -> pd.DataFrame:
    """Gaps between consecutive eligible orders per customer, labelled by the order number they lead to."""
    o = orders[~orders["is_cancelled"]].sort_values(["customer_id", "order_ts", "order_id"])[["customer_id", "order_date"]].copy()
    o["order_date"] = pd.to_datetime(o["order_date"])
    o["prev"] = o.groupby("customer_id")["order_date"].shift()
    o["n"] = o.groupby("customer_id").cumcount() + 1
    o = o.dropna(subset=["prev"])
    o["days"] = (o["order_date"] - o["prev"]).dt.days
    return o[["customer_id", "n", "days"]]


def time_to_second_purchase(orders: pd.DataFrame) -> pd.Series:
    g = days_between_orders(orders)
    return g.loc[g["n"] == 2, "days"]
