import math
from datetime import date

import pandas as pd
import pytest

from analytics import kpis
from tests.conftest import make_lines, orders_from_lines


def test_kpis_hand_computed():
    lines = make_lines([
        # order 1: 2 x £50, £10 discount, 1 unit returned for £45, cost £20
        dict(order_id="O1", customer_id="C1", quantity=2, unit_price_gbp=50.0, discount_gbp=10.0,
             returned_quantity=1, refund_gbp=45.0, unit_cost_gbp=20.0),
        # order 2: 1 x £100, product without reliable cost
        dict(order_id="O2", customer_id="C2", quantity=1, unit_price_gbp=100.0, has_reliable_cost=False, unit_cost_gbp=None),
        # order 3: cancelled - must be ignored everywhere except cancellation rate
        dict(order_id="O3", customer_id="C1", quantity=5, unit_price_gbp=10.0, unit_cost_gbp=5.0, is_cancelled=True),
    ])
    k = kpis.commercial_kpis(lines, orders_from_lines(lines))
    assert k["gross_sales"] == pytest.approx(200.0)
    assert k["discounts"] == pytest.approx(10.0)
    assert k["refunds"] == pytest.approx(45.0)
    assert k["net_revenue"] == pytest.approx(145.0)
    assert k["orders"] == 2
    assert k["aov"] == pytest.approx(72.5)
    # GP only on costed line: net 45 - cogs (2-1)*20 = 25 ; margin 25/45
    assert k["gross_profit"] == pytest.approx(25.0)
    assert k["gross_margin_pct"] == pytest.approx(25 / 45)
    assert k["cost_coverage"] == pytest.approx(45 / 145)
    assert k["return_rate_units"] == pytest.approx(1 / 3)
    assert k["return_rate_orders"] == pytest.approx(0.5)
    assert k["cancellation_rate"] == pytest.approx(1 / 3)


def test_zero_revenue_and_all_returned_handled():
    lines = make_lines([dict(order_id="O1", customer_id="C1", quantity=1, unit_price_gbp=40.0, returned_quantity=1,
                             refund_gbp=40.0, unit_cost_gbp=10.0)])
    k = kpis.commercial_kpis(lines, orders_from_lines(lines))
    assert k["net_revenue"] == 0
    assert math.isnan(k["gross_margin_pct"])  # zero denominator -> NaN, never inf / error
    assert k["gross_profit"] == pytest.approx(0.0)  # returned unit restocked: cost reversed
    assert k["return_rate_units"] == 1.0


def test_only_cancelled_orders():
    lines = make_lines([dict(order_id="O1", customer_id="C1", quantity=1, unit_price_gbp=40.0, unit_cost_gbp=10.0,
                             is_cancelled=True)])
    k = kpis.commercial_kpis(lines, orders_from_lines(lines))
    assert k["orders"] == 0 and k["net_revenue"] == 0
    assert math.isnan(k["aov"])
    assert k["cancellation_rate"] == 1.0


def test_missing_cost_everywhere_gives_no_gross_profit():
    lines = make_lines([dict(order_id="O1", customer_id="C1", quantity=1, unit_price_gbp=40.0,
                             has_reliable_cost=False, unit_cost_gbp=None)])
    k = kpis.commercial_kpis(lines, orders_from_lines(lines))
    assert math.isnan(k["gross_profit"]) and math.isnan(k["gross_margin_pct"])


def test_safe_div_and_pct_change():
    assert math.isnan(kpis.safe_div(1, 0))
    assert math.isnan(kpis.safe_div(1, -5))
    assert kpis.safe_div(1, 4) == 0.25
    assert math.isnan(kpis.pct_change(10, 0))
    assert kpis.pct_change(110, 100) == pytest.approx(0.10)


def test_previous_period_equal_length():
    s, e = kpis.previous_period(date(2026, 3, 1), date(2026, 3, 31))
    assert (s, e) == (date(2026, 1, 29), date(2026, 2, 28))


def test_repeat_purchase_rate_ignores_cancelled():
    orders = pd.DataFrame({"customer_id": ["A", "A", "B", "B", "C"],
                           "is_cancelled": [False, False, False, True, False]})
    assert kpis.repeat_purchase_rate(orders) == pytest.approx(1 / 3)


def test_product_performance_shares_and_discount_dependency():
    lines = make_lines([
        dict(order_id="O1", customer_id="C1", quantity=1, unit_price_gbp=100.0, discount_gbp=20.0, unit_cost_gbp=40.0,
             category="A"),
        dict(order_id="O2", customer_id="C2", quantity=1, unit_price_gbp=20.0, unit_cost_gbp=5.0, category="B"),
    ])
    p = kpis.product_performance(lines, "category").set_index("category")
    assert p["revenue_share"].sum() == pytest.approx(1.0)
    assert p.loc["A", "discounted_sales_share"] == pytest.approx(1.0)
    assert p.loc["B", "discounted_sales_share"] == pytest.approx(0.0)
    assert p.loc["A", "gross_margin_pct"] == pytest.approx(40 / 80)
