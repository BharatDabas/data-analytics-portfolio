"""End-to-end pipeline: extract -> validate -> cleanse/standardise -> load -> model -> analytics marts.

Bronze  = raw CSV extracts (data/raw)
Silver  = cleansed, standardised facts and dimensions (DuckDB tables)
Gold    = analytical views (database/views.sql) and marts (classification, forecast, replenishment, ageing)
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from analytics import classification, forecasting, inventory, planning
from config.settings import BUSINESS, PIPELINE_VERSION
from database import db
from ingestion.sources import SOURCE_SCHEMAS, CsvSource, SourceAdapter
from ingestion.synthetic import generate
from transformations import quality as q
from transformations.standardise import dedupe, quarantine_orphans, to_usd, uom_factor

MOVEMENT_TYPES = {"RECEIPT", "ISSUE", "TRANSFER_IN", "TRANSFER_OUT", "ADJUSTMENT"}
PO_STATUSES = {"Open", "Partially Received", "Closed", "Cancelled"}


def transform(raw: dict[str, pd.DataFrame], cfg: dict, as_of: date, run_id: str
              ) -> tuple[dict[str, pd.DataFrame], list[q.CheckResult], pd.DataFrame]:
    """Pure transformation step (no IO) so that it can be unit tested."""
    res: list[q.CheckResult] = []
    rejected: list[pd.DataFrame] = []
    for entity, (cols, _) in SOURCE_SCHEMAS.items():
        r = q.check_schema(raw[entity], cols, entity)
        res.append(r)
        if r.status == q.FAIL:
            raise ValueError(f"{entity}: {r.detail}")

    fixed = cfg["uom"]["fixed_factors"]
    pack_uoms = cfg["uom"]["product_factor_uoms"]
    fx = cfg["currency"]["fx_to_usd"]

    # ---- dimensions --------------------------------------------------------------
    products = raw["products"].copy()
    res += [q.check_unique(products, ["sku"], "dim_product"),
            q.check_not_null(products, ["sku", "category", "unit_cost_usd", "case_pack"], "dim_product"),
            q.check_range(products, "unit_cost_usd", "dim_product", min_value=0.0001),
            q.check_range(products, "case_pack", "dim_product", min_value=1)]
    products, _ = dedupe(products, ["sku"])
    products["launch_date"] = pd.to_datetime(products["launch_date"]).dt.date
    suppliers, _ = dedupe(raw["suppliers"].copy(), ["supplier_id"])
    locations, _ = dedupe(raw["locations"].copy(), ["location_id"])
    res += [q.check_unique(raw["suppliers"], ["supplier_id"], "dim_supplier"),
            q.check_unique(raw["locations"], ["location_id"], "dim_location"),
            q.check_referential(products, "primary_supplier_id", suppliers["supplier_id"], "dim_product",
                                "dim_supplier")]
    skus, sups, locs = set(products["sku"]), set(suppliers["supplier_id"]), set(locations["location_id"])
    pack = dict(zip(products["sku"], products["case_pack"]))

    def _orphans(df, checks, entity):
        for col, valid in checks.items():
            res.append(q.check_referential(df, col, pd.Series(sorted(valid)), entity, col, severity=q.WARN))
        clean, rej = quarantine_orphans(df, checks, entity, run_id)
        rejected.append(rej)
        return clean

    def _dedupe(df, keys, entity):
        res.append(q.check_unique(df, keys, entity, severity=q.WARN))
        return dedupe(df, keys)[0]

    # ---- inventory snapshots -----------------------------------------------------
    s = _dedupe(raw["inventory_snapshots"].copy(), ["sku", "location_id", "snapshot_ts"], "fact_inventory_snapshot")
    s = _orphans(s, {"sku": skus, "location_id": locs}, "fact_inventory_snapshot")
    f = uom_factor(s["uom"], s["sku"], pack, fixed, pack_uoms)
    for col in ["on_hand_qty", "reserved_qty", "in_transit_qty"]:
        s[col] = s[col].astype(float) * f
    s["available_qty"] = s["on_hand_qty"] - s["reserved_qty"]
    s["is_negative_on_hand"] = s["on_hand_qty"] < 0
    s["snapshot_date"] = s["snapshot_ts"].dt.date
    res.append(q.check_range(s, "on_hand_qty", "fact_inventory_snapshot", min_value=0, severity=q.WARN))
    now = datetime.combine(as_of + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    for loc, g in s.groupby("location_id"):
        r = q.check_freshness(g["snapshot_ts"].max(), cfg["inventory"]["snapshot_max_age_hours"],
                              f"fact_inventory_snapshot[{loc}]", now=now)
        res.append(r)

    # ---- movements -----------------------------------------------------------------
    m = _dedupe(raw["inventory_movements"].copy(), ["movement_id"], "fact_inventory_movement")
    m = _orphans(m, {"sku": skus, "location_id": locs}, "fact_inventory_movement")
    bad_type = ~m["movement_type"].isin(MOVEMENT_TYPES)
    res.append(q.CheckResult("domain:movement_type", "fact_inventory_movement", q.FAIL if bad_type.any() else q.PASS,
                             int(bad_type.sum()), f"allowed: {sorted(MOVEMENT_TYPES)}"))
    m = m[~bad_type].copy()
    m["source_uom"], m["source_quantity"] = m["uom"], m["quantity"].astype(float)
    m["quantity"] = m["source_quantity"] * uom_factor(m["uom"], m["sku"], pack, fixed, pack_uoms)
    m["movement_date"] = m["movement_ts"].dt.date

    # ---- demand ---------------------------------------------------------------------
    d = _dedupe(raw["demand"].copy(), ["sku", "location_id", "demand_date"], "fact_demand")
    d = _orphans(d, {"sku": skus, "location_id": locs}, "fact_demand")
    f = uom_factor(d["uom"], d["sku"], pack, fixed, pack_uoms)
    for col in ["requested_qty", "fulfilled_qty", "backordered_qty"]:
        d[col] = d[col].astype(float) * f
    d["demand_date"] = d["demand_date"].dt.date
    inconsistent = (d["fulfilled_qty"] + d["backordered_qty"] - d["requested_qty"]).abs() > 1e-9
    res.append(q.CheckResult("consistency:requested=fulfilled+backordered", "fact_demand",
                             q.FAIL if inconsistent.any() else q.PASS, int(inconsistent.sum()),
                             "demand lines balance" if not inconsistent.any() else "lines do not balance"))

    # ---- purchase orders ----------------------------------------------------------------
    po = _dedupe(raw["purchase_orders"].copy(), ["po_number", "po_line"], "fact_purchase_order_line")
    po = _orphans(po, {"supplier_id": sups, "sku": skus, "location_id": locs}, "fact_purchase_order_line")
    f = uom_factor(po["uom"], po["sku"], pack, fixed, pack_uoms)
    po["source_uom"] = po["uom"]
    po["ordered_qty"] = po["ordered_qty"].astype(float) * f
    po["unit_cost_source"] = po["unit_cost"].astype(float)
    po["unit_cost_usd"] = to_usd(po["unit_cost_source"], po["currency"], fx) / f
    po["line_value_usd"] = po["ordered_qty"] * po["unit_cost_usd"]
    for c in ["order_date", "approval_date", "promised_date"]:
        po[c] = po[c].dt.date
    bad_dates = po["promised_date"] < po["order_date"]
    res.append(q.CheckResult("logic:promised>=order_date", "fact_purchase_order_line",
                             q.WARN if bad_dates.any() else q.PASS, int(bad_dates.sum()), "promise after order"))
    res.append(q.CheckResult("domain:po_status", "fact_purchase_order_line",
                             q.FAIL if (~po["status"].isin(PO_STATUSES)).any() else q.PASS,
                             int((~po["status"].isin(PO_STATUSES)).sum()), f"allowed: {sorted(PO_STATUSES)}"))

    # ---- receipts -------------------------------------------------------------------------
    rc = _dedupe(raw["receipts"].copy(), ["receipt_id"], "fact_receipt_line")
    rc["po_key"] = rc["po_number"] + "#" + rc["po_line"].astype(str)
    valid_lines = set(po["po_number"] + "#" + po["po_line"].astype(str))
    rc = _orphans(rc, {"po_key": valid_lines}, "fact_receipt_line").drop(columns="po_key")
    rc = rc.merge(po[["po_number", "po_line", "sku"]], on=["po_number", "po_line"], how="left")
    f = uom_factor(rc["uom"], rc["sku"], pack, fixed, pack_uoms)
    rc["source_uom"] = rc["uom"]
    for col in ["received_qty", "accepted_qty", "rejected_qty"]:
        rc[col] = rc[col].astype(float) * f
    rc["receipt_date"] = rc["receipt_ts"].dt.date
    unbalanced = (rc["accepted_qty"] + rc["rejected_qty"] - rc["received_qty"]).abs() > 1e-6
    res.append(q.CheckResult("consistency:received=accepted+rejected", "fact_receipt_line",
                             q.FAIL if unbalanced.any() else q.PASS, int(unbalanced.sum()), "receipt lines balance"))

    # ---- cross-source reconciliation ----------------------------------------------------------
    res.append(q.check_reconciliation(rc["accepted_qty"].sum(), m.loc[m["movement_type"] == "RECEIPT", "quantity"].sum(),
                                      "receipts_accepted_vs_receipt_movements", "fact_receipt_line"))
    res.append(q.check_reconciliation(d["fulfilled_qty"].sum(), -m.loc[m["movement_type"] == "ISSUE", "quantity"].sum(),
                                      "demand_fulfilled_vs_issue_movements", "fact_demand"))
    transfers = m.loc[m["movement_type"].isin(["TRANSFER_IN", "TRANSFER_OUT"]), "quantity"].sum()
    res.append(q.CheckResult("reconcile:transfers_net_zero", "fact_inventory_movement",
                             q.PASS if abs(transfers) < 1e-6 else q.FAIL, 0 if abs(transfers) < 1e-6 else 1,
                             f"net transfer quantity across network = {transfers:,.0f}"))

    tables = {
        "dim_product": products, "dim_supplier": suppliers, "dim_location": locations,
        "fact_inventory_snapshot": s, "fact_inventory_movement": m, "fact_demand": d,
        "fact_purchase_order_line": po, "fact_receipt_line": rc,
    }
    rej = pd.concat(rejected, ignore_index=True) if rejected else pd.DataFrame()
    return tables, res, rej


def dim_date(start: date, end: date) -> pd.DataFrame:
    dates = pd.date_range(start, end, freq="D")
    fy = np.where(dates.month >= 4, dates.year + 1, dates.year)
    return pd.DataFrame({
        "date_key": dates.date, "year": dates.year, "quarter": dates.quarter, "month": dates.month,
        "month_name": dates.strftime("%b"), "iso_week": dates.isocalendar().week.to_numpy(),
        "week_start": (dates - pd.to_timedelta(dates.weekday, unit="D")).date,
        "day_of_week": dates.weekday + 1, "is_working_day": dates.weekday < 5,
        "fiscal_year": fy, "fiscal_period": ((dates.month - 4) % 12) + 1,
    })


def run_classification(con, cfg: dict, as_of: date) -> None:
    c = cfg["classification"]
    acv = db.query(con, """
        SELECT p.sku, coalesce(sum(d.requested_qty), 0) * any_value(p.unit_cost_usd) AS annual_consumption_value
        FROM dim_product p LEFT JOIN fact_demand d
          ON d.sku = p.sku AND d.demand_date > ?::DATE - 365 AND d.demand_date <= ?::DATE
        GROUP BY p.sku""", [as_of, as_of])
    weekly = db.query(con, """
        SELECT w.sku, w.week_start, sum(w.requested_qty) AS qty
        FROM v_demand_weekly w JOIN dim_product p USING (sku)
        WHERE w.days_in_week = 7 AND w.week_start >= date_trunc('week', coalesce(p.launch_date, DATE '1900-01-01'))
          AND w.week_start > ?::DATE - 7 * ?
        GROUP BY ALL""", [as_of, c["lookback_weeks"]])
    cv = weekly.groupby("sku")["qty"].apply(
        lambda s: classification.coefficient_of_variation(s) if len(s) >= 8 else float("nan"))
    acv["abc_class"] = classification.abc_classify(acv["annual_consumption_value"], c["abc_a_share"], c["abc_b_share"])
    acv["demand_cv"] = acv["sku"].map(cv)
    acv["xyz_class"] = classification.xyz_classify(acv["demand_cv"], c["xyz_x_cv"], c["xyz_y_cv"])
    con.register("_cls", acv)
    con.execute("""UPDATE dim_product SET abc_class = c.abc_class, xyz_class = c.xyz_class,
                   annual_consumption_value = c.annual_consumption_value, demand_cv = c.demand_cv
                   FROM _cls c WHERE dim_product.sku = c.sku""")
    con.unregister("_cls")


def run_forecasts(con, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    fc = cfg["forecast"]
    weekly = db.query(con, """
        SELECT w.sku, w.location_id, w.week_start, w.requested_qty
        FROM v_demand_weekly w JOIN dim_product p USING (sku)
        WHERE w.days_in_week = 7
          AND w.week_start >= date_trunc('week', coalesce(p.launch_date, DATE '1900-01-01'))
        ORDER BY w.sku, w.location_id, w.week_start""")
    fact_rows, acc_rows = [], []
    for (sku, loc), g in weekly.groupby(["sku", "location_id"], sort=True):
        hist = g["requested_qty"].to_numpy(float)
        weeks = pd.to_datetime(g["week_start"]).dt.date.to_numpy()
        method, bt, scores, point, rmse = forecasting.forecast_series(
            hist, fc["horizon_weeks"], fc["backtest_origins"], fc["backtest_horizon_weeks"])
        pattern = forecasting.demand_pattern(hist[-26:])
        for r in bt.itertuples():
            origin_week = weeks[r.origin_idx - 1]
            fact_rows.append((f"BT-{origin_week}-{r.method}", "backtest", origin_week, weeks[r.target_idx],
                              r.horizon, sku, loc, r.method, r.forecast, None, None, "baseline-v1"))
        last = weeks[-1]
        half = fc["interval_z"] * rmse if rmse == rmse else 0.0
        for i, p in enumerate(point):
            fact_rows.append((f"PROD-{last}", "production", last, last + timedelta(weeks=i + 1), i + 1, sku, loc,
                              method, float(p), max(float(p) - half, 0.0), float(p) + half, "baseline-v1"))
        for r in scores.itertuples():
            acc_rows.append((sku, loc, r.method, r.wape, r.mae, r.bias, r.actual_qty, bool(r.selected), pattern))
    fact = pd.DataFrame(fact_rows, columns=["forecast_run_id", "run_type", "origin_week", "target_week",
                                            "horizon_weeks", "sku", "location_id", "method", "predicted_qty",
                                            "lower_qty", "upper_qty", "model_version"])
    acc = pd.DataFrame(acc_rows, columns=["sku", "location_id", "method", "wape", "mae", "bias", "actual_qty",
                                          "selected", "demand_pattern"])
    return fact, acc


def replenishment_inputs(con) -> pd.DataFrame:
    """Per SKU/location inputs: demand stats, observed supplier lead time (nominal fallback), stock, open POs."""
    return db.query(con, """
        SELECT ip.sku, ip.location_id, coalesce(ip.abc_class, 'C') AS abc_class,
               ip.avg_daily_demand, ip.demand_std,
               CASE WHEN sc.lead_time_obs >= 3 THEN sc.lead_time_mean ELSE s.nominal_lead_time_days END AS lead_time_days,
               CASE WHEN sc.lead_time_obs >= 3 THEN coalesce(sc.lead_time_std, 0) ELSE 0 END AS lead_time_std,
               ip.on_hand_qty, ip.available_qty, ip.on_order_qty, p.moq_units, p.case_pack, p.unit_cost_usd
        FROM v_inventory_position ip
        JOIN dim_product p USING (sku)
        JOIN dim_supplier s ON s.supplier_id = p.primary_supplier_id
        LEFT JOIN v_supplier_scorecard sc ON sc.supplier_id = p.primary_supplier_id
        ORDER BY ip.sku, ip.location_id""")


def run_replenishment(con, cfg: dict, as_of: date, **what_if) -> pd.DataFrame:
    sl = cfg["service_level"]
    return planning.recommend(replenishment_inputs(con), as_of, cfg["inventory"]["review_period_days"],
                              sl["by_abc"], sl["default"], **what_if)


def build(db_path: str | Path, raw_dir: str | Path, as_of: date, seed: int = 42, regenerate: bool = True,
          source: SourceAdapter | None = None, cfg: dict | None = None) -> dict:
    cfg = cfg or BUSINESS
    run_id = f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    con = db.connect(db_path)
    db.run_sql_file(con, "schema.sql")
    started = datetime.now(timezone.utc)
    con.execute("INSERT INTO ops_pipeline_runs VALUES (?, ?, NULL, 'running', ?, 0, ?, NULL)",
                [run_id, started, as_of, PIPELINE_VERSION])
    try:
        if regenerate and source is None:
            generate(raw_dir, as_of, seed)
        src = source or CsvSource(raw_dir)
        raw = {e: src.read(e) for e in SOURCE_SCHEMAS}
        tables, results, rejected = transform(raw, cfg, as_of, run_id)

        rows = 0
        for name, df in tables.items():
            rows += db.replace_table(con, name, df)
        start = min(tables["fact_demand"]["demand_date"].min(), tables["fact_purchase_order_line"]["order_date"].min())
        db.replace_table(con, "dim_date", dim_date(start, as_of + timedelta(weeks=cfg["forecast"]["horizon_weeks"] + 1)))
        db.replace_table(con, "ops_rejected_rows", rejected)
        inv = cfg["inventory"]
        db.replace_table(con, "meta_build", pd.DataFrame([{
            "run_id": run_id, "data_as_of": as_of, "built_at": datetime.now(timezone.utc),
            "demand_lookback_days": inv["demand_lookback_days"],
            "in_full_tolerance": cfg["procurement"]["in_full_tolerance"],
            "slow_moving_days": inv["slow_moving_days"], "carrying_cost_rate": inv["carrying_cost_rate"],
            "snapshot_max_age_hours": inv["snapshot_max_age_hours"],
            "data_label": "SYNTHETIC DEMO DATA" if source is None else src.name}]))
        db.run_sql_file(con, "views.sql")

        run_classification(con, cfg, as_of)
        fact_fc, acc = run_forecasts(con, cfg)
        db.replace_table(con, "fact_forecast", fact_fc)
        db.replace_table(con, "mart_forecast_accuracy", acc)
        db.replace_table(con, "mart_replenishment", run_replenishment(con, cfg, as_of))
        positions = db.query(con, """SELECT s.sku, s.location_id, s.on_hand_qty, p.unit_cost_usd
                                     FROM v_latest_snapshot s JOIN dim_product p USING (sku)""")
        inbound = db.query(con, """SELECT sku, location_id, movement_date, quantity FROM fact_inventory_movement
                                   WHERE quantity > 0 AND movement_type IN ('RECEIPT', 'TRANSFER_IN', 'ADJUSTMENT')""")
        db.replace_table(con, "mart_stock_ageing", inventory.ageing_table(positions, inbound, as_of))

        breaks = con.execute("SELECT count(*) FROM v_snapshot_reconciliation WHERE abs(variance_qty) > 0.5").fetchone()[0]
        checked = con.execute("SELECT count(*) FROM v_snapshot_reconciliation").fetchone()[0]
        results.append(q.CheckResult("reconcile:snapshot=prev+movements", "fact_inventory_snapshot",
                                     q.WARN if breaks else q.PASS, int(breaks),
                                     f"{breaks} of {checked:,} snapshot rows do not equal previous snapshot + net movements"))
        db.append(con, "ops_dq_results", q.results_frame(results, run_id))
        status = "failed" if any(r.status == q.FAIL for r in results) else "success"
        msg = f"{sum(r.status != q.PASS for r in results)} checks raised exceptions; {len(rejected)} rows quarantined"
        con.execute("UPDATE ops_pipeline_runs SET finished_at = ?, status = ?, rows_loaded = ?, message = ? "
                    "WHERE run_id = ?", [datetime.now(timezone.utc), status, rows, msg, run_id])
        return {"run_id": run_id, "status": status, "rows_loaded": rows, "message": msg,
                "checks": len(results), "rejected": len(rejected)}
    except Exception as exc:
        con.execute("UPDATE ops_pipeline_runs SET finished_at = ?, status = 'failed', message = ? WHERE run_id = ?",
                    [datetime.now(timezone.utc), f"{type(exc).__name__}: {exc}"[:500], run_id])
        raise
    finally:
        con.close()
