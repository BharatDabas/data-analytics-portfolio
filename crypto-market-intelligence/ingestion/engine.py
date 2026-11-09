"""Provider-agnostic ingestion: fetch -> persist raw JSON -> parse -> validate ->
deduplicate -> idempotent upsert -> run log. One run-log row per asset request."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd

from database import db
from ingestion.http_client import CallStats, IngestionError, RetryPolicy, call_with_retry
from ingestion.providers import MarketDataProvider
from transformations.market import deduplicate, parse_market_chart, validate_prices


@dataclass
class BatchSummary:
    batch_id: str
    succeeded: list[str]
    failed: list[str]
    rows_loaded: int
    duplicates_dropped: int


def ingest_assets(con: duckdb.DuckDBPyConnection, provider: MarketDataProvider, asset_ids: list[str],
                  days: int, raw_dir: Path, quote: str = "usd", policy: RetryPolicy | None = None,
                  sleep: Callable[[float], None] = time.sleep, now: datetime | None = None,
                  batch_id: str | None = None) -> BatchSummary:
    policy = policy or RetryPolicy()
    batch_id = batch_id or f"B{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    succeeded, failed, total_rows, total_dups = [], [], 0, 0
    for asset_id in asset_ids:
        run_id = f"{batch_id}-{asset_id}"
        started = datetime.now(timezone.utc)
        stats = CallStats()
        log = {"run_id": run_id, "batch_id": batch_id, "source_id": provider.source_id,
               "endpoint": f"/coins/{asset_id}/market_chart", "asset_id": asset_id, "started_at": started}
        try:
            payload = call_with_retry(lambda: provider.fetch_history(asset_id, days, quote), policy, stats, sleep)
            retrieved = datetime.now(timezone.utc)
            raw_path = raw_dir / provider.source_id / batch_id / f"{asset_id}.json"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(json.dumps({"asset_id": asset_id, "quote": quote, "retrieved_at": retrieved.isoformat(),
                                            "source_id": provider.source_id, "payload": payload}))
            df = parse_market_chart(payload, asset_id, provider.source_id, quote, retrieved, run_id, today=now)
            received = len(df)
            valid = validate_prices(df) if received else pd.Series(dtype=bool)
            invalid = int((~valid).sum()) if received else 0
            df = df[valid] if received else df
            df, dups = deduplicate(df)
            loaded = db.upsert(con, "fact_market_price", df, ["asset_id", "quote_currency", "interval", "observed_at"])
            log.update(status="success", http_status=stats.last_status, attempts=stats.attempts,
                       rows_received=received, rows_loaded=loaded, duplicates_dropped=dups,
                       invalid_rows=invalid, raw_path=str(raw_path.as_posix()), error_message=None)
            succeeded.append(asset_id)
            total_rows += loaded
            total_dups += dups
        except IngestionError as exc:
            log.update(status="failed", http_status=exc.status, attempts=stats.attempts or exc.attempts,
                       rows_received=0, rows_loaded=0, duplicates_dropped=0, invalid_rows=0, raw_path=None,
                       error_message=str(exc))
            failed.append(asset_id)
        log["finished_at"] = datetime.now(timezone.utc)
        db.append(con, "ops_ingestion_runs", pd.DataFrame([log]))
    if succeeded:
        con.execute("UPDATE dim_source SET last_success_at = ?, status = ? WHERE source_id = ?",
                    [datetime.now(timezone.utc), "degraded" if failed else "active", provider.source_id])
    return BatchSummary(batch_id, succeeded, failed, total_rows, total_dups)
