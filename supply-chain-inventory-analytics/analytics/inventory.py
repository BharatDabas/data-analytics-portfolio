"""Inventory KPIs: cover, turnover, service, stockout rate and FIFO stock ageing."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

AGE_BUCKETS = [(0, 30, "0-30 days"), (31, 60, "31-60 days"), (61, 90, "61-90 days"),
               (91, 180, "91-180 days"), (181, 10**9, "180+ days")]


def days_of_cover(stock, avg_daily_demand):
    """Stock divided by average daily demand.

    Edge cases (documented in the KPI catalogue):
      * demand == 0 and stock > 0  -> +inf (stock never consumed at current rate)
      * demand == 0 and stock <= 0 -> NaN  (undefined: nothing held, nothing needed)
      * stock < 0 is treated as 0 cover.
    """
    stock = np.maximum(np.asarray(stock, dtype=float), 0.0)
    d = np.asarray(avg_daily_demand, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(d > 0, stock / np.where(d > 0, d, 1.0), np.where(stock > 0, np.inf, np.nan))
    return out if out.shape else float(out)


def inventory_turnover(cogs: float, avg_inventory_value: float) -> float:
    """COGS over the period divided by average inventory value over the same period."""
    return float(cogs) / avg_inventory_value if avg_inventory_value > 0 else float("nan")


def fill_rate(fulfilled: float, requested: float) -> float:
    """Quantity fulfilled from stock / quantity requested. Backorders count as not filled."""
    return float(fulfilled) / requested if requested > 0 else float("nan")


def stockout_rate(requested: pd.Series, backordered: pd.Series) -> float:
    """Share of eligible SKU-location-days (requested > 0) where demand could not be met in full."""
    eligible = requested > 0
    n = int(eligible.sum())
    return float((backordered[eligible] > 0).sum()) / n if n else float("nan")


def fifo_ageing(on_hand: float, inbound: list[tuple[date, float]], as_of: date) -> dict[str, float]:
    """Age current on-hand stock assuming FIFO consumption.

    Remaining stock is attributed to the most recent inbound receipts first; any
    quantity not explained by inbound history in the window is placed in the oldest
    bucket (it pre-dates the data). Returns {bucket_label: qty}.
    """
    result = {label: 0.0 for _, _, label in AGE_BUCKETS}
    remaining = max(float(on_hand), 0.0)
    for d, qty in sorted(inbound, key=lambda x: x[0], reverse=True):
        if remaining <= 0:
            break
        take = min(remaining, max(float(qty), 0.0))
        result[_bucket((as_of - d).days)] += take
        remaining -= take
    if remaining > 0:
        result[AGE_BUCKETS[-1][2]] += remaining
    return result


def _bucket(age_days: int) -> str:
    for lo, hi, label in AGE_BUCKETS:
        if lo <= age_days <= hi:
            return label
    return AGE_BUCKETS[-1][2]


def ageing_table(positions: pd.DataFrame, movements: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """positions: sku, location_id, on_hand_qty, unit_cost_usd. movements: inbound rows
    (sku, location_id, movement_date, quantity>0)."""
    dates = pd.to_datetime(movements["movement_date"]).dt.date
    groups = {k: list(zip(dates[g.index], g["quantity"]))
              for k, g in movements.groupby(["sku", "location_id"])}
    order = {label: i for i, (_, _, label) in enumerate(AGE_BUCKETS)}
    rows = []
    for r in positions.itertuples():
        for label, qty in fifo_ageing(r.on_hand_qty, groups.get((r.sku, r.location_id), []), as_of).items():
            if qty > 0:
                rows.append((r.sku, r.location_id, label, order[label], qty, qty * r.unit_cost_usd))
    return pd.DataFrame(rows, columns=["sku", "location_id", "age_bucket", "bucket_order", "qty", "value_usd"])
