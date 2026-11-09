"""Raw extracts -> validated star schema in DuckDB.

Steps: contract validation & cleansing -> referential/business-rule validation
(rejects logged) -> currency standardisation -> fact/dim derivation ->
attribution bridge -> load -> data-quality checks and reconciliation.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from database import db
from ingestion.sources import SourceAdapter
from transformations import quality as q
from transformations.cleanse import CONTRACTS, PRIMARY_KEYS, reject, standardise, to_gbp

REGION = {"United Kingdom": "UK & Ireland", "Ireland": "UK & Ireland", "Germany": "Europe", "France": "Europe",
          "Netherlands": "Europe", "United States": "North America"}


def build_dim_date(start: date, end: date) -> pd.DataFrame:
    d = pd.date_range(start, end, freq="D")
    fy = np.where(d.month >= 4, d.year + 1, d.year)
    return pd.DataFrame({
        "date_key": d.date, "year": d.year, "quarter": d.quarter, "month": d.month,
        "month_name": d.strftime("%b"), "month_start": d.to_period("M").start_time.date,
        "week_start": (d - pd.to_timedelta(d.dayofweek, unit="D")).date, "iso_week": d.isocalendar().week.to_numpy(),
        "day_of_week": d.dayofweek + 1, "is_weekend": d.dayofweek >= 5,
        "fiscal_year": fy, "fiscal_quarter": ((d.month - 4) % 12) // 3 + 1,
    })


def attribute_orders(orders: pd.DataFrame, campaigns: pd.DataFrame, window_days: int) -> pd.DataFrame:
    """Last-click attribution bridge: an order is credited (weight 1.0) to the campaign of its last
    click only when that click happened 0..window_days before the order and the campaign exists.
    Cancelled orders are never attributed."""
    o = orders[(~orders["is_cancelled"]) & orders["last_touch_campaign_id"].notna() & orders["last_touch_ts"].notna()]
    o = o.merge(campaigns[["campaign_id", "channel"]].rename(columns={"channel": "campaign_channel"}),
                left_on="last_touch_campaign_id", right_on="campaign_id", how="inner")
    lag_days = (o["order_ts"] - o["last_touch_ts"]).dt.total_seconds() / 86400
    o = o[(lag_days >= 0) & (lag_days <= window_days)].assign(days_touch_to_order=lag_days)
    return pd.DataFrame({
        "order_id": o["order_id"], "campaign_id": o["campaign_id"], "channel": o["campaign_channel"],
        "attribution_model": "last_click", "window_days": window_days, "touch_ts": o["last_touch_ts"],
        "order_date": o["order_date"], "days_touch_to_order": o["days_touch_to_order"].round(3),
        "weight": 1.0, "is_new_customer": o["is_new_customer_order"],
    }).reset_index(drop=True)


def transform(raw: dict[str, pd.DataFrame], rules: dict) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    """Pure transformation. Returns (tables, rejects, file_stats)."""
    fx = rules["currency"]["fx_to_gbp"]
    clean, rejects, stats = {}, [], []
    for entity in CONTRACTS:
        df, rej, dups = standardise(raw[entity], entity)
        clean[entity] = df
        rejects.append(rej)
        stats.append({"entity": entity, "rows_read": len(raw[entity]), "duplicates_removed": dups})

    def drop(entity: str, mask: pd.Series, reason: str) -> None:
        df = clean[entity]
        rejects.append(reject(df, mask, entity, PRIMARY_KEYS[entity], reason))
        clean[entity] = df[~mask].reset_index(drop=True)

    # ---- referential & business-rule validation ----
    products, customers, campaigns = clean["products"], clean["customers"], clean["campaigns"]
    o = clean["orders"]
    drop("orders", ~o["customer_id"].isin(customers["customer_id"]), "customer_id not found in customers")
    drop("orders", ~clean["orders"]["status"].isin(["completed", "cancelled"]), "unknown order status")
    drop("orders", ~clean["orders"]["currency"].isin(list(fx)), "currency without configured FX rate")
    it = clean["order_items"]
    drop("order_items", ~it["order_id"].isin(clean["orders"]["order_id"]), "order_id not found in orders (orphan line)")
    it = clean["order_items"]
    drop("order_items", ~it["product_id"].isin(products["product_id"]), "product_id not found in products")
    it = clean["order_items"]
    drop("order_items", it["quantity"] <= 0, "quantity must be positive")
    it = clean["order_items"]
    drop("order_items", (it["unit_price"] < 0) | (it["discount_amount"] < 0)
         | (it["discount_amount"] > it["unit_price"] * it["quantity"]), "invalid price or discount")
    r = clean["returns"]
    drop("returns", ~r["order_item_id"].isin(clean["order_items"]["order_item_id"]), "order_item_id not found")
    r = clean["returns"].merge(clean["order_items"][["order_item_id", "quantity"]], on="order_item_id",
                               suffixes=("", "_sold")).sort_values(["order_item_id", "return_date", "return_id"])
    over = r.groupby("order_item_id")["quantity"].cumsum() > r["quantity_sold"]
    drop("returns", clean["returns"]["return_id"].isin(r.loc[over, "return_id"]), "returned quantity exceeds quantity sold")
    sp = clean["marketing_spend"]
    drop("marketing_spend", ~sp["campaign_id"].isin(campaigns["campaign_id"]), "campaign_id not found")
    drop("marketing_spend", clean["marketing_spend"]["spend"] < 0, "negative spend")

    # ---- dimensions ----
    dim_product = products.assign(
        sku=products["sku"].str.upper(),
        has_reliable_cost=products["unit_cost_gbp"].notna() & (products["unit_cost_gbp"] > 0),
    )
    dim_product.loc[~dim_product["has_reliable_cost"], "unit_cost_gbp"] = np.nan

    # ---- order lines ----
    o = clean["orders"].copy()
    o["fx_rate_to_gbp"] = o["currency"].map(fx).astype(float)
    it = clean["order_items"].merge(o[["order_id", "order_ts", "currency"]], on="order_id", suffixes=("_line", ""))
    it["unit_price_gbp"] = to_gbp(it["unit_price"], it["currency"], fx)
    it["gross_sales_gbp"] = (it["quantity"] * it["unit_price_gbp"]).round(4)
    it["discount_gbp"] = to_gbp(it["discount_amount"], it["currency"], fx)
    it["net_sales_gbp"] = it["gross_sales_gbp"] - it["discount_gbp"]
    it["is_discounted"] = it["discount_gbp"] > 0
    it["order_date"] = it["order_ts"].dt.date
    it = it.merge(dim_product[["product_id", "unit_cost_gbp"]], on="product_id", how="left")

    ret = clean["returns"].merge(it[["order_item_id", "order_id", "product_id", "currency"]], on="order_item_id",
                                 suffixes=("_ret", ""))
    ret["refund_gbp"] = to_gbp(ret["refund_amount"], ret["currency"], fx)
    fact_returns = ret[["return_id", "order_item_id", "order_id", "product_id", "return_date", "quantity", "reason",
                        "refund_gbp"]]
    per_line = fact_returns.groupby("order_item_id").agg(returned_quantity=("quantity", "sum"),
                                                         refund_gbp=("refund_gbp", "sum"))
    it = it.merge(per_line, left_on="order_item_id", right_index=True, how="left")
    it["returned_quantity"] = it["returned_quantity"].fillna(0).astype(int)
    it["refund_gbp"] = it["refund_gbp"].fillna(0.0)
    fact_items = it[["order_item_id", "order_id", "product_id", "order_date", "quantity", "unit_price_gbp",
                     "gross_sales_gbp", "discount_gbp", "net_sales_gbp", "is_discounted", "unit_cost_gbp",
                     "returned_quantity", "refund_gbp"]]

    # ---- orders ----
    line_tot = fact_items.groupby("order_id").agg(items_gross_gbp=("gross_sales_gbp", "sum"),
                                                  items_discount_gbp=("discount_gbp", "sum"),
                                                  items_net_gbp=("net_sales_gbp", "sum"), units=("quantity", "sum"))
    o = o.merge(line_tot, left_on="order_id", right_index=True, how="left")
    for c in ["items_gross_gbp", "items_discount_gbp", "items_net_gbp"]:
        o[c] = o[c].fillna(0.0)
    o["units"] = o["units"].fillna(0).astype(int)
    o["shipping_gbp"] = to_gbp(o["shipping_amount"], o["currency"], fx)
    o["tax_gbp"] = to_gbp(o["tax_amount"], o["currency"], fx)
    o["header_total_gbp"] = to_gbp(o["order_total"], o["currency"], fx)
    o["reconciles_to_items"] = (o["header_total_gbp"] - (o["items_net_gbp"] + o["shipping_gbp"] + o["tax_gbp"])).abs() \
        <= 0.02 + 0.0001 * o["header_total_gbp"].abs()
    o["order_date"] = o["order_ts"].dt.date
    o["is_cancelled"] = o["status"] == "cancelled"
    o["region"] = o["country"].map(REGION).fillna("Other")
    o = o.sort_values(["customer_id", "order_ts", "order_id"])
    eligible = ~o["is_cancelled"]
    o["customer_order_seq"] = pd.NA
    o.loc[eligible, "customer_order_seq"] = o[eligible].groupby("customer_id").cumcount() + 1
    o["customer_order_seq"] = o["customer_order_seq"].astype("Int64")
    o["is_new_customer_order"] = o["customer_order_seq"] == 1
    o["is_new_customer_order"] = o["is_new_customer_order"].fillna(False).astype(bool)
    fact_orders = o.rename(columns={"currency": "currency_local"})[[
        "order_id", "customer_id", "order_ts", "order_date", "channel", "status", "is_cancelled", "country", "region",
        "currency_local", "fx_rate_to_gbp", "shipping_gbp", "tax_gbp", "header_total_gbp", "items_gross_gbp",
        "items_discount_gbp", "items_net_gbp", "units", "reconciles_to_items", "customer_order_seq",
        "is_new_customer_order", "last_touch_campaign_id", "last_touch_ts", "shipped_ts", "delivered_ts",
        "promised_delivery_date"]].reset_index(drop=True)

    # ---- customers ----
    first = fact_orders[~fact_orders["is_cancelled"]].groupby("customer_id")["order_date"].min().rename("first_order_date")
    dim_customer = customers.merge(first, left_on="customer_id", right_index=True, how="left")
    dim_customer["region"] = dim_customer["country"].map(REGION).fillna("Other")
    dim_customer["cohort_month"] = pd.to_datetime(dim_customer["first_order_date"]).dt.to_period("M").dt.start_time.dt.date

    # ---- marketing ----
    sp = clean["marketing_spend"].merge(campaigns[["campaign_id", "channel"]], on="campaign_id")
    fact_spend = pd.DataFrame({
        "spend_date": sp["date"], "campaign_id": sp["campaign_id"], "channel": sp["channel"],
        "spend_gbp": to_gbp(sp["spend"], sp["currency"], fx), "currency_local": sp["currency"],
        "impressions": sp["impressions"].astype("int64"), "clicks": sp["clicks"].astype("int64"),
    })
    bridge = attribute_orders(fact_orders, campaigns, int(rules["attribution"]["window_days"]))

    inv = clean["inventory_snapshots"]
    inv = inv[inv["product_id"].isin(products["product_id"])]
    fact_inventory = inv.assign(available=(inv["on_hand"] - inv["reserved"]).clip(lower=0))

    all_dates = pd.concat([pd.Series(fact_orders["order_date"]), pd.Series(fact_spend["spend_date"]),
                           pd.Series(fact_returns["return_date"])])
    tables = {
        "dim_date": build_dim_date(min(all_dates), max(all_dates)),
        "dim_customer": dim_customer, "dim_product": dim_product, "dim_campaign": campaigns,
        "fact_orders": fact_orders, "fact_order_items": fact_items, "fact_returns": fact_returns,
        "fact_marketing_spend": fact_spend, "bridge_order_attribution": bridge, "fact_inventory": fact_inventory,
    }
    rej = pd.concat([r for r in rejects if not r.empty], ignore_index=True) if any(not r.empty for r in rejects) \
        else pd.DataFrame(columns=["entity", "record_key", "reason", "raw_record"])
    st = pd.DataFrame(stats)
    st["rows_rejected"] = st["entity"].map(rej.groupby("entity").size()).fillna(0).astype(int)
    st["rows_loaded"] = st["entity"].map({k: len(v) for k, v in clean.items()})
    return tables, rej, st


def quality_checks(t: dict[str, pd.DataFrame], rules: dict, now: datetime | None = None) -> list[q.CheckResult]:
    tol = rules["quality"]["reconciliation_tolerance"]
    o, it, r, sp, br = t["fact_orders"], t["fact_order_items"], t["fact_returns"], t["fact_marketing_spend"], \
        t["bridge_order_attribution"]
    res = [
        q.check_unique(o, ["order_id"], "fact_orders"),
        q.check_unique(it, ["order_item_id"], "fact_order_items"),
        q.check_unique(r, ["return_id"], "fact_returns"),
        q.check_unique(sp, ["spend_date", "campaign_id"], "fact_marketing_spend"),
        q.check_unique(br, ["order_id"], "bridge_order_attribution"),
        q.check_not_null(o, ["order_id", "customer_id", "order_ts"], "fact_orders"),
        q.check_referential(o, "customer_id", t["dim_customer"]["customer_id"], "fact_orders", "dim_customer"),
        q.check_referential(it, "product_id", t["dim_product"]["product_id"], "fact_order_items", "dim_product"),
        q.check_referential(it, "order_id", o["order_id"], "fact_order_items", "fact_orders"),
        q.check_referential(sp, "campaign_id", t["dim_campaign"]["campaign_id"], "fact_marketing_spend", "dim_campaign"),
        q.check_range(it, "quantity", "fact_order_items", min_value=1),
        q.check_range(it.assign(over=it["returned_quantity"] - it["quantity"]), "over", "fact_order_items", max_value=0),
        q.check_range(it, "discount_gbp", "fact_order_items", min_value=0),
        q.check_reconciliation(it["refund_gbp"].sum(), r["refund_gbp"].sum(), "line_refunds=return_events", "fact_returns", tol),
        q.check_reconciliation(o["items_net_gbp"].sum(), it["net_sales_gbp"].sum(), "order_net=line_net", "fact_orders", tol),
    ]
    unrec = int((~o["reconciles_to_items"]).sum())
    res.append(q.CheckResult("reconcile:order_header_vs_lines", "fact_orders", q.WARN if unrec else q.PASS, unrec,
                             f"{unrec} orders whose header total differs from loaded lines + shipping + tax"
                             if unrec else "all order headers reconcile to their lines"))
    no_cost = int(it["unit_cost_gbp"].isna().sum())
    res.append(q.CheckResult("coverage:product_cost", "fact_order_items", q.WARN if no_cost else q.PASS, no_cost,
                             f"{no_cost} order lines ({no_cost / max(len(it), 1):.1%}) have no reliable product cost "
                             "and are excluded from gross profit"))
    res.append(q.check_freshness(o["order_ts"].max(), rules["quality"]["freshness_max_age_hours"], "fact_orders", now=now))
    return res


def load(con, tables: dict[str, pd.DataFrame], rejects: pd.DataFrame, stats: pd.DataFrame,
         dq: list[q.CheckResult], run_id: str) -> int:
    db.run_sql_file(con, "schema.sql")
    rows = 0
    for name, df in tables.items():
        rows += db.replace_table(con, name, df)
    now = pd.Timestamp.now(tz="UTC")
    if not rejects.empty:
        db.append(con, "ops_load_failures", rejects.assign(run_id=run_id, logged_at=now))
    max_ts = {"orders": tables["fact_orders"]["order_ts"].max(),
              "marketing_spend": pd.Timestamp(tables["fact_marketing_spend"]["spend_date"].max(), tz="UTC"),
              "returns": pd.Timestamp(tables["fact_returns"]["return_date"].max(), tz="UTC")}
    db.append(con, "ops_source_files", stats.assign(run_id=run_id, loaded_at=now,
                                                    max_record_ts=stats["entity"].map(max_ts)))
    db.append(con, "ops_dq_results", q.results_frame(dq, run_id))
    db.run_sql_file(con, "views.sql")
    return rows


def run(con, source: SourceAdapter, rules: dict, run_id: str, as_of: date) -> dict:
    """Full refresh with run logging. Failures are logged before re-raising."""
    started = datetime.now(timezone.utc)
    db.run_sql_file(con, "schema.sql")
    try:
        raw = {e: source.read(e) for e in CONTRACTS}
        tables, rejects, stats = transform(raw, rules)
        dq = quality_checks(tables, rules)
        rows = load(con, tables, rejects, stats, dq, run_id)
        failed = sum(r.status == q.FAIL for r in dq)
        status = "succeeded_with_dq_failures" if failed else "succeeded"
        msg = f"{rows:,} rows loaded; {len(rejects):,} records rejected; {failed} failing DQ checks"
    except Exception as exc:
        db.append(con, "ops_pipeline_runs", pd.DataFrame([{
            "run_id": run_id, "started_at": started, "finished_at": datetime.now(timezone.utc), "status": "failed",
            "data_as_of": as_of, "source": source.name, "rows_loaded": 0, "message": f"{type(exc).__name__}: {exc}"}]))
        raise
    db.append(con, "ops_pipeline_runs", pd.DataFrame([{
        "run_id": run_id, "started_at": started, "finished_at": datetime.now(timezone.utc), "status": status,
        "data_as_of": as_of, "source": source.name, "rows_loaded": rows, "message": msg}]))
    return {"run_id": run_id, "status": status, "rows_loaded": rows, "rejected": len(rejects), "message": msg}
