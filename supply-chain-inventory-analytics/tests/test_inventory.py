import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from analytics.inventory import days_of_cover, fifo_ageing, fill_rate, inventory_turnover, stockout_rate


def test_days_of_cover_regular():
    assert days_of_cover(100, 10) == pytest.approx(10.0)


def test_days_of_cover_zero_demand_with_stock_is_infinite():
    assert math.isinf(days_of_cover(50, 0))


def test_days_of_cover_zero_demand_zero_stock_is_undefined():
    assert math.isnan(days_of_cover(0, 0))


def test_days_of_cover_negative_stock_is_zero_cover():
    assert days_of_cover(-5, 2) == 0.0


def test_days_of_cover_vectorised():
    out = days_of_cover(np.array([10, 5, 0]), np.array([2, 0, 0]))
    assert out[0] == 5 and math.isinf(out[1]) and math.isnan(out[2])


def test_inventory_turnover_and_zero_inventory():
    assert inventory_turnover(1200, 300) == 4
    assert math.isnan(inventory_turnover(1200, 0))


def test_fill_rate_counts_backorders_as_unfilled():
    assert fill_rate(90, 100) == 0.9
    assert math.isnan(fill_rate(0, 0))


def test_stockout_rate_uses_days_with_demand_as_denominator():
    req = pd.Series([0, 5, 5, 0, 3])
    back = pd.Series([0, 0, 2, 0, 3])   # 2 of 3 eligible days short
    assert stockout_rate(req, back) == pytest.approx(2 / 3)


def test_stockout_rate_intermittent_no_demand():
    assert math.isnan(stockout_rate(pd.Series([0, 0]), pd.Series([0, 0])))


def test_fifo_ageing_assigns_stock_to_latest_receipts_first():
    as_of = date(2026, 6, 30)
    inbound = [(date(2026, 6, 20), 40), (date(2026, 5, 1), 100), (date(2026, 1, 1), 100)]
    out = fifo_ageing(100, inbound, as_of)
    assert out["0-30 days"] == 40          # 10 days old
    assert out["31-60 days"] == 60         # 60 days old, partly consumed
    assert out["180+ days"] == 0


def test_fifo_ageing_unexplained_stock_goes_to_oldest_bucket():
    out = fifo_ageing(30, [(date(2026, 6, 29), 10)], date(2026, 6, 30))
    assert out["0-30 days"] == 10 and out["180+ days"] == 20


def test_fifo_ageing_negative_on_hand_is_zero():
    assert sum(fifo_ageing(-5, [], date(2026, 6, 30)).values()) == 0
