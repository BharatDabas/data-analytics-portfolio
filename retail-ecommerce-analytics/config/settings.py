"""Project paths and runtime settings. Environment variables override defaults."""
from __future__ import annotations

import os
import tomllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = Path(os.getenv("RETAIL_RAW_DIR", DATA_DIR / "raw"))
PROCESSED_DIR = DATA_DIR / "processed"
SAMPLE_DIR = DATA_DIR / "sample"
DB_PATH = Path(os.getenv("RETAIL_DB_PATH", PROCESSED_DIR / "retail.duckdb"))
SEED = int(os.getenv("RETAIL_SEED", "42"))
DATA_LABEL = "Synthetic demo data"


def as_of_date() -> date:
    """Last complete day of data. Default: yesterday (UTC); override with RETAIL_AS_OF=YYYY-MM-DD."""
    raw = os.getenv("RETAIL_AS_OF")
    if raw:
        return date.fromisoformat(raw)
    return (datetime.now(timezone.utc) - timedelta(days=1)).date()


def load_business_rules() -> dict:
    with open(PROJECT_ROOT / "config" / "business_rules.toml", "rb") as f:
        return tomllib.load(f)
