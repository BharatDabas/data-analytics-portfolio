"""A. Executive Trading Overview."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics import kpis
from dashboards import theme
from dashboards.data import Context
from dashboards.pages.common import complete_months, dataframe, gbp, header, series_color

TOOLTIP = ("Net revenue = gross sales − discounts − refunds. Excludes tax and shipping; cancelled orders "
           "excluded; refunds attributed to the original order date.")


def render(ctx: Context) -> None:
    header(ctx, "Executive Trading Overview", "Reconciled sales, margin and growth for the selected period")
    lines, orders = ctx.window("lines"), ctx.window("orders")
    if orders.empty:
        theme.empty_state("No orders match the selected filters.")
        return
    p_start, p_end = kpis.previous_period(ctx.start, ctx.end)
    k = kpis.commercial_kpis(lines, orders)
    pk = kpis.commercial_kpis(ctx.window("lines", start=p_start, end=p_end), ctx.window("orders", start=p_start, end=p_end))
    ch = lambda m: kpis.pct_change(k[m], pk[m])  # noqa: E731
    vs = f"vs {p_start:%d %b %y}–{p_end:%d %b %y}"

    theme.kpi_row([
        theme.kpi_card("Net revenue", gbp(k["net_revenue"]), vs, ch("net_revenue")),
        theme.kpi_card("Gross sales", gbp(k["gross_sales"]), vs, ch("gross_sales")),
        theme.kpi_card("Orders", theme.fmt_num(k["orders"]), vs, ch("orders")),
        theme.kpi_card("Average order value", theme.fmt_money(k["aov"], "£", 2), vs, ch("aov")),
    ])
    st.write("")
    theme.kpi_row([
        theme.kpi_card("Units sold", theme.fmt_num(k["units"]), vs, ch("units")),
        theme.kpi_card("Gross profit", gbp(k["gross_profit"]),
                       f"on {theme.fmt_pct(k['cost_coverage'])} of revenue with reliable cost", ch("gross_profit")),
        theme.kpi_card("Gross margin", theme.fmt_pct(k["gross_margin_pct"]),
                       f"prev. {theme.fmt_pct(pk['gross_margin_pct'])}"),
        theme.kpi_card("Return rate (units)", theme.fmt_pct(k["return_rate_units"]),
                       f"prev. {theme.fmt_pct(pk['return_rate_units'])}", ch("return_rate_units"), good_when_up=False),
    ])
    theme.note(TOOLTIP + " Gross profit uses only products with a reliable unit cost; returned units are assumed restocked.")

    c1, c2 = st.columns([1.35, 1])
    with c1:
        theme.section("Net revenue trend", "Weekly net revenue (£), current window vs the preceding window of equal length")
        cur = _weekly(lines)
        prev = _weekly(ctx.window("lines", start=p_start, end=p_end))
        fig = go.Figure()
        if not prev.empty:
            fig.add_scatter(x=cur["week"].iloc[:len(prev)] if len(cur) >= len(prev) else cur["week"],
                            y=prev["net_revenue_gbp"].iloc[:len(cur)], name="Previous period (aligned)",
                            line=dict(color=theme.INK_3, width=2), hovertemplate="£%{y:,.0f}")
        fig.add_scatter(x=cur["week"], y=cur["net_revenue_gbp"], name="Current period",
                        line=dict(color=theme.SERIES[0], width=2), hovertemplate="£%{y:,.0f}")
        st.plotly_chart(theme.style_fig(fig, 330, y_title="Net revenue (£)"), width="stretch")
    with c2:
        theme.section("Revenue to gross profit bridge", "How gross sales become net revenue and gross profit (£)")
        costed = lines[~lines["is_cancelled"] & lines["has_reliable_cost"].astype(bool)]
        uncosted_rev = k["net_revenue"] - float(costed["net_revenue_gbp"].sum())
        steps = [("Gross sales", k["gross_sales"], "absolute"), ("Discounts", -k["discounts"], "relative"),
                 ("Refunds", -k["refunds"], "relative"), ("Net revenue", k["net_revenue"], "total"),
                 ("No cost data", -uncosted_rev, "relative"), ("COGS", -k["cogs"], "relative"),
                 ("Gross profit", k["gross_profit"], "total")]
        fig = go.Figure(go.Waterfall(
            x=[s[0] for s in steps], y=[s[1] if s[2] != "total" else 0 for s in steps], measure=[s[2] for s in steps],
            connector=dict(line=dict(color=theme.HAIRLINE, width=1)),
            increasing=dict(marker=dict(color=theme.SERIES[0])), decreasing=dict(marker=dict(color=theme.SERIES[7])),
            totals=dict(marker=dict(color=theme.SERIES[6])), hovertemplate="%{x}: £%{y:,.0f}<extra></extra>",
            text=[gbp(abs(s[1])) for s in steps], textposition="outside", cliponaxis=False))
        fig.update_layout(showlegend=False, hovermode="closest")
        fig.update_xaxes(tickangle=0, tickfont=dict(size=10))
        st.plotly_chart(theme.style_fig(fig, 330, y_title="£"), width="stretch")
        st.caption("“No cost data” = net revenue of products without a reliable unit cost, excluded from gross profit.")

    c3, c4 = st.columns(2)
    with c3:
        theme.section("Net revenue by sales channel", "Monthly net revenue (£), complete months in the window")
        m = lines[~lines["is_cancelled"]].assign(month=lambda d: pd.to_datetime(d["order_date"]).dt.to_period("M").dt.start_time)
        g = complete_months(m.groupby(["month", "channel"])["net_revenue_gbp"].sum().reset_index(), "month", ctx.as_of)
        colors = series_color(list(ctx.frames["orders"]["channel"].unique()))
        fig = go.Figure()
        for chn in sorted(g["channel"].unique()):
            d = g[g["channel"] == chn]
            fig.add_scatter(x=d["month"], y=d["net_revenue_gbp"], name=chn, mode="lines+markers",
                            line=dict(color=colors[chn], width=2), marker=dict(size=7), hovertemplate="£%{y:,.0f}")
        st.plotly_chart(theme.style_fig(fig, 300, y_title="Net revenue (£)"), width="stretch")
    with c4:
        theme.section("Net revenue by region", "Share of net revenue in the selected window")
        g = lines[~lines["is_cancelled"]].groupby("region")["net_revenue_gbp"].sum().sort_values()
        colors = series_color(list(ctx.frames["orders"]["region"].unique()))
        fig = go.Figure(go.Bar(x=g.values, y=g.index, orientation="h", marker_color=[colors[r] for r in g.index],
                               text=[f"{gbp(v)} · {v / g.sum():.0%}" for v in g.values], textposition="auto",
                               hovertemplate="%{y}: £%{x:,.0f}<extra></extra>"))
        fig.update_layout(hovermode="closest")
        st.plotly_chart(theme.style_fig(fig, 300, x_title="Net revenue (£)"), width="stretch")

    theme.section("Period-over-period comparison", f"Current window vs {p_start:%d %b %Y} – {p_end:%d %b %Y}")
    rows = [("Gross sales", "gross_sales", gbp), ("Discounts", "discounts", gbp), ("Refunds", "refunds", gbp),
            ("Net revenue", "net_revenue", gbp), ("Orders", "orders", theme.fmt_num),
            ("Average order value", "aov", lambda x: theme.fmt_money(x, "£", 2)), ("Units", "units", theme.fmt_num),
            ("Gross profit", "gross_profit", gbp), ("Gross margin %", "gross_margin_pct", theme.fmt_pct),
            ("Discount rate (of gross)", "discount_rate", theme.fmt_pct),
            ("Return rate (units)", "return_rate_units", theme.fmt_pct),
            ("Cancellation rate (orders)", "cancellation_rate", theme.fmt_pct),
            ("Shipping income (excluded from revenue)", "shipping_income", gbp),
            ("Tax collected (excluded from revenue)", "tax_collected", gbp)]
    table = pd.DataFrame([{"Metric": n, "Current": f(k[m]), "Previous": f(pk[m]),
                           "Change": theme.fmt_pct(kpis.pct_change(k[m], pk[m]), signed=True)} for n, m, f in rows])
    dataframe(table)


def _weekly(lines: pd.DataFrame) -> pd.DataFrame:
    d = lines[~lines["is_cancelled"]]
    if d.empty:
        return pd.DataFrame(columns=["week", "net_revenue_gbp"])
    w = pd.to_datetime(d["order_date"]).dt.to_period("W-SUN").dt.start_time
    return d.groupby(w)["net_revenue_gbp"].sum().rename_axis("week").reset_index()
