"""G. Replenishment Workbench - decision-support order suggestions with what-if levers and planner review.

Recording a planner decision writes to planner_review only. No purchase order is ever created.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboards import theme as t
from dashboards.context import Ctx, cursor
from database import db
from transformations.build import run_replenishment

REVIEW_STATUSES = ["Pending", "Approved", "Rejected", "Deferred"]
URGENCY_STATUS = {"Critical": "critical", "High": "serious", "Medium": "warning", "Low": "good", "No demand": "good"}


def render(ctx: Ctx) -> None:
    ctx.header("Replenishment Workbench",
               "Reorder points, safety stock and suggested order quantities — decision support for planner review")
    t.note("<b>Decision support only.</b> Suggestions are not purchase orders; approving a line records the planner "
           "decision for hand-off to the procurement workflow. Formulas: SS = z·√(LT·σ<sub>d</sub>² + d²·σ<sub>LT</sub>²), "
           "ROP = d·LT + SS, target = d·(LT + review period) + SS, order up to target when position ≤ ROP, "
           "rounded up to MOQ and case pack. Lead time uses each supplier's observed mean and standard deviation.")

    with st.container(border=True):
        st.markdown("**What-if scenario**")
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
        uplift = c1.slider("Demand change", -30, 50, 0, 5, format="%d%%") / 100
        delay = c2.slider("Supplier delay (days)", 0, 30, 0, 1)
        override = c3.toggle("Override service level", value=False, help="Default: A 98%, B 95%, C 90%")
        sl = c4.slider("Target service level", 0.80, 0.995, 0.95, 0.005, disabled=not override, format="%.3f")
    recs = run_replenishment(cursor(ctx.con), ctx.cfg, ctx.as_of, demand_uplift=uplift, supplier_delay_days=delay,
                             service_level_override=sl if override else None)
    prod = ctx.q("SELECT sku, product_name, category, primary_supplier_id FROM dim_product")
    recs = recs.merge(prod, on="sku")
    recs = recs[recs["location_id"].isin(ctx.locations) & recs["category"].isin(ctx.categories)]
    base = ctx.q("SELECT sku, location_id, suggested_order_qty base_qty FROM mart_replenishment")
    recs = recs.merge(base, on=["sku", "location_id"], how="left")
    reviews = ctx.q("SELECT sku, location_id, status review_status, note, reviewed_by, reviewed_at FROM planner_review")
    recs = recs.merge(reviews, on=["sku", "location_id"], how="left")
    recs["review_status"] = recs["review_status"].fillna("Pending")
    recs["order_value_usd"] = recs["suggested_order_qty"] * recs["unit_cost_usd"]

    to_order = recs[recs["suggested_order_qty"] > 0]
    scenario = uplift != 0 or delay != 0 or override
    t.kpi_row([
        t.kpi_card("Lines to order", f"{len(to_order):,}", "SKU-locations at or below reorder point"),
        t.kpi_card("Suggested order value", t.fmt_money(to_order["order_value_usd"].sum()), "at standard cost"),
        t.kpi_card("Critical", f"{int((recs['urgency'] == 'Critical').sum()):,}", "out of stock or cover < lead time"),
        t.kpi_card("Safety stock value", t.fmt_money((recs["safety_stock"] * recs["unit_cost_usd"]).sum()),
                   "scenario" if scenario else "default policy"),
        t.kpi_card("Approved", f"{int((recs['review_status'] == 'Approved').sum()):,}",
                   f"{int((recs['review_status'] == 'Pending').sum()):,} pending review"),
    ])
    if scenario:
        delta_qty = recs["suggested_order_qty"].sum() - recs["base_qty"].fillna(0).sum()
        st.caption(f"Scenario vs default policy: {delta_qty:+,.0f} units of suggested orders.")

    c1, c2 = st.columns([3, 2])
    with c1:
        f1, f2 = st.columns(2)
        urg = f1.multiselect("Urgency", list(URGENCY_STATUS), default=["Critical", "High", "Medium"])
        only_orders = f2.toggle("Only lines with a suggested order", value=False)
    view = recs[recs["urgency"].isin(urg)]
    if only_orders:
        view = view[view["suggested_order_qty"] > 0]
    with c2:
        u = recs.groupby("urgency")["order_value_usd"].sum().reindex(list(URGENCY_STATUS)).fillna(0)
        fig = go.Figure(go.Bar(y=[t.status_label(URGENCY_STATUS[k], k) for k in u.index], x=u.values, orientation="h",
                               marker_color=[t.STATUS[URGENCY_STATUS[k]] for k in u.index],
                               text=[t.fmt_money(v) for v in u.values], textposition="outside",
                               hovertemplate="%{x:$,.0f}<extra></extra>"))
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(hovermode="closest", title="Suggested order value by urgency")
        st.plotly_chart(t.style_fig(fig, 220), width="stretch")

    if view.empty:
        t.empty_state("No recommendations match the selected urgency filters.")
        return
    view = view.assign(urgency_label=[t.status_label(URGENCY_STATUS[u], u) for u in view["urgency"]])
    cols = ["sku", "product_name", "location_id", "abc_class", "urgency_label", "available_qty", "on_order_qty",
            "avg_daily_demand", "lead_time_days", "service_level", "safety_stock", "reorder_point", "target_stock",
            "suggested_order_qty", "order_value_usd", "days_of_cover", "expected_cover_after_order",
            "projected_stockout_date", "review_status", "note"]
    edited = st.data_editor(
        view[cols].reset_index(drop=True), hide_index=True, width="stretch", height=460, key="workbench",
        disabled=[c for c in cols if c not in ("review_status", "note")],
        column_config={
            "sku": "SKU", "product_name": "Product", "location_id": "Location", "abc_class": "ABC",
            "urgency_label": "Urgency",
            "available_qty": st.column_config.NumberColumn("Available", format="%.0f"),
            "on_order_qty": st.column_config.NumberColumn("On order", format="%.0f"),
            "avg_daily_demand": st.column_config.NumberColumn("Daily demand", format="%.1f"),
            "lead_time_days": st.column_config.NumberColumn("Lead time (d)", format="%.1f"),
            "service_level": st.column_config.NumberColumn("Service level", format="percent"),
            "safety_stock": st.column_config.NumberColumn("Safety stock", format="%.0f"),
            "reorder_point": st.column_config.NumberColumn("ROP", format="%.0f"),
            "target_stock": st.column_config.NumberColumn("Target", format="%.0f"),
            "suggested_order_qty": st.column_config.NumberColumn("Suggested qty", format="%.0f"),
            "order_value_usd": st.column_config.NumberColumn("Order value", format="$%.0f"),
            "days_of_cover": st.column_config.NumberColumn("Cover now (d)", format="%.1f"),
            "expected_cover_after_order": st.column_config.NumberColumn("Cover after order (d)", format="%.1f"),
            "projected_stockout_date": st.column_config.DateColumn("Projected stockout"),
            "review_status": st.column_config.SelectboxColumn("Planner review", options=REVIEW_STATUSES, required=True),
            "note": st.column_config.TextColumn("Planner note", max_chars=200),
        })
    b1, b2, b3 = st.columns([1, 1, 3])
    reviewer = b3.text_input("Reviewer", value="planner", max_chars=40, label_visibility="collapsed",
                             placeholder="Reviewer name")
    if b1.button("Save review decisions", type="primary"):
        before = view[cols].reset_index(drop=True)
        changed = edited[(edited["review_status"] != before["review_status"])
                         | (edited["note"].fillna("") != before["note"].fillna(""))]
        if changed.empty:
            st.info("No review changes to save.")
        else:
            save = pd.DataFrame({"sku": changed["sku"], "location_id": changed["location_id"],
                                 "recommended_qty": changed["suggested_order_qty"], "status": changed["review_status"],
                                 "note": changed["note"], "reviewed_by": reviewer.strip() or "planner",
                                 "reviewed_at": datetime.now(timezone.utc)})
            db.upsert(cursor(ctx.con), "planner_review", save, ["sku", "location_id"])
            st.success(f"Saved {len(save)} review decision(s). No purchase orders were created.")
            st.rerun()
    b2.download_button("Download CSV", view[cols].to_csv(index=False).encode("utf-8"),
                       file_name=f"replenishment_suggestions_{ctx.as_of}.csv", mime="text/csv")
