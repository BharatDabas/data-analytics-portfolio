"""Replenishment decision support: safety stock, reorder point, target stock and order suggestions.

Outputs are RECOMMENDATIONS for planner review. Nothing here creates purchase orders.

Formulas (daily units, lead time in days):
    safety stock   SS  = z * sqrt(LT * sigma_d^2 + d^2 * sigma_LT^2)
    reorder point  ROP = d * LT + SS
    target stock   T   = d * (LT + R) + SS          (R = review period)
    inventory position IP = available + on order
    order qty      Q   = max(T - IP, MOQ) rounded up to the order multiple, only when IP <= ROP
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import NormalDist

import numpy as np
import pandas as pd


def z_score(service_level: float) -> float:
    """Cycle service level -> standard normal z (e.g. 0.95 -> 1.645)."""
    if not 0 < service_level < 1:
        raise ValueError("service level must be between 0 and 1 (exclusive)")
    return NormalDist().inv_cdf(service_level)


def safety_stock(z: float, d_mean: float, d_std: float, lt_mean: float, lt_std: float) -> float:
    return z * math.sqrt(max(lt_mean, 0) * d_std ** 2 + d_mean ** 2 * lt_std ** 2)


def reorder_point(d_mean: float, lt_mean: float, ss: float) -> float:
    return d_mean * lt_mean + ss


def target_stock(d_mean: float, lt_mean: float, review_days: float, ss: float) -> float:
    return d_mean * (lt_mean + review_days) + ss


def suggested_order_qty(position: float, rop: float, target: float, moq: float, multiple: float) -> float:
    """Order up to target when position is at/below ROP; respect MOQ and pack multiple."""
    if position > rop:
        return 0.0
    qty = max(target - position, moq, 0.0)
    if multiple and multiple > 1:
        qty = math.ceil(qty / multiple - 1e-9) * multiple
    return float(qty)


def projected_stockout_date(available: float, d_mean: float, as_of: date) -> date | None:
    """Date stock runs out at the current average rate (None when there is no demand)."""
    if d_mean <= 0:
        return None
    return as_of + timedelta(days=int(math.floor(max(available, 0.0) / d_mean)))


def urgency(available: float, position: float, rop: float, d_mean: float, lt_mean: float,
            review_days: float) -> str:
    if d_mean <= 0:
        return "No demand"
    cover = max(available, 0.0) / d_mean
    if available <= 0 or (cover < lt_mean and position <= rop):
        return "Critical"
    if position <= rop:
        return "High"
    if position <= rop + d_mean * review_days:
        return "Medium"
    return "Low"


URGENCY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "No demand": 4}


def recommend(inputs: pd.DataFrame, as_of: date, review_days: float, service_by_abc: dict[str, float],
              default_service: float, demand_uplift: float = 0.0, supplier_delay_days: float = 0.0,
              service_level_override: float | None = None) -> pd.DataFrame:
    """Vectorised-by-row replenishment calculation with what-if levers.

    inputs columns: sku, location_id, abc_class, avg_daily_demand, demand_std, lead_time_days,
    lead_time_std, on_hand_qty, available_qty, on_order_qty, moq_units, case_pack, unit_cost_usd.
    demand_uplift scales mean and std of demand (e.g. 0.2 = +20%); supplier_delay_days adds to lead time.
    """
    rows = []
    for r in inputs.itertuples(index=False):
        sl = service_level_override or service_by_abc.get(r.abc_class, default_service)
        z = z_score(sl)
        d = float(r.avg_daily_demand) * (1 + demand_uplift)
        sd = float(r.demand_std) * (1 + demand_uplift)
        lt = float(r.lead_time_days) + supplier_delay_days
        ss = safety_stock(z, d, sd, lt, float(r.lead_time_std))
        rop = reorder_point(d, lt, ss)
        tgt = target_stock(d, lt, review_days, ss)
        pos = float(r.available_qty) + float(r.on_order_qty)
        q = suggested_order_qty(pos, rop, tgt, float(r.moq_units), float(r.case_pack)) if d > 0 else 0.0
        on_hand = max(float(r.on_hand_qty), 0.0)
        excess = max(on_hand - tgt, 0.0) if d > 0 else on_hand
        rows.append({
            "sku": r.sku, "location_id": r.location_id, "abc_class": r.abc_class, "service_level": sl,
            "avg_daily_demand": d, "demand_std": sd, "lead_time_days": lt, "lead_time_std": float(r.lead_time_std),
            "on_hand_qty": float(r.on_hand_qty), "available_qty": float(r.available_qty),
            "on_order_qty": float(r.on_order_qty), "inventory_position": pos,
            "safety_stock": ss, "reorder_point": rop, "target_stock": tgt, "suggested_order_qty": q,
            "days_of_cover": max(float(r.available_qty), 0.0) / d if d > 0 else np.inf,
            "projected_stockout_date": projected_stockout_date(float(r.available_qty), d, as_of),
            "urgency": urgency(float(r.available_qty), pos, rop, d, lt, review_days),
            "unit_cost_usd": float(r.unit_cost_usd), "stock_value_usd": on_hand * float(r.unit_cost_usd),
            "excess_qty": excess, "excess_value_usd": excess * float(r.unit_cost_usd),
            "expected_cover_after_order": (pos + q) / d if d > 0 else np.inf,
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["_u"] = out["urgency"].map(URGENCY_ORDER)
    return out.sort_values(["_u", "days_of_cover"]).drop(columns="_u").reset_index(drop=True)
