"""G. Forecast & Planning."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics import forecast as fc
from dashboards import theme
from dashboards.data import Context
from dashboards.pages.common import dataframe, gbp, header, pct_col


@st.cache_data(show_spinner="Backtesting forecast models…")
def _run(series_key: str, series: pd.DataFrame, holdout: int, horizon: int, z: float):
    return fc.backtest_and_forecast(series, "net_revenue_gbp", holdout, horizon, z)


def render(ctx: Context) -> None:
    header(ctx, "Forecast & Planning",
           "Weekly net revenue forecasts by category with backtested accuracy (full history; channel/region filters apply)",
           show_window=False)
    f = ctx.rules["forecast"]
    lines = ctx.all_dates("lines")
    lines = lines[~lines["is_cancelled"]]
    if lines.empty:
        theme.empty_state("No sales history for the selected filters.")
        return
    weekly = fc.weekly_series(lines, "order_date", "net_revenue_gbp", ctx.as_of, "category")
    total = weekly.groupby("week")["net_revenue_gbp"].sum().reset_index()
    key = f"{ctx.db_path}|{ctx.channels}|{ctx.regions}"

    results = {}
    for cat in ["All categories"] + sorted(weekly["category"].unique()):
        s = total if cat == "All categories" else weekly[weekly["category"] == cat][["week", "net_revenue_gbp"]]
        try:
            results[cat] = _run(f"{key}|{cat}", s, int(f["holdout_weeks"]), int(f["horizon_weeks"]), float(f["interval_z"]))
        except ValueError:
            continue
    if not results:
        theme.empty_state("Not enough history to backtest a forecast.")
        return

    choice = st.selectbox("Series", list(results))
    frame, met = results[choice]
    theme.kpi_row([
        theme.kpi_card("Selected model", met["model"], f"lowest backtest WAPE of {len(met['candidates'])} candidates"),
        theme.kpi_card("Backtest WAPE", theme.fmt_pct(met["wape"]), f"last {met['holdout_weeks']} complete weeks"),
        theme.kpi_card("Backtest MAE", gbp(met["mae"]), "mean absolute weekly error"),
        theme.kpi_card("Forecast bias", theme.fmt_pct(met["bias"], signed=True), "positive = over-forecast"),
        theme.kpi_card(f"Next {f['horizon_weeks']} weeks", gbp(frame["forecast"].sum()), "point forecast, net revenue"),
    ])

    theme.section(f"{choice}: actual, backtest and forecast", "Weekly net revenue (£); shaded band = ≈95% empirical interval")
    hist = frame[frame["actual"].notna()].tail(78)
    bt = frame[frame["backtest"].notna()]
    fut = frame[frame["forecast"].notna()]
    fig = go.Figure()
    for band, col in ((bt, "backtest"), (fut, "forecast")):
        fig.add_scatter(x=pd.concat([band["week"], band["week"][::-1]]), y=pd.concat([band["upper"], band["lower"][::-1]]),
                        fill="toself", fillcolor="rgba(42,120,214,0.12)", line=dict(width=0), hoverinfo="skip",
                        showlegend=False)
    fig.add_scatter(x=hist["week"], y=hist["actual"], name="Actual", line=dict(color=theme.INK, width=2),
                    hovertemplate="Actual £%{y:,.0f}")
    fig.add_scatter(x=bt["week"], y=bt["backtest"], name="Backtest (out-of-sample)",
                    line=dict(color=theme.SERIES[1], width=2), hovertemplate="Backtest £%{y:,.0f}")
    fig.add_scatter(x=fut["week"], y=fut["forecast"], name="Forecast", line=dict(color=theme.SERIES[0], width=2),
                    hovertemplate="Forecast £%{y:,.0f}")
    st.plotly_chart(theme.style_fig(fig, 380, y_title="Net revenue (£ per week)"), width="stretch")

    c1, c2 = st.columns([1, 1.3])
    with c1:
        theme.section("Model comparison", "Backtest on the same holdout weeks")
        comp = pd.DataFrame([{"Model": k, **v} for k, v in met["candidates"].items()])
        dataframe(comp, column_config={"wape": pct_col("WAPE"), "bias": pct_col("Bias"),
                                       "mae": st.column_config.NumberColumn("MAE (£)", format="%.0f")})
    with c2:
        theme.section("Forecast exception list", f"Series with WAPE above {f['wape_exception_threshold']:.0%}, "
                      "or backtest weeks where the actual fell outside the interval")
        rows = []
        for cat, (fr, m) in results.items():
            outside = int(fr["outside_interval"].sum())
            if m["wape"] > f["wape_exception_threshold"] or outside:
                lvl = "critical" if m["wape"] > f["wape_exception_threshold"] else "warning"
                rows.append({"Series": cat, "WAPE": m["wape"], "Weeks outside interval": outside,
                             "Bias": m["bias"], "Status": theme.status_label(lvl, "Review model" if lvl == "critical" else "Monitor")})
        if rows:
            dataframe(pd.DataFrame(rows).sort_values("WAPE", ascending=False),
                      column_config={"WAPE": pct_col("WAPE"), "Bias": pct_col("Bias")})
        else:
            theme.empty_state("No forecast exceptions.")
    theme.note("Forecasts are decision-support estimates from synthetic history. Intervals use the RMSE of backtest "
               "errors and do not widen with horizon; validate against real data before operational use.")
