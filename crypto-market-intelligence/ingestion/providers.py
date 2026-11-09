"""Market-data provider adapters.

Every provider returns payloads in one canonical shape (modelled on CoinGecko's
`/coins/{id}/market_chart` response) so parsing, validation and loading are
provider-independent:

    {"prices": [[ms, price], ...], "market_caps": [[ms, cap], ...],
     "total_volumes": [[ms, vol], ...], "ohlc": [[ms, o, h, l, c], ...] | None}

Adding a provider = implementing `list_assets` and `fetch_history`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Protocol

import numpy as np

from ingestion.http_client import RateLimitedClient, TransientError


@dataclass(frozen=True)
class AssetMeta:
    asset_id: str           # canonical ID (provider ID, prefixed for synthetic assets)
    symbol: str
    name: str
    category: str
    is_stablecoin: bool = False


class MarketDataProvider(Protocol):
    source_id: str
    data_kind: str  # 'synthetic' | 'provider_api'

    def list_assets(self, limit: int) -> list[AssetMeta]: ...

    def fetch_history(self, asset_id: str, days: int, quote: str) -> dict: ...


# --------------------------------------------------------------------------- CoinGecko

class CoinGeckoProvider:
    """Public CoinGecko API v3. Review current terms, rate limits and attribution
    requirements before any commercial use (https://www.coingecko.com/en/api)."""

    source_id = "coingecko"
    data_kind = "provider_api"

    def __init__(self, client: RateLimitedClient, page_size: int = 100):
        self.client = client
        self.page_size = page_size

    def list_assets(self, limit: int = 100) -> list[AssetMeta]:
        """Paginates /coins/markets until `limit` assets or an empty page."""
        out: list[AssetMeta] = []
        page = 1
        while len(out) < limit:
            rows = self.client.get_json("/coins/markets", {
                "vs_currency": "usd", "order": "market_cap_desc",
                "per_page": self.page_size, "page": page})
            if not rows:
                break
            for r in rows:
                out.append(AssetMeta(r["id"], str(r.get("symbol", "")).upper(), r.get("name", r["id"]),
                                     "Unclassified", str(r.get("symbol", "")).lower() in {"usdt", "usdc", "dai"}))
            if len(rows) < self.page_size:
                break
            page += 1
        return out[:limit]

    def fetch_history(self, asset_id: str, days: int, quote: str = "usd") -> dict:
        payload = self.client.get_json(f"/coins/{asset_id}/market_chart",
                                       {"vs_currency": quote, "days": days, "interval": "daily"})
        # Free-tier OHLC granularity for >30 days is 4-day candles, which does not match
        # the daily grain, so OHLC is treated as "not supported" for this provider.
        payload["ohlc"] = None
        return payload


# --------------------------------------------------------------------------- Synthetic

SYNTHETIC_UNIVERSE: list[tuple[AssetMeta, float, float, float]] = [
    # meta, start price, annual vol, beta to the synthetic market factor
    (AssetMeta("demo-aurora", "AUR", "Aurora", "Layer 1"), 38000.0, 0.55, 1.0),
    (AssetMeta("demo-borealis", "BOR", "Borealis", "Smart-contract platform"), 2100.0, 0.70, 1.15),
    (AssetMeta("demo-cobalt", "CBT", "Cobalt Chain", "Layer 1"), 95.0, 0.95, 1.35),
    (AssetMeta("demo-dune", "DUN", "Dune", "Payments"), 0.45, 0.85, 1.1),
    (AssetMeta("demo-ember", "EMB", "Ember", "DeFi"), 12.0, 1.00, 1.4),
    # Two assets share the ticker FLX - the canonical ID keeps them apart.
    (AssetMeta("demo-flux", "FLX", "Flux", "Oracle"), 7.5, 0.85, 1.2),
    (AssetMeta("demo-flux-network", "FLX", "Flux Network", "Infrastructure"), 0.80, 1.10, 1.3),
    (AssetMeta("demo-harbor-usd", "HUSD", "Harbor USD", "Stablecoin", True), 1.0, 0.0, 0.0),
]

# Starting market capitalisation (USD) per universe slot - sets each asset's circulating supply.
SYNTHETIC_START_CAPS = [7.5e11, 2.5e11, 4.0e10, 1.6e10, 6.0e9, 9.0e9, 1.8e9, 3.0e9]

# Deterministic data defects so data-quality controls have something real to catch.
SYNTHETIC_DEFECTS = {
    "missing_days": {"demo-dune": [200, 201, 202], "demo-cobalt": [415]},  # day offsets dropped
    "duplicate_days": {"demo-aurora": [500]},                               # repeated observation
    "stale_tail_days": {"demo-harbor-usd": 2},                              # feed stopped early
    # Simulated provider faults (exercise retry/backoff + failed-run logging)
    "transient_faults": {"demo-ember": 2, "demo-borealis": 1},              # n failures then success
    "hard_fail_first_run": {"demo-flux-network"},                           # fails all retries once
}


class SyntheticProvider:
    """Generates realistic but entirely FICTIONAL market history for fictional
    assets (names prefixed 'demo-'). It never represents real market prices."""

    source_id = "synthetic_demo"
    data_kind = "synthetic"

    def __init__(self, as_of: date, seed: int = 42, history_days: int = 730, outage_calls: int = 6):
        self.as_of = as_of
        self.outage_calls = outage_calls  # calls that fail during the simulated outage
        self.seed = seed
        self.history_days = history_days
        self._calls: dict[str, int] = {}
        self._market = self._market_factor()

    def list_assets(self, limit: int = 100) -> list[AssetMeta]:
        return [m for m, *_ in SYNTHETIC_UNIVERSE][:limit]

    def _market_factor(self) -> np.ndarray:
        rng = np.random.default_rng(self.seed)
        n = self.history_days
        # Regime-switching drift: bull, correction, recovery - gives drawdowns to analyse.
        drift = np.concatenate([np.full(n // 3, 0.0024), np.full(n // 4, -0.0024),
                                np.full(n - n // 3 - n // 4, 0.0022)])
        shocks = rng.standard_t(df=4, size=n) / np.sqrt(2.0) * 0.026
        return drift + shocks

    def _simulate(self, idx: int) -> dict:
        meta, p0, vol, beta = SYNTHETIC_UNIVERSE[idx]
        n = self.history_days
        rng = np.random.default_rng(self.seed * 1000 + idx)
        start = self.as_of - timedelta(days=n - 1)
        if meta.is_stablecoin:
            closes = 1.0 + np.clip(rng.normal(0, 0.0012, n), -0.006, 0.006)
            supply = SYNTHETIC_START_CAPS[idx] * np.cumprod(1 + rng.normal(0.0004, 0.002, n))
        else:
            # total daily variance = (annual vol)^2 / 365 = beta^2 * market var + idiosyncratic var
            idio_sd = max((vol / np.sqrt(365)) ** 2 - (beta * 0.026) ** 2, 1e-6) ** 0.5
            rets = beta * self._market + rng.standard_t(df=5, size=n) / np.sqrt(5 / 3) * idio_sd
            rets = np.clip(rets, -0.45, 0.6)
            closes = p0 * np.cumprod(1 + rets)
            supply = (SYNTHETIC_START_CAPS[idx] / p0) * np.cumprod(1 + np.full(n, 0.00005))
        prev = np.concatenate([[closes[0]], closes[:-1]])
        opens = prev * (1 + rng.normal(0, 0.002, n))
        spread = np.abs(rng.normal(0, 0.018 if not meta.is_stablecoin else 0.001, n))
        highs = np.maximum(opens, closes) * (1 + spread)
        lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, 0.018 if not meta.is_stablecoin else 0.001, n)))
        abs_move = np.abs(closes / prev - 1)
        base_vol = closes * supply * (0.035 if not meta.is_stablecoin else 0.12)
        volume = base_vol * np.exp(rng.normal(0, 0.25, n)) * (1 + 6 * abs_move)
        spike_days = rng.choice(n, size=4, replace=False)
        volume[spike_days] *= rng.uniform(3.5, 6.0, size=4)
        mcap = closes * supply

        drop = set(SYNTHETIC_DEFECTS["missing_days"].get(meta.asset_id, []))
        tail = SYNTHETIC_DEFECTS["stale_tail_days"].get(meta.asset_id, 0)
        dups = set(SYNTHETIC_DEFECTS["duplicate_days"].get(meta.asset_id, []))
        prices, caps, vols, ohlc = [], [], [], []
        for i in range(n - tail):
            if i in drop:
                continue
            day = start + timedelta(days=i)
            # observation taken at end of the UTC day (= 00:00 of the following day)
            ms = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000) + 86_400_000
            reps = 2 if i in dups else 1
            for _ in range(reps):
                prices.append([ms, float(closes[i])])
                caps.append([ms, float(mcap[i])])
                vols.append([ms, float(volume[i])])
                ohlc.append([ms, float(opens[i]), float(highs[i]), float(lows[i]), float(closes[i])])
        return {"prices": prices, "market_caps": caps, "total_volumes": vols, "ohlc": ohlc}

    def fetch_history(self, asset_id: str, days: int, quote: str = "usd") -> dict:
        self._calls[asset_id] = self._calls.get(asset_id, 0) + 1
        n_faults = SYNTHETIC_DEFECTS["transient_faults"].get(asset_id, 0)
        if self._calls[asset_id] <= n_faults:
            raise TransientError("simulated HTTP 429 Too Many Requests", 429, retry_after=0.0)
        if asset_id in SYNTHETIC_DEFECTS["hard_fail_first_run"] and self._calls[asset_id] <= self.outage_calls:
            raise TransientError("simulated HTTP 503 Service Unavailable", 503, retry_after=0.0)
        idx = next(i for i, (m, *_) in enumerate(SYNTHETIC_UNIVERSE) if m.asset_id == asset_id)
        payload = self._simulate(idx)
        cutoff = (self.as_of - timedelta(days=days - 1))
        cutoff_ms = int(datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=timezone.utc).timestamp() * 1000)
        return {k: [row for row in v if row[0] > cutoff_ms] for k, v in payload.items()}
