import math
from datetime import date

import pandas as pd
import pytest

from analytics.planning import (projected_stockout_date, recommend, reorder_point, safety_stock, suggested_order_qty,
                                target_stock, urgency, z_score)


def test_z_score_95():
    assert z_score(0.95) == pytest.approx(1.6449, abs=1e-4)
    with pytest.raises(ValueError):
        z_score(1.0)


def test_safety_stock_zero_lead_time_variance_reduces_to_classic_formula():
    # z * sigma_d * sqrt(LT)
    assert safety_stock(1.645, 10, 4, 9, 0) == pytest.approx(1.645 * 4 * 3)


def test_safety_stock_includes_lead_time_variability():
    # sqrt(9*16 + 100*4) = sqrt(544)
    assert safety_stock(2.0, 10, 4, 9, 2) == pytest.approx(2.0 * math.sqrt(544))


def test_safety_stock_zero_demand_variability_and_zero_lt_variance_is_zero():
    assert safety_stock(1.645, 10, 0, 9, 0) == 0


def test_reorder_point_and_target():
    assert reorder_point(10, 5, 20) == 70
    assert target_stock(10, 5, 7, 20) == 140


def test_order_rounds_up_to_pack_and_respects_moq():
    # need 140 - 55 = 85 -> pack 12 -> 96
    assert suggested_order_qty(55, 70, 140, moq=24, multiple=12) == 96
    # small need below MOQ -> MOQ (already a pack multiple)
    assert suggested_order_qty(69, 70, 75, moq=48, multiple=12) == 48


def test_no_order_above_reorder_point():
    assert suggested_order_qty(71, 70, 140, moq=1, multiple=1) == 0


def test_order_at_exact_reorder_point():
    assert suggested_order_qty(70, 70, 140, moq=1, multiple=1) == 70


def test_projected_stockout_date():
    assert projected_stockout_date(25, 10, date(2026, 6, 30)) == date(2026, 7, 2)
    assert projected_stockout_date(25, 0, date(2026, 6, 30)) is None


def test_urgency_levels():
    assert urgency(0, 0, 10, 1, 5, 7) == "Critical"
    assert urgency(8, 9, 10, 1, 5, 7) == "High"
    assert urgency(12, 15, 10, 1, 5, 7) == "Medium"
    assert urgency(100, 100, 10, 1, 5, 7) == "Low"
    assert urgency(5, 5, 0, 0, 5, 7) == "No demand"


def _inputs(**kw):
    row = dict(sku="S1", location_id="L1", abc_class="A", avg_daily_demand=10.0, demand_std=3.0, lead_time_days=10.0,
               lead_time_std=2.0, on_hand_qty=60.0, available_qty=50.0, on_order_qty=0.0, moq_units=12,
               case_pack=12, unit_cost_usd=2.0)
    row.update(kw)
    return pd.DataFrame([row])


def test_recommend_what_if_levers_increase_requirements():
    base = recommend(_inputs(), date(2026, 6, 30), 7, {"A": 0.98}, 0.95).iloc[0]
    up = recommend(_inputs(), date(2026, 6, 30), 7, {"A": 0.98}, 0.95, demand_uplift=0.2).iloc[0]
    late = recommend(_inputs(), date(2026, 6, 30), 7, {"A": 0.98}, 0.95, supplier_delay_days=5).iloc[0]
    assert base["service_level"] == 0.98
    assert up["reorder_point"] > base["reorder_point"]
    assert late["safety_stock"] > base["safety_stock"] and late["lead_time_days"] == 15
    assert base["suggested_order_qty"] % 12 == 0 and base["suggested_order_qty"] > 0


def test_recommend_service_override_and_no_demand():
    out = recommend(_inputs(avg_daily_demand=0.0, demand_std=0.0), date(2026, 6, 30), 7, {"A": 0.98}, 0.95,
                    service_level_override=0.9).iloc[0]
    assert out["service_level"] == 0.9
    assert out["suggested_order_qty"] == 0 and out["urgency"] == "No demand"
    assert out["excess_qty"] == 60  # all stock is excess when nothing is demanded
