"""F. Warehouse & Network - stock distribution, movements, transfers and location imbalance."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from dashboards import theme as t
from dashboards.context import Ctx, show_table

MOVE_TYPES = ["ISSUE", "RECEIPT", "TRANSFER_IN", "TRANSFER_OUT", "ADJUSTMENT"]


def render(ctx: Ctx) -> None:
    ctx.header("Warehouse & Network", "Inventory distribution across locations, stock movements and rebalancing opportunities")
    loc = ctx.q("""SELECT l.location_id, l.location_name, l.region, l.location_type, l.capacity_units,
                          sum(greatest(ip.on_hand_qty, 0)) on_hand, sum(ip.stock_value_usd) value,
                          max(ip.snapshot_ts) latest_snapshot
                   FROM dim_location l JOIN v_inventory_position ip USING (location_id)
                   WHERE list_contains($locs, l.location_id) AND list_contains($cats, ip.category)
                   GROUP BY ALL ORDER BY l.location_id""")
    if loc.empty:
        t.empty_state("No locations match the filters.")
        return
    loc["utilisation"] = loc["on_hand"] / loc["capacity_units"]
    t.kpi_row([t.kpi_card(r.location_id, t.fmt_money(r.value),
                          f"{t.fmt_num(r.on_hand)} units · {r.utilisation:.0%} of capacity") for r in loc.itertuples()])
    t.note("Capacity utilisation = on-hand units ÷ nominal capacity in units (all categories shown only when every "
           "category is selected). Latest snapshot per location is used — a stale feed is flagged in the header.")

    c1, c2 = st.columns(2)
    with c1:
        t.section("Stock value by category and location", "Latest snapshot, USD at standard cost")
        h = ctx.q("""SELECT category, location_id, sum(stock_value_usd) value FROM v_inventory_position
                     WHERE list_contains($locs, location_id) AND list_contains($cats, category) GROUP BY ALL""")
        pv = h.pivot_table(index="category", columns="location_id", values="value", aggfunc="sum").fillna(0)
        fig = go.Figure(go.Heatmap(z=pv.values, x=pv.columns, y=pv.index, xgap=2, ygap=2,
                                   colorscale=[[0, t.SEQUENTIAL[0]], [1, t.SEQUENTIAL[6]]],
                                   text=[[t.fmt_money(v) for v in row] for row in pv.values], texttemplate="%{text}",
                                   hovertemplate="%{y} @ %{x}<br>%{z:$,.0f}<extra></extra>",
                                   colorbar=dict(title="USD", thickness=10)))
        fig.update_layout(hovermode="closest")
        fig.update_yaxes(showgrid=False)
        st.plotly_chart(t.style_fig(fig, 340), width="stretch")
    with c2:
        t.section("Weekly stock movements", "Gross units moved per week by movement type")
        mv = ctx.q("""SELECT date_trunc('week', m.movement_date) wk, m.movement_type, sum(abs(m.quantity)) qty
                      FROM fact_inventory_movement m JOIN dim_product p USING (sku)
                      WHERE list_contains($locs, m.location_id) AND list_contains($cats, p.category) GROUP BY ALL ORDER BY 1""")
        fig = go.Figure()
        for i, mt in enumerate(MOVE_TYPES):
            g = mv[mv["movement_type"] == mt]
            if not g.empty:
                fig.add_scatter(x=g["wk"], y=g["qty"], name=mt.replace("_", " ").title(), mode="lines",
                                line=dict(width=2, color=t.SERIES[i]), hovertemplate="%{y:,.0f} units")
        fig.update_yaxes(type="log")
        st.plotly_chart(t.style_fig(fig, 340, y_title="Units per week (log scale)"), width="stretch")

    t.section("Location imbalance", "SKUs where one location holds > 60 days of cover while another holds < 7 days — "
              "candidate transfers (decision support)")
    imb = ctx.q("""WITH p AS (SELECT sku, product_name, location_id, available_qty, avg_daily_demand, days_of_cover
                              FROM v_inventory_position WHERE avg_daily_demand > 0
                                AND list_contains($locs, location_id) AND list_contains($cats, category))
                   SELECT a.sku, a.product_name, a.location_id AS from_location, a.days_of_cover AS from_cover,
                          b.location_id AS to_location, b.days_of_cover AS to_cover,
                          floor(least(greatest(a.available_qty - a.avg_daily_demand * 30, 0), b.avg_daily_demand * 21)) AS suggested_transfer_qty
                   FROM p a JOIN p b ON a.sku = b.sku AND a.location_id <> b.location_id
                   WHERE a.days_of_cover > 60 AND b.days_of_cover < 7
                   QUALIFY row_number() OVER (PARTITION BY b.sku, b.location_id ORDER BY a.days_of_cover DESC) = 1
                   ORDER BY b.days_of_cover""")
    show_table(imb[imb["suggested_transfer_qty"] > 0],
               empty="No imbalance today: no SKU has > 60 days of cover at one location and < 7 days at another.",
               from_cover=st.column_config.NumberColumn("From cover (d)", format="%.0f"),
               to_cover=st.column_config.NumberColumn("To cover (d)", format="%.1f"),
               suggested_transfer_qty=st.column_config.NumberColumn("Suggested transfer qty", format="%.0f"))
    st.caption("Suggested transfer keeps 30 days of cover at the source and sends up to 21 days of the destination's demand.")

    t.section("Recent transfers", "Inter-location stock transfers (latest 200 movement lines)")
    tr = ctx.q("""SELECT movement_date, reference, sku, location_id, movement_type, quantity FROM fact_inventory_movement
                  WHERE movement_type IN ('TRANSFER_IN', 'TRANSFER_OUT') AND list_contains($locs, location_id)
                  ORDER BY movement_ts DESC LIMIT 200""")
    show_table(tr, height=300, movement_date=st.column_config.DateColumn("Date"),
               quantity=st.column_config.NumberColumn("Qty (EA)", format="%.0f"))
