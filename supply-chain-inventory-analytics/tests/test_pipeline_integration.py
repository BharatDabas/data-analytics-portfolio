"""Integration: full synthetic build, grain uniqueness, no duplication in views, DQ detection, idempotency."""
import pandas as pd
import pytest

from analytics.inventory import days_of_cover
from database import db
from tests.conftest import AS_OF
from transformations.build import build

GRAINS = {
    "fact_inventory_snapshot": ["sku", "location_id", "snapshot_ts"],
    "fact_inventory_movement": ["movement_id"],
    "fact_demand": ["sku", "location_id", "demand_date"],
    "fact_purchase_order_line": ["po_number", "po_line"],
    "fact_receipt_line": ["receipt_id"],
    "fact_forecast": ["forecast_run_id", "sku", "location_id", "target_week"],
    "dim_product": ["sku"], "dim_supplier": ["supplier_id"], "dim_location": ["location_id"], "dim_date": ["date_key"],
}


def scalar(con, sql):
    return con.execute(sql).fetchall()[0][0]


def test_build_succeeds(built_db):
    assert built_db[2]["status"] == "success"


@pytest.mark.parametrize("table,keys", GRAINS.items())
def test_fact_grain_is_unique(con, table, keys):
    k = ", ".join(keys)
    assert scalar(con, f"SELECT count(*) - count(DISTINCT ({k})) FROM {table}") == 0
    assert scalar(con, f"SELECT count(*) FROM {table}") > 0


def test_views_do_not_duplicate_quantities(con):
    latest = scalar(con, """SELECT sum(on_hand_qty) FROM (SELECT on_hand_qty FROM fact_inventory_snapshot
                            QUALIFY row_number() OVER (PARTITION BY sku, location_id ORDER BY snapshot_ts DESC) = 1)""")
    assert scalar(con, "SELECT sum(on_hand_qty) FROM v_inventory_position") == pytest.approx(latest)
    assert scalar(con, "SELECT sum(on_order_qty) FROM v_inventory_position") == pytest.approx(
        scalar(con, "SELECT sum(open_qty) FROM v_po_line_status"))
    assert scalar(con, "SELECT sum(requested_qty) FROM v_service_daily") == pytest.approx(
        scalar(con, "SELECT sum(requested_qty) FROM fact_demand"))
    assert scalar(con, "SELECT sum(ordered_qty) FROM v_po_line_status") == pytest.approx(
        scalar(con, "SELECT sum(ordered_qty) FROM fact_purchase_order_line"))
    assert scalar(con, "SELECT sum(received_qty) FROM v_po_line_status") == pytest.approx(
        scalar(con, "SELECT sum(received_qty) FROM fact_receipt_line"))
    assert scalar(con, "SELECT sum(spend_usd) FROM v_supplier_scorecard") == pytest.approx(
        scalar(con, "SELECT sum(line_value_usd) FROM fact_purchase_order_line WHERE status <> 'Cancelled'"))
    assert scalar(con, "SELECT count(*) FROM v_inventory_position") == scalar(
        con, "SELECT count(DISTINCT (sku, location_id)) FROM fact_inventory_snapshot")


def test_sql_cover_matches_python_definition(con):
    ip = db.query(con, "SELECT available_qty, avg_daily_demand, days_of_cover FROM v_inventory_position")
    py = days_of_cover(ip["available_qty"], ip["avg_daily_demand"])
    for sql_v, py_v, d in zip(ip["days_of_cover"], py, ip["avg_daily_demand"]):
        if d > 0:
            assert sql_v == pytest.approx(py_v)
        else:
            assert pd.isna(sql_v)  # SQL leaves undefined/infinite cover NULL ("No demand")


def test_injected_defects_are_detected(con):
    dq = db.query(con, "SELECT * FROM ops_dq_results WHERE run_id = (SELECT run_id FROM meta_build)")
    by = dq.set_index(["check_name", "table_name"])
    assert by.loc[("unique:movement_id", "fact_inventory_movement"), "failed_rows"] == 5
    assert by.loc[("range:on_hand_qty", "fact_inventory_snapshot"), "failed_rows"] == 1
    assert by.loc[("freshness", "fact_inventory_snapshot[HUB-EST]"), "status"] == "warn"
    assert by.loc[("reconcile:transfers_net_zero", "fact_inventory_movement"), "status"] == "pass"
    assert by.loc[("reconcile:receipts_accepted_vs_receipt_movements", "fact_receipt_line"), "status"] == "pass"
    assert by.loc[("reconcile:demand_fulfilled_vs_issue_movements", "fact_demand"), "status"] == "pass"
    assert scalar(con, "SELECT count(*) FROM fact_inventory_snapshot WHERE is_negative_on_hand") == 1
    assert scalar(con, "SELECT count(*) FROM ops_rejected_rows") == 4
    assert scalar(con, "SELECT count(*) FROM fact_purchase_order_line WHERE supplier_id = 'SUP-099'") == 0
    # the negative snapshot is the only reconciliation break (day of error + reversal next day)
    assert scalar(con, "SELECT count(*) FROM v_snapshot_reconciliation WHERE abs(variance_qty) > 0.5") == 2
    assert bool(scalar(con, "SELECT is_stale FROM v_location_freshness WHERE location_id = 'HUB-EST'"))


def test_units_and_currency_standardised(con):
    assert scalar(con, "SELECT count(*) FROM fact_receipt_line WHERE source_uom = 'CS'") > 0
    # CS receipts converted to each: whole multiples of the case pack
    assert scalar(con, """SELECT count(*) FROM fact_receipt_line r JOIN fact_purchase_order_line l USING (po_number, po_line)
                          JOIN dim_product p ON p.sku = l.sku
                          WHERE r.source_uom = 'CS' AND r.received_qty % p.case_pack <> 0""") == 0
    # USD unit cost near standard cost for EUR/GBP suppliers (within synthetic price noise)
    assert scalar(con, """SELECT max(abs(l.unit_cost_usd / p.unit_cost_usd - 1)) FROM fact_purchase_order_line l
                          JOIN dim_product p USING (sku) WHERE l.currency <> 'USD'""") < 0.25


def test_classification_and_forecasts_populated(con):
    assert scalar(con, "SELECT count(*) FROM dim_product WHERE abc_class IS NULL OR xyz_class IS NULL") == 0
    assert scalar(con, "SELECT count(DISTINCT abc_class) FROM dim_product") == 3
    assert scalar(con, "SELECT count(*) FROM mart_forecast_accuracy WHERE selected") == scalar(
        con, "SELECT count(DISTINCT (sku, location_id)) FROM fact_forecast WHERE run_type = 'production'")
    assert scalar(con, "SELECT min(lower_qty) FROM fact_forecast WHERE run_type = 'production'") >= 0
    # forecast targets never precede their origin
    assert scalar(con, "SELECT count(*) FROM fact_forecast WHERE target_week <= origin_week") == 0


def test_replenishment_respects_pack_and_moq(con):
    r = db.query(con, """SELECT r.*, p.case_pack, p.moq_units FROM mart_replenishment r JOIN dim_product p USING (sku)
                         WHERE suggested_order_qty > 0""")
    assert not r.empty
    assert (r["suggested_order_qty"] % r["case_pack"] == 0).all()
    assert (r["suggested_order_qty"] >= r["moq_units"]).all()
    assert (r["inventory_position"] <= r["reorder_point"] + 1e-9).all()


def test_rebuild_is_idempotent_and_preserves_reviews_without_creating_pos(built_db):
    path, raw, _ = built_db
    con = db.connect(path)
    before = {t: scalar(con, f"SELECT count(*) FROM {t}") for t in GRAINS}
    db.upsert(con, "planner_review", pd.DataFrame([{"sku": "SKU-1001", "location_id": "DC-NTH", "recommended_qty": 10,
                                                   "status": "Approved", "note": "test", "reviewed_by": "pytest"}]),
              ["sku", "location_id"])
    runs = scalar(con, "SELECT count(*) FROM ops_pipeline_runs")
    con.close()
    build(path, raw, AS_OF, seed=42)
    con = db.connect(path)
    after = {t: scalar(con, f"SELECT count(*) FROM {t}") for t in GRAINS}
    assert after == before                                   # same data, no accumulation, no POs created
    assert scalar(con, "SELECT status FROM planner_review WHERE sku = 'SKU-1001'") == "Approved"
    assert scalar(con, "SELECT count(*) FROM ops_pipeline_runs") == runs + 1
    con.close()


def test_planner_review_save_path_used_by_workbench(con):
    """Same call the workbench makes: upsert through a UTC-pinned cursor; second save overwrites, never duplicates."""
    from dashboards.context import cursor

    row = {"sku": "SKU-1002", "location_id": "DC-STH", "recommended_qty": 24.0, "note": "", "reviewed_by": "pytest"}
    db.upsert(cursor(con), "planner_review", pd.DataFrame([{**row, "status": "Deferred"}]), ["sku", "location_id"])
    db.upsert(cursor(con), "planner_review", pd.DataFrame([{**row, "status": "Rejected"}]), ["sku", "location_id"])
    got = db.query(con, "SELECT status FROM planner_review WHERE sku = 'SKU-1002' AND location_id = 'DC-STH'")
    assert got["status"].tolist() == ["Rejected"]
