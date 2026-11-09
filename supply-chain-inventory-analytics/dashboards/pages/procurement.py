"""E. Procurement & Purchase Order Tracking."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboards import theme as t
from dashboards.context import Ctx, show_table

STATUS_LEVEL = {"Open - not yet due": "good", "Partial - not yet due": "warning", "Open - overdue": "critical",
                "Partial - overdue": "critical", "Received on time": "good", "Received late": "serious",
                "Cancelled": "warning"}
# Chart groups: status colours only where the group means good/bad; neutral greys otherwise.
CHART_GROUP = {"Received on time": "On time", "Received late": "Late", "Open - overdue": "Overdue",
               "Partial - overdue": "Overdue", "Open - not yet due": "Not yet due",
               "Partial - not yet due": "Not yet due", "Cancelled": "Cancelled"}
GROUP_STYLE = {"On time": (t.STATUS["good"], t.status_label("good", "On time")),
               "Late": (t.STATUS["serious"], t.status_label("serious", "Late")),
               "Overdue": (t.STATUS["critical"], t.status_label("critical", "Overdue")),
               "Not yet due": ("#b9b8b2", "○ Not yet due"), "Cancelled": ("#dcdbd5", "× Cancelled")}


def render(ctx: Ctx) -> None:
    ctx.header("Procurement & PO Tracking", "Open purchase orders, overdue and partial receipts, promised vs actual dates")
    po = ctx.q("""SELECT l.*, s.supplier_name, p.product_name, p.category
                  FROM v_po_line_status l JOIN dim_supplier s USING (supplier_id) JOIN dim_product p USING (sku)
                  WHERE list_contains($locs, l.location_id) AND list_contains($cats, p.category)""")
    if po.empty:
        t.empty_state("No purchase-order lines for the selected filters.")
        return
    open_ = po[po["open_qty"] > 0]
    t.kpi_row([
        t.kpi_card("Open PO lines", f"{len(open_):,}", f"{open_['po_number'].nunique():,} purchase orders"),
        t.kpi_card("Open PO value", t.fmt_money((open_["open_qty"] * open_["unit_cost_usd"]).sum()), "outstanding qty × cost"),
        t.kpi_card("Overdue lines", f"{int(po['is_overdue'].sum()):,}", "promised date passed, not fully received"),
        t.kpi_card("Partially received", f"{int((po['status'] == 'Partially Received').sum()):,}", "open lines with ≥1 receipt"),
        t.kpi_card("Lines with split receipts", f"{int((po['receipt_count'] > 1).sum()):,}", "all history"),
    ])

    t.section("Open and overdue purchase-order lines", "Expected deliveries; overdue rows first")
    view = open_.assign(
        status=[t.status_label(STATUS_LEVEL[s], s) for s in open_["delivery_status"]],
        open_value=open_["open_qty"] * open_["unit_cost_usd"]).sort_values(["is_overdue", "promised_date"],
                                                                            ascending=[False, True])
    show_table(view[["po_number", "po_line", "supplier_name", "sku", "product_name", "location_id", "order_date",
                     "promised_date", "ordered_qty", "received_qty", "open_qty", "open_value", "days_late", "status"]],
               po_number="PO", po_line="Line", supplier_name="Supplier", sku="SKU", product_name="Product",
               location_id="Location", order_date=st.column_config.DateColumn("Ordered"),
               promised_date=st.column_config.DateColumn("Promised"),
               ordered_qty=st.column_config.NumberColumn("Ordered qty", format="%.0f"),
               received_qty=st.column_config.NumberColumn("Received", format="%.0f"),
               open_qty=st.column_config.NumberColumn("Outstanding", format="%.0f"),
               open_value=st.column_config.NumberColumn("Outstanding value", format="$%.0f"),
               days_late=st.column_config.NumberColumn("Days late"), status="Delivery status")

    c1, c2 = st.columns(2)
    with c1:
        t.section("Delivery status by supplier", "PO lines by lifecycle status (all history)")
        g = po.assign(grp=po["delivery_status"].map(CHART_GROUP)).groupby(["supplier_name", "grp"]).size()             .rename("n").reset_index()
        fig = go.Figure()
        for grp, (colour, label) in GROUP_STYLE.items():
            d = g[g["grp"] == grp]
            if not d.empty:
                fig.add_bar(y=d["supplier_name"], x=d["n"], name=label, orientation="h", marker_color=colour,
                            hovertemplate=grp + ": %{x} lines<extra></extra>")
        fig.update_layout(barmode="stack", hovermode="closest", legend=dict(font=dict(size=10)))
        st.plotly_chart(t.style_fig(fig, 380, x_title="PO lines"), width="stretch")
    with c2:
        t.section("Promised vs actual first receipt", "Days early (−) or late (+), received lines")
        rec = po[po["first_receipt_date"].notna()]
        fig = go.Figure(go.Histogram(x=rec["days_late"], marker_color=t.SERIES[0], xbins=dict(size=1),
                                     hovertemplate="%{x} days: %{y} lines<extra></extra>"))
        fig.add_vline(x=0, line_width=1, line_color=t.INK_2)
        fig.update_layout(hovermode="closest", bargap=0.05)
        st.plotly_chart(t.style_fig(fig, 380, x_title="Days relative to promised date", y_title="PO lines"),
                        width="stretch")

    t.section("Receipt history", "Goods-receipt lines with inspection outcome (latest 500)")
    rc = ctx.q("""SELECT r.receipt_id, r.po_number, r.po_line, r.receipt_date, l.promised_date, s.supplier_name, l.sku,
                         l.location_id, r.received_qty, r.accepted_qty, r.rejected_qty, r.source_uom, r.inspection_outcome
                  FROM fact_receipt_line r JOIN fact_purchase_order_line l USING (po_number, po_line)
                  JOIN dim_supplier s USING (supplier_id) JOIN dim_product p ON p.sku = l.sku
                  WHERE list_contains($locs, l.location_id) AND list_contains($cats, p.category)
                  ORDER BY r.receipt_ts DESC LIMIT 500""")
    show_table(rc, height=320, receipt_id="Receipt", po_number="PO", po_line="Line", supplier_name="Supplier",
               sku="SKU", location_id="Location", receipt_date=st.column_config.DateColumn("Received"),
               promised_date=st.column_config.DateColumn("Promised"),
               received_qty=st.column_config.NumberColumn("Received (EA)", format="%.0f"),
               accepted_qty=st.column_config.NumberColumn("Accepted (EA)", format="%.0f"),
               rejected_qty=st.column_config.NumberColumn("Rejected (EA)", format="%.0f"),
               source_uom="Source UoM", inspection_outcome="Inspection")
