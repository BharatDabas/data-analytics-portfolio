"""H. Data Quality & Operations - pipeline runs, DQ checks, freshness, reconciliation and quarantine."""
from __future__ import annotations

import streamlit as st

from dashboards import theme as t
from dashboards.context import Ctx, show_table

LEVEL = {"pass": ("good", "Pass"), "warn": ("warning", "Warning"), "fail": ("critical", "Fail")}


def render(ctx: Ctx) -> None:
    ctx.header("Data Quality & Operations", "Pipeline runs, validation results, snapshot freshness and source reconciliation")
    dq = ctx.q("SELECT * FROM ops_dq_results WHERE run_id = (SELECT run_id FROM meta_build) ORDER BY status DESC, check_name")
    runs = ctx.q("SELECT * FROM ops_pipeline_runs ORDER BY started_at DESC LIMIT 25")
    last_ok = runs[runs["status"] == "success"]["finished_at"].max() if not runs.empty else None
    t.kpi_row([
        t.kpi_card("Checks executed", f"{len(dq):,}", "latest build"),
        t.kpi_card("Passed", f"{int((dq['status'] == 'pass').sum()):,}", t.status_label("good", "pass")),
        t.kpi_card("Warnings", f"{int((dq['status'] == 'warn').sum()):,}", t.status_label("warning", "investigate")),
        t.kpi_card("Failures", f"{int((dq['status'] == 'fail').sum()):,}", t.status_label("critical", "blocking")),
        t.kpi_card("Last successful refresh", f"{last_ok:%d %b %H:%M}" if last_ok is not None and last_ok == last_ok else "—",
                   "UTC"),
    ])

    t.section("Validation results", "Schema, uniqueness, referential integrity, ranges, consistency, freshness and reconciliation")
    dq["result"] = [t.status_label(*LEVEL[s]) for s in dq["status"]]
    show_table(dq[["result", "check_name", "table_name", "failed_rows", "detail"]], result="Result", check_name="Check",
               table_name="Table", failed_rows="Rows affected", detail="Detail")

    c1, c2 = st.columns(2)
    with c1:
        t.section("Snapshot freshness by location", f"Limit {ctx.cfg['inventory']['snapshot_max_age_hours']} h relative to the data as-of cut-off")
        fr = ctx.q("SELECT location_id, location_name, latest_snapshot_ts, age_hours, is_stale FROM v_location_freshness ORDER BY 1")
        fr["status"] = [t.status_label("critical", "Stale") if s else t.status_label("good", "Fresh") for s in fr["is_stale"]]
        show_table(fr.drop(columns="is_stale"), latest_snapshot_ts=st.column_config.DatetimeColumn("Latest snapshot (UTC)"),
                   age_hours=st.column_config.NumberColumn("Age (h)"))
    with c2:
        t.section("Reconciliation breaks", "Snapshot on-hand ≠ previous snapshot + net movements")
        br = ctx.q("""SELECT sku, location_id, snapshot_date, prev_on_hand, net_movement_qty, expected_on_hand, on_hand_qty,
                             variance_qty FROM v_snapshot_reconciliation WHERE abs(variance_qty) > 0.5 ORDER BY snapshot_date""")
        show_table(br, empty="No reconciliation breaks.", snapshot_date=st.column_config.DateColumn("Snapshot"))
        st.caption("A negative on-hand keyed in error produces a break on that day and the reverse break the next day.")

    t.section("Quarantined source rows", "Rows whose keys do not resolve to master data — excluded from facts, kept for correction")
    show_table(ctx.q("SELECT source_entity, reason, row_json FROM ops_rejected_rows"), empty="No rows quarantined.",
               source_entity="Source entity", reason="Reason", row_json="Source row (JSON)")

    t.section("Pipeline run log", "Most recent 25 runs")
    show_table(runs, started_at=st.column_config.DatetimeColumn("Started (UTC)"),
               finished_at=st.column_config.DatetimeColumn("Finished (UTC)"))
