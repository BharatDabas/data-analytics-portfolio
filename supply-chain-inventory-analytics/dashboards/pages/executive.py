"""A. Supply Chain Executive Overview."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from analytics.inventory import fill_rate, inventory_turnover
from dashboards import theme as t
from dashboards.context import Ctx, show_table

URGENCY_STATUS = {"Critical": "critical", "High": "serious", "Medium": "warning", "Low": "good", "No demand": "good"}


def render(ctx: Ctx) -> None:
    ctx.header("Supply Chain Executive Overview",
               "Network inventory exposure, service performance and supplier reliability")
    lb = ctx.cfg["inventory"]["demand_lookback_days"]

    pos = ctx.q("""SELECT sum(stock_value_usd) value, sum(greatest(on_hand_qty,0)) units
                   FROM v_inventory_position WHERE list_contains($locs, location_id) AND list_contains($cats, category)""").iloc[0]
    svc = ctx.q("""SELECT (demand_date > $as_of::DATE - $lb) AS cur, sum(requested_qty) req, sum(fulfilled_qty) ful,
                          sum(eligible_sku_days) elig, sum(stockout_sku_days) so, sum(unfilled_value_usd) unfilled
                   FROM v_service_daily WHERE demand_date > $as_of::DATE - 2 * $lb
                     AND list_contains($locs, location_id) AND list_contains($cats, category) GROUP BY 1""", lb=lb)
    cur = svc[svc["cur"]].iloc[0] if svc["cur"].any() else None
    prev = svc[~svc["cur"]].iloc[0] if (~svc["cur"]).any() else None
    fr = fill_rate(cur["ful"], cur["req"]) if cur is not None else float("nan")
    fr_prev = fill_rate(prev["ful"], prev["req"]) if prev is not None else float("nan")
    so = cur["so"] / cur["elig"] if cur is not None and cur["elig"] else float("nan")
    so_prev = prev["so"] / prev["elig"] if prev is not None and prev["elig"] else float("nan")

    turn = ctx.q("""WITH v AS (SELECT snapshot_date, sum(stock_value_usd) val FROM v_inventory_value_daily
                               WHERE snapshot_date > $as_of::DATE - 365 AND list_contains($locs, location_id)
                                 AND list_contains($cats, category) GROUP BY 1)
                    SELECT (SELECT sum(cogs_usd) FROM v_service_daily WHERE demand_date > $as_of::DATE - 365
                              AND list_contains($locs, location_id) AND list_contains($cats, category)) cogs,
                           (SELECT avg(val) FROM v) avg_val""").iloc[0]
    excess = ctx.q("""SELECT sum(r.excess_value_usd) ex, count(*) FILTER (WHERE r.urgency = 'Critical') crit
                      FROM mart_replenishment r JOIN dim_product p USING (sku)
                      WHERE list_contains($locs, r.location_id) AND list_contains($cats, p.category)""").iloc[0]
    otif = ctx.q("""SELECT avg(CASE WHEN otif THEN 1.0 ELSE 0 END) otif, count(*) n
                    FROM v_po_line_status l JOIN dim_product p USING (sku)
                    WHERE is_due AND promised_date > $as_of::DATE - 180
                      AND list_contains($locs, l.location_id) AND list_contains($cats, p.category)""").iloc[0]

    t.kpi_row([
        t.kpi_card("Inventory value", t.fmt_money(pos["value"]), f"{t.fmt_num(pos['units'])} units on hand (at cost)"),
        t.kpi_card("Fill rate", t.fmt_pct(fr), f"last {lb} days vs prior", delta=fr - fr_prev if fr_prev == fr_prev else None,
                   delta_fmt="{:+.1%}"),
        t.kpi_card("Stockout rate", t.fmt_pct(so), "of SKU-location-days with demand",
                   delta=so - so_prev if so_prev == so_prev else None, good_when_up=False),
        t.kpi_card("Excess inventory", t.fmt_money(excess["ex"]), "value above target stock"),
        t.kpi_card("Supplier OTIF", t.fmt_pct(otif["otif"]), f"{int(otif['n']):,} PO lines due, last 180 days"),
        t.kpi_card("Inventory turnover", f"{inventory_turnover(turn['cogs'] or 0, turn['avg_val'] or 0):.1f}x",
                   "COGS ÷ avg inventory value, 365 days"),
    ])
    rate = ctx.cfg["inventory"]["carrying_cost_rate"]
    t.note(f"Carrying-cost estimate: <b>{t.fmt_money((pos['value'] or 0) * rate)}</b> per year at {rate:.0%} of inventory value. "
           f"Unfilled demand in the last {lb} days: <b>{t.fmt_money(cur['unfilled'] if cur is not None else 0)}</b> at cost "
           f"(lost-sales proxy). Critical SKU-locations: <b>{int(excess['crit'] or 0)}</b>.")

    c1, c2 = st.columns(2)
    with c1:
        t.section("Inventory value by location", "Daily closing stock at standard cost (USD)")
        df = ctx.q("""SELECT snapshot_date, location_id, sum(stock_value_usd) value FROM v_inventory_value_daily
                      WHERE list_contains($locs, location_id) AND list_contains($cats, category) GROUP BY ALL ORDER BY 1""")
        all_locs = sorted(ctx.q("SELECT location_id FROM dim_location")["location_id"])
        # A stacked total is only shown for days where every selected location reported a snapshot;
        # otherwise a stale feed would look like a fall in inventory.
        per_day = df.groupby("snapshot_date")["location_id"].nunique()
        complete = per_day[per_day == df["location_id"].nunique()].index
        dropped = int(len(per_day) - len(complete))
        df = df[df["snapshot_date"].isin(complete)]
        fig = go.Figure()
        for loc in all_locs:
            g = df[df["location_id"] == loc]
            if not g.empty:
                fig.add_scatter(x=g["snapshot_date"], y=g["value"], name=loc, mode="lines", stackgroup="v",
                                line=dict(width=1.5, color=t.SERIES[all_locs.index(loc)]),
                                hovertemplate="%{y:$,.0f}")
        st.plotly_chart(t.style_fig(fig, 320, y_title="Inventory value (USD)"), width="stretch")
        if dropped:
            st.caption(f"{dropped} day(s) hidden: not every selected location has reported a snapshot "
                       "(see Data Quality).")
    with c2:
        t.section("Service level trend", "Weekly fill rate and stockout rate (share of SKU-location-days)")
        w = ctx.q("""SELECT date_trunc('week', demand_date) wk, sum(fulfilled_qty)/nullif(sum(requested_qty),0) fill,
                            sum(stockout_sku_days)/nullif(sum(eligible_sku_days),0) so
                     FROM v_service_daily WHERE list_contains($locs, location_id) AND list_contains($cats, category)
                     GROUP BY 1 HAVING count(DISTINCT demand_date) = 7 ORDER BY 1""")
        fig = go.Figure()
        fig.add_scatter(x=w["wk"], y=w["fill"], name="Fill rate", line=dict(width=2, color=t.SERIES[0]),
                        hovertemplate="%{y:.1%}")
        fig.add_scatter(x=w["wk"], y=w["so"], name="Stockout rate", line=dict(width=2, color=t.SERIES[1]),
                        hovertemplate="%{y:.1%}")
        fig.update_yaxes(tickformat=".0%", range=[0, 1])
        st.plotly_chart(t.style_fig(fig, 320, y_title="Share"), width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        t.section("Replenishment urgency", "SKU-locations by urgency under the default policy")
        u = ctx.q("""SELECT r.urgency, count(*) n FROM mart_replenishment r JOIN dim_product p USING (sku)
                     WHERE list_contains($locs, r.location_id) AND list_contains($cats, p.category) GROUP BY 1""")
        order = ["Critical", "High", "Medium", "Low", "No demand"]
        u = u.set_index("urgency").reindex(order).fillna(0).reset_index()
        fig = go.Figure(go.Bar(
            y=[t.status_label(URGENCY_STATUS[x], x) for x in u["urgency"]], x=u["n"], orientation="h",
            marker_color=[t.STATUS[URGENCY_STATUS[x]] for x in u["urgency"]], text=u["n"].astype(int),
            textposition="outside", hovertemplate="%{x} SKU-locations<extra></extra>"))
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(hovermode="closest")
        st.plotly_chart(t.style_fig(fig, 280, x_title="SKU-locations"), width="stretch")
    with c4:
        t.section("Supplier OTIF", "Share of due PO lines delivered on time and in full (all history)")
        s = ctx.q("SELECT supplier_name, otif_rate, due_lines FROM v_supplier_scorecard WHERE due_lines > 0 ORDER BY otif_rate")
        fig = go.Figure(go.Bar(y=s["supplier_name"], x=s["otif_rate"], orientation="h", marker_color=t.SERIES[0],
                               customdata=s["due_lines"],
                               hovertemplate="OTIF %{x:.1%}<br>%{customdata} due lines<extra></extra>"))
        fig.update_xaxes(tickformat=".0%", range=[0, 1])
        fig.update_layout(hovermode="closest")
        st.plotly_chart(t.style_fig(fig, 280, x_title="OTIF"), width="stretch")

    t.section("Operational exceptions", "Items needing attention today")
    ex = ctx.q("""
        SELECT 'Critical stock (SKU-locations)' AS exception, count(*) AS items FROM mart_replenishment r
          JOIN dim_product p USING (sku) WHERE r.urgency = 'Critical' AND list_contains($locs, r.location_id) AND list_contains($cats, p.category)
        UNION ALL SELECT 'Overdue PO lines', count(*) FROM v_po_line_status l JOIN dim_product p USING (sku)
          WHERE is_overdue AND list_contains($locs, l.location_id) AND list_contains($cats, p.category)
        UNION ALL SELECT 'Stale location snapshots', count(*) FROM v_location_freshness WHERE is_stale AND list_contains($locs, location_id)
        UNION ALL SELECT 'Negative on-hand records', count(*) FROM fact_inventory_snapshot WHERE is_negative_on_hand
        UNION ALL SELECT 'Forecast exceptions (WAPE > 60%)', count(*) FROM mart_forecast_accuracy a JOIN dim_product p USING (sku)
          WHERE selected AND wape > 0.6 AND list_contains($locs, a.location_id) AND list_contains($cats, p.category)
        UNION ALL SELECT 'Quarantined source rows', count(*) FROM ops_rejected_rows""")
    ex["status"] = [t.status_label("critical" if n and i < 2 else ("warning" if n else "good"), "Action" if n else "OK")
                    for i, n in enumerate(ex["items"])]
    show_table(ex)
