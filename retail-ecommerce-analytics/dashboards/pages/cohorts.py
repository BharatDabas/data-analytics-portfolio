"""D. Cohort & Retention."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics import cohorts as coh
from analytics import customers as cust
from analytics import kpis
from dashboards import theme
from dashboards.data import Context
from dashboards.pages.common import gbp, header


def render(ctx: Context) -> None:
    header(ctx, "Cohort & Retention",
           "First-purchase cohorts, repeat behaviour and time between orders (full history; channel/region filters apply)",
           show_window=False)
    orders = ctx.all_dates("orders")
    if orders[~orders["is_cancelled"]].empty:
        theme.empty_state("No orders match the selected filters.")
        return
    max_p = int(ctx.rules["cohort"]["max_periods"])
    active, rate = coh.cohort_matrix(orders, ctx.as_of, max_p)
    elig = orders[~orders["is_cancelled"]]
    sizes = elig.groupby("customer_id")["order_date"].min().pipe(
        lambda s: pd.to_datetime(s).dt.to_period("M").dt.start_time.value_counts())
    curve = coh.retention_curve(rate, sizes)
    t2 = coh.time_to_second_purchase(orders)
    gaps = coh.days_between_orders(orders)

    theme.kpi_row([
        theme.kpi_card("Repeat purchase rate", theme.fmt_pct(kpis.repeat_purchase_rate(orders)),
                       "customers with ≥2 eligible orders, all history"),
        theme.kpi_card("Month-1 retention", theme.fmt_pct(curve.get(1)), "customer-weighted across cohorts"),
        theme.kpi_card("Month-3 retention", theme.fmt_pct(curve.get(3)), "share active in 3rd month after first order"),
        theme.kpi_card("Median days to 2nd order", theme.fmt_num(t2.median()) if len(t2) else "—",
                       f"{len(t2):,} customers placed a 2nd order"),
    ])

    theme.section("Retention matrix", "Share of each first-purchase cohort placing ≥1 order in month k after acquisition. "
                  "Blank = month not yet complete (not churn).")
    show = rate.tail(18).drop(columns=0)  # month 0 is 100% by definition; omit to keep the scale informative
    show = show[show.notna().any(axis=1)]  # cohorts with no completed month yet have nothing to show
    z = show.to_numpy(dtype=float, copy=True)
    text = [[("" if np.isnan(v) else f"{v:.0%}") for v in row] for row in z]
    fig = go.Figure(go.Heatmap(
        z=z, x=[f"M{k}" for k in show.columns], y=[f"{c:%b %Y} (n={int(sizes.get(c, 0)):,})" for c in show.index],
        colorscale=[[i / (len(theme.SEQUENTIAL) - 1), c] for i, c in enumerate(theme.SEQUENTIAL)],
        text=text, texttemplate="%{text}", textfont=dict(size=10), xgap=2, ygap=2,
        hovertemplate="Cohort %{y}<br>%{x}: %{z:.1%} active<extra></extra>", colorbar=dict(tickformat=".0%", title="")))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_layout(hovermode="closest")
    st.plotly_chart(theme.style_fig(fig, 520), width="stretch")

    c1, c2 = st.columns(2)
    with c1:
        theme.section("Cohort retention curves", "Average curve plus the four most recent cohorts with ≥3 months observed")
        fig = go.Figure()
        recent = [c for c in rate.index if rate.loc[c].iloc[1:4].notna().all()][-4:]
        for i, c in enumerate(recent):
            r = rate.loc[c].iloc[1:].dropna()
            fig.add_scatter(x=r.index, y=r.values, name=f"{c:%b %Y}", mode="lines",
                            line=dict(color=theme.SERIES[i + 1], width=2), hovertemplate="%{y:.1%}")
        fig.add_scatter(x=curve.index[1:], y=curve.values[1:], name="All cohorts (weighted)", mode="lines+markers",
                        line=dict(color=theme.SERIES[0], width=3), marker=dict(size=8), hovertemplate="%{y:.1%}")
        fig.update_yaxes(tickformat=".0%")
        st.plotly_chart(theme.style_fig(fig, 360, x_title="Months since first order", y_title="Customers active"),
                        width="stretch")
    with c2:
        theme.section("Time to second purchase", "Days between first and second eligible order")
        if len(t2):
            fig = go.Figure(go.Histogram(x=t2.clip(upper=365), xbins=dict(start=0, end=366, size=14),
                                         marker_color=theme.SERIES[0], hovertemplate="%{x} days: %{y:,} customers<extra></extra>"))
            fig.update_layout(hovermode="closest")
            st.plotly_chart(theme.style_fig(fig, 330, x_title="Days (capped at 365)", y_title="Customers"), width="stretch")
        else:
            theme.empty_state("No repeat orders yet.")

    c3, c4 = st.columns(2)
    with c3:
        theme.section("Time to next purchase by order number", "Median and interquartile range of days since previous order")
        g = gaps[gaps["n"] <= 6].groupby("n")["days"].describe()[["25%", "50%", "75%", "count"]].reset_index()
        fig = go.Figure(go.Bar(x=[f"Order {n}" for n in g["n"]], y=g["50%"], marker_color=theme.SERIES[0],
                               error_y=dict(type="data", symmetric=False, array=g["75%"] - g["50%"],
                                            arrayminus=g["50%"] - g["25%"], color=theme.INK_3, thickness=1.5),
                               customdata=g["count"],
                               hovertemplate="%{x}: median %{y:.0f} days (n=%{customdata:,})<extra></extra>"))
        fig.update_layout(hovermode="closest")
        st.plotly_chart(theme.style_fig(fig, 300, y_title="Days since previous order"), width="stretch")
    with c4:
        theme.section("Customer value: observed vs modelled", "12-month margin CLV is a MODEL; observed value is actual")
        lines = ctx.all_dates("lines")
        k = kpis.commercial_kpis(lines, orders)
        active_months = elig.assign(m=pd.to_datetime(elig["order_date"]).dt.to_period("M")).groupby(
            ["customer_id", "m"])["net_revenue_gbp"].sum()
        monthly_value = float(active_months.mean()) if len(active_months) else float("nan")
        cl = ctx.rules["clv"]
        model = cust.modelled_clv(curve.reset_index(drop=True), monthly_value, k["gross_margin_pct"],
                                  int(cl["horizon_months"]), float(cl["annual_discount_rate"]))
        cv = ctx.frames["customers"]
        cv = cv[cv["orders"] > 0]
        mature = cv[pd.to_datetime(cv["first_order_date"]) <= pd.Timestamp(ctx.as_of) - pd.DateOffset(months=12)]
        obs12, obs12_gp = _observed_12m(elig, mature["customer_id"])
        theme.kpi_row([
            theme.kpi_card("Observed 12-month gross profit", theme.fmt_money(obs12_gp, "£", 2),
                           f"net revenue {theme.fmt_money(obs12, '£', 2)}; avg of {len(mature):,} customers acquired ≥12 months ago"),
            theme.kpi_card("Modelled 12-month margin CLV", theme.fmt_money(model, "£", 2), "estimate - see assumptions"),
        ])
        theme.note(f"<b>Model assumptions:</b> monthly net revenue per active customer {gbp(monthly_value)} × "
                   f"gross margin {theme.fmt_pct(k['gross_margin_pct'])} × Σ retention(t) over "
                   f"{cl['horizon_months']} months (incl. acquisition month), discounted at {cl['annual_discount_rate']:.0%} p.a.; retention beyond "
                   "observed history held at the last observed level. The observed figure is what mature cohorts actually "
                   "generated in their first 12 months (gross profit on costed products only); the model is an estimate "
                   "for a newly acquired customer and is not expected to match exactly.")


def _observed_12m(orders: pd.DataFrame, customer_ids: pd.Series) -> tuple[float, float]:
    """Average net revenue and gross profit in each customer's first 12 months (observed)."""
    o = orders[orders["customer_id"].isin(customer_ids)].copy()
    if o.empty:
        return float("nan"), float("nan")
    first = o.groupby("customer_id")["order_date"].transform("min")
    o = o[pd.to_datetime(o["order_date"]) < pd.to_datetime(first) + pd.DateOffset(months=12)]
    per = o.groupby("customer_id")[["net_revenue_gbp", "gross_profit_gbp"]].sum().mean()
    return float(per["net_revenue_gbp"]), float(per["gross_profit_gbp"])
