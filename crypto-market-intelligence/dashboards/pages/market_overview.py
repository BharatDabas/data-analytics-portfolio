"""A. Market Overview - watchlist, latest available prices, market context, biggest movers."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboards import theme


def render(ctx) -> None:
    theme.page_header("Market Overview", "Latest available observation per asset, market context and largest moves.",
                      ctx.chips())
    latest = ctx.q("SELECT * FROM v_latest_market WHERE source_id = ? ORDER BY latest_market_cap DESC NULLS LAST",
                   (ctx.source_id,))
    if latest.empty:
        theme.empty_state("No market observations for this source yet.")
        return

    total_cap = latest["latest_market_cap"].sum()
    total_vol = latest["latest_volume"].sum()
    risky = latest[~latest["is_stablecoin"]]
    adv = int((risky["change_1d"] > 0).sum())
    dec = int((risky["change_1d"] < 0).sum())
    prev_cap = ctx.q("""SELECT SUM(market_cap) AS cap FROM v_market_daily WHERE source_id = ?
                        AND price_date = (SELECT MAX(price_date) - INTERVAL 1 DAY FROM v_market_daily WHERE source_id = ?)""",
                     (ctx.source_id, ctx.source_id))["cap"].iloc[0]
    cap_change = total_cap / prev_cap - 1 if prev_cap else None
    stale = latest[pd.to_datetime(latest["latest_price_date"]).dt.date < ctx.as_of]
    theme.kpi_row([
        theme.kpi_card("Tracked market cap", theme.fmt_money(total_cap), "vs prior day", cap_change),
        theme.kpi_card("24h volume (latest day)", theme.fmt_money(total_vol), f"{len(latest)} assets in universe"),
        theme.kpi_card("Advancers / decliners", f"{adv} / {dec}", "non-stablecoin assets, 1-day change"),
        theme.kpi_card("Assets with stale data", str(len(stale)),
                       "latest price older than as-of date" if len(stale) else "all assets current"),
    ])

    theme.section("Watchlist", "Latest available close per asset. Ticker symbols are not unique — rows are keyed "
                               "by canonical asset ID.")
    spark = ctx.prices()
    spark_map = spark.groupby("asset_id")["close"].apply(lambda s: s.tail(90).round(8).tolist())
    table = latest.assign(
        trend=latest["asset_id"].map(spark_map),
        status=[theme.status_label("warning", "Stale") if d < ctx.as_of else theme.status_label("good", "Current")
                for d in pd.to_datetime(latest["latest_price_date"]).dt.date])
    st.dataframe(
        table[["name", "symbol", "asset_id", "category", "latest_close", "change_1d", "change_7d", "change_30d",
               "latest_market_cap", "latest_volume", "trend", "latest_price_date", "status"]],
        hide_index=True, width="stretch",
        column_config={
            "name": "Asset", "symbol": "Symbol", "asset_id": "Canonical ID", "category": "Category",
            "latest_close": st.column_config.NumberColumn("Close (USD)", format="$%.4g"),
            "change_1d": st.column_config.NumberColumn("1D", format="percent"),
            "change_7d": st.column_config.NumberColumn("7D", format="percent"),
            "change_30d": st.column_config.NumberColumn("30D", format="percent"),
            "latest_market_cap": st.column_config.NumberColumn("Market cap", format="compact"),
            "latest_volume": st.column_config.NumberColumn("Volume", format="compact"),
            "trend": st.column_config.LineChartColumn("Last 90 days", width="small"),
            "latest_price_date": st.column_config.DateColumn("Price date"),
            "status": "Freshness",
        })

    left, right = st.columns([1.1, 1])
    with left:
        theme.section("Largest observed moves", "Latest consecutive-day simple return per asset (USD).")
        movers = latest.dropna(subset=["change_1d"]).sort_values("change_1d")
        colors = ["#2a78d6" if v >= 0 else "#d03b3b" for v in movers["change_1d"]]
        fig = go.Figure(go.Bar(x=movers["change_1d"], y=movers["name"], orientation="h", marker_color=colors,
                               text=[theme.fmt_pct(v, 1, True) for v in movers["change_1d"]], textposition="outside",
                               hovertemplate="%{y}: %{x:+.2%}<extra></extra>"))
        fig.update_layout(hovermode="closest")
        fig.update_xaxes(tickformat="+.0%", zeroline=True, zerolinecolor=theme.HAIRLINE)
        st.plotly_chart(theme.style_fig(fig, 320, x_title="1-day change"), width="stretch")
    with right:
        theme.section("Market-cap share", "Share of tracked market capitalisation, latest day.")
        share = latest.assign(share=latest["latest_market_cap"] / total_cap).sort_values("share")
        fig = go.Figure(go.Bar(x=share["share"], y=share["name"], orientation="h",
                               marker_color=[ctx.colors.get(a) for a in share["asset_id"]],
                               text=[theme.fmt_pct(v, 1) for v in share["share"]], textposition="outside",
                               hovertemplate="%{y}: %{x:.1%} of tracked cap<extra></extra>"))
        fig.update_layout(hovermode="closest")
        fig.update_xaxes(tickformat=".0%")
        st.plotly_chart(theme.style_fig(fig, 320, x_title="Share of tracked market cap"), width="stretch")

    theme.section("Market snapshot over time", "Total tracked market capitalisation and traded volume (USD), "
                                               "selected period. Days with missing observations are not filled.")
    agg = spark.groupby("price_date").agg(market_cap=("market_cap", "sum"), volume=("volume", "sum"),
                                          n=("asset_id", "nunique")).reset_index()
    full_n = latest["asset_id"].nunique()
    agg.loc[agg["n"] < full_n, ["market_cap", "volume"]] = None  # partial days would understate totals
    c1, c2 = st.columns(2)
    fig = go.Figure(go.Scatter(x=agg["price_date"], y=agg["market_cap"], mode="lines", line=dict(width=2, color=theme.SERIES[0]),
                               connectgaps=False, hovertemplate="%{x|%d %b %Y}: $%{y:,.3s}<extra></extra>", name="Market cap"))
    c1.plotly_chart(theme.style_fig(fig, 280, "Tracked market cap (USD)"), width="stretch")
    fig = go.Figure(go.Bar(x=agg["price_date"], y=agg["volume"], marker_color=theme.SERIES[0], name="Volume",
                           hovertemplate="%{x|%d %b %Y}: $%{y:,.3s}<extra></extra>"))
    c2.plotly_chart(theme.style_fig(fig, 280, "Daily traded volume (USD)"), width="stretch")
    theme.note("Days where at least one asset has no observation are shown as gaps rather than partial totals. "
               f"{int((agg['n'] < full_n).sum())} such day(s) in the selected period.")
