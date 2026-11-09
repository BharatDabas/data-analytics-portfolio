"""D. Risk & Diversification - volatility, drawdown, correlation, concentration, rolling returns, Sharpe."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics import returns as R
from analytics.portfolio import portfolio_daily
from dashboards import theme


def render(ctx) -> None:
    theme.page_header("Risk & Diversification", "Historical risk of the portfolio and the asset universe.",
                      ctx.chips())
    a = ctx.cfg["analytics"]
    factor, window = a["annualisation_factor"], a["rolling_window_days"]
    rf = st.sidebar.number_input("Risk-free rate (annual, for Sharpe)", value=float(a["risk_free_rate_annual"]),
                                 min_value=0.0, max_value=0.2, step=0.005, format="%.3f")

    snaps = ctx.q("SELECT * FROM fact_portfolio_snapshot WHERE portfolio_id = ?", (ctx.portfolio_id,)) \
        if ctx.portfolio_id else pd.DataFrame()
    pdaily = portfolio_daily(snaps) if len(snaps) else pd.DataFrame()
    if len(pdaily):
        pdaily = pdaily[(pdaily["snapshot_date"].dt.date >= ctx.start) & (pdaily["snapshot_date"].dt.date <= ctx.end)]
        pr = pdaily.set_index("snapshot_date")["twr_return"]
        p_index = R.cumulative_index(pr)
        p_vol = R.annualised_volatility(pr, factor)
        p_mdd, peak, trough = R.max_drawdown(p_index)
        sharpe = R.sharpe_ratio(pr, rf, factor)
        hold = ctx.q("SELECT * FROM v_current_holdings WHERE portfolio_id = ?", (ctx.portfolio_id,))
        w = hold.set_index("asset_id")["market_value"] / hold["market_value"].sum()
        hhi = R.herfindahl(w)
        top = w.idxmax() if len(w) else None
        theme.kpi_row([
            theme.kpi_card("Portfolio volatility", theme.fmt_pct(p_vol), f"annualised, TWR daily × √{factor}"),
            theme.kpi_card("Maximum drawdown", theme.fmt_pct(p_mdd),
                           f"{peak:%d %b %y} → {trough:%d %b %y}" if peak is not None else ""),
            theme.kpi_card("Sharpe ratio", f"{sharpe:.2f}" if sharpe is not None else "n/a",
                           f"rf {rf:.1%}, daily, √{factor}" if sharpe is not None else "insufficient data"),
            theme.kpi_card("Concentration (HHI)", f"{hhi:.2f}", f"effective # assets ≈ {1 / hhi:.1f}" if hhi else ""),
            theme.kpi_card("Largest position", theme.fmt_pct(w.max()) if len(w) else "—", ctx.label(top) if top else ""),
        ])
        theme.note("Sharpe = mean daily excess return ÷ standard deviation of daily returns × √365, using the "
                   "risk-free rate set in the sidebar. Returns are time-weighted (cash flows removed). Historical risk "
                   "measures describe the past and do not predict future outcomes.")
    else:
        theme.empty_state("No portfolio in scope — showing universe risk only.")

    theme.section("Universe risk table", "Per-asset statistics over the selected period (consecutive-day returns only).")
    rw = ctx.returns_wide()
    prices = ctx.prices()
    rows = []
    for asset in rw.columns:
        r = rw[asset]
        close = prices[prices["asset_id"] == asset].set_index("price_date")["close"]
        rows.append({"Asset": ctx.label(asset), "Return": R.cumulative_return(r),
                     "Ann. volatility": R.annualised_volatility(r, factor),
                     "Max drawdown": R.max_drawdown(R.to_daily(close))[0],
                     "Sharpe": R.sharpe_ratio(r, rf, factor), "Observations": int(r.notna().sum())})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
        "Return": st.column_config.NumberColumn(format="percent"),
        "Ann. volatility": st.column_config.NumberColumn(format="percent"),
        "Max drawdown": st.column_config.NumberColumn(format="percent"),
        "Sharpe": st.column_config.NumberColumn(format="%.2f")})

    left, right = st.columns([1.15, 1])
    with left:
        min_overlap = a["correlation_min_overlap"]
        theme.section("Correlation matrix", f"Pearson correlation of daily returns, pairwise aligned; pairs with "
                                            f"< {min_overlap} common days are blank.")
        corr = R.correlation_matrix(rw, min_overlap)
        labels = [ctx.assets.set_index("asset_id").loc[c, "name"] for c in corr.columns]
        fig = go.Figure(go.Heatmap(z=corr.values, x=labels, y=labels, zmin=-1, zmax=1, colorscale=theme.DIVERGING,
                                   text=np.round(corr.values, 2), texttemplate="%{text}", textfont=dict(size=10),
                                   hovertemplate="%{y} vs %{x}: %{z:.2f}<extra></extra>",
                                   colorbar=dict(title="ρ", thickness=10)))
        fig.update_layout(hovermode="closest")
        fig.update_yaxes(autorange="reversed", showgrid=False)
        st.plotly_chart(theme.style_fig(fig, 420), width="stretch")
    with right:
        if len(pdaily):
            theme.section("Portfolio drawdown", "Decline of the time-weighted index from its running peak.")
            dd = R.drawdown_series(p_index)
            fig = go.Figure(go.Scatter(x=dd.index, y=dd.values, fill="tozeroy", line=dict(color="#d03b3b", width=1.5),
                                       fillcolor="rgba(208,59,59,0.12)", hovertemplate="%{x|%d %b %Y}: %{y:.1%}<extra></extra>"))
            fig.update_yaxes(tickformat=".0%")
            st.plotly_chart(theme.style_fig(fig, 200), width="stretch")
            theme.section(f"Rolling {window}-day return & volatility", "Portfolio, annualised volatility; separate panels.")
            roll_ret = (1 + pr.fillna(0)).rolling(window).apply(np.prod, raw=True) - 1
            roll_vol = R.rolling_volatility(pr, window, factor)
            fig = go.Figure(go.Scatter(x=roll_ret.index, y=roll_ret.values, line=dict(color=theme.SERIES[0], width=2),
                                       name="Rolling return", hovertemplate="%{y:+.1%}<extra>return</extra>"))
            fig.update_yaxes(tickformat="+.0%", zeroline=True, zerolinecolor=theme.HAIRLINE)
            st.plotly_chart(theme.style_fig(fig, 170, f"{window}-day rolling return"), width="stretch")
            fig = go.Figure(go.Scatter(x=roll_vol.index, y=roll_vol.values, line=dict(color=theme.SERIES[6], width=2),
                                       name="Rolling volatility", hovertemplate="%{y:.1%}<extra>volatility</extra>"))
            fig.update_yaxes(tickformat=".0%")
            st.plotly_chart(theme.style_fig(fig, 170, f"{window}-day rolling volatility (annualised)"), width="stretch")
