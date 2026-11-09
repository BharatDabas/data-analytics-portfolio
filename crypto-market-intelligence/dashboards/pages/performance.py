"""E. Performance & Benchmark - cumulative TWR vs benchmarks, period comparison, optional backtest."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics import returns as R
from analytics.backtest import backtest
from analytics.portfolio import portfolio_daily
from dashboards import theme


def _ew_index_returns(rw: pd.DataFrame, stable: set[str]) -> pd.Series:
    """Equal-weight daily-rebalanced index of non-stablecoin assets, using assets observed that day."""
    cols = [c for c in rw.columns if c not in stable]
    return rw[cols].mean(axis=1, skipna=True).rename("EW_INDEX")


def render(ctx) -> None:
    theme.page_header("Performance & Benchmark", "Time-weighted portfolio performance against market benchmarks.",
                      ctx.chips())
    stable = set(ctx.assets.loc[ctx.assets["is_stablecoin"], "asset_id"])
    rw_all = ctx.prices(full_history=True).pivot_table(index="price_date", columns="asset_id", values="daily_return")
    series: dict[str, pd.Series] = {}
    if ctx.portfolio_id:
        snaps = ctx.q("SELECT * FROM fact_portfolio_snapshot WHERE portfolio_id = ?", (ctx.portfolio_id,))
        if len(snaps):
            pdaily = portfolio_daily(snaps).set_index("snapshot_date")
            series["Portfolio (TWR)"] = pdaily["twr_return"]
    defaults = [b for b in ctx.cfg["benchmarks"]["default"] if b == "EW_INDEX" or b in rw_all.columns]
    options = ["EW_INDEX"] + list(rw_all.columns)
    bench = st.multiselect("Benchmarks", options, default=defaults,
                           format_func=lambda b: "Equal-weight universe index" if b == "EW_INDEX" else ctx.label(b))
    for b in bench:
        series["Equal-weight universe index" if b == "EW_INDEX" else ctx.label(b)] = \
            _ew_index_returns(rw_all, stable) if b == "EW_INDEX" else rw_all[b]
    if not series:
        theme.empty_state("Select at least one benchmark or add portfolio transactions.")
        return
    color_for = {"Portfolio (TWR)": theme.INK, "Equal-weight universe index": theme.INK_3}
    for b in bench:
        if b != "EW_INDEX":
            color_for[ctx.label(b)] = ctx.colors.get(b)

    df = pd.DataFrame(series)
    df.index = pd.to_datetime(df.index)
    df = df[(df.index.date >= ctx.start) & (df.index.date <= ctx.end)]
    if "Portfolio (TWR)" in df:
        first = df["Portfolio (TWR)"].first_valid_index()
        df = df.loc[first:] if first is not None else df
    idx = df.apply(R.cumulative_index)

    end = idx.index.max()
    kpis = []
    for name in df.columns[:5]:
        kpis.append(theme.kpi_card(name, theme.fmt_pct(R.cumulative_return(df[name]), 1, True),
                                   f"{idx.index.min():%d %b %y} → {end:%d %b %y}"))
    theme.kpi_row(kpis)

    theme.section("Cumulative return", "Indexed to 100 at the start of the aligned period. Missing days contribute 0% "
                                       "(gap is visible in the data-operations page).")
    fig = go.Figure()
    for name in idx.columns:
        fig.add_trace(go.Scatter(x=idx.index, y=idx[name], name=name,
                                 line=dict(color=color_for.get(name), width=2.4 if name.startswith("Portfolio") else 1.8),
                                 hovertemplate="%{y:.1f}<extra>" + name + "</extra>"))
    st.plotly_chart(theme.style_fig(fig, 380, y_title="Index (start = 100)"), width="stretch")

    theme.section("Period comparison", "Compounded returns over trailing windows ending at the latest date.")
    windows = {"1M": 30, "3M": 91, "6M": 182, "1Y": 365}
    rows = []
    full = pd.DataFrame(series)
    full.index = pd.to_datetime(full.index)
    full = full[full.index.date <= ctx.end]
    for name in full.columns:
        r = {"Series": name}
        for label, d in windows.items():
            win = full[name].loc[full.index > full.index.max() - pd.Timedelta(days=d)]
            r[label] = R.cumulative_return(win) if win.notna().sum() >= d * 0.8 else None
        ytd = full[name].loc[full.index >= pd.Timestamp(full.index.max().year, 1, 1)]
        r["YTD"] = R.cumulative_return(ytd)
        r["Selected period"] = R.cumulative_return(df[name]) if name in df else None
        rows.append(r)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="percent")
                                for c in [*windows, "YTD", "Selected period"]})
    if "Portfolio (TWR)" in df and len(df.columns) > 1:
        theme.section("Excess return vs benchmark", "Portfolio cumulative return minus benchmark, percentage points.")
        b = st.selectbox("Benchmark for excess return", [c for c in df.columns if c != "Portfolio (TWR)"])
        excess = (idx["Portfolio (TWR)"] - idx[b]) / 100
        fig = go.Figure(go.Scatter(x=excess.index, y=excess.values, fill="tozeroy", line=dict(color=theme.SERIES[0], width=1.6),
                                   fillcolor="rgba(42,120,214,0.10)", hovertemplate="%{y:+.1%}<extra></extra>"))
        fig.update_yaxes(tickformat="+.0%", zeroline=True, zerolinecolor=theme.HAIRLINE)
        st.plotly_chart(theme.style_fig(fig, 240), width="stretch")

    with st.expander("Historical simulation (optional backtest)"):
        theme.note("<b>Assumptions:</b> trades at daily close; fee and slippage charged as basis points of traded "
                   "notional; rebalancing on the first day of each period; missing prices contribute 0% that day. "
                   "<b>Survivorship bias:</b> the universe is fixed with today's assets, so failed/delisted assets are "
                   "absent and results are biased upwards. Simulated past performance does not predict future results.")
        c1, c2, c3 = st.columns(3)
        fee = c1.number_input("Fee (bps)", 0.0, 200.0, 10.0, 1.0)
        slip = c2.number_input("Slippage (bps)", 0.0, 200.0, 5.0, 1.0)
        freq = c3.selectbox("Rebalance", ["Monthly", "Weekly", "Never (buy & hold)"])
        assets = st.multiselect("Equal-weight basket", list(rw_all.columns),
                                default=[c for c in rw_all.columns if c not in stable][:5], format_func=ctx.label)
        if assets:
            rw = rw_all.loc[(rw_all.index.date >= ctx.start) & (rw_all.index.date <= ctx.end)]
            weights = {a: 1 / len(assets) for a in assets}
            reb = {"Monthly": "M", "Weekly": "W", "Never (buy & hold)": None}[freq]
            res = backtest(rw, weights, reb, fee, slip)
            hold = backtest(rw, weights, None, fee, slip)
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=res.index, y=res["value"], name=f"Rebalanced ({freq.lower()})",
                                     line=dict(color=theme.SERIES[0], width=2)))
            if reb:
                fig.add_trace(go.Scatter(x=hold.index, y=hold["value"], name="Buy & hold",
                                         line=dict(color=theme.SERIES[1], width=2)))
            fig.update_yaxes(tickprefix="$")
            st.plotly_chart(theme.style_fig(fig, 300, "Simulated value of $10,000 (USD)"), width="stretch")
            st.caption(f"Total simulated costs: {theme.fmt_money(res['costs'].sum() + 10_000 * (fee + slip) / 1e4)} · "
                       f"turnover {theme.fmt_money(res['turnover'].sum())} · final value {theme.fmt_money(res['value'].iloc[-1])}")
