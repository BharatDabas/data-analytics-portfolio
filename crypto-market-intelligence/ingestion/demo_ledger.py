"""Deterministic SYNTHETIC demo transaction ledger.

Execution prices are derived from the synthetic market closes (with a small
spread) so valuations are internally consistent. Includes buys, partial sells,
transfers in/out and standalone fees so every accounting path is exercised.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

PORTFOLIO_ID = "demo-portfolio"

# (day offset from history start, type, asset, quantity or None for notional, notional USD, fee bps)
PLAN = [
    (30, "BUY", "demo-aurora", None, 20_000, 10),
    (32, "BUY", "demo-borealis", None, 12_000, 10),
    (45, "BUY", "demo-cobalt", None, 6_000, 15),
    (60, "BUY", "demo-flux", None, 3_000, 15),
    (75, "TRANSFER_IN", "demo-aurora", 0.10, None, 0),       # from an external cold wallet
    (90, "BUY", "demo-harbor-usd", 5_000.0, None, 5),
    (120, "BUY", "demo-ember", None, 2_500, 20),
    (150, "SELL", "demo-borealis", None, 4_000, 10),         # partial disposal
    (180, "BUY", "demo-aurora", None, 8_000, 10),
    (200, "FEE", "demo-aurora", 0.0, 25, 0),                 # custody fee (USD)
    (230, "BUY", "demo-dune", None, 2_000, 20),
    (260, "SELL", "demo-ember", None, 1_500, 20),
    (300, "BUY", "demo-borealis", None, 5_000, 10),
    (330, "TRANSFER_OUT", "demo-cobalt", None, 1_000, 0),    # to an external wallet - not a sale
    (365, "SELL", "demo-aurora", None, 9_000, 10),           # spans several FIFO lots
    (400, "BUY", "demo-flux-network", None, 1_500, 25),
    (430, "SELL", "demo-harbor-usd", 2_000.0, None, 5),
    (470, "BUY", "demo-cobalt", None, 3_000, 15),
    (520, "SELL", "demo-flux", None, 3_500, 15),             # likely full exit
    (560, "BUY", "demo-ember", None, 2_000, 20),
    (600, "SELL", "demo-borealis", None, 3_000, 10),
    (640, "BUY", "demo-aurora", None, 5_000, 10),
    (680, "BUY", "demo-dune", None, 1_000, 20),
]


def generate_demo_ledger(prices: pd.DataFrame, seed: int = 42) -> pd.DataFrame:
    """prices: asset_id, price_date, close (synthetic)."""
    rng = np.random.default_rng(seed + 7)
    start = pd.to_datetime(prices["price_date"]).min()
    lookup = prices.assign(price_date=pd.to_datetime(prices["price_date"])).set_index(["asset_id", "price_date"])["close"]
    lookup = lookup[~lookup.index.duplicated()]
    held: dict[str, float] = {}
    rows = []
    for n, (offset, ttype, asset, qty, notional, fee_bps) in enumerate(PLAN, start=1):
        day = start + timedelta(days=offset)
        # use the nearest earlier observed close (synthetic data has deliberate gaps)
        s = lookup.loc[asset]
        s = s[s.index <= day]
        if s.empty:
            continue
        px = float(s.iloc[-1]) * (1 + rng.uniform(-0.004, 0.004))
        if ttype == "FEE":
            q, price, fee = 0.0, None, float(notional)
        else:
            q = float(qty) if qty is not None else round(notional / px, 8)
            if ttype in ("SELL", "TRANSFER_OUT"):
                q = min(q, held.get(asset, 0.0))
                if ttype == "SELL" and asset == "demo-flux":
                    q = held.get(asset, 0.0)  # full exit of the position
                if q <= 0:
                    continue
            price = px if ttype in ("BUY", "SELL") else (round(px * 0.82, 6) if ttype == "TRANSFER_IN" else None)
            fee = round(q * px * fee_bps / 10_000, 2) if ttype in ("BUY", "SELL") else (4.5 if "TRANSFER" in ttype else 0.0)
        held[asset] = held.get(asset, 0.0) + (q if ttype in ("BUY", "TRANSFER_IN") else -q if ttype in ("SELL", "TRANSFER_OUT") else 0)
        ts = pd.Timestamp(day).tz_localize("UTC") + pd.Timedelta(hours=int(rng.integers(8, 20)), minutes=int(rng.integers(0, 60)))
        rows.append({"transaction_id": f"DEMO-{n:04d}", "portfolio_id": PORTFOLIO_ID, "asset_id": asset,
                     "txn_type": ttype, "quantity": q, "unit_price": price, "fee": fee, "quote_currency": "usd",
                     "executed_at": ts, "reference": f"synthetic-{ttype.lower()}-{n}", "entry_source": "demo_ledger"})
    return pd.DataFrame(rows)
