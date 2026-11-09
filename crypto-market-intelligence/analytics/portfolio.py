"""Daily portfolio snapshots, valuation with a documented stale-price fallback, and
time-weighted returns.

Valuation fallback: if an asset has no close for the snapshot day, the most recent
close no older than `max_stale_days` is used and the snapshot is flagged
`price_is_stale`. Beyond that window the position is left unvalued (NULL) - prices
are never silently forward-filled.

Time-weighted return (TWR): external flows are assumed to occur at the start of the day:
    r_t = MV_t / (MV_{t-1} + F_t) - 1
where F_t = buy cost (incl. fee) - sale proceeds (net of fee) + market value of units
transferred in - market value of units transferred out. Standalone fees are not flows.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.ledger import run_ledger


def price_lookup(prices: pd.DataFrame, dates: pd.DatetimeIndex, max_stale_days: int) -> pd.DataFrame:
    """prices: columns asset_id, price_date, close. Returns long frame per asset x date with
    close_price, price_date (used) and price_is_stale."""
    out = []
    for asset, g in prices.groupby("asset_id"):
        s = g.assign(price_date=pd.to_datetime(g["price_date"])).set_index("price_date")["close"].sort_index()
        s = s[~s.index.duplicated(keep="last")]
        obs_dates = pd.Series(s.index, index=s.index)
        idx = pd.DatetimeIndex(dates)
        used = obs_dates.reindex(obs_dates.index.union(idx)).ffill().reindex(idx)
        close = s.reindex(s.index.union(idx)).ffill().reindex(idx)
        age = (idx - pd.DatetimeIndex(used)).days
        ok = used.notna() & (age <= max_stale_days)
        out.append(pd.DataFrame({
            "asset_id": asset, "snapshot_date": idx,
            "close_price": close.where(ok).values,
            "price_date": pd.to_datetime(used.where(ok)).values,
            "price_is_stale": ok & (age > 0)}))
    if not out:
        return pd.DataFrame(columns=["asset_id", "snapshot_date", "close_price", "price_date", "price_is_stale"])
    return pd.concat(out, ignore_index=True)


def build_snapshots(transactions: pd.DataFrame, prices: pd.DataFrame, end_date: pd.Timestamp,
                    max_stale_days: int = 3, valuation_currency: str = "USD") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (fact_portfolio_snapshot rows, fact_disposals rows)."""
    cols = ["portfolio_id", "asset_id", "snapshot_date", "units_held", "cost_basis", "close_price",
            "price_date", "price_is_stale", "market_value", "external_flow", "valuation_currency"]
    if transactions.empty:
        return pd.DataFrame(columns=cols), pd.DataFrame()
    result = run_ledger(transactions)
    states = result.states.copy()
    states["snapshot_date"] = states["executed_at"].dt.tz_convert("UTC").dt.tz_localize(None).dt.normalize()
    # end-of-day state per portfolio/asset
    eod = states.groupby(["portfolio_id", "asset_id", "snapshot_date"]).last().reset_index()

    start = eod["snapshot_date"].min()
    dates = pd.date_range(start, pd.Timestamp(end_date).normalize(), freq="D")
    px = price_lookup(prices, dates, max_stale_days)

    tx = transactions.copy()
    tx["txn_type"] = tx["txn_type"].str.upper()
    tx["snapshot_date"] = pd.to_datetime(tx["executed_at"], utc=True).dt.tz_localize(None).dt.normalize()
    tx = tx.merge(px[["asset_id", "snapshot_date", "close_price"]], on=["asset_id", "snapshot_date"], how="left")
    fee = pd.to_numeric(tx["fee"], errors="coerce").fillna(0)
    q = tx["quantity"].astype(float)
    p = pd.to_numeric(tx["unit_price"], errors="coerce")
    transfer_value = q * tx["close_price"].fillna(p)  # market value; carried cost if no price
    tx["flow"] = np.select(
        [tx["txn_type"] == "BUY", tx["txn_type"] == "SELL",
         tx["txn_type"] == "TRANSFER_IN", tx["txn_type"] == "TRANSFER_OUT"],
        [q * p + fee, -(q * p - fee), transfer_value, -transfer_value], default=0.0)
    flows = tx.groupby(["portfolio_id", "asset_id", "snapshot_date"])["flow"].sum()

    frames = []
    for (pf, asset), g in eod.groupby(["portfolio_id", "asset_id"]):
        g = g.set_index("snapshot_date")[["units", "cost_basis"]]
        g = g.reindex(dates).ffill()  # holdings persist until the next transaction (not a price fill)
        g = g.dropna(subset=["units"])
        g["portfolio_id"], g["asset_id"] = pf, asset
        frames.append(g.reset_index(names="snapshot_date"))
    snap = pd.concat(frames, ignore_index=True)
    snap = snap.merge(px, on=["asset_id", "snapshot_date"], how="left")
    snap["price_is_stale"] = snap["price_is_stale"].fillna(False).astype(bool)
    snap["market_value"] = snap["units"] * snap["close_price"]
    snap.loc[snap["units"].abs() < 1e-12, "market_value"] = 0.0  # closed position: value 0, not unknown
    snap = snap.merge(flows.rename("external_flow").reset_index(), on=["portfolio_id", "asset_id", "snapshot_date"],
                      how="left")
    snap["external_flow"] = snap["external_flow"].fillna(0.0)
    snap = snap.rename(columns={"units": "units_held"})
    snap["valuation_currency"] = valuation_currency
    # drop days after a position was fully closed with no activity (value is 0, keeps table lean)
    closed = (snap["units_held"].abs() < 1e-12) & (snap["external_flow"] == 0)
    snap = snap[~closed].copy()
    snap["snapshot_date"] = snap["snapshot_date"].dt.date
    snap["price_date"] = pd.to_datetime(snap["price_date"]).dt.date
    return snap[cols].reset_index(drop=True), result.disposals


def portfolio_daily(snapshots: pd.DataFrame) -> pd.DataFrame:
    """Portfolio-level daily value, flow and TWR. A day is 'complete' only if every held
    position has a permitted price; incomplete days get NaN value and NaN return."""
    s = snapshots.copy()
    s["snapshot_date"] = pd.to_datetime(s["snapshot_date"])
    s["unvalued"] = s["market_value"].isna() & (s["units_held"].abs() > 1e-12)
    g = s.groupby("snapshot_date").agg(market_value=("market_value", "sum"), cost_basis=("cost_basis", "sum"),
                                       external_flow=("external_flow", "sum"), unvalued=("unvalued", "sum"),
                                       stale=("price_is_stale", "sum"))
    g.loc[g["unvalued"] > 0, "market_value"] = np.nan
    prev = g["market_value"].shift(1).fillna(0.0)
    denom = prev + g["external_flow"]
    g["twr_return"] = np.where(denom > 1e-9, g["market_value"] / denom.where(denom > 1e-9) - 1, np.nan)
    g.loc[g["market_value"].isna(), "twr_return"] = np.nan
    g["twr_index"] = 100 * (1 + g["twr_return"].fillna(0)).cumprod()
    return g.reset_index()
