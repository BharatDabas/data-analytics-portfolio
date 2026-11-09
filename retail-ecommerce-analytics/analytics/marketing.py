"""Marketing performance from v_marketing_daily (grain: campaign x day).

Attribution: last click within a configured window (see config/business_rules.toml).
These are OBSERVATIONAL, attributed figures - they describe association under the chosen
attribution rule, not the incremental (causal) effect of spend.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SUM_COLS = ["spend_gbp", "impressions", "clicks", "attributed_orders", "attributed_new_customers",
            "attributed_net_revenue_gbp"]


def _ratios(g: pd.DataFrame) -> pd.DataFrame:
    def div(a, b):
        return (a / b).where(b > 0)

    g["ctr"] = div(g["clicks"], g["impressions"])
    g["cpc_gbp"] = div(g["spend_gbp"], g["clicks"])
    g["conversion_rate"] = div(g["attributed_orders"], g["clicks"])
    g["roas"] = div(g["attributed_net_revenue_gbp"], g["spend_gbp"])
    # CAC only for acquisition-objective spend; retention campaigns do not acquire customers by design.
    acq_spend = g["acquisition_spend_gbp"] if "acquisition_spend_gbp" in g else g["spend_gbp"]
    g["cac_gbp"] = div(acq_spend, g["attributed_new_customers"])
    return g


def performance(mkt: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    m = mkt.assign(acquisition_spend_gbp=np.where(mkt["objective"] == "acquisition", mkt["spend_gbp"], 0.0))
    g = m.groupby(by, dropna=False)[SUM_COLS + ["acquisition_spend_gbp"]].sum().reset_index()
    return _ratios(g)


def totals(mkt: pd.DataFrame, new_customers_all: int) -> dict[str, float]:
    """Headline marketing KPIs. Blended CAC divides acquisition spend by ALL new customers
    (attributed or not); attributed CAC uses only new customers credited to a campaign."""
    acq = mkt[mkt["objective"] == "acquisition"]
    spend = float(mkt["spend_gbp"].sum())
    acq_spend = float(acq["spend_gbp"].sum())
    attributed_new = float(acq["attributed_new_customers"].sum())
    rev = float(mkt["attributed_net_revenue_gbp"].sum())
    return {
        "spend": spend,
        "acquisition_spend": acq_spend,
        "attributed_revenue": rev,
        "roas": rev / spend if spend > 0 else float("nan"),
        "attributed_cac": acq_spend / attributed_new if attributed_new > 0 else float("nan"),
        "blended_cac": acq_spend / new_customers_all if new_customers_all > 0 else float("nan"),
        "attributed_orders": float(mkt["attributed_orders"].sum()),
        "attributed_new_customers": attributed_new,
        "clicks": float(mkt["clicks"].sum()),
        "conversion_rate": float(mkt["attributed_orders"].sum()) / float(mkt["clicks"].sum())
        if mkt["clicks"].sum() > 0 else float("nan"),
    }
