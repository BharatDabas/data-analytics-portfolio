"""Conformed dimension builders."""
from __future__ import annotations

from datetime import date

import pandas as pd

from ingestion.providers import AssetMeta


def build_dim_date(start: date, end: date) -> pd.DataFrame:
    d = pd.date_range(start, end, freq="D")
    return pd.DataFrame({
        "date_key": d.date, "year": d.year, "quarter": d.quarter, "month": d.month,
        "month_name": d.strftime("%b"), "year_month": d.strftime("%Y-%m"), "iso_week": d.isocalendar().week.values,
        "day_of_week": d.dayofweek + 1, "day_name": d.strftime("%a"), "is_weekend": d.dayofweek >= 5,
        "is_month_end": d.is_month_end})


def build_dim_asset(assets: list[AssetMeta], source_id: str) -> pd.DataFrame:
    return pd.DataFrame([{"asset_id": a.asset_id, "symbol": a.symbol, "name": a.name, "category": a.category,
                          "is_stablecoin": a.is_stablecoin, "source_id": source_id,
                          "provider_asset_id": a.asset_id} for a in assets])


SOURCES = pd.DataFrame([
    {"source_id": "synthetic_demo", "provider": "Synthetic generator (this repo)",
     "endpoint": "ingestion.providers.SyntheticProvider", "data_kind": "synthetic",
     "description": "Fictional assets and simulated prices for offline demonstration. NOT real market data.",
     "attribution": "Generated data - no third-party licence", "status": "inactive", "last_success_at": None},
    {"source_id": "coingecko", "provider": "CoinGecko public API v3", "endpoint": "https://api.coingecko.com/api/v3",
     "data_kind": "provider_api", "description": "Aggregated market data (API estimates across exchanges).",
     "attribution": "Data provided by CoinGecko - review terms before commercial use", "status": "inactive",
     "last_success_at": None},
    {"source_id": "user_ledger", "provider": "User-entered transactions", "endpoint": "manual entry / CSV import",
     "data_kind": "user_entered", "description": "Portfolio transactions entered or imported by the user.",
     "attribution": None, "status": "active", "last_success_at": None},
])
