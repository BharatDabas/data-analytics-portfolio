"""Hand-built fixtures for the SQL semantic layer (OTD/OTIF/partial receipts, reconciliation, no fan-out)."""
from datetime import date, datetime, time

import pandas as pd
import pytest

from database import db

AS_OF = date(2026, 6, 30)


@pytest.fixture()
def mini():
    con = db.connect(":memory:")
    db.run_sql_file(con, "schema.sql")
    db.append(con, "meta_build", pd.DataFrame([{"run_id": "t", "data_as_of": AS_OF, "demand_lookback_days": 90,
                                                "in_full_tolerance": 0.0, "slow_moving_days": 90,
                                                "carrying_cost_rate": 0.25, "snapshot_max_age_hours": 36,
                                                "data_label": "TEST"}]))
    db.append(con, "dim_product", pd.DataFrame([{"sku": "S1", "category": "C", "unit_cost_usd": 2.0, "case_pack": 10,
                                                 "primary_supplier_id": "V1", "moq_units": 10}]))
    db.append(con, "dim_supplier", pd.DataFrame([{"supplier_id": "V1", "supplier_name": "Vendor",
                                                  "nominal_lead_time_days": 10}]))
    db.append(con, "dim_location", pd.DataFrame([{"location_id": "L1", "location_name": "DC"}]))
    po = [  # po, line, promised, ordered, status
        ("P1", 1, date(2026, 6, 10), 100, "Closed"),               # on time, in full
        ("P1", 2, date(2026, 6, 10), 100, "Closed"),               # partial on time, rest late -> OTD yes, OTIF no
        ("P2", 1, date(2026, 6, 10), 100, "Closed"),               # late, in full -> OTD no, OTIF no
        ("P3", 1, date(2026, 6, 20), 100, "Partially Received"),   # overdue partial
        ("P4", 1, date(2026, 7, 20), 100, "Open"),                 # not yet due -> excluded from OTIF
        ("P5", 1, date(2026, 6, 1), 100, "Cancelled"),             # excluded
        ("P6", 1, date(2026, 6, 10), 100, "Closed"),               # on time but quality rejects -> not in full
    ]
    db.append(con, "fact_purchase_order_line", pd.DataFrame([{
        "po_number": p, "po_line": ln, "supplier_id": "V1", "sku": "S1", "location_id": "L1",
        "order_date": date(2026, 5, 25), "approval_date": date(2026, 6, 1), "promised_date": pr,
        "ordered_qty": float(qty), "unit_cost_usd": 2.0, "line_value_usd": 2.0 * qty, "status": st}
        for p, ln, pr, qty, st in po]))
    rc = [("R1", "P1", 1, date(2026, 6, 9), 100, 100), ("R2", "P1", 2, date(2026, 6, 10), 60, 60),
          ("R3", "P1", 2, date(2026, 6, 15), 40, 40), ("R4", "P2", 1, date(2026, 6, 12), 100, 100),
          ("R5", "P3", 1, date(2026, 6, 18), 30, 30), ("R6", "P6", 1, date(2026, 6, 10), 100, 95)]
    db.append(con, "fact_receipt_line", pd.DataFrame([{
        "receipt_id": r, "po_number": p, "po_line": ln, "receipt_ts": datetime.combine(d, time(9)),
        "receipt_date": d, "received_qty": float(qty), "accepted_qty": float(a), "rejected_qty": float(qty - a)}
        for r, p, ln, d, qty, a in rc]))
    db.run_sql_file(con, "views.sql")
    yield con
    con.close()


def test_po_line_otd_otif_partial_and_late(mini):
    s = db.query(mini, "SELECT * FROM v_po_line_status").set_index(["po_number", "po_line"])
    assert bool(s.loc[("P1", 1), "otif"]) and bool(s.loc[("P1", 1), "on_time"])
    assert bool(s.loc[("P1", 2), "on_time"]) and not bool(s.loc[("P1", 2), "otif"]) and bool(s.loc[("P1", 2), "in_full"])
    assert not bool(s.loc[("P2", 1), "on_time"]) and s.loc[("P2", 1), "days_late"] == 2
    assert bool(s.loc[("P3", 1), "is_overdue"]) and s.loc[("P3", 1), "open_qty"] == 70
    assert s.loc[("P3", 1), "delivery_status"] == "Partial - overdue"
    assert not bool(s.loc[("P4", 1), "is_due"]) and not bool(s.loc[("P5", 1), "is_due"])
    assert not bool(s.loc[("P6", 1), "otif"])  # rejected units are not "in full"
    assert s.loc[("P1", 2), "lead_time_days"] == 14   # approval 1 Jun -> final receipt 15 Jun


def test_supplier_scorecard_rates(mini):
    sc = db.query(mini, "SELECT * FROM v_supplier_scorecard").iloc[0]
    # due lines: P1/1, P1/2, P2/1, P3/1, P6/1 = 5
    assert sc["due_lines"] == 5
    assert sc["otd_rate"] == pytest.approx(4 / 5)    # P1/1, P1/2, P3/1, P6/1
    assert sc["otif_rate"] == pytest.approx(1 / 5)   # P1/1 only


def test_inventory_position_does_not_fan_out(mini):
    db.append(mini, "fact_inventory_snapshot", pd.DataFrame([{
        "snapshot_ts": datetime(2026, 6, 30, 23, 59), "snapshot_date": AS_OF, "sku": "S1", "location_id": "L1",
        "on_hand_qty": 50.0, "reserved_qty": 5.0, "available_qty": 45.0, "in_transit_qty": 0.0,
        "is_negative_on_hand": False}]))
    days = pd.date_range("2026-04-02", "2026-06-30").date
    db.append(mini, "fact_demand", pd.DataFrame({"demand_date": days, "sku": "S1", "location_id": "L1",
                                                 "requested_qty": 3.0, "fulfilled_qty": 3.0, "backordered_qty": 0.0}))
    ip = db.query(mini, "SELECT * FROM v_inventory_position")
    assert len(ip) == 1
    assert ip.iloc[0]["on_hand_qty"] == 50                 # not multiplied by PO lines or demand days
    assert ip.iloc[0]["on_order_qty"] == 70 + 100          # P3 outstanding + P4 open
    assert ip.iloc[0]["days_of_cover"] == pytest.approx(15)


def test_snapshot_reconciliation_detects_break(mini):
    snaps = [(date(2026, 6, 1), 100.0), (date(2026, 6, 2), 90.0), (date(2026, 6, 3), 95.0)]
    db.append(mini, "fact_inventory_snapshot", pd.DataFrame([{
        "snapshot_ts": datetime.combine(d, time(23, 59, 59)), "snapshot_date": d, "sku": "S1",
        "location_id": "L1", "on_hand_qty": qty} for d, qty in snaps]))
    db.append(mini, "fact_inventory_movement", pd.DataFrame([
        {"movement_id": "M1", "movement_date": date(2026, 6, 2), "sku": "S1", "location_id": "L1", "quantity": -10.0},
        {"movement_id": "M2", "movement_date": date(2026, 6, 3), "sku": "S1", "location_id": "L1", "quantity": 2.0}]))
    r = db.query(mini, "SELECT * FROM v_snapshot_reconciliation ORDER BY snapshot_date")
    assert r["variance_qty"].tolist() == [0.0, 3.0]   # day 3: 90 + 2 expected, 95 reported
