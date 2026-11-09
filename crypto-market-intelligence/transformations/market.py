"""Parse provider payloads into the fact_market_price grain and run market-data checks.

Grain: one row per asset, quote currency, interval and observation timestamp.
- `observed_at` is the provider's observation timestamp (UTC).
- `retrieved_at` is when *we* fetched it (UTC) - kept separately for lineage.
- `price_date` is the UTC calendar day the daily observation closes; a point
  stamped 00:00 UTC belongs to the previous day (end-of-day close).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

PRICE_COLUMNS = ["asset_id", "quote_currency", "interval", "price_date", "observed_at", "open", "high", "low",
                 "close", "volume", "market_cap", "source_id", "retrieved_at", "ingestion_run_id"]
GRAIN = ["asset_id", "quote_currency", "interval", "observed_at"]


def parse_market_chart(payload: dict, asset_id: str, source_id: str, quote: str = "usd",
                       retrieved_at: datetime | None = None, run_id: str = "",
                       today: datetime | None = None) -> pd.DataFrame:
    retrieved_at = retrieved_at or datetime.now(timezone.utc)
    today = today or datetime.now(timezone.utc)

    def frame(key: str, cols: list[str]) -> pd.DataFrame:
        rows = payload.get(key) or []
        return pd.DataFrame(rows, columns=["ms", *cols]) if rows else pd.DataFrame(columns=["ms", *cols])

    prices = frame("prices", ["close"])
    if prices.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    # Positional key keeps duplicated provider timestamps aligned across the arrays.
    prices["k"] = prices.groupby("ms").cumcount()
    df = prices
    for key, cols in (("market_caps", ["market_cap"]), ("total_volumes", ["volume"]),
                      ("ohlc", ["open", "high", "low", "ohlc_close"])):
        other = frame(key, cols)
        other["k"] = other.groupby("ms").cumcount() if not other.empty else pd.Series(dtype="int64")
        df = df.merge(other, on=["ms", "k"], how="left")
    df["observed_at"] = pd.to_datetime(df["ms"].astype("int64"), unit="ms", utc=True)
    # Drop intraday "latest" points (not aligned to a day boundary on the current day):
    aligned = (df["observed_at"].dt.hour == 0) & (df["observed_at"].dt.minute == 0)
    df = df[aligned | (df["observed_at"] < pd.Timestamp(today).floor("D"))]
    df["price_date"] = (df["observed_at"] - pd.Timedelta(seconds=1)).dt.date
    df["asset_id"] = asset_id
    df["quote_currency"] = quote.lower()
    df["interval"] = "1d"
    df["source_id"] = source_id
    df["retrieved_at"] = pd.Timestamp(retrieved_at)
    df["ingestion_run_id"] = run_id
    for c in ("open", "high", "low", "volume", "market_cap"):
        if c not in df:
            df[c] = None
    return df[PRICE_COLUMNS].reset_index(drop=True)


def deduplicate(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Idempotency key = asset, quote, interval, timestamp. Keeps the latest retrieval."""
    before = len(df)
    out = (df.sort_values("retrieved_at")
             .drop_duplicates(subset=GRAIN, keep="last")
             .sort_values(["asset_id", "observed_at"])
             .reset_index(drop=True))
    return out, before - len(out)


def detect_missing_intervals(df: pd.DataFrame, expected_end: pd.Timestamp | None = None) -> pd.DataFrame:
    """Find gaps in the daily calendar per asset/quote. Gaps are reported, never filled.

    If `expected_end` (a date) is given, a missing tail up to that date is also reported.
    """
    cols = ["asset_id", "quote_currency", "interval", "gap_start", "gap_end", "missing_points"]
    out = []
    for (asset, quote), g in df.groupby(["asset_id", "quote_currency"]):
        days = pd.to_datetime(pd.Series(sorted(set(g["price_date"]))))
        if days.empty:
            continue
        end = max(days.iloc[-1], pd.Timestamp(expected_end)) if expected_end is not None else days.iloc[-1]
        full = pd.date_range(days.iloc[0], end, freq="D")
        missing = full.difference(pd.DatetimeIndex(days))
        if missing.empty:
            continue
        # collapse consecutive missing days into ranges
        grp = (missing.to_series().diff() != pd.Timedelta(days=1)).cumsum()
        for _, block in missing.to_series().groupby(grp.values):
            out.append([asset, quote, "1d", block.iloc[0].date(), block.iloc[-1].date(), len(block)])
    return pd.DataFrame(out, columns=cols)


def validate_prices(df: pd.DataFrame) -> pd.Series:
    """Row-level validity mask: positive close, high >= low, OHLC inside [low, high]."""
    ok = df["close"].notna() & (df["close"] > 0)
    has_ohlc = df[["open", "high", "low"]].notna().all(axis=1)
    hl = df["high"].astype(float) >= df["low"].astype(float)
    inside = (df["close"].astype(float) <= df["high"].astype(float) * 1.000001) & \
             (df["close"].astype(float) >= df["low"].astype(float) * 0.999999)
    ok &= ~has_ohlc | (hl & inside)
    ok &= df["volume"].isna() | (df["volume"].astype(float) >= 0)
    return ok
