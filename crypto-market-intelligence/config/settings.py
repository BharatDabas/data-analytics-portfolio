"""Central configuration. Business parameters live in config/settings.toml;
paths and secrets come from environment variables (see ../.env.example)."""
from __future__ import annotations

import os
import tomllib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
SAMPLE_DIR = DATA_DIR / "sample"
LOG_DIR = PROJECT_ROOT / "logs"


def db_path() -> Path:
    return Path(os.environ.get("CRYPTO_DB_PATH", PROCESSED_DIR / "crypto.duckdb"))


def seed() -> int:
    return int(os.environ.get("CRYPTO_SEED", "42"))


def as_of_date() -> date:
    """Last complete UTC day of the synthetic demo history (overridable for tests)."""
    v = os.environ.get("CRYPTO_AS_OF")
    if v:
        return date.fromisoformat(v)
    return (datetime.now(timezone.utc) - timedelta(days=1)).date()


def coingecko_api_key() -> str | None:
    # Optional demo/pro key. Never commit it - set it in .env or the shell.
    return os.environ.get("COINGECKO_API_KEY") or None


def load_settings() -> dict:
    with open(CONFIG_DIR / "settings.toml", "rb") as f:
        return tomllib.load(f)


def load_alert_rules() -> list[dict]:
    with open(CONFIG_DIR / "alert_rules.toml", "rb") as f:
        return tomllib.load(f)["rule"]
