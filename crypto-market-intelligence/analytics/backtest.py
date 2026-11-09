"""Optional historical simulation: periodic rebalancing to target weights vs buy-and-hold.

Disclosed assumptions (also shown on the dashboard):
- Trades execute at the daily close; slippage is a fixed bps haircut on traded notional.
- Fees are a fixed bps of traded notional; no minimum ticket, no taxes, no funding costs.
- Rebalancing happens on the first available day of each period ('M' monthly, 'W' weekly).
- Days with a missing price for any held asset carry the previous weights (no fill of prices;
  the asset contributes 0% return that day, which understates volatility slightly).
- Survivorship: the universe is fixed today; delisted/failed assets are absent, which biases
  historical results upwards. Past simulated performance does not predict future results.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def backtest(returns: pd.DataFrame, weights: dict[str, float], rebalance: str | None = "M",
             fee_bps: float = 10.0, slippage_bps: float = 5.0, initial: float = 10_000.0) -> pd.DataFrame:
    """returns: daily simple returns, columns = asset_id. Returns daily value, turnover and costs."""
    assets = [a for a in weights if a in returns.columns]
    if not assets:
        raise ValueError("no weighted assets present in returns")
    r = returns[assets].fillna(0.0)
    target = np.array([weights[a] for a in assets], dtype=float)
    target = target / target.sum()
    cost_rate = (fee_bps + slippage_bps) / 10_000
    holdings = initial * target * (1 - cost_rate)          # initial purchase pays costs too
    period = r.index.to_period(rebalance) if rebalance else None
    rows = []
    for i, (d, row) in enumerate(r.iterrows()):
        holdings = holdings * (1 + row.values)
        value = holdings.sum()
        turnover = 0.0
        cost = 0.0
        if rebalance and i > 0 and period[i] != period[i - 1]:
            desired = value * target
            turnover = float(np.abs(desired - holdings).sum())
            cost = turnover * cost_rate
            holdings = (value - cost) * target
            value = holdings.sum()
        rows.append((d, value, turnover, cost))
    out = pd.DataFrame(rows, columns=["date", "value", "turnover", "costs"]).set_index("date")
    out["return"] = out["value"].pct_change(fill_method=None)
    return out
