"""Customer analytics: RFM segmentation, new vs returning mix and customer value (observed vs modelled)."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

# Transparent segment rules on (R score, F score); evaluated top to bottom, first match wins.
SEGMENT_RULES: list[tuple[str, str, callable]] = [
    ("Champions", "Bought recently, buy often and spend the most", lambda r, f: (r >= 4) & (f >= 4)),
    ("Loyal", "Buy regularly; responsive to retention activity", lambda r, f: (r >= 3) & (f >= 4)),
    ("Potential loyalists", "Recent customers with growing frequency", lambda r, f: (r >= 4) & (f >= 2)),
    ("New customers", "First purchase very recently", lambda r, f: (r >= 4) & (f < 2)),
    ("At risk", "Used to buy often, not seen for a while", lambda r, f: (r <= 2) & (f >= 3)),
    ("Needs attention", "Average recency and frequency", lambda r, f: (r == 3) & (f >= 2)),
    ("About to sleep", "Below-average recency, low frequency", lambda r, f: (r == 3) & (f < 2)),
    ("Hibernating / lost", "Long inactive, low frequency", lambda r, f: (r <= 2) & (f < 3)),
]
SEGMENT_ORDER = [s[0] for s in SEGMENT_RULES]


def _score(values: pd.Series, bins: int, higher_is_better: bool) -> pd.Series:
    """Quantile score 1..bins. Ranking with method='first' keeps bins well-defined when many ties exist."""
    if len(values) == 0:
        return pd.Series(dtype=int)
    n_bins = min(bins, len(values))
    ranks = values.rank(method="first", ascending=higher_is_better)
    scores = pd.qcut(ranks, n_bins, labels=False) + 1
    if n_bins < bins:  # stretch to full 1..bins scale for tiny populations
        scores = np.ceil(scores * bins / n_bins)
    return scores.astype(int)


def rfm(orders: pd.DataFrame, as_of: date, bins: int = 5) -> pd.DataFrame:
    """orders: eligible (non-cancelled) rows of v_order_value. One output row per customer."""
    o = orders[~orders["is_cancelled"]]
    g = o.groupby("customer_id").agg(last_order=("order_date", "max"), frequency=("order_id", "count"),
                                     monetary=("net_revenue_gbp", "sum")).reset_index()
    g["recency_days"] = (pd.Timestamp(as_of) - pd.to_datetime(g["last_order"])).dt.days
    g["r_score"] = _score(-g["recency_days"], bins, True)
    # Frequency is heavily tied at 1; customers with a single order always score 1.
    g["f_score"] = _score(g["frequency"], bins, True).where(g["frequency"] > 1, 1)
    g["m_score"] = _score(g["monetary"], bins, True)
    seg = pd.Series("Unclassified", index=g.index)
    assigned = pd.Series(False, index=g.index)
    for name, _, rule in SEGMENT_RULES:
        m = rule(g["r_score"], g["f_score"]) & ~assigned
        seg[m] = name
        assigned |= m
    g["segment"] = seg
    g["rfm_code"] = g["r_score"].astype(str) + g["f_score"].astype(str) + g["m_score"].astype(str)
    return g


def segment_summary(rfm_df: pd.DataFrame) -> pd.DataFrame:
    s = rfm_df.groupby("segment").agg(customers=("customer_id", "count"), revenue=("monetary", "sum"),
                                      avg_orders=("frequency", "mean"), avg_recency_days=("recency_days", "mean"),
                                      avg_value=("monetary", "mean"))
    s["customer_share"] = s["customers"] / s["customers"].sum()
    s["revenue_share"] = s["revenue"] / s["revenue"].sum() if s["revenue"].sum() > 0 else np.nan
    desc = {n: d for n, d, _ in SEGMENT_RULES}
    s["definition"] = s.index.map(desc)
    order = [x for x in SEGMENT_ORDER if x in s.index] + [x for x in s.index if x not in SEGMENT_ORDER]
    return s.loc[order].reset_index()


def new_vs_returning(orders: pd.DataFrame, freq: str = "M") -> pd.DataFrame:
    o = orders[~orders["is_cancelled"]].copy()
    o["period"] = pd.to_datetime(o["order_date"]).dt.to_period(freq).dt.start_time
    o["customer_type"] = np.where(o["customer_order_seq"] == 1, "New", "Returning")
    return o.groupby(["period", "customer_type"]).agg(orders=("order_id", "count"), customers=("customer_id", "nunique"),
                                                      net_revenue=("net_revenue_gbp", "sum")).reset_index()


def observed_value_by_cohort(customer_value: pd.DataFrame) -> pd.DataFrame:
    """OBSERVED value: actual net revenue to date per customer, averaged by first-order cohort."""
    c = customer_value[customer_value["orders"] > 0]
    return c.groupby("cohort_month").agg(customers=("customer_id", "count"),
                                         avg_net_revenue=("net_revenue_gbp", "mean"),
                                         avg_orders=("orders", "mean")).reset_index()


def modelled_clv(survival: pd.Series, monthly_value_per_active: float, gross_margin_pct: float,
                 horizon_months: int = 12, annual_discount_rate: float = 0.10) -> float:
    """MODELLED margin CLV over a stated horizon (an estimate, not an observation).

    CLV = monthly net revenue per active customer x gross margin %
          x sum_{t=0..H-1} S(t) / (1 + d)^t
    where S(t) is the share of a cohort purchasing in month t after acquisition (S(0) = 1,
    the acquisition month; later values from the cohort retention curve) and d is the
    monthly discount rate. S(t) beyond observed history is held at the last observed
    value (plateau assumption).
    """
    if pd.isna(gross_margin_pct) or pd.isna(monthly_value_per_active):
        return float("nan")
    s = survival.dropna().to_numpy(dtype=float)
    if s.size == 0:
        return float("nan")
    months = np.arange(horizon_months)
    surv = np.array([s[t] if t < s.size else s[-1] for t in months])
    d = (1 + annual_discount_rate) ** (1 / 12) - 1
    return float(monthly_value_per_active * gross_margin_pct * np.sum(surv / (1 + d) ** months))
