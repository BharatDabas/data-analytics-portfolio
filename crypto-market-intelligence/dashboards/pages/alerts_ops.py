"""F. Alerts & Data Operations - triggered rules, rule configuration, ingestion health, gaps, DQ results."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import settings
from dashboards import theme

RULE_TYPES = ["pct_move", "volume_spike", "price_above", "price_below", "stale_data"]
STATUS_LEVEL = {"pass": ("good", "Pass"), "warn": ("warning", "Warning"), "fail": ("critical", "Fail"),
                "success": ("good", "Success"), "failed": ("critical", "Failed")}


def _status(s: str) -> str:
    level, text = STATUS_LEVEL.get(s, ("warning", s))
    return theme.status_label(level, text)


def render(ctx) -> None:
    theme.page_header("Alerts & Data Operations", "Configurable alert rules, ingestion health and data-quality exceptions.",
                      ctx.chips())
    runs = ctx.q("SELECT * FROM ops_ingestion_runs WHERE source_id = ? ORDER BY started_at DESC", (ctx.source_id,))
    gaps = ctx.q("""SELECT g.* FROM ops_missing_intervals g JOIN dim_asset a USING (asset_id)
                    WHERE a.source_id = ? ORDER BY gap_start""", (ctx.source_id,))
    dq = ctx.q("SELECT * FROM ops_dq_results ORDER BY CASE status WHEN 'fail' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END")
    events = ctx.q("SELECT e.*, a.name FROM fact_alert_events e LEFT JOIN dim_asset a USING (asset_id) "
                   "ORDER BY observed_at DESC")
    pipe = ctx.q("SELECT * FROM ops_pipeline_runs ORDER BY started_at DESC")
    src = ctx.q("SELECT * FROM dim_source WHERE source_id = ?", (ctx.source_id,))

    last_ok = src["last_success_at"].iloc[0] if len(src) else None
    failed = runs[runs["status"] == "failed"]
    recent = events[pd.to_datetime(events["observed_at"]) >= pd.Timestamp(ctx.as_of, tz="UTC") - pd.Timedelta(days=30)]
    theme.kpi_row([
        theme.kpi_card("Last successful refresh", f"{pd.Timestamp(last_ok):%d %b %H:%M} UTC" if pd.notna(last_ok) else "never",
                       f"source: {ctx.source_id}"),
        theme.kpi_card("Alerts (last 30 days)", str(len(recent)), f"{len(events)} in full history"),
        theme.kpi_card("Failed ingestion runs", str(len(failed)), f"of {len(runs)} asset requests logged"),
        theme.kpi_card("Missing intervals", str(int(gaps["missing_points"].sum()) if len(gaps) else 0),
                       f"{len(gaps)} gap(s) — reported, not filled"),
        theme.kpi_card("DQ checks failing / warning", f"{int((dq['status'] == 'fail').sum())} / {int((dq['status'] == 'warn').sum())}",
                       f"{len(dq)} checks in latest run"),
    ])

    tab_alerts, tab_rules, tab_ingest, tab_dq = st.tabs(["Triggered alerts", "Alert rules", "Ingestion log", "Data quality"])
    with tab_alerts:
        c1, c2 = st.columns(2)
        types = c1.multiselect("Rule type", sorted(events["rule_type"].unique()), default=sorted(events["rule_type"].unique()))
        assets = c2.multiselect("Asset", sorted(events["asset_id"].unique()), format_func=ctx.label)
        ev = events[events["rule_type"].isin(types)]
        if assets:
            ev = ev[ev["asset_id"].isin(assets)]
        ev = ev[(pd.to_datetime(ev["observed_at"]).dt.date >= ctx.start) & (pd.to_datetime(ev["observed_at"]).dt.date <= ctx.end)]
        if ev.empty:
            theme.empty_state("No alerts match the filters in the selected period.")
        else:
            by_week = ev.assign(week=pd.to_datetime(ev["observed_at"]).dt.tz_localize(None).dt.to_period("W").dt.start_time) \
                .groupby(["week", "rule_type"]).size().unstack(fill_value=0)
            fig = go.Figure()
            for i, t in enumerate(RULE_TYPES):
                if t in by_week:
                    fig.add_trace(go.Bar(x=by_week.index, y=by_week[t], name=t, marker_color=theme.SERIES[i]))
            fig.update_layout(barmode="stack")
            st.plotly_chart(theme.style_fig(fig, 240, "Alerts per week by rule type", "Alerts"), width="stretch")
            st.dataframe(ev[["observed_at", "rule_id", "rule_name", "rule_type", "name", "threshold", "observed_value",
                             "channels", "message"]], hide_index=True, width="stretch", height=320,
                         column_config={"observed_at": st.column_config.DatetimeColumn("Observed (UTC)", format="YYYY-MM-DD"),
                                        "name": "Asset", "observed_value": st.column_config.NumberColumn("Observed", format="%.4g"),
                                        "threshold": st.column_config.NumberColumn(format="%.4g")})
    with tab_rules:
        st.caption("Edit thresholds or add rules, then re-evaluate. Defaults live in `config/alert_rules.toml`. "
                   "Delivery channels: `dashboard` (this table) and `log` (logs/alerts.log).")
        rules = pd.DataFrame(st.session_state.get("alert_rules", settings.load_alert_rules()))
        rules["channels"] = rules["channels"].apply(lambda c: ",".join(c) if isinstance(c, list) else c)
        edited = st.data_editor(rules, num_rows="dynamic", hide_index=True, width="stretch", column_config={
            "type": st.column_config.SelectboxColumn("type", options=RULE_TYPES, required=True),
            "asset_id": st.column_config.SelectboxColumn("asset_id", options=["*", *ctx.assets["asset_id"]], required=True),
            "threshold": st.column_config.NumberColumn("threshold", required=True)})
        if st.button("Re-evaluate alert rules", type="primary"):
            clean = edited.dropna(subset=["rule_id", "type", "threshold"]).to_dict("records")
            for r in clean:
                r["channels"] = [c.strip() for c in str(r.get("channels") or "dashboard").split(",")]
            st.session_state["alert_rules"] = clean
            from pipeline import refresh_alerts
            n = refresh_alerts(ctx.con, clean, datetime.now(timezone.utc))
            st.session_state["data_version"] += 1
            st.success(f"{n} alert events generated from {len(clean)} rules.")
            st.rerun()
    with tab_ingest:
        c1, c2 = st.columns([1, 1])
        with c1:
            theme.section("Freshness by asset")
            fresh = ctx.q("SELECT * FROM v_data_freshness WHERE source_id = ?", (ctx.source_id,))
            age = (pd.Timestamp.now(tz="UTC") - pd.to_datetime(fresh["latest_observed_at"], utc=True)).dt.total_seconds() / 3600
            limit = ctx.cfg["freshness"]["max_age_hours"]
            fresh = fresh.assign(age_hours=age.round(1), completeness=fresh["observations"] / fresh["expected_days"],
                                 status=[theme.status_label("good", "Fresh") if a <= limit else theme.status_label("warning", "Stale")
                                         for a in age])
            st.dataframe(fresh[["name", "last_price_date", "latest_observed_at", "latest_retrieved_at", "age_hours",
                                "completeness", "status"]], hide_index=True, width="stretch",
                         column_config={"completeness": st.column_config.ProgressColumn("Completeness", format="percent",
                                                                                         min_value=0, max_value=1),
                                        "age_hours": st.column_config.NumberColumn("Age (h)", format="%.1f")})
        with c2:
            theme.section("Missing intervals", "Detected gaps in the daily calendar. Not forward-filled.")
            if gaps.empty:
                theme.empty_state("No missing intervals detected.")
            else:
                st.dataframe(gaps[["asset_id", "gap_start", "gap_end", "missing_points"]], hide_index=True, width="stretch")
        theme.section("API ingestion log", "One row per asset request: attempts include retries with exponential backoff.")
        view = runs.assign(result=runs["status"].map(_status))
        st.dataframe(view[["started_at", "batch_id", "asset_id", "result", "http_status", "attempts", "rows_received",
                           "rows_loaded", "duplicates_dropped", "invalid_rows", "error_message"]],
                     hide_index=True, width="stretch", height=300,
                     column_config={"started_at": st.column_config.DatetimeColumn("Started (UTC)", format="YYYY-MM-DD HH:mm:ss")})
        if len(failed):
            theme.section("Failed runs")
            st.dataframe(failed[["started_at", "asset_id", "http_status", "attempts", "error_message"]], hide_index=True,
                         width="stretch")
    with tab_dq:
        dq_view = dq.assign(result=dq["status"].map(_status))
        st.dataframe(dq_view[["result", "check_name", "table_name", "failed_rows", "detail", "checked_at"]],
                     hide_index=True, width="stretch",
                     column_config={"checked_at": st.column_config.DatetimeColumn("Checked (UTC)", format="YYYY-MM-DD HH:mm")})
        theme.section("Pipeline runs")
        st.dataframe(pipe.assign(result=pipe["status"].map(_status))[["started_at", "pipeline", "result", "rows_loaded",
                                                                        "message", "code_version"]],
                     hide_index=True, width="stretch")
