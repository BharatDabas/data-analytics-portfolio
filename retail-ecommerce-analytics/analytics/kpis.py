"""Commercial KPI calculations (pure functions over the semantic views).

Inputs:
  lines  - rows of v_order_lines (grain: order line)
  orders - rows of v_order_value (grain: order)
Cancelled orders are excluded from all sales KPIs; they only feed the cancellation rate.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd


def safe_div(num: float, den: float) -> float:
    """Ratio with an explicit rule for zero / negative / missing denominators: returns NaN (shown as '—')."""
    if den is None or pd.isna(den) or den <= 0:
        return float("nan")
    return float(num) / float(den)


def commercial_kpis(lines: pd.DataFrame, orders: pd.DataFrame) -> dict[str, float]:
    sold = lines[~lines["is_cancelled"]]
    eligible = orders[~orders["is_cancelled"]]
    gross = float(sold["gross_sales_gbp"].sum())
    disc = float(sold["discount_gbp"].sum())
    refunds = float(sold["refund_gbp"].sum())
    net = gross - disc - refunds
    costed = sold[sold["has_reliable_cost"].astype(bool)]
    net_costed = float(costed["net_revenue_gbp"].sum())
    cogs = float(costed["cogs_gbp"].sum())
    gp = net_costed - cogs
    units = float(sold["quantity"].sum())
    n_orders = int(len(eligible))
    returned_orders = int((eligible["returned_units"] > 0).sum()) if "returned_units" in eligible else 0
    return {
        "gross_sales": gross,
        "discounts": disc,
        "refunds": refunds,
        "net_revenue": net,
        "orders": n_orders,
        "units": units,
        "aov": safe_div(net, n_orders),
        "cogs": cogs,
        "gross_profit": gp if len(costed) else float("nan"),
        "gross_margin_pct": safe_div(gp, net_costed) if len(costed) else float("nan"),
        "cost_coverage": safe_div(net_costed, net) if net > 0 else float("nan"),
        "return_rate_units": safe_div(sold["returned_quantity"].sum(), units),
        "return_rate_orders": safe_div(returned_orders, n_orders),
        "discount_rate": safe_div(disc, gross),
        "cancellation_rate": safe_div(int(orders["is_cancelled"].sum()), len(orders)),
        "shipping_income": float(eligible["shipping_gbp"].sum()),
        "tax_collected": float(eligible["tax_gbp"].sum()),
        "customers": int(eligible["customer_id"].nunique()),
    }


def previous_period(start: date, end: date) -> tuple[date, date]:
    """Immediately preceding window of equal length."""
    days = (end - start).days + 1
    return start - timedelta(days=days), start - timedelta(days=1)


def pct_change(current: float, previous: float) -> float:
    if previous is None or pd.isna(previous) or previous == 0:
        return float("nan")
    return (current - previous) / abs(previous)


def repeat_purchase_rate(orders: pd.DataFrame) -> float:
    """Customers with >= 2 eligible (non-cancelled) orders / customers with >= 1, within the given order set."""
    counts = orders[~orders["is_cancelled"]].groupby("customer_id").size()
    return safe_div(int((counts >= 2).sum()), int(len(counts)))


def product_performance(lines: pd.DataFrame, level: str = "product_name") -> pd.DataFrame:
    """Revenue, margin, discount dependency and returns per product / category / brand."""
    sold = lines[~lines["is_cancelled"]]
    g = sold.groupby(level).agg(
        gross_sales=("gross_sales_gbp", "sum"), discounts=("discount_gbp", "sum"), refunds=("refund_gbp", "sum"),
        net_revenue=("net_revenue_gbp", "sum"), units=("quantity", "sum"), returned_units=("returned_quantity", "sum"),
        cogs=("cogs_gbp", "sum"), net_revenue_costed=("net_revenue_costed_gbp", "sum"),
        gross_profit=("gross_profit_gbp", "sum"), costed_lines=("has_reliable_cost", "sum"), lines=("order_item_id", "count"),
    )
    disc_sales = sold[sold["is_discounted"]].groupby(level)["net_sales_gbp"].sum()
    g["discounted_sales_share"] = (disc_sales.reindex(g.index).fillna(0) / (g["gross_sales"] - g["discounts"])).replace([np.inf, -np.inf], np.nan)
    g["return_rate"] = (g["returned_units"] / g["units"]).where(g["units"] > 0)
    g["gross_margin_pct"] = (g["gross_profit"] / g["net_revenue_costed"]).where(g["net_revenue_costed"] > 0)
    g.loc[g["costed_lines"] == 0, ["gross_profit", "gross_margin_pct", "cogs"]] = np.nan  # no reliable cost
    total = g["net_revenue"].sum()
    g["revenue_share"] = g["net_revenue"] / total if total > 0 else np.nan
    return g.sort_values("net_revenue", ascending=False).reset_index()
