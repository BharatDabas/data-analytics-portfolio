"""CSV import of user portfolio transactions (template: data/sample/transactions_template.csv)."""
from __future__ import annotations

import io
from datetime import datetime, timezone

import pandas as pd

from analytics.ledger import REQUIRED_COLUMNS, validate_transactions

OPTIONAL_DEFAULTS = {"quote_currency": "usd", "reference": None}


class ImportResult:
    def __init__(self, frame: pd.DataFrame, errors: list[str]):
        self.frame = frame
        self.errors = errors

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_transactions_csv(content: bytes | str, portfolio_id: str | None = None,
                           known_assets: set[str] | None = None, entry_source: str = "csv_import") -> ImportResult:
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    try:
        df = pd.read_csv(io.StringIO(text), dtype={"transaction_id": str, "asset_id": str, "portfolio_id": str})
    except Exception as exc:  # malformed file
        return ImportResult(pd.DataFrame(), [f"could not parse CSV: {exc}"])
    df.columns = [c.strip().lower() for c in df.columns]
    if portfolio_id and "portfolio_id" not in df.columns:
        df["portfolio_id"] = portfolio_id
    for col, default in OPTIONAL_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default
    if "fee" in df.columns:
        df["fee"] = df["fee"].fillna(0)
    errors = validate_transactions(df)
    if not errors and known_assets is not None:
        unknown = sorted(set(df["asset_id"]) - known_assets)
        if unknown:
            errors.append(f"unknown asset_id (use canonical IDs, not tickers): {unknown}")
    if errors:
        return ImportResult(df, errors)
    df["txn_type"] = df["txn_type"].str.upper()
    df["executed_at"] = pd.to_datetime(df["executed_at"], utc=True)
    df["entry_source"] = entry_source
    df["loaded_at"] = datetime.now(timezone.utc)
    return ImportResult(df[REQUIRED_COLUMNS + ["quote_currency", "reference", "entry_source", "loaded_at"]], [])
