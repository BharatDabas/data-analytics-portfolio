import math

import numpy as np
import pandas as pd
import pytest

from analytics.forecasting import (backtest, bias, croston_sba, demand_pattern, forecast_series, mae,
                                   moving_average, seasonal_naive, select_method, ses, wape)


def test_wape_mae_bias():
    a, f = [10, 0, 20], [12, 1, 15]
    assert wape(a, f) == pytest.approx(8 / 30)
    assert mae(a, f) == pytest.approx(8 / 3)
    assert bias(a, f) == pytest.approx((28 - 30) / 30)


def test_wape_and_bias_undefined_when_all_actuals_zero():
    assert math.isnan(wape([0, 0], [1, 2]))
    assert math.isnan(bias([0, 0], [1, 2]))
    assert mae([0, 0], [1, 2]) == 1.5


def test_seasonal_naive_repeats_last_season_and_falls_back():
    h = np.arange(104, dtype=float)
    assert seasonal_naive(h, 3).tolist() == [52, 53, 54]
    assert seasonal_naive(np.array([2.0, 4.0]), 2).tolist() == [3.0, 3.0]  # MA fallback


def test_ses_and_ma():
    assert ses(np.array([10.0, 20.0]), 2, alpha=0.5).tolist() == [15.0, 15.0]
    assert moving_average(np.array([1.0] * 4 + [3.0] * 8), 1).tolist() == [3.0]


def test_croston_handles_intermittent_and_zero_series():
    hist = np.array([0, 0, 6, 0, 0, 6, 0, 0, 6], float)
    fc = croston_sba(hist, 2)
    assert fc[0] == pytest.approx((1 - 0.05) * 6 / 3)
    assert croston_sba(np.zeros(10), 3).tolist() == [0, 0, 0]


def test_backtest_has_no_leakage():
    hist = np.arange(30, dtype=float)
    bt = backtest(hist, origins=2, horizon=4)
    for r in bt.itertuples():
        assert r.target_idx >= r.origin_idx
        assert r.actual == hist[r.target_idx]
    # moving average at origin 26 uses only weeks 18..25 => 21.5
    ma = bt[(bt.method == "moving_average") & (bt.origin_idx == 26)]["forecast"].unique()
    assert ma.tolist() == [21.5]


def test_select_method_falls_back_to_mae_when_wape_undefined():
    bt = pd.DataFrame({"method": ["a", "a", "b", "b"], "forecast": [1, 1, 0, 0], "actual": [0, 0, 0, 0]})
    best, scores = select_method(bt)
    assert best == "b" and scores["wape"].isna().all()


def test_forecast_series_short_history():
    method, bt, scores, point, rmse = forecast_series(np.array([1.0, 2.0, 3.0]), 4, 4, 4)
    assert method == "moving_average" and bt.empty and len(point) == 4 and math.isnan(rmse)


def test_demand_pattern():
    assert demand_pattern(np.array([0, 0, 5, 0])) == "Intermittent"
    assert demand_pattern(np.array([3, 4, 5, 2])) == "Regular"
    assert demand_pattern(np.zeros(4)) == "No demand"
