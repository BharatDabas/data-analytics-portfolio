"""C. Demand & Forecast - history, seasonality, forecast vs actual, error, exceptions and ABC/XYZ."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics.forecasting import bias, mae, wape
from dashboards import theme as t
from dashboards.context import Ctx, show_table


def render(ctx: Ctx) -> None:
    ctx.header("Demand & Forecast", "Weekly demand history, baseline forecasts, backtest accuracy and planner exceptions")
    bt = ctx.q("""SELECT f.sku, f.location_id, p.category, f.horizon_weeks, f.predicted_qty, f.actual_qty
                  FROM v_forecast_vs_actual f JOIN dim_product p USING (sku)
                  JOIN mart_forecast_accuracy a ON a.sku = f.sku AND a.location_id = f.location_id
                       AND a.method = f.method AND a.selected
                  WHERE f.run_type = 'backtest' AND list_contains($locs, f.location_id) AND list_contains($cats, p.category)""")
    if bt.empty:
        t.empty_state("No forecasts for the selected filters.")
        return
    prod = ctx.q("""SELECT sum(predicted_qty) q FROM fact_forecast f JOIN dim_product p USING (sku)
                    WHERE run_type = 'production' AND horizon_weeks <= 4
                      AND list_contains($locs, f.location_id) AND list_contains($cats, p.category)""").iloc[0]
    last4 = ctx.q("""SELECT sum(requested_qty) q FROM v_demand_weekly w JOIN dim_product p USING (sku)
                     WHERE days_in_week = 7 AND week_start > (SELECT max(week_start) FROM v_demand_weekly WHERE days_in_week = 7) - 28
                       AND list_contains($locs, w.location_id) AND list_contains($cats, p.category)""").iloc[0]
    t.kpi_row([
        t.kpi_card("Backtest WAPE", t.fmt_pct(wape(bt["actual_qty"], bt["predicted_qty"])), "selected method, all horizons"),
        t.kpi_card("Backtest MAE", f"{mae(bt['actual_qty'], bt['predicted_qty']):.1f}", "units per SKU-location-week"),
        t.kpi_card("Forecast bias", t.fmt_pct(bias(bt["actual_qty"], bt["predicted_qty"]), signed=True),
                   "+ = over-forecast"),
        t.kpi_card("Next 4 weeks forecast", t.fmt_num(prod["q"]), f"vs {t.fmt_num(last4['q'])} units in last 4 weeks"),
    ])

    t.section("Demand history and forecast", "Weekly requested quantity (units); shaded band = ~80% prediction interval")
    skus = ctx.q("""SELECT DISTINCT p.sku || ' · ' || p.product_name AS label, p.sku FROM dim_product p
                    WHERE list_contains($cats, p.category) ORDER BY 1""")
    c1, c2 = st.columns([2, 1])
    choice = c1.selectbox("Series", ["All selected SKUs (aggregate)"] + skus["label"].tolist())
    sku_filter = None if choice.startswith("All") else skus.loc[skus["label"] == choice, "sku"].iloc[0]
    show_bt = c2.toggle("Show backtest forecasts", value=True)
    sku_sql = "" if sku_filter is None else " AND w.sku = $sku"
    hist = ctx.q(f"""SELECT week_start, sum(requested_qty) qty FROM v_demand_weekly w JOIN dim_product p USING (sku)
                     WHERE days_in_week = 7 AND list_contains($locs, w.location_id) AND list_contains($cats, p.category)
                     {sku_sql} GROUP BY 1 ORDER BY 1""", sku=sku_filter)
    fsql = sku_sql.replace("w.sku", "f.sku")
    fc = ctx.q(f"""SELECT target_week, sum(predicted_qty) q, sum(lower_qty) lo, sum(upper_qty) hi
                   FROM fact_forecast f JOIN dim_product p USING (sku) WHERE run_type = 'production'
                   AND list_contains($locs, f.location_id) AND list_contains($cats, p.category) {fsql} GROUP BY 1 ORDER BY 1""",
                sku=sku_filter)
    fig = go.Figure()
    fig.add_scatter(x=hist["week_start"], y=hist["qty"], name="Actual demand", line=dict(width=2, color=t.SERIES[0]),
                    hovertemplate="%{y:,.0f} units")
    if show_bt:
        b = ctx.q(f"""SELECT f.target_week, sum(f.predicted_qty) q FROM fact_forecast f JOIN dim_product p USING (sku)
                      JOIN mart_forecast_accuracy a ON a.sku = f.sku AND a.location_id = f.location_id
                           AND a.method = f.method AND a.selected
                      WHERE f.run_type = 'backtest' AND f.horizon_weeks = 1 AND list_contains($locs, f.location_id)
                        AND list_contains($cats, p.category) {fsql} GROUP BY 1 ORDER BY 1""", sku=sku_filter)
        fig.add_scatter(x=b["target_week"], y=b["q"], name="Backtest (1-week ahead)", mode="lines+markers",
                        line=dict(width=2, color=t.SERIES[1]), marker=dict(size=8), hovertemplate="%{y:,.0f} units")
    fig.add_scatter(x=list(fc["target_week"]) + list(fc["target_week"])[::-1], y=list(fc["hi"]) + list(fc["lo"])[::-1],
                    fill="toself", fillcolor="rgba(27,175,122,0.15)", line=dict(width=0), name="Prediction interval",
                    hoverinfo="skip")
    fig.add_scatter(x=fc["target_week"], y=fc["q"], name="Forecast", line=dict(width=2, color=t.SERIES[2]),
                    hovertemplate="%{y:,.0f} units")
    st.plotly_chart(t.style_fig(fig, 360, y_title="Units per week"), width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        t.section("Seasonality", "Monthly demand index by category (1.0 = category average)")
        s = ctx.q("""WITH m AS (SELECT p.category, month(d.demand_date) mon, sum(d.requested_qty) / count(DISTINCT d.demand_date) per_day
                               FROM fact_demand d JOIN dim_product p USING (sku)
                               WHERE list_contains($locs, d.location_id) AND list_contains($cats, p.category) GROUP BY ALL)
                     SELECT category, mon, per_day / avg(per_day) OVER (PARTITION BY category) idx FROM m ORDER BY category, mon""")
        all_cats = sorted(ctx.q("SELECT DISTINCT category FROM dim_product")["category"])
        fig = go.Figure()
        for cat in all_cats:
            g = s[s["category"] == cat]
            if not g.empty:
                fig.add_scatter(x=g["mon"], y=g["idx"], name=cat, line=dict(width=2, color=t.SERIES[all_cats.index(cat)]),
                                hovertemplate="%{y:.2f}")
        fig.update_xaxes(tickvals=list(range(1, 13)), ticktext=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
                                                                "Sep", "Oct", "Nov", "Dec"])
        st.plotly_chart(t.style_fig(fig, 320, y_title="Demand index"), width="stretch")
    with c4:
        t.section("ABC / XYZ matrix", "Annual consumption value (ABC) × weekly demand variability (XYZ); cell = SKUs")
        m = ctx.q("""SELECT abc_class, xyz_class, count(*) n, sum(annual_consumption_value) acv FROM dim_product
                     WHERE list_contains($cats, category) GROUP BY ALL""")
        grid = m.pivot_table(index="abc_class", columns="xyz_class", values="n", aggfunc="sum").reindex(
            index=["A", "B", "C"], columns=["X", "Y", "Z"]).fillna(0)
        acv = m.pivot_table(index="abc_class", columns="xyz_class", values="acv", aggfunc="sum").reindex(
            index=["A", "B", "C"], columns=["X", "Y", "Z"]).fillna(0)
        fig = go.Figure(go.Heatmap(z=grid.values, x=["X stable", "Y variable", "Z erratic"], y=["A", "B", "C"],
                                   colorscale=[[0, t.SEQUENTIAL[0]], [1, t.SEQUENTIAL[5]]], customdata=acv.values,
                                   text=grid.values.astype(int), texttemplate="%{text}", xgap=2, ygap=2,
                                   hovertemplate="%{y}%{x}<br>%{z} SKUs<br>ACV %{customdata:$,.0f}<extra></extra>",
                                   colorbar=dict(title="SKUs", thickness=10)))
        fig.update_yaxes(autorange="reversed", showgrid=False)
        fig.update_layout(hovermode="closest")
        st.plotly_chart(t.style_fig(fig, 320), width="stretch")

    t.section("Forecast accuracy by category", "Backtest of the selected method per series; rolling origin, 1–4 weeks ahead")
    rows = []
    for cat, g in bt.groupby("category"):
        rows.append({"Category": cat, "WAPE": wape(g["actual_qty"], g["predicted_qty"]),
                     "MAE": mae(g["actual_qty"], g["predicted_qty"]), "Bias": bias(g["actual_qty"], g["predicted_qty"]),
                     "Actual units": g["actual_qty"].sum()})
    show_table(pd.DataFrame(rows), WAPE=st.column_config.NumberColumn(format="percent"),
               Bias=st.column_config.NumberColumn(format="percent"), MAE=st.column_config.NumberColumn(format="%.1f"),
               **{"Actual units": st.column_config.NumberColumn(format="%.0f")})

    t.section("Forecast exceptions", "Series with WAPE above 60% or absolute bias above 25% — review method or treat as intermittent")
    exc = ctx.q("""SELECT a.sku, p.product_name, a.location_id, p.abc_class, p.xyz_class, a.demand_pattern, a.method,
                          a.wape, a.bias, a.mae, a.actual_qty
                   FROM mart_forecast_accuracy a JOIN dim_product p USING (sku)
                   WHERE a.selected AND (a.wape > 0.6 OR abs(a.bias) > 0.25 OR a.wape IS NULL)
                     AND list_contains($locs, a.location_id) AND list_contains($cats, p.category)
                   ORDER BY a.actual_qty DESC""")
    show_table(exc, sku="SKU", product_name="Product", location_id="Location", abc_class="ABC", xyz_class="XYZ",
               demand_pattern="Pattern", method="Selected method", wape=st.column_config.NumberColumn("WAPE", format="percent"),
               bias=st.column_config.NumberColumn("Bias", format="percent"),
               mae=st.column_config.NumberColumn("MAE", format="%.1f"),
               actual_qty=st.column_config.NumberColumn("Backtest actual units", format="%.0f"))
    t.note("Methods: 8-week moving average, simple exponential smoothing (α = 0.3), seasonal naïve (52 weeks) and "
           "Croston-SBA for intermittent demand. The method with the lowest backtest WAPE is selected per SKU-location "
           "(MAE when WAPE is undefined because actual demand is zero). Selection uses the same backtest window, so "
           "reported accuracy is slightly optimistic. Forecasts are decision support and need planner validation.")
