"""B. Inventory Health - stock position, cover, ageing, slow-moving, excess and critical stock."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboards import theme as t
from dashboards.context import Ctx, show_table

COVER_BANDS = ["Out of stock", "< 7 days", "7-30 days", "30-90 days", "90-180 days", "> 180 days", "No demand"]


def render(ctx: Ctx) -> None:
    ctx.header("Inventory Health", "On-hand, available, reserved and in-transit stock with cover and ageing by SKU/location")
    slow_days = ctx.cfg["inventory"]["slow_moving_days"]
    slow_cover = ctx.cfg["inventory"]["slow_cover_days"]
    df = ctx.q("""
        SELECT ip.*, r.target_stock, r.safety_stock, r.reorder_point, r.excess_qty, r.excess_value_usd, r.urgency
        FROM v_inventory_position ip LEFT JOIN mart_replenishment r USING (sku, location_id)
        WHERE list_contains($locs, ip.location_id) AND list_contains($cats, ip.category)""")
    if df.empty:
        t.empty_state("No inventory positions for the selected filters.")
        return

    t.kpi_row([
        t.kpi_card("On hand", t.fmt_num(df["on_hand_qty"].clip(lower=0).sum()), "units (base UoM: each)"),
        t.kpi_card("Available", t.fmt_num(df["available_qty"].clip(lower=0).sum()), "on hand − reserved"),
        t.kpi_card("Reserved", t.fmt_num(df["reserved_qty"].sum()), "allocated to open orders"),
        t.kpi_card("In transit", t.fmt_num(df["in_transit_qty"].sum()), "dispatched, not yet received"),
        t.kpi_card("Stock value", t.fmt_money(df["stock_value_usd"].sum()), "at standard cost"),
        t.kpi_card("Median cover", f"{df['days_of_cover'].median():.0f} days", "available ÷ avg daily demand"),
    ])
    lb = ctx.cfg["inventory"]["demand_lookback_days"]
    t.note(f"Days of cover = available stock ÷ average daily demand over the last {lb} days. SKU-locations without demand "
           "in the window have undefined cover and are shown as <i>No demand</i>. Negative on-hand records are flagged "
           "and valued at zero.")

    def band(r):
        if r.avg_daily_demand <= 0:
            return "No demand"
        if r.available_qty <= 0:
            return "Out of stock"
        c = r.days_of_cover
        return "< 7 days" if c < 7 else "7-30 days" if c < 30 else "30-90 days" if c < 90 else \
            "90-180 days" if c < 180 else "> 180 days"
    df["cover_band"] = [band(r) for r in df.itertuples()]

    c1, c2 = st.columns(2)
    with c1:
        t.section("Stock value by days-of-cover band", "Where working capital sits relative to demand")
        b = df.groupby("cover_band").agg(value=("stock_value_usd", "sum"), n=("sku", "size")).reindex(COVER_BANDS).fillna(0)
        ramp = [t.SEQUENTIAL[i] for i in [6, 5, 4, 3, 2, 1]] + ["#b9b8b2"]  # ordinal: short cover = darkest
        fig = go.Figure(go.Bar(x=b.index, y=b["value"], marker_color=ramp, customdata=b["n"],
                               hovertemplate="%{x}<br>%{y:$,.0f}<br>%{customdata} SKU-locations<extra></extra>"))
        fig.update_layout(hovermode="closest")
        st.plotly_chart(t.style_fig(fig, 300, y_title="Stock value (USD)"), width="stretch")
    with c2:
        t.section("Stock ageing (FIFO)", "Value of current on-hand by age since receipt, per location")
        a = ctx.q("""SELECT a.location_id, a.age_bucket, a.bucket_order, sum(a.value_usd) value
                     FROM mart_stock_ageing a JOIN dim_product p USING (sku)
                     WHERE list_contains($locs, a.location_id) AND list_contains($cats, p.category)
                     GROUP BY ALL ORDER BY bucket_order""")
        fig = go.Figure()
        buckets = a.drop_duplicates("age_bucket").sort_values("bucket_order")
        for i, (_, row) in enumerate(buckets.iterrows()):
            g = a[a["age_bucket"] == row["age_bucket"]]
            fig.add_bar(x=g["location_id"], y=g["value"], name=row["age_bucket"],
                        marker_color=t.SEQUENTIAL[min(1 + i, 6)], hovertemplate="%{y:$,.0f}")
        fig.update_layout(barmode="stack")
        st.plotly_chart(t.style_fig(fig, 300, y_title="Stock value (USD)"), width="stretch")

    tabs = st.tabs(["Critical stock", "Excess inventory", "Slow-moving / no movement", "All SKU-locations"])
    cols = {"sku": "SKU", "product_name": "Product", "location_id": "Location", "abc_class": "ABC",
            "on_hand_qty": "On hand", "available_qty": "Available", "on_order_qty": "On order",
            "avg_daily_demand": "Avg daily demand", "days_of_cover": "Days cover", "safety_stock": "Safety stock",
            "target_stock": "Target stock", "excess_qty": "Excess qty", "excess_value_usd": "Excess value",
            "stock_value_usd": "Stock value", "days_since_last_outbound": "Days since last issue", "urgency": "Urgency"}
    fmt = {"Avg daily demand": st.column_config.NumberColumn(format="%.1f"),
           "Days cover": st.column_config.NumberColumn(format="%.1f"),
           "Safety stock": st.column_config.NumberColumn(format="%.0f"),
           "Target stock": st.column_config.NumberColumn(format="%.0f"),
           "Excess qty": st.column_config.NumberColumn(format="%.0f"),
           "Excess value": st.column_config.NumberColumn(format="$%.0f"),
           "Stock value": st.column_config.NumberColumn(format="$%.0f")}
    view = df.rename(columns=cols)
    with tabs[0]:
        crit = view[(df["available_qty"] < df["safety_stock"].fillna(0)) & (df["avg_daily_demand"] > 0)]
        st.caption(f"{len(crit)} SKU-locations with available stock below safety stock.")
        show_table(crit.sort_values("Days cover")[["SKU", "Product", "Location", "ABC", "Available", "Safety stock",
                                                   "On order", "Avg daily demand", "Days cover", "Urgency"]], **fmt)
    with tabs[1]:
        exc = view[df["excess_qty"].fillna(0) > 0].sort_values("Excess value", ascending=False)
        st.caption(f"{len(exc)} SKU-locations above target stock · excess value {t.fmt_money(exc['Excess value'].sum())}.")
        show_table(exc[["SKU", "Product", "Location", "ABC", "On hand", "Target stock", "Excess qty", "Excess value",
                        "Days cover"]], **fmt)
    with tabs[2]:
        slow = view[(df["on_hand_qty"] > 0) & ((df["days_since_last_outbound"].fillna(9999) >= slow_days)
                                               | (df["days_of_cover"].fillna(0) > slow_cover))]
        st.caption(f"Stock with no issue for ≥ {slow_days} days or cover above {slow_cover} days.")
        show_table(slow.sort_values("Stock value", ascending=False)[
            ["SKU", "Product", "Location", "On hand", "Stock value", "Days cover", "Days since last issue"]], **fmt)
    with tabs[3]:
        show_table(view[list(cols.values())].sort_values(["SKU", "Location"]), height=420, **fmt)
