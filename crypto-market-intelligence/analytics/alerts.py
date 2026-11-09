"""Rule-based alert engine (price threshold, % move, volume spike, stale data).

Rules come from config/alert_rules.toml (or the dashboard editor). Events are
de-duplicated by (rule, asset, observation) so re-running is idempotent.
Threshold rules fire on the *crossing* day, not on every day above the level.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

EVENT_COLUMNS = ["alert_id", "rule_id", "rule_name", "rule_type", "asset_id", "threshold", "observed_value",
                 "observed_at", "triggered_at", "channels", "message"]


def _alert_id(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


def evaluate_rules(prices: pd.DataFrame, rules: list[dict], now: datetime | None = None,
                   assets: pd.DataFrame | None = None) -> pd.DataFrame:
    """prices: asset_id, price_date, observed_at, close, volume (daily, deduplicated)."""
    now = now or datetime.now(timezone.utc)
    names = dict(zip(assets["asset_id"], assets["name"])) if assets is not None else {}
    events = []
    universe = sorted(prices["asset_id"].unique())
    for rule in rules:
        targets = universe if rule.get("asset_id", "*") == "*" else [rule["asset_id"]]
        thr = float(rule["threshold"])
        rtype = rule["type"]
        for asset in targets:
            g = prices[prices["asset_id"] == asset].sort_values("price_date")
            if g.empty:
                continue
            label = names.get(asset, asset)
            daily = g.set_index(pd.to_datetime(g["price_date"]))
            close = daily["close"].reindex(pd.date_range(daily.index.min(), daily.index.max(), freq="D"))
            hits: list[tuple[pd.Timestamp, float, str]] = []
            if rtype == "pct_move":
                ret = close / close.shift(1) - 1   # consecutive days only, no gap-spanning moves
                for d, v in ret[ret.abs() >= thr].items():
                    hits.append((d, float(v), f"{label} moved {v:+.1%} in one day (threshold ±{thr:.0%})"))
            elif rtype in ("price_above", "price_below"):
                above = close > thr if rtype == "price_above" else close < thr
                prev = above.shift(1, fill_value=False)
                cross = above & ~prev & close.notna() & close.shift(1).notna()
                word = "above" if rtype == "price_above" else "below"
                for d in cross[cross].index:
                    hits.append((d, float(close[d]), f"{label} closed {word} {thr:,.2f} at {close[d]:,.2f}"))
            elif rtype == "volume_spike":
                vol = daily["volume"].reindex(close.index).astype(float)
                med = vol.rolling(30, min_periods=20).median().shift(1)
                ratio = vol / med
                for d, v in ratio[ratio >= thr].items():
                    hits.append((d, float(v), f"{label} volume {v:.1f}x its 30-day median (threshold {thr:.1f}x)"))
            elif rtype == "stale_data":
                latest = pd.Timestamp(g["observed_at"].max())
                latest = latest.tz_localize("UTC") if latest.tzinfo is None else latest
                age_h = (pd.Timestamp(now) - latest).total_seconds() / 3600
                if age_h > thr:
                    hits.append((latest, age_h, f"{label}: latest observation is {age_h:,.0f}h old (limit {thr:.0f}h)"))
            else:
                raise ValueError(f"unknown rule type {rtype}")
            for d, value, msg in hits:
                obs = pd.Timestamp(d)
                obs = obs.tz_localize("UTC") if obs.tzinfo is None else obs
                events.append([_alert_id(rule["rule_id"], asset, obs.isoformat()), rule["rule_id"],
                               rule.get("name", rule["rule_id"]), rtype, asset, thr, value, obs,
                               pd.Timestamp(now), ",".join(rule.get("channels", ["dashboard"])), msg])
    return pd.DataFrame(events, columns=EVENT_COLUMNS)


def deliver(events: pd.DataFrame, log_path: Path) -> int:
    """'dashboard' delivery = the fact_alert_events table. 'log' appends to a file."""
    to_log = events[events["channels"].str.contains("log", na=False)]
    if to_log.empty:
        return 0
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        for r in to_log.itertuples(index=False):
            f.write(f"{r.triggered_at.isoformat()}\t{r.rule_id}\t{r.asset_id}\t{r.message}\n")
    return len(to_log)
