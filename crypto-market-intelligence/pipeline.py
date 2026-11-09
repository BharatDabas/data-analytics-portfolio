"""End-to-end pipeline: ingest -> validate -> transform -> model -> alerts -> data quality.

Usage:
    python pipeline.py                       # synthetic offline demo (default)
    python pipeline.py --rebuild             # delete and rebuild the DuckDB file
    python pipeline.py --source coingecko --assets bitcoin,ethereum --days 365
"""
from __future__ import annotations

import argparse
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pandas as pd

from analytics.alerts import deliver, evaluate_rules
from analytics.ledger import positions_frame, run_ledger
from analytics.portfolio import build_snapshots
from config import settings
from database import db
from ingestion.demo_ledger import generate_demo_ledger
from ingestion.engine import ingest_assets
from ingestion.http_client import RateLimitedClient, RetryPolicy
from ingestion.providers import AssetMeta, CoinGeckoProvider, SyntheticProvider
from transformations import quality as q
from transformations.dimensions import SOURCES, build_dim_asset, build_dim_date
from transformations.market import detect_missing_intervals

CODE_VERSION = "1.0.0"


def init_db(con: duckdb.DuckDBPyConnection) -> None:
    db.run_sql_file(con, "schema.sql")
    existing = set(db.query(con, "SELECT source_id FROM dim_source")["source_id"])
    db.append(con, "dim_source", SOURCES[~SOURCES["source_id"].isin(existing)])
    db.run_sql_file(con, "views.sql")


def market_prices(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return db.query(con, """SELECT asset_id, price_date, observed_at, close, volume
                            FROM fact_market_price WHERE interval = '1d' ORDER BY asset_id, price_date""")


def refresh_portfolio(con: duckdb.DuckDBPyConnection, end_date: date, max_stale_days: int) -> dict:
    """Recompute FIFO lots, disposals and daily snapshots from fact_transactions."""
    tx = db.query(con, "SELECT * FROM fact_transactions")
    snaps, disposals = build_snapshots(tx, market_prices(con), pd.Timestamp(end_date), max_stale_days)
    db.replace_table(con, "fact_portfolio_snapshot", snaps)
    db.replace_table(con, "fact_disposals", disposals)
    return {"snapshots": len(snaps), "disposals": len(disposals)}


def refresh_alerts(con: duckdb.DuckDBPyConnection, rules: list[dict], now: datetime) -> int:
    events = evaluate_rules(market_prices(con), rules, now, db.query(con, "SELECT asset_id, name FROM dim_asset"))
    db.replace_table(con, "fact_alert_events", events)
    deliver(events[events["rule_type"] == "stale_data"], settings.LOG_DIR / "alerts.log")
    return len(events)


def run_quality_checks(con: duckdb.DuckDBPyConnection, run_id: str, now: datetime, max_age_hours: float) -> pd.DataFrame:
    prices = db.query(con, "SELECT * FROM fact_market_price")
    assets = db.query(con, "SELECT * FROM dim_asset")
    tx = db.query(con, "SELECT * FROM fact_transactions")
    checks = [
        q.check_unique(prices, ["asset_id", "quote_currency", "interval", "observed_at"], "fact_market_price"),
        q.check_not_null(prices, ["asset_id", "observed_at", "close", "retrieved_at"], "fact_market_price"),
        q.check_range(prices, "close", "fact_market_price", min_value=1e-12),
        q.check_referential(prices, "asset_id", assets["asset_id"], "fact_market_price", "dim_asset"),
        q.check_unique(assets, ["asset_id"], "dim_asset"),
        q.check_unique(tx, ["transaction_id"], "fact_transactions"),
        q.check_referential(tx, "asset_id", assets["asset_id"], "fact_transactions", "dim_asset"),
    ]
    for src, latest in db.query(con, "SELECT source_id, MAX(observed_at) AS latest FROM fact_market_price GROUP BY 1").itertuples(index=False):
        r = q.check_freshness(latest, max_age_hours, "fact_market_price", now)
        r.check = f"freshness:{src}"
        checks.append(r)
    for asset, latest in db.query(con, "SELECT asset_id, MAX(observed_at) FROM fact_market_price GROUP BY 1").itertuples(index=False):
        r = q.check_freshness(latest, max_age_hours, "fact_market_price", now)
        if r.status != q.PASS:
            r.check = f"freshness:{asset}"
            checks.append(r)
    gaps = db.query(con, "SELECT COALESCE(SUM(missing_points), 0) AS n FROM ops_missing_intervals")["n"].iloc[0]
    checks.append(q.CheckResult("missing_intervals", "fact_market_price", q.WARN if gaps else q.PASS, int(gaps),
                                f"{int(gaps)} missing daily observations (reported, not filled)"))
    dups = db.query(con, "SELECT COALESCE(SUM(duplicates_dropped),0) AS n FROM ops_ingestion_runs")["n"].iloc[0]
    checks.append(q.CheckResult("duplicates_detected", "ops_ingestion_runs", q.WARN if dups else q.PASS, int(dups),
                                f"{int(dups)} duplicate provider observations removed during ingestion"))
    failed = db.query(con, """SELECT COUNT(*) AS n FROM ops_ingestion_runs r WHERE status = 'failed' AND NOT EXISTS
                              (SELECT 1 FROM ops_ingestion_runs s WHERE s.asset_id = r.asset_id AND s.status = 'success'
                               AND s.started_at > r.started_at)""")["n"].iloc[0]
    checks.append(q.CheckResult("unrecovered_failed_runs", "ops_ingestion_runs", q.FAIL if failed else q.PASS, int(failed),
                                f"{int(failed)} failed ingestion runs without a later successful retry"))
    if not tx.empty:
        ledger = positions_frame(run_ledger(tx))
        hold = db.query(con, "SELECT COALESCE(SUM(units_held),0) AS u FROM v_current_holdings")["u"].iloc[0]
        checks.append(q.check_reconciliation(float(ledger["units"].sum()), float(hold), "ledger_units_vs_snapshot",
                                             "fact_portfolio_snapshot", tolerance=1e-9))
        disp = db.query(con, "SELECT COALESCE(SUM(realised_pnl),0) AS p FROM fact_disposals")["p"].iloc[0]
        checks.append(q.check_reconciliation(float(ledger["realised_pnl"].sum()), float(disp),
                                             "ledger_realised_vs_disposals", "fact_disposals", tolerance=1e-9))
    stale = db.query(con, "SELECT COUNT(*) AS n FROM v_current_holdings WHERE price_is_stale")["n"].iloc[0]
    checks.append(q.CheckResult("stale_valuation", "fact_portfolio_snapshot", q.WARN if stale else q.PASS, int(stale),
                                f"{int(stale)} current holdings valued with the documented stale-price fallback"))
    frame = q.results_frame(checks, run_id)
    con.execute("DELETE FROM ops_dq_results")
    db.append(con, "ops_dq_results", frame)
    return frame


def build(db_path: Path | None = None, as_of: date | None = None, seed: int | None = None,
          source: str = "synthetic", assets: list[str] | None = None, days: int | None = None,
          rebuild: bool = False, now: datetime | None = None, raw_dir: Path | None = None) -> dict:
    cfg = settings.load_settings()
    db_path = Path(db_path or settings.db_path())
    as_of = as_of or settings.as_of_date()
    seed = settings.seed() if seed is None else seed
    now = now or datetime.now(timezone.utc)
    raw_dir = raw_dir or settings.RAW_DIR
    days = days or cfg["general"]["history_days"]
    if rebuild and db_path.exists():
        db_path.unlink()
    con = db.connect(db_path)
    run_id = f"P{now:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    started = datetime.now(timezone.utc)
    try:
        init_db(con)
        pc = cfg["provider"]["coingecko"]
        retry = RetryPolicy(pc["max_retries"], pc["backoff_base_seconds"])
        if source == "synthetic":
            provider = SyntheticProvider(as_of, seed, days, outage_calls=retry.max_retries + 1)
            metas = provider.list_assets()
            sleep = lambda s: None  # simulated faults carry Retry-After: 0
        elif source == "coingecko":
            key = settings.coingecko_api_key()
            client = RateLimitedClient(pc["pro_base_url"] if key else pc["base_url"], pc["requests_per_minute"],
                                       pc["timeout_seconds"], headers={"x-cg-demo-api-key": key} if key else None)
            provider = CoinGeckoProvider(client, pc["page_size"])
            wanted = assets or ["bitcoin", "ethereum", "solana"]
            listed = {m.asset_id: m for m in provider.list_assets(limit=250)}
            metas = [listed.get(a, AssetMeta(a, a.upper()[:6], a.replace("-", " ").title(), "Unclassified"))
                     for a in wanted]
            sleep = None
        else:
            raise ValueError(f"unknown source {source}")
        db.upsert(con, "dim_asset", build_dim_asset(metas, provider.source_id), ["asset_id"])
        start = as_of - timedelta(days=days + 1)
        existing = db.query(con, "SELECT MIN(date_key) AS lo, MAX(date_key) AS hi FROM dim_date")
        if pd.notna(existing["lo"].iloc[0]):  # keep the calendar covering every source already loaded
            start = min(start, pd.Timestamp(existing["lo"].iloc[0]).date())
        end = max(as_of + timedelta(days=1), pd.Timestamp(existing["hi"].iloc[0]).date())             if pd.notna(existing["hi"].iloc[0]) else as_of + timedelta(days=1)
        db.replace_table(con, "dim_date", build_dim_date(start, end))

        kwargs = {"sleep": sleep} if sleep else {}
        ids = [m.asset_id for m in metas]
        first = ingest_assets(con, provider, ids, days, raw_dir, policy=retry, now=now, **kwargs)
        recovered = None
        if first.failed:  # recovery pass: re-queue failed assets once in a new batch
            recovered = ingest_assets(con, provider, first.failed, days, raw_dir, policy=retry, now=now, **kwargs)

        prices = db.query(con, "SELECT asset_id, quote_currency, price_date FROM fact_market_price WHERE source_id = ?",
                          [provider.source_id])
        gaps = detect_missing_intervals(prices, expected_end=pd.Timestamp(as_of))
        gaps["detected_at"] = now
        con.execute("DELETE FROM ops_missing_intervals WHERE asset_id IN (SELECT asset_id FROM dim_asset WHERE source_id = ?)",
                    [provider.source_id])
        db.append(con, "ops_missing_intervals", gaps)

        if source == "synthetic":
            demo = generate_demo_ledger(db.query(con, "SELECT asset_id, price_date, close FROM fact_market_price "
                                                      "WHERE source_id = 'synthetic_demo'"), seed)
            demo["loaded_at"] = now
            con.execute("DELETE FROM fact_transactions WHERE entry_source = 'demo_ledger'")
            db.append(con, "fact_transactions", demo)

        port = refresh_portfolio(con, as_of, cfg["valuation"]["max_price_staleness_days"])
        n_alerts = refresh_alerts(con, settings.load_alert_rules(), now)
        dq = run_quality_checks(con, run_id, now, cfg["freshness"]["max_age_hours"])
        loaded = first.rows_loaded + (recovered.rows_loaded if recovered else 0)
        msg = (f"{len(first.succeeded)}/{len(ids)} assets first pass; recovered {len(recovered.succeeded) if recovered else 0}; "
               f"{loaded} price rows; {port['snapshots']} snapshots; {n_alerts} alerts; "
               f"DQ fail={int((dq['status'] == 'fail').sum())} warn={int((dq['status'] == 'warn').sum())}")
        db.append(con, "ops_pipeline_runs", pd.DataFrame([{
            "run_id": run_id, "pipeline": f"market+portfolio ({source})", "started_at": started,
            "finished_at": datetime.now(timezone.utc), "status": "success", "rows_loaded": loaded, "message": msg,
            "code_version": CODE_VERSION}]))
        return {"run_id": run_id, "db_path": str(db_path), "message": msg, "dq": dq}
    except Exception as exc:
        db.append(con, "ops_pipeline_runs", pd.DataFrame([{
            "run_id": run_id, "pipeline": f"market+portfolio ({source})", "started_at": started,
            "finished_at": datetime.now(timezone.utc), "status": "failed", "rows_loaded": 0,
            "message": str(exc)[:500], "code_version": CODE_VERSION}]))
        raise
    finally:
        con.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["synthetic", "coingecko"], default="synthetic")
    ap.add_argument("--assets", help="comma-separated canonical provider IDs (coingecko only)")
    ap.add_argument("--days", type=int)
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    out = build(source=a.source, assets=a.assets.split(",") if a.assets else None, days=a.days, rebuild=a.rebuild)
    print(f"[{out['run_id']}] {out['message']}\nDatabase: {out['db_path']}")
    print(out["dq"][["check_name", "status", "detail"]].to_string(index=False))


if __name__ == "__main__":
    main()
