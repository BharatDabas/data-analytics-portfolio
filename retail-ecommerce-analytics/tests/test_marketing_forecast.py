from datetime import date

import numpy as np
import pandas as pd
import pytest

from analytics import forecast as fc
from analytics import marketing as mk
from transformations.build import attribute_orders


def _orders():
    t = pd.Timestamp("2026-05-10 12:00", tz="UTC")
    return pd.DataFrame({
        "order_id": ["O1", "O2", "O3", "O4"],
        "order_ts": [t] * 4, "order_date": [t.date()] * 4,
        "is_cancelled": [False, False, False, True],
        "last_touch_campaign_id": ["C1", "C1", "C2", "C1"],
        "last_touch_ts": [t - pd.Timedelta(days=2), t - pd.Timedelta(days=10), t + pd.Timedelta(hours=1),
                          t - pd.Timedelta(days=1)],
        "is_new_customer_order": [True, False, True, True],
    })


def test_attribution_window_and_exclusions():
    camps = pd.DataFrame({"campaign_id": ["C1", "C2"], "channel": ["Paid Search", "Email"]})
    b = attribute_orders(_orders(), camps, window_days=7)
    # O2 outside window, O3 click after order, O4 cancelled -> only O1 attributed
    assert b["order_id"].tolist() == ["O1"]
    assert b["weight"].sum() == 1.0
    assert b["order_id"].is_unique


def test_marketing_ratios_and_cac_only_for_acquisition():
    m = pd.DataFrame({
        "marketing_channel": ["Search", "Search", "Email"], "objective": ["acquisition", "acquisition", "retention"],
        "spend_gbp": [100.0, 100.0, 50.0], "impressions": [1000, 1000, 0], "clicks": [50, 50, 0],
        "attributed_orders": [5, 5, 4], "attributed_new_customers": [4, 0, 0],
        "attributed_net_revenue_gbp": [300.0, 100.0, 200.0],
    })
    p = mk.performance(m, ["marketing_channel"]).set_index("marketing_channel")
    assert p.loc["Search", "roas"] == pytest.approx(2.0)
    assert p.loc["Search", "cac_gbp"] == pytest.approx(50.0)
    assert np.isnan(p.loc["Email", "cac_gbp"])          # no acquisition spend
    assert np.isnan(p.loc["Email", "ctr"])              # zero impressions -> NaN not inf
    t = mk.totals(m, new_customers_all=10)
    assert t["roas"] == pytest.approx(600 / 250)
    assert t["blended_cac"] == pytest.approx(20.0)
    assert t["attributed_cac"] == pytest.approx(50.0)


def test_forecast_metrics():
    a, f = [10, 0, 30], [12, 3, 27]
    assert fc.mae(a, f) == pytest.approx((2 + 3 + 3) / 3)
    assert fc.wape(a, f) == pytest.approx(8 / 40)
    assert fc.bias(a, f) == pytest.approx(2 / 40)
    assert np.isnan(fc.wape([0, 0], [1, 1]))            # zero-demand denominator


def test_weekly_series_zero_fills_and_drops_partial_weeks():
    df = pd.DataFrame({"d": pd.to_datetime(["2026-01-01", "2026-01-06", "2026-01-21", "2026-01-28"]).date,
                       "v": [5.0, 1.0, 2.0, 4.0]})
    s = fc.weekly_series(df, "d", "v", as_of=date(2026, 1, 28))
    # first partial week (w/c 29 Dec) and the incomplete as-of week (w/c 26 Jan) are dropped
    assert s["week"].min() == pd.Timestamp("2026-01-05") and s["week"].max() == pd.Timestamp("2026-01-19")
    assert s["v"].tolist() == [1.0, 0.0, 2.0]


def test_backtest_recovers_seasonal_series():
    weeks = pd.date_range("2024-01-01", periods=130, freq="7D")
    y = 1000 + 300 * np.sin(2 * np.pi * np.arange(130) / 52)
    out, m = fc.backtest_and_forecast(pd.DataFrame({"week": weeks, "v": y}), "v", holdout=12, horizon=8)
    assert m["model"].startswith("Seasonal naive")
    assert m["wape"] < 0.05
    fut = out[out["forecast"].notna()]
    assert len(fut) == 8 and (fut["upper"] >= fut["forecast"]).all() and (fut["lower"] <= fut["forecast"]).all()


def test_backtest_requires_history():
    with pytest.raises(ValueError):
        fc.backtest_and_forecast(pd.DataFrame({"week": pd.date_range("2026-01-05", periods=10, freq="7D"),
                                               "v": np.ones(10)}), "v", holdout=12)
