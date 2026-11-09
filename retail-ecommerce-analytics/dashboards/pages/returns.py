"""F. Returns & Operations."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics import kpis
from dashboards import theme
from dashboards.data import Context
from dashboards.pages.common import complete_months, dataframe, gbp, header, money_col, pct_col, series_color


def render(ctx: Context) -> None:
    header(ctx, "Returns & Operations", "Returns, refunds, cancellations and fulfilment indicators (by order date)")
    lines, orders = ctx.window("lines"), ctx.window("orders")
    if orders.empty:
        theme.empty_state("No orders match the selected filters.")
        return
    k = kpis.commercial_kpis(lines, orders)
    rets = ctx.window("returns")  # filtered on the ORIGINAL order date, consistent with refund attribution
    ful = ctx.window("fulfilment")
    on_time = ful.loc[ful["is_delivered"], "delivered_on_time"].mean() if ful["is_delivered"].any() else float("nan")
    theme.kpi_row([
        theme.kpi_card("Return rate (units)", theme.fmt_pct(k["return_rate_units"]), "returned ÷ sold units"),
        theme.kpi_card("Return rate (orders)", theme.fmt_pct(k["return_rate_orders"]), "orders with ≥1 return"),
        theme.kpi_card("Refund value", gbp(k["refunds"]), f"{theme.fmt_pct(kpis.safe_div(k['refunds'], k['gross_sales']))} of gross sales"),
        theme.kpi_card("Cancellation rate", theme.fmt_pct(k["cancellation_rate"]),
                       f"{int(orders['is_cancelled'].sum()):,} cancelled orders"),
        theme.kpi_card("Delivered on time", theme.fmt_pct(on_time), "delivered on/before promised date"),
        theme.kpi_card("Median days to ship", theme.fmt_num(ful["days_to_ship"].median(), 1), "order → dispatch"),
    ])

    c1, c2 = st.columns(2)
    with c1:
        theme.section("Return reasons", "Returned units by stated reason")
        if rets.empty:
            theme.empty_state("No returns in the selected window.")
        else:
            g = rets.groupby("reason").agg(units=("quantity", "sum"), refund=("refund_gbp", "sum")).sort_values("units")
            fig = go.Figure(go.Bar(x=g["units"], y=g.index, orientation="h", marker_color=theme.SERIES[0],
                                   customdata=g["refund"], text=[f"{u / g['units'].sum():.0%}" for u in g["units"]],
                                   textposition="auto",
                                   hovertemplate="%{y}: %{x:,} units · £%{customdata:,.0f} refunded<extra></extra>"))
            fig.update_layout(hovermode="closest")
            st.plotly_chart(theme.style_fig(fig, 300, x_title="Returned units"), width="stretch")
    with c2:
        theme.section("Return rate by category", "Returned units ÷ units sold")
        sold = lines[~lines["is_cancelled"]].groupby("category").agg(units=("quantity", "sum"),
                                                                     returned=("returned_quantity", "sum"))
        sold["rate"] = sold["returned"] / sold["units"]
        sold = sold.sort_values("rate")
        colors = series_color(list(ctx.frames["products"]["category"].unique()))
        fig = go.Figure(go.Bar(x=sold["rate"], y=sold.index, orientation="h",
                               marker_color=[colors[c] for c in sold.index],
                               hovertemplate="%{y}: %{x:.1%}<extra></extra>"))
        fig.update_xaxes(tickformat=".0%")
        fig.update_layout(hovermode="closest")
        st.plotly_chart(theme.style_fig(fig, 300, x_title="Return rate (units)"), width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        theme.section("Refunds and cancellations over time", "Monthly refund value (£) by original order month, complete months")
        o = orders.assign(month=pd.to_datetime(orders["order_date"]).dt.to_period("M").dt.start_time)
        g = o.groupby("month").agg(refunds=("refund_gbp", "sum"), cancelled=("is_cancelled", "sum"),
                                   orders=("order_id", "count")).reset_index()
        g = complete_months(g, "month", ctx.as_of)
        fig = go.Figure(go.Bar(x=g["month"], y=g["refunds"], marker_color=theme.SERIES[1], name="Refunds",
                               customdata=g["cancelled"] / g["orders"],
                               hovertemplate="£%{y:,.0f} refunded · %{customdata:.1%} of orders cancelled<extra></extra>"))
        fig.update_layout(hovermode="closest")
        st.plotly_chart(theme.style_fig(fig, 300, y_title="Refund value (£)"), width="stretch")
    with c4:
        theme.section("Delivery performance by region", "Share delivered on time and median days in transit")
        f = ful[ful["is_delivered"]].groupby("region").agg(on_time=("delivered_on_time", "mean"),
                                                           transit=("days_in_transit", "median"),
                                                           orders=("order_id", "count")).reset_index()
        f["status"] = f["on_time"].apply(lambda v: theme.status_label(
            "good" if v >= 0.95 else "warning" if v >= 0.90 else "critical",
            "On target" if v >= 0.95 else "Watch" if v >= 0.90 else "Below target"))
        dataframe(f.round(2), column_config={"on_time": pct_col("Delivered on time"),
                                             "transit": st.column_config.NumberColumn("Median transit (days)", format="%.1f"),
                                             "status": st.column_config.TextColumn("Status (target 95%)")})
        theme.note("Fulfilment indicators come from order shipping/delivery timestamps in the synthetic source. "
                   "Orders not yet delivered are excluded from on-time rates.")

    theme.section("Largest refunds by product", "Top 15 products by refund value in the window")
    if not rets.empty:
        top = rets.groupby(["product_name", "category"]).agg(refund=("refund_gbp", "sum"), units=("quantity", "sum"),
                                                             returns=("return_id", "count")).reset_index()
        dataframe(top.nlargest(15, "refund").round(0), column_config={"refund": money_col("Refund value")})
