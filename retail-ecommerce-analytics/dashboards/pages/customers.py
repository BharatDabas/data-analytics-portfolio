"""C. Customer Intelligence."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics import customers as cust
from analytics import kpis
from dashboards import theme
from dashboards.data import Context
from dashboards.pages.common import complete_months, dataframe, gbp, header, money_col, pct_col, series_color

SPEND_BANDS = [0, 50, 100, 250, 500, 1000, np.inf]
SPEND_LABELS = ["< £50", "£50–100", "£100–250", "£250–500", "£500–1k", "£1k+"]


def render(ctx: Context) -> None:
    header(ctx, "Customer Intelligence", "RFM segments, new vs returning mix, spending patterns and geography")
    orders = ctx.window("orders")
    eligible = orders[~orders["is_cancelled"]]
    if eligible.empty:
        theme.empty_state("No customers purchased in the selected window.")
        return
    rf = cust.rfm(orders, ctx.end, bins=int(ctx.rules["rfm"]["bins"]))
    k = kpis.commercial_kpis(ctx.window("lines"), orders)
    new_c = int(eligible["is_new_customer_order"].sum())
    theme.kpi_row([
        theme.kpi_card("Active customers", theme.fmt_num(k["customers"]), "≥1 eligible order in window"),
        theme.kpi_card("New customers", theme.fmt_num(new_c), "first-ever eligible order in window"),
        theme.kpi_card("Repeat purchase rate", theme.fmt_pct(kpis.repeat_purchase_rate(orders)),
                       "active customers with ≥2 orders in window"),
        theme.kpi_card("Revenue per customer", theme.fmt_money(kpis.safe_div(k["net_revenue"], k["customers"]), "£", 2),
                       "observed, net revenue ÷ active customers"),
    ])

    theme.section("RFM segmentation",
                  f"Recency, frequency and monetary quintile scores (1–5) as of {ctx.end:%d %b %Y}, "
                  "using orders in the selected window")
    seg = cust.segment_summary(rf)
    c1, c2 = st.columns([1, 1.2])
    with c1:
        d = seg.iloc[::-1]
        fig = go.Figure()
        fig.add_bar(y=d["segment"], x=d["customer_share"], name="Share of customers", orientation="h",
                    marker_color=theme.SERIES[0], hovertemplate="%{y}: %{x:.1%} of customers<extra></extra>")
        fig.add_bar(y=d["segment"], x=d["revenue_share"], name="Share of revenue", orientation="h",
                    marker_color=theme.SERIES[1], hovertemplate="%{y}: %{x:.1%} of revenue<extra></extra>")
        fig.update_xaxes(tickformat=".0%")
        fig.update_layout(barmode="group", hovermode="closest", bargap=0.3, bargroupgap=0.08)
        st.plotly_chart(theme.style_fig(fig, 360, x_title="Share (%)"), width="stretch")
    with c2:
        out = seg.assign(revenue=seg["revenue"].round(0), avg_value=seg["avg_value"].round(2),
                         avg_orders=seg["avg_orders"].round(2), avg_recency_days=seg["avg_recency_days"].round(0))
        dataframe(out[["segment", "definition", "customers", "customer_share", "revenue", "revenue_share",
                       "avg_orders", "avg_recency_days", "avg_value"]],
                  column_config={"customer_share": pct_col("Customers %"), "revenue_share": pct_col("Revenue %"),
                                 "revenue": money_col("Revenue"), "avg_value": money_col("Avg value"),
                                 "avg_recency_days": st.column_config.NumberColumn("Avg recency (days)")},
                  height=360)
    theme.note("Segment rules are explicit (see <code>analytics/customers.py</code>): e.g. <b>Champions</b> = R≥4 and F≥4; "
               "<b>At risk</b> = R≤2 and F≥3. Customers with a single order always receive F=1.")

    c3, c4 = st.columns(2)
    with c3:
        theme.section("New vs returning customers", "Monthly net revenue (£) by first vs repeat orders, complete months")
        nvr = complete_months(cust.new_vs_returning(orders), "period", ctx.as_of)
        fig = go.Figure()
        for i, t in enumerate(["New", "Returning"]):
            d = nvr[nvr["customer_type"] == t]
            fig.add_bar(x=d["period"], y=d["net_revenue"], name=t, marker_color=theme.SERIES[i],
                        customdata=d["customers"], hovertemplate=f"{t}: £%{{y:,.0f}} · %{{customdata:,}} customers<extra></extra>")
        fig.update_layout(barmode="stack")
        st.plotly_chart(theme.style_fig(fig, 320, y_title="Net revenue (£)"), width="stretch")
    with c4:
        theme.section("Customer spending bands", "Customers by net revenue in the window")
        spend = eligible.groupby("customer_id")["net_revenue_gbp"].sum()
        bands = pd.cut(spend, SPEND_BANDS, labels=SPEND_LABELS, right=False).value_counts().reindex(SPEND_LABELS)
        fig = go.Figure(go.Bar(x=SPEND_LABELS, y=bands.values, marker_color=theme.SERIES[0],
                               hovertemplate="%{x}: %{y:,} customers<extra></extra>"))
        fig.update_layout(hovermode="closest")
        st.plotly_chart(theme.style_fig(fig, 320, y_title="Customers"), width="stretch")

    c5, c6 = st.columns(2)
    with c5:
        theme.section("Purchase frequency", "Customers by number of eligible orders in the window")
        freq = eligible.groupby("customer_id").size().clip(upper=6).value_counts().sort_index()
        labels = [f"{i}" if i < 6 else "6+" for i in freq.index]
        fig = go.Figure(go.Bar(x=labels, y=freq.values, marker_color=theme.SERIES[0],
                               hovertemplate="%{x} orders: %{y:,} customers<extra></extra>"))
        fig.update_layout(hovermode="closest")
        st.plotly_chart(theme.style_fig(fig, 300, x_title="Orders per customer", y_title="Customers"), width="stretch")
    with c6:
        theme.section("Geography", "Net revenue and customers by country")
        geo = eligible.groupby(["country", "region"]).agg(net_revenue=("net_revenue_gbp", "sum"),
                                                           customers=("customer_id", "nunique"),
                                                           orders=("order_id", "count")).reset_index()
        geo["revenue_per_customer"] = geo["net_revenue"] / geo["customers"]
        geo = geo.sort_values("net_revenue", ascending=False)
        colors = series_color(list(ctx.frames["orders"]["region"].unique()))
        d = geo.iloc[::-1]
        fig = go.Figure(go.Bar(x=d["net_revenue"], y=d["country"], orientation="h",
                               marker_color=[colors[r] for r in d["region"]], customdata=np.c_[d["customers"], d["region"]],
                               hovertemplate="%{y} (%{customdata[1]}): £%{x:,.0f} · %{customdata[0]:,} customers<extra></extra>"))
        fig.update_layout(hovermode="closest")
        st.plotly_chart(theme.style_fig(fig, 300, x_title="Net revenue (£)"), width="stretch")

    theme.section("Observed customer value by acquisition channel",
                  "Cumulative net revenue to date per customer (all history, OBSERVED - not modelled)")
    cv = ctx.frames["customers"]
    cv = cv[cv["orders"] > 0]
    if ctx.regions:
        cv = cv[cv["region"].isin(ctx.regions)]
    acq = cv.groupby("acquisition_channel").agg(customers=("customer_id", "count"),
                                                avg_orders=("orders", "mean"),
                                                avg_net_revenue=("net_revenue_gbp", "mean"),
                                                avg_gross_profit=("gross_profit_gbp", "mean")).reset_index()
    dataframe(acq.round(2).sort_values("avg_net_revenue", ascending=False),
              column_config={"avg_net_revenue": money_col("Avg observed net revenue"),
                             "avg_gross_profit": money_col("Avg observed gross profit"),
                             "avg_orders": st.column_config.NumberColumn("Avg orders", format="%.2f")})
