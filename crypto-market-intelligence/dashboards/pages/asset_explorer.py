"""B. Asset Explorer - price/OHLCV history, returns, moving averages, volatility, drawdown, comparison."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from analytics import returns as R
from dashboards import theme


def render(ctx) -> None:
    theme.page_header("Asset Explorer", "Historical behaviour of a single asset and side-by-side comparison.",
                      ctx.chips())
    ids = ctx.assets["asset_id"].tolist()
    if not ids:
        theme.empty_state("No assets available for this source.")
        return
    c1, c2 = st.columns([2, 3])
    asset = c1.selectbox("Asset", ids, format_func=ctx.label)
    mas = c2.multiselect("Moving averages (days)", ctx.cfg["analytics"]["moving_averages"],
                         default=ctx.cfg["analytics"]["moving_averages"][:2])

    full = ctx.prices([asset], full_history=True)
    if full.empty:
        theme.empty_state("No observations for this asset.")
        return
    close_full = full.set_index("price_date")["close"]
    df = full[(full["price_date"].dt.date >= ctx.start) & (full["price_date"].dt.date <= ctx.end)]
    close = df.set_index("price_date")["close"]
    rets = R.simple_returns(close)
    factor = ctx.cfg["analytics"]["annualisation_factor"]
    mdd, peak, trough = R.max_drawdown(R.to_daily(close))
    end_ts = pd.Timestamp(ctx.end)

    theme.kpi_row([
        theme.kpi_card("Latest close", theme.fmt_money(close.iloc[-1], decimals=4 if close.iloc[-1] < 10 else 2),
                       f"on {close.index[-1]:%d %b %Y}"),
        theme.kpi_card("Period return", theme.fmt_pct(R.cumulative_return(rets), 1, True),
                       f"{ctx.start:%d %b %Y} → {ctx.end:%d %b %Y}"),
        theme.kpi_card("Annualised volatility", theme.fmt_pct(R.annualised_volatility(rets, factor)),
                       f"daily returns × √{factor}"),
        theme.kpi_card("Maximum drawdown", theme.fmt_pct(mdd, 1),
                       f"{peak:%d %b %y} → {trough:%d %b %y}" if peak is not None else ""),
        theme.kpi_card("Missing days", str(int(R.to_daily(close).isna().sum())), "in period — not filled"),
    ])

    theme.section("Price & volume", "Daily OHLC candles where the source supplies them; otherwise close-only line. "
                                    "Volume in a separate panel (no dual axis).")
    has_ohlc = df[["open", "high", "low"]].notna().all(axis=1).all()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.04)
    if has_ohlc:
        fig.add_trace(go.Candlestick(x=df["price_date"], open=df["open"], high=df["high"], low=df["low"],
                                     close=df["close"], name="OHLC", increasing_line_color=theme.SERIES[0],
                                     decreasing_line_color="#d03b3b", increasing_fillcolor=theme.SERIES[0],
                                     decreasing_fillcolor="#d03b3b", line_width=1), row=1, col=1)
    else:
        fig.add_trace(go.Scatter(x=df["price_date"], y=df["close"], name="Close", line=dict(color=theme.SERIES[0], width=2)),
                      row=1, col=1)
    ma_colors = [theme.SERIES[1], theme.SERIES[6], theme.SERIES[2]]
    for i, w in enumerate(sorted(mas)):
        ma = R.moving_average(close_full, w).loc[close.index.min():close.index.max()]
        fig.add_trace(go.Scatter(x=ma.index, y=ma.values, name=f"{w}-day MA", line=dict(width=1.6, color=ma_colors[i % 3])),
                      row=1, col=1)
    fig.add_trace(go.Bar(x=df["price_date"], y=df["volume"], name="Volume", marker_color=theme.INK_3, showlegend=False,
                         hovertemplate="%{x|%d %b %Y}: $%{y:,.3s}<extra>Volume</extra>"), row=2, col=1)
    fig.update_layout(xaxis_rangeslider_visible=False)
    fig.update_yaxes(title_text="USD", row=1, col=1)
    fig.update_yaxes(title_text="Volume (USD)", row=2, col=1)
    st.plotly_chart(theme.style_fig(fig, 520), width="stretch")

    left, right = st.columns(2)
    with left:
        theme.section("Period performance", "Point-to-point returns ending at the latest observation in range.")
        perf = pd.DataFrame({
            "Window": ["1 day", "7 days", "30 days", "90 days", "Month to date", "Year to date"],
            "Return": [R.period_return(close_full, end_ts, 1), R.period_return(close_full, end_ts, 7),
                       R.period_return(close_full, end_ts, 30), R.period_return(close_full, end_ts, 90),
                       R.period_return(close_full, end_ts, start=end_ts.replace(day=1) - pd.Timedelta(days=1)),
                       R.period_return(close_full, end_ts, start=end_ts.replace(month=1, day=1) - pd.Timedelta(days=1))]})
        st.dataframe(perf, hide_index=True, width="stretch",
                     column_config={"Return": st.column_config.NumberColumn(format="percent")})
        monthly = R.to_daily(close_full).resample("ME").last().pct_change(fill_method=None).dropna().tail(12)
        fig = go.Figure(go.Bar(x=monthly.index.strftime("%b %y"), y=monthly.values,
                               marker_color=["#2a78d6" if v >= 0 else "#d03b3b" for v in monthly.values],
                               hovertemplate="%{x}: %{y:+.1%}<extra></extra>"))
        fig.update_yaxes(tickformat="+.0%")
        fig.update_layout(hovermode="closest")
        st.plotly_chart(theme.style_fig(fig, 260, "Monthly returns, last 12 months"), width="stretch")
    with right:
        w = ctx.cfg["analytics"]["rolling_window_days"]
        theme.section("Risk profile", f"Rolling {w}-day annualised volatility and drawdown from running peak.")
        rv = R.rolling_volatility(R.simple_returns(close_full), w, factor).loc[close.index.min():close.index.max()]
        fig = go.Figure(go.Scatter(x=rv.index, y=rv.values, line=dict(color=theme.SERIES[0], width=2), name="Volatility",
                                   hovertemplate="%{x|%d %b %Y}: %{y:.1%}<extra></extra>"))
        fig.update_yaxes(tickformat=".0%")
        st.plotly_chart(theme.style_fig(fig, 220, f"Rolling {w}-day volatility (annualised)"), width="stretch")
        dd = R.drawdown_series(R.to_daily(close))
        fig = go.Figure(go.Scatter(x=dd.index, y=dd.values, fill="tozeroy", line=dict(color="#d03b3b", width=1.5),
                                   fillcolor="rgba(208,59,59,0.12)", name="Drawdown",
                                   hovertemplate="%{x|%d %b %Y}: %{y:.1%}<extra></extra>"))
        fig.update_yaxes(tickformat=".0%")
        st.plotly_chart(theme.style_fig(fig, 220, "Drawdown from running peak"), width="stretch")

    theme.section("Asset comparison", "Prices indexed to 100 at the first common date in the selected period.")
    compare = st.multiselect("Compare assets", ids, default=[a for a in ids if not
                             ctx.assets.set_index("asset_id").loc[a, "is_stablecoin"]][:4], format_func=ctx.label)
    if compare:
        wide = ctx.prices(compare).pivot_table(index="price_date", columns="asset_id", values="close")
        common = wide.dropna()
        if common.empty:
            theme.empty_state("Selected assets have no common observation date in this period.")
        else:
            base = common.iloc[0]
            idx = wide.loc[common.index[0]:] / base * 100
            fig = go.Figure()
            for a in compare:
                fig.add_trace(go.Scatter(x=idx.index, y=idx[a], name=ctx.label(a), connectgaps=False,
                                         line=dict(color=ctx.colors.get(a), width=2),
                                         hovertemplate="%{y:.1f}<extra>" + ctx.label(a) + "</extra>"))
            st.plotly_chart(theme.style_fig(fig, 360, None, "Index (start = 100)"), width="stretch")
            stats = pd.DataFrame([{
                "Asset": ctx.label(a),
                "Return": R.cumulative_return(R.simple_returns(wide[a].dropna())),
                "Ann. volatility": R.annualised_volatility(R.simple_returns(wide[a].dropna()), factor),
                "Max drawdown": R.max_drawdown(R.to_daily(wide[a].dropna()))[0]} for a in compare])
            st.dataframe(stats, hide_index=True, width="stretch", column_config={
                c: st.column_config.NumberColumn(format="percent") for c in ["Return", "Ann. volatility", "Max drawdown"]})
