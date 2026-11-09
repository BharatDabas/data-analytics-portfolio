from datetime import date

import numpy as np
import pandas as pd
import pytest

from analytics import cohorts as coh
from analytics import customers as cust


def _orders(rows):
    df = pd.DataFrame(rows, columns=["order_id", "customer_id", "order_date", "net_revenue_gbp", "is_cancelled"])
    df["order_date"] = pd.to_datetime(df["order_date"]).dt.date
    df["order_ts"] = pd.to_datetime(df["order_date"]).dt.tz_localize("UTC")
    df = df.sort_values(["customer_id", "order_date"])
    df["customer_order_seq"] = df[~df["is_cancelled"]].groupby("customer_id").cumcount() + 1
    return df


ORDERS = _orders([
    ("1", "A", "2026-01-05", 100, False), ("2", "A", "2026-02-10", 50, False), ("3", "A", "2026-03-01", 70, False),
    ("4", "B", "2026-01-20", 30, False), ("5", "B", "2026-03-15", 20, True),   # cancelled repeat
    ("6", "C", "2026-02-02", 500, False), ("7", "C", "2026-02-20", 80, False),
])


def test_cohort_matrix_counts_and_rates():
    active, rate = coh.cohort_matrix(ORDERS, date(2026, 3, 31), max_periods=3)
    jan, feb = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-02-01")
    assert active.loc[jan, 0] == 2           # A, B
    assert active.loc[jan, 1] == 1           # A in Feb
    assert active.loc[jan, 2] == 1           # A in Mar; the March order of B is cancelled
    assert rate.loc[jan, 1] == pytest.approx(0.5)
    assert rate.loc[feb, 0] == 1.0 and rate.loc[feb, 1] == 0.0
    assert np.isnan(rate.loc[feb, 2])        # April not yet elapsed -> NaN, not 0
    assert np.isnan(rate.loc[jan, 3])


def test_incomplete_current_month_is_masked():
    _, rate = coh.cohort_matrix(ORDERS, date(2026, 3, 15), max_periods=3)
    assert np.isnan(rate.loc[pd.Timestamp("2026-01-01"), 2])  # March only half elapsed


def test_retention_curve_weighted():
    rate = pd.DataFrame({0: [1.0, 1.0], 1: [0.5, 0.0]}, index=["x", "y"])
    curve = coh.retention_curve(rate, pd.Series({"x": 3, "y": 1}))
    assert curve[1] == pytest.approx(1.5 / 4)


def test_time_to_second_purchase():
    t2 = coh.time_to_second_purchase(ORDERS)
    assert sorted(t2.tolist()) == [18, 36]   # C: 18 days, A: 36 days; B has no eligible 2nd order


def test_rfm_scores_and_segments():
    r = cust.rfm(ORDERS, date(2026, 3, 31), bins=5).set_index("customer_id")
    assert r.loc["A", "frequency"] == 3 and r.loc["B", "frequency"] == 1
    assert r.loc["B", "f_score"] == 1                    # single order always F=1
    assert r.loc["A", "recency_days"] == 30
    assert r["r_score"].between(1, 5).all() and r["m_score"].between(1, 5).all()
    assert r.loc["C", "m_score"] == 5                    # highest monetary value
    assert set(r["segment"]) <= set(cust.SEGMENT_ORDER)


def test_rfm_segment_rule_precedence():
    seg = []
    for r, f in [(5, 5), (3, 5), (1, 4), (1, 1)]:
        for name, _, rule in cust.SEGMENT_RULES:
            if rule(pd.Series([r]), pd.Series([f])).iloc[0]:
                seg.append(name)
                break
    assert seg == ["Champions", "Loyal", "At risk", "Hibernating / lost"]


def test_modelled_clv_formula():
    surv = pd.Series([1.0, 0.5, 0.25])   # months 0, 1, 2
    v = cust.modelled_clv(surv, monthly_value_per_active=100, gross_margin_pct=0.4, horizon_months=4,
                          annual_discount_rate=0.0)
    assert v == pytest.approx(100 * 0.4 * (1 + 0.5 + 0.25 + 0.25))   # plateau after observed history
    disc = cust.modelled_clv(surv, 100, 0.4, horizon_months=4, annual_discount_rate=0.10)
    assert disc < v
    assert np.isnan(cust.modelled_clv(surv, 100, float("nan")))


def test_new_vs_returning():
    nvr = cust.new_vs_returning(ORDERS)
    feb = nvr[nvr["period"] == pd.Timestamp("2026-02-01")].set_index("customer_type")
    assert feb.loc["New", "orders"] == 1 and feb.loc["Returning", "orders"] == 2
