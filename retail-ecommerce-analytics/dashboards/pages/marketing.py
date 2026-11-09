"""E. Marketing Performance."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics import marketing as mk
from dashboards import theme
from dashboards.data import Context
from dashboards.pages.common import complete_months, dataframe, gbp, header, money_col, pct_col, series_color


def render(ctx: Context) -> None:
    header(ctx, "Marketing Performance", "Spend, attributed outcomes, CAC and ROAS by channel and campaign")
    win = ctx.rules["attribution"]["window_days"]
    m = ctx.frames["marketing"]
    m = m[(m["activity_date"] >= ctx.start) & (m["activity_date"] <= ctx.end)]
    if m.empty:
        theme.empty_state("No marketing activity in the selected window.")
        return
    orders = ctx.frames["orders"]
    new_all = int(orders[(orders["order_date"] >= ctx.start) & (orders["order_date"] <= ctx.end)
                         & orders["is_new_customer_order"]].shape[0])
    t = mk.totals(m, new_all)
    theme.note(f"<b>Attribution:</b> last click within {win} days before the order (100% credit to that campaign); "
               "orders with no qualifying click are unattributed. ROAS uses attributed <i>net</i> revenue. "
               "These are observational, rule-based figures - they show association, not the incremental effect of spend. "
               "Sales-channel and region filters do not apply on this page (spend is not recorded at that grain).")
    theme.kpi_row([
        theme.kpi_card("Marketing spend", gbp(t["spend"]), f"{gbp(t['acquisition_spend'])} acquisition objective"),
        theme.kpi_card("Attributed net revenue", gbp(t["attributed_revenue"]), f"{t['attributed_orders']:,.0f} orders"),
        theme.kpi_card("ROAS", f"{t['roas']:.2f}×" if t["roas"] == t["roas"] else "—", "attributed revenue ÷ spend"),
        theme.kpi_card("CAC (attributed)", theme.fmt_money(t["attributed_cac"], "£", 2),
                       f"{t['attributed_new_customers']:,.0f} attributed new customers"),
        theme.kpi_card("CAC (blended)", theme.fmt_money(t["blended_cac"], "£", 2),
                       f"acquisition spend ÷ all {new_all:,} new customers"),
        theme.kpi_card("Conversion rate", theme.fmt_pct(t["conversion_rate"], 2), "attributed orders ÷ clicks"),
    ])

    ch = mk.performance(m, ["marketing_channel"])
    colors = series_color(list(ctx.frames["marketing"]["marketing_channel"].unique()))
    c1, c2 = st.columns(2)
    with c1:
        theme.section("Spend vs attributed net revenue by channel", "£ in the selected window")
        d = ch.sort_values("spend_gbp", ascending=False)
        fig = go.Figure()
        fig.add_bar(x=d["marketing_channel"], y=d["spend_gbp"], name="Spend", marker_color=theme.SERIES[1],
                    hovertemplate="%{x} spend: £%{y:,.0f}<extra></extra>")
        fig.add_bar(x=d["marketing_channel"], y=d["attributed_net_revenue_gbp"], name="Attributed net revenue",
                    marker_color=theme.SERIES[0], hovertemplate="%{x} attributed: £%{y:,.0f}<extra></extra>")
        fig.update_layout(barmode="group", hovermode="closest", bargroupgap=0.08)
        st.plotly_chart(theme.style_fig(fig, 320, y_title="£"), width="stretch")
    with c2:
        theme.section("ROAS and CAC by channel", "Two measures with different units - shown as separate panels")
        d = ch.sort_values("marketing_channel")
        s1, s2 = st.columns(2)
        with s1:
            fig = go.Figure(go.Bar(x=d["roas"], y=d["marketing_channel"], orientation="h",
                                   marker_color=[colors[c] for c in d["marketing_channel"]],
                                   hovertemplate="%{y}: %{x:.2f}×<extra></extra>"))
            fig.update_layout(hovermode="closest", title="ROAS (×)")
            st.plotly_chart(theme.style_fig(fig, 300), width="stretch")
        with s2:
            acq = d[d["acquisition_spend_gbp"] > 0]
            fig = go.Figure(go.Bar(x=acq["cac_gbp"], y=acq["marketing_channel"], orientation="h",
                                   marker_color=[colors[c] for c in acq["marketing_channel"]],
                                   hovertemplate="%{y}: £%{x:,.2f}<extra></extra>"))
            fig.update_layout(hovermode="closest", title="Attributed CAC (£)")
            st.plotly_chart(theme.style_fig(fig, 300), width="stretch")

    theme.section("Acquisition trend", "Monthly new customers attributed to each acquisition channel (complete months)")
    mm = m[m["objective"] == "acquisition"].assign(
        month=lambda x: pd.to_datetime(x["activity_date"]).dt.to_period("M").dt.start_time)
    g = complete_months(mm.groupby(["month", "marketing_channel"])["attributed_new_customers"].sum().reset_index(),
                        "month", ctx.as_of)
    fig = go.Figure()
    for c in sorted(g["marketing_channel"].unique()):
        d = g[g["marketing_channel"] == c]
        fig.add_scatter(x=d["month"], y=d["attributed_new_customers"], name=c, mode="lines+markers",
                        line=dict(color=colors[c], width=2), marker=dict(size=7), hovertemplate="%{y:,.0f}")
    st.plotly_chart(theme.style_fig(fig, 300, y_title="New customers"), width="stretch")

    tab1, tab2 = st.tabs(["Channel performance", "Campaign performance"])
    cfg = {"spend_gbp": money_col("Spend"), "attributed_net_revenue_gbp": money_col("Attributed revenue"),
           "acquisition_spend_gbp": money_col("Acquisition spend"), "cac_gbp": money_col("CAC"),
           "cpc_gbp": st.column_config.NumberColumn("CPC (£)", format="%.2f"),
           "roas": st.column_config.NumberColumn("ROAS (×)", format="%.2f"),
           "ctr": pct_col("CTR"), "conversion_rate": pct_col("Conversion rate"),
           "impressions": st.column_config.NumberColumn("Impressions", format="localized"),
           "clicks": st.column_config.NumberColumn("Clicks", format="localized"),
           "marketing_channel": "Channel", "campaign_name": "Campaign", "objective": "Objective",
           "attributed_orders": st.column_config.NumberColumn("Attributed orders", format="localized"),
           "attributed_new_customers": st.column_config.NumberColumn("Attributed new customers", format="localized")}
    cols = ["spend_gbp", "impressions", "clicks", "ctr", "cpc_gbp", "attributed_orders", "conversion_rate",
            "attributed_new_customers", "attributed_net_revenue_gbp", "roas", "cac_gbp"]
    with tab1:
        dataframe(_r(ch[["marketing_channel"] + cols]), column_config=cfg)
    with tab2:
        camp = mk.performance(m, ["campaign_id", "campaign_name", "marketing_channel", "objective"])
        dataframe(_r(camp.sort_values("spend_gbp", ascending=False)[["campaign_name", "marketing_channel", "objective"] + cols]),
                  column_config=cfg)
    theme.note("CAC is reported only for acquisition-objective campaigns; retention (email) campaigns are measured "
               "on ROAS. Blended CAC includes organic/direct new customers in the denominator.")


def _r(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for c in [c for c in ["spend_gbp", "attributed_net_revenue_gbp", "acquisition_spend_gbp"] if c in out]:
        out[c] = out[c].round(0)
    out["cac_gbp"] = out["cac_gbp"].round(2)
    return out
