"""Runtime settings: filesystem paths, environment overrides and business parameters."""
from __future__ import annotations

import os
import tomllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = Path(os.getenv("SUPPLY_RAW_DIR", DATA_DIR / "raw"))
PROCESSED_DIR = DATA_DIR / "processed"
SAMPLE_DIR = DATA_DIR / "sample"
DB_PATH = Path(os.getenv("SUPPLY_DB_PATH", PROCESSED_DIR / "supply_chain.duckdb"))
SEED = int(os.getenv("SUPPLY_SEED", "42"))
PIPELINE_VERSION = "1.0.0"


def default_as_of() -> date:
    """Snapshot 'as of' date for the synthetic dataset (yesterday UTC unless overridden)."""
    env = os.getenv("SUPPLY_AS_OF")
    if env:
        return date.fromisoformat(env)
    return (datetime.now(timezone.utc) - timedelta(days=1)).date()


def load_business_config(path: Path | None = None) -> dict:
    path = path or PROJECT_ROOT / "config" / "business.toml"
    with open(path, "rb") as fh:
        return tomllib.load(fh)


BUSINESS = load_business_config()
