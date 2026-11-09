"""Data Quality & Pipeline operations."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from dashboards import theme
from dashboards.data import Context
from dashboards.pages.common import dataframe, header
from transformations import quality as q

LEVEL = {"pass": ("good", "Pass"), "warn": ("warning", "Warning"), "fail": ("critical", "Fail")}


def render(ctx: Context) -> None:
    header(ctx, "Data Quality & Pipeline", "Load status, validation results, rejected records and reconciliation",
           show_window=False)
    f = ctx.frames
    runs, dq, fails, files, recon = f["runs"], f["dq"], f["failures"], f["source_files"], f["reconciliation"]
    last = runs.iloc[0] if not runs.empty else None
    fresh = q.check_freshness(f["orders"]["order_ts"].max(), ctx.rules["quality"]["freshness_max_age_hours"],
                              "fact_orders", now=datetime.now(timezone.utc))
    n_fail = int((dq["status"] == "fail").sum()) if not dq.empty else 0
    n_warn = int((dq["status"] == "warn").sum()) if not dq.empty else 0
    theme.kpi_row([
        theme.kpi_card("Last pipeline run", last["status"].replace("_", " ") if last is not None else "—",
                       f"{last['finished_at']:%d %b %Y %H:%M} UTC" if last is not None else ""),
        theme.kpi_card("Data-quality checks", f"{len(dq) - n_fail - n_warn}/{len(dq)} pass",
                       f"{n_warn} warnings · {n_fail} failures"),
        theme.kpi_card("Records rejected", theme.fmt_num(len(fails)), "logged with reason, not loaded"),
        theme.kpi_card("Unreconciled orders", theme.fmt_num(len(recon)), "header ≠ lines + shipping + tax"),
        theme.kpi_card("Freshness (now)", theme.status_label(LEVEL[fresh.status][0], LEVEL[fresh.status][1]),
                       fresh.detail.split(" is ")[-1] if " is " in fresh.detail else fresh.detail),
    ])

    theme.section("Data-quality checks (latest successful run)")
    if dq.empty:
        theme.empty_state("No data-quality results recorded yet.")
    else:
        d = dq.assign(result=dq["status"].map(lambda s: theme.status_label(*LEVEL.get(s, ("warning", s)))))
        dataframe(d[["result", "check_name", "table_name", "failed_rows", "detail"]],
                  column_config={"result": "Result", "check_name": "Check", "table_name": "Table",
                                 "failed_rows": "Rows affected", "detail": "Detail"})

    c1, c2 = st.columns(2)
    with c1:
        theme.section("Source extracts", "Rows read, loaded, rejected and de-duplicated per entity")
        dataframe(files[["entity", "rows_read", "rows_loaded", "rows_rejected", "duplicates_removed", "max_record_ts"]])
    with c2:
        theme.section("Rejected records by reason", "Failed-load log (latest run)")
        if fails.empty:
            theme.empty_state("No rejected records.")
        else:
            dataframe(fails.groupby(["entity", "reason"]).size().rename("records").reset_index())

    theme.section("Order-to-item reconciliation exceptions",
                  "Orders whose source header total does not match the loaded lines (usually because a line was rejected)")
    if recon.empty:
        theme.empty_state("All order headers reconcile to their lines.")
    else:
        dataframe(recon.round({"header_total_gbp": 2, "rebuilt_total_gbp": 2, "difference_gbp": 2}).head(200))

    with st.expander("Rejected record detail (raw payloads)"):
        dataframe(fails[["entity", "record_key", "reason", "raw_record"]].head(500))
    with st.expander("Pipeline run history"):
        dataframe(runs)
