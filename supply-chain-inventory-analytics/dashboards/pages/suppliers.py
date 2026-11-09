"""D. Supplier Performance - OTIF, OTD, lead time and variability, quality, spend and scorecards."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboards import theme as t
from dashboards.context import Ctx, show_table


def score(otif: float, fill: float, defect: float) -> float:
    """Illustrative composite (0-100): 50% OTIF, 25% fill rate, 25% quality (10% defects => 0)."""
    vals = [0 if v != v else v for v in (otif, fill)]
    q = 1 - min((defect if defect == defect else 0) * 10, 1)
    return 100 * (0.5 * vals[0] + 0.25 * vals[1] + 0.25 * q)


def rating(s: float) -> str:
    return t.status_label("good", "Preferred") if s >= 85 else (
        t.status_label("warning", "Monitor") if s >= 70 else t.status_label("critical", "At risk"))


def render(ctx: Ctx) -> None:
    ctx.header("Supplier Performance", "Delivery reliability, lead-time behaviour, quality and spend per supplier")
    # Supplier KPIs recomputed from PO lines so that location/category filters apply.
    sc = ctx.q("""
        SELECT s.supplier_id, s.supplier_name, s.region, s.currency, s.approved_status, s.nominal_lead_time_days,
               count(*) FILTER (WHERE l.is_due) due_lines,
               avg(CASE WHEN l.on_time THEN 1.0 ELSE 0 END) FILTER (WHERE l.is_due) otd,
               avg(CASE WHEN l.otif THEN 1.0 ELSE 0 END) FILTER (WHERE l.is_due) otif,
               sum(l.accepted_qty) FILTER (WHERE l.is_due) / nullif(sum(l.ordered_qty) FILTER (WHERE l.is_due), 0) fill_rate,
               sum(l.rejected_qty) / nullif(sum(l.received_qty), 0) defect_rate,
               avg(l.lead_time_days) lt_mean, stddev_samp(l.lead_time_days) lt_std,
               quantile_cont(l.lead_time_days, 0.9) lt_p90,
               sum(l.line_value_usd) FILTER (WHERE l.status <> 'Cancelled' AND l.order_date > $as_of::DATE - 365) spend_12m,
               sum((l.unit_cost_usd - p.unit_cost_usd) * l.ordered_qty) FILTER (WHERE l.status <> 'Cancelled')
                 / nullif(sum(p.unit_cost_usd * l.ordered_qty) FILTER (WHERE l.status <> 'Cancelled'), 0) price_var
        FROM dim_supplier s JOIN v_po_line_status l USING (supplier_id) JOIN dim_product p USING (sku)
        WHERE list_contains($locs, l.location_id) AND list_contains($cats, p.category)
        GROUP BY ALL ORDER BY s.supplier_id""")
    if sc.empty:
        t.empty_state("No purchase-order lines for the selected filters.")
        return
    sc["score"] = [score(r.otif, r.fill_rate, r.defect_rate) for r in sc.itertuples()]
    sc["rating"] = sc["score"].map(rating)
    w = sc["due_lines"]
    t.kpi_row([
        t.kpi_card("OTIF", t.fmt_pct((sc["otif"] * w).sum() / w.sum()), f"{int(w.sum()):,} due PO lines"),
        t.kpi_card("OTD", t.fmt_pct((sc["otd"] * w).sum() / w.sum()), "first receipt by promised date"),
        t.kpi_card("Avg lead time", f"{(sc['lt_mean'] * w).sum() / w.sum():.1f} days", "approval → final receipt"),
        t.kpi_card("Defect rate", t.fmt_pct((sc["defect_rate"] * w).sum() / w.sum(), 2), "rejected ÷ received qty"),
        t.kpi_card("Supplier spend (12m)", t.fmt_money(sc["spend_12m"].sum()), "PO value, USD"),
    ])

    t.section("Supplier scorecard", "Composite score = 50% OTIF + 25% fill rate + 25% quality (illustrative weighting)")
    show_table(sc[["supplier_id", "supplier_name", "region", "approved_status", "due_lines", "otif", "otd", "fill_rate",
                   "defect_rate", "lt_mean", "lt_std", "lt_p90", "nominal_lead_time_days", "spend_12m", "price_var",
                   "score", "rating"]],
               supplier_id="ID", supplier_name="Supplier", region="Region", approved_status="Status",
               due_lines=st.column_config.NumberColumn("Due lines"),
               otif=st.column_config.ProgressColumn("OTIF", min_value=0, max_value=1, format="percent"),
               otd=st.column_config.NumberColumn("OTD", format="percent"),
               fill_rate=st.column_config.NumberColumn("Fill rate", format="percent"),
               defect_rate=st.column_config.NumberColumn("Defects", format="percent"),
               lt_mean=st.column_config.NumberColumn("LT mean (d)", format="%.1f"),
               lt_std=st.column_config.NumberColumn("LT std (d)", format="%.1f"),
               lt_p90=st.column_config.NumberColumn("LT p90 (d)", format="%.0f"),
               nominal_lead_time_days=st.column_config.NumberColumn("Quoted LT (d)"),
               spend_12m=st.column_config.NumberColumn("Spend 12m", format="$%.0f"),
               price_var=st.column_config.NumberColumn("Price var.", format="percent"),
               score=st.column_config.NumberColumn("Score", format="%.0f"), rating="Rating")

    c1, c2 = st.columns(2)
    with c1:
        t.section("OTD vs OTIF by supplier", "Gap between the bars = late or short deliveries counted as on time")
        fig = go.Figure()
        fig.add_bar(y=sc["supplier_name"], x=sc["otd"], name="OTD", orientation="h", marker_color=t.SERIES[0],
                    hovertemplate="OTD %{x:.1%}<extra></extra>")
        fig.add_bar(y=sc["supplier_name"], x=sc["otif"], name="OTIF", orientation="h", marker_color=t.SERIES[1],
                    hovertemplate="OTIF %{x:.1%}<extra></extra>")
        fig.update_xaxes(tickformat=".0%", range=[0, 1])
        fig.update_layout(barmode="group", hovermode="closest")
        st.plotly_chart(t.style_fig(fig, 380, x_title="Share of due PO lines"), width="stretch")
    with c2:
        t.section("Lead-time distribution", "Approval to final receipt, closed lines; diamond = quoted lead time")
        lt = ctx.q("""SELECT s.supplier_name, l.lead_time_days FROM v_po_line_status l JOIN dim_supplier s USING (supplier_id)
                      JOIN dim_product p USING (sku) WHERE l.lead_time_days IS NOT NULL
                        AND list_contains($locs, l.location_id) AND list_contains($cats, p.category)""")
        fig = go.Figure()
        fig.add_box(y=lt["supplier_name"], x=lt["lead_time_days"], orientation="h", marker_color=t.SERIES[0],
                    line=dict(width=1.5), boxpoints=False, name="Lead time")
        fig.add_scatter(y=sc["supplier_name"], x=sc["nominal_lead_time_days"], mode="markers", name="Quoted",
                        marker=dict(symbol="diamond", size=9, color=t.SERIES[1]),
                        hovertemplate="Quoted %{x} days<extra></extra>")
        fig.update_layout(hovermode="closest")
        st.plotly_chart(t.style_fig(fig, 380, x_title="Lead time (days)"), width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        t.section("Monthly OTIF trend", "Due PO lines by promised month")
        m = ctx.q("""SELECT date_trunc('month', promised_date) mon, s.supplier_name,
                            avg(CASE WHEN otif THEN 1.0 ELSE 0 END) otif, count(*) n
                     FROM v_po_line_status l JOIN dim_supplier s USING (supplier_id) JOIN dim_product p USING (sku)
                     WHERE is_due AND list_contains($locs, l.location_id) AND list_contains($cats, p.category)
                     GROUP BY ALL HAVING count(*) >= 5 ORDER BY 1""")
        st.caption("Months with fewer than 5 due lines for a supplier are hidden.")
        pick = st.multiselect("Suppliers", sc["supplier_name"].tolist(), default=sc["supplier_name"].tolist()[:3],
                              max_selections=8)
        fig = go.Figure()
        for name in pick:
            g = m[m["supplier_name"] == name]
            fig.add_scatter(x=g["mon"], y=g["otif"], name=name, customdata=g["n"],
                            line=dict(width=2, color=t.SERIES[sc["supplier_name"].tolist().index(name) % 8]),
                            hovertemplate="%{y:.0%} (%{customdata} lines)")
        fig.update_yaxes(tickformat=".0%", range=[0, 1.05])
        st.plotly_chart(t.style_fig(fig, 320, y_title="OTIF"), width="stretch")
    with c4:
        t.section("Supplier spend, last 12 months", "Non-cancelled PO line value in USD (converted at configured FX)")
        s = sc.sort_values("spend_12m")
        fig = go.Figure(go.Bar(y=s["supplier_name"], x=s["spend_12m"], orientation="h", marker_color=t.SERIES[0],
                               text=[t.fmt_money(v) for v in s["spend_12m"]], textposition="outside",
                               hovertemplate="%{x:$,.0f}<extra></extra>"))
        fig.update_layout(hovermode="closest")
        st.plotly_chart(t.style_fig(fig, 320 + 0, x_title="Spend (USD)"), width="stretch")
    t.note("Definitions: <b>OTD</b> = first receipt on or before the promised date. <b>OTIF</b> = accepted quantity "
           "received by the promised date ≥ ordered quantity (tolerance configurable, default 0%). Partial receipts after "
           "the promised date count as on time (OTD) only if the first delivery was on time, and never as in full. "
           "<b>Lead time</b> runs from PO approval to the final receipt of fully received lines.")
