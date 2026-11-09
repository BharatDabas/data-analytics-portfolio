"""B. Product & Category Intelligence."""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics import kpis
from dashboards import theme
from dashboards.data import Context
from dashboards.pages.common import dataframe, gbp, header, money_col, pct_col, series_color


def render(ctx: Context) -> None:
    header(ctx, "Product & Category Intelligence",
           "Contribution, margin, discount dependency and returns by category, brand and product")
    lines = ctx.window("lines")
    if lines[~lines["is_cancelled"]].empty:
        theme.empty_state("No sales match the selected filters.")
        return
    cats = kpis.product_performance(lines, "category")
    cat_colors = series_color(list(ctx.frames["products"]["category"].unique()))

    c1, c2 = st.columns([1, 1])
    with c1:
        theme.section("Category ranking", "Net revenue (£) with share of total")
        d = cats.sort_values("net_revenue")
        fig = go.Figure(go.Bar(x=d["net_revenue"], y=d["category"], orientation="h",
                               marker_color=[cat_colors[c] for c in d["category"]],
                               text=[f"{gbp(v)} · {s:.0%}" for v, s in zip(d["net_revenue"], d["revenue_share"])],
                               textposition="auto", hovertemplate="%{y}: £%{x:,.0f}<extra></extra>"))
        fig.update_layout(hovermode="closest")
        st.plotly_chart(theme.style_fig(fig, 320, x_title="Net revenue (£)"), width="stretch")
    with c2:
        theme.section("Margin vs discount dependency",
                      "Gross margin % against share of sales sold on discount; bubble size = net revenue")
        d = cats.dropna(subset=["gross_margin_pct"])
        fig = go.Figure()
        for _, r in d.iterrows():
            fig.add_scatter(x=[r["discounted_sales_share"]], y=[r["gross_margin_pct"]], mode="markers+text",
                            name=r["category"], text=[r["category"]], textposition="top center",
                            marker=dict(size=max(10, np.sqrt(r["net_revenue"]) / 12), color=cat_colors[r["category"]],
                                        line=dict(color="white", width=2)),
                            hovertemplate=(f"<b>{r['category']}</b><br>Discounted share %{{x:.1%}}<br>"
                                           f"Gross margin %{{y:.1%}}<br>Net revenue {gbp(r['net_revenue'])}<extra></extra>"))
        fig.update_layout(showlegend=False, hovermode="closest")
        fig.update_xaxes(tickformat=".0%")
        fig.update_yaxes(tickformat=".0%")
        st.plotly_chart(theme.style_fig(fig, 320, x_title="Share of net sales sold on discount",
                                        y_title="Gross margin %"), width="stretch")

    tab_cat, tab_brand, tab_prod = st.tabs(["Categories", "Brands", "Products"])
    cols = {
        "net_revenue": money_col("Net revenue"), "gross_sales": money_col("Gross sales"),
        "discounts": money_col("Discounts"), "refunds": money_col("Refunds"),
        "gross_profit": money_col("Gross profit", "Blank when no reliable product cost exists"),
        "gross_margin_pct": pct_col("Gross margin %"), "revenue_share": pct_col("Share of revenue"),
        "discounted_sales_share": pct_col("Discount dependency", "Share of net sales sold with a discount"),
        "return_rate": pct_col("Return rate (units)"), "units": st.column_config.NumberColumn("Units", format="localized"),
    }
    show = ["net_revenue", "revenue_share", "gross_sales", "discounts", "refunds", "units", "gross_profit",
            "gross_margin_pct", "discounted_sales_share", "return_rate"]
    with tab_cat:
        dataframe(_round(cats[["category"] + show]), column_config=cols)
    with tab_brand:
        dataframe(_round(kpis.product_performance(lines, "brand")[["brand"] + show]), column_config=cols)
    with tab_prod:
        prods = kpis.product_performance(lines, "product_id").merge(
            ctx.frames["products"][["product_id", "sku", "product_name", "category", "brand", "lifecycle_status",
                                    "has_reliable_cost"]], on="product_id")
        prods = prods.merge(_weeks_of_cover(ctx), on="product_id", how="left")
        prods["rank"] = np.arange(1, len(prods) + 1)
        cols_p = dict(cols, weeks_of_cover=st.column_config.NumberColumn(
            "Weeks of cover", format="%.1f", help="Latest available stock ÷ average weekly units sold over the last 8 weeks"),
            has_reliable_cost=st.column_config.CheckboxColumn("Cost data"))
        dataframe(_round(prods[["rank", "sku", "product_name", "category", "brand", "lifecycle_status"] + show
                               + ["weeks_of_cover", "has_reliable_cost"]]), column_config=cols_p, height=420)

    c3, c4 = st.columns(2)
    prods_all = kpis.product_performance(lines, "product_name")
    with c3:
        theme.section("Top 10 products by contribution", "Net revenue (£); single series - ranked")
        d = prods_all.head(10).iloc[::-1]
        fig = go.Figure(go.Bar(x=d["net_revenue"], y=d["product_name"], orientation="h", marker_color=theme.SERIES[0],
                               hovertemplate="%{y}: £%{x:,.0f}<extra></extra>"))
        fig.update_layout(hovermode="closest")
        st.plotly_chart(theme.style_fig(fig, 340, x_title="Net revenue (£)"), width="stretch")
    with c4:
        theme.section("Highest return rates", "Products with at least 30 units sold; returned units ÷ units sold")
        d = prods_all[prods_all["units"] >= 30].nlargest(10, "return_rate").iloc[::-1]
        if d.empty:
            theme.empty_state("No product has enough volume in this window.")
        else:
            fig = go.Figure(go.Bar(x=d["return_rate"], y=d["product_name"], orientation="h",
                                   marker_color=theme.SERIES[1],
                                   customdata=np.c_[d["units"], d["returned_units"]],
                                   hovertemplate="%{y}: %{x:.1%} (%{customdata[1]:,.0f} of %{customdata[0]:,.0f} units)<extra></extra>"))
            fig.update_xaxes(tickformat=".0%")
            fig.update_layout(hovermode="closest")
            st.plotly_chart(theme.style_fig(fig, 340, x_title="Return rate (units)"), width="stretch")
    theme.note("Discount dependency is descriptive: a high share of discounted sales does not prove that discounts "
               "<i>caused</i> the sales. Promotional uplift requires a valid baseline or experiment.")


def _round(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in ["net_revenue", "gross_sales", "discounts", "refunds", "gross_profit"]:
        if c in out:
            out[c] = out[c].round(0)
    return out


def _weeks_of_cover(ctx: Context) -> pd.DataFrame:
    inv = ctx.frames["inventory"]
    if inv.empty:
        return pd.DataFrame(columns=["product_id", "weeks_of_cover"])
    latest = inv[inv["snapshot_date"] == inv["snapshot_date"].max()].groupby("product_id")["available"].sum()
    recent = ctx.frames["lines"]
    recent = recent[(~recent["is_cancelled"]) & (recent["order_date"] > ctx.as_of - timedelta(weeks=8))]
    weekly = recent.groupby("product_id")["quantity"].sum() / 8
    cover = (latest / weekly.reindex(latest.index)).replace([np.inf, -np.inf], np.nan)
    return cover.rename("weeks_of_cover").reset_index()
