"""C. Portfolio Overview - valuation, holdings, allocation, P&L, fees, contribution, transaction ledger."""
from __future__ import annotations

from datetime import datetime, time, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analytics.ledger import LedgerError, positions_frame, run_ledger
from database import db
from dashboards import theme
from ingestion.csv_import import parse_transactions_csv
from config import settings


def _save_transactions(ctx, new: pd.DataFrame) -> None:
    """Validate against the whole ledger (catches overselling) then persist and recompute."""
    existing = ctx.q("SELECT * FROM fact_transactions")
    clash = set(new["transaction_id"]) & set(existing["transaction_id"])
    if clash:
        raise LedgerError(f"transaction_id already exists: {sorted(clash)[:5]}")
    run_ledger(pd.concat([existing, new], ignore_index=True))  # raises LedgerError if invalid
    db.append(ctx.con, "fact_transactions", new)
    from pipeline import refresh_portfolio
    refresh_portfolio(ctx.con, ctx.as_of, ctx.cfg["valuation"]["max_price_staleness_days"])
    st.session_state["data_version"] += 1


def _ledger_editor(ctx) -> None:
    theme.section("Transaction ledger", "Buys, sells, transfers and fees. Cost basis method: FIFO "
                                        "(see KPI catalogue for transfer and fee treatment).")
    tx = ctx.q("SELECT * FROM fact_transactions WHERE portfolio_id = ? ORDER BY executed_at DESC", (ctx.portfolio_id,))
    st.dataframe(tx[["transaction_id", "executed_at", "txn_type", "asset_id", "quantity", "unit_price", "fee",
                     "entry_source", "reference"]], hide_index=True, width="stretch", height=280,
                 column_config={"executed_at": st.column_config.DatetimeColumn("Executed (UTC)", format="YYYY-MM-DD HH:mm"),
                                "unit_price": st.column_config.NumberColumn("Unit price / carried cost", format="%.6g"),
                                "fee": st.column_config.NumberColumn("Fee (USD)", format="%.2f"),
                                "quantity": st.column_config.NumberColumn(format="%.8g")})
    add, imp = st.tabs(["Add transaction", "Import CSV"])
    with add:
        with st.form("add_txn", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            ttype = c1.selectbox("Type", ["BUY", "SELL", "TRANSFER_IN", "TRANSFER_OUT", "FEE"])
            asset = c2.selectbox("Asset (canonical ID)", ctx.assets["asset_id"].tolist(), format_func=ctx.label)
            day = c3.date_input("Date (UTC)", value=ctx.as_of, max_value=ctx.as_of)
            c4, c5, c6 = st.columns(3)
            qty = c4.number_input("Quantity", min_value=0.0, value=0.0, step=0.01, format="%.8f")
            price = c5.number_input("Unit price (USD) — carried cost basis for TRANSFER_IN", min_value=0.0, value=0.0,
                                    format="%.6f")
            fee = c6.number_input("Fee (USD)", min_value=0.0, value=0.0, format="%.2f")
            ref = st.text_input("Reference (optional)")
            submitted = st.form_submit_button("Add to ledger", type="primary")
        if submitted:
            new = pd.DataFrame([{
                "transaction_id": f"MAN-{datetime.now(timezone.utc):%Y%m%d%H%M%S%f}", "portfolio_id": ctx.portfolio_id,
                "asset_id": asset, "txn_type": ttype, "quantity": qty,
                "unit_price": price if ttype in ("BUY", "SELL", "TRANSFER_IN") else None, "fee": fee,
                "quote_currency": "usd", "executed_at": datetime.combine(day, time(12, 0), tzinfo=timezone.utc),
                "reference": ref or None, "entry_source": "manual", "loaded_at": datetime.now(timezone.utc)}])
            from analytics.ledger import validate_transactions
            errors = validate_transactions(new)
            if errors:
                st.error("; ".join(errors))
            else:
                try:
                    _save_transactions(ctx, new)
                    st.success(f"Added {ttype} {qty:g} {asset}. Holdings and P&L recalculated.")
                    st.rerun()
                except LedgerError as exc:
                    st.error(f"Rejected: {exc}")
    with imp:
        st.caption("Columns: transaction_id, asset_id, txn_type, quantity, unit_price, fee, executed_at "
                   "(+ optional portfolio_id, quote_currency, reference). Use canonical asset IDs, not tickers.")
        st.download_button("Download CSV template", (settings.SAMPLE_DIR / "transactions_template.csv").read_bytes(),
                           "transactions_template.csv", "text/csv")
        up = st.file_uploader("Upload transactions CSV", type=["csv"])
        if up is not None:
            res = parse_transactions_csv(up.getvalue(), ctx.portfolio_id, set(ctx.assets["asset_id"]))
            if not res.ok:
                st.error("Import rejected — nothing was loaded:\n\n- " + "\n- ".join(res.errors[:20]))
            else:
                st.dataframe(res.frame, hide_index=True, width="stretch")
                if st.button(f"Load {len(res.frame)} transactions", type="primary"):
                    try:
                        _save_transactions(ctx, res.frame)
                        st.success("Imported and recalculated.")
                        st.rerun()
                    except LedgerError as exc:
                        st.error(f"Rejected: {exc}")


def render(ctx) -> None:
    kinds = ctx.q("SELECT DISTINCT entry_source FROM fact_transactions WHERE portfolio_id = ?",
                  (ctx.portfolio_id,))["entry_source"].tolist() if ctx.portfolio_id else []
    ledger_chip = [("Synthetic demo ledger", "warn")] if kinds == ["demo_ledger"] else         [("Ledger: " + ", ".join(sorted(k.replace("_", " ") for k in kinds)), "")] if kinds else []
    theme.page_header("Portfolio Overview", "Holdings valued at the latest permitted close; FIFO cost basis.",
                      ctx.chips() + ledger_chip)
    if not ctx.portfolio_id:
        theme.empty_state("No portfolio transactions yet. Add one below or import a CSV.")
        return
    hold = ctx.q("SELECT * FROM v_current_holdings WHERE portfolio_id = ? ORDER BY market_value DESC NULLS LAST",
                 (ctx.portfolio_id,))
    tx = ctx.q("SELECT * FROM fact_transactions WHERE portfolio_id = ?", (ctx.portfolio_id,))
    pos = positions_frame(run_ledger(tx)) if not tx.empty else pd.DataFrame()
    daily = ctx.q("SELECT * FROM v_portfolio_daily WHERE portfolio_id = ? ORDER BY snapshot_date", (ctx.portfolio_id,))

    mv = hold["market_value"].sum()
    cost = hold["cost_basis"].sum()
    unreal = mv - cost
    realised = pos["realised_pnl"].sum() if len(pos) else 0.0
    fees = pos[["capitalised_fees", "sale_fees", "other_fees"]].sum() if len(pos) else pd.Series(dtype=float)
    total_fees = float(fees.sum()) if len(fees) else 0.0
    prev = daily.iloc[-2] if len(daily) > 1 else None
    day_change = (mv - prev["market_value"] - daily.iloc[-1]["external_flow"]) / prev["market_value"] \
        if prev is not None and prev["market_value"] else None
    unvalued = int(hold["market_value"].isna().sum())
    theme.kpi_row([
        theme.kpi_card("Portfolio value", theme.fmt_money(mv), "1-day change excl. flows", day_change),
        theme.kpi_card("Cost basis (open)", theme.fmt_money(cost), f"{len(hold)} open positions"),
        theme.kpi_card("Unrealised P&L", theme.fmt_money(unreal), "of open cost basis", unreal / cost if cost else None),
        theme.kpi_card("Realised P&L", theme.fmt_money(realised), "FIFO, net of sale fees"),
        theme.kpi_card("Fees paid", theme.fmt_money(total_fees),
                       f"buy {theme.fmt_money(fees.get('capitalised_fees', 0))} · sell {theme.fmt_money(fees.get('sale_fees', 0))}"
                       f" · other {theme.fmt_money(fees.get('other_fees', 0))}"),
    ])
    if unvalued or hold["price_is_stale"].any():
        theme.note(f"⚠ {int(hold['price_is_stale'].sum())} position(s) valued with the stale-price fallback "
                   f"(latest close ≤ {ctx.cfg['valuation']['max_price_staleness_days']} days old) and {unvalued} "
                   "position(s) could not be valued. Prices are never silently forward-filled.")

    theme.section("Holdings")
    view = hold.assign(weight=hold["market_value"] / mv if mv else None,
                       price_status=[theme.status_label("warning", f"Stale ({d})") if s else theme.status_label("good", "Current")
                                     for s, d in zip(hold["price_is_stale"], hold["price_date"])])
    st.dataframe(view[["name", "symbol", "units_held", "avg_cost", "close_price", "cost_basis", "market_value",
                       "unrealised_pnl", "weight", "price_status"]], hide_index=True, width="stretch",
                 column_config={"name": "Asset", "symbol": "Symbol",
                                "units_held": st.column_config.NumberColumn("Units", format="%.6g"),
                                "avg_cost": st.column_config.NumberColumn("Avg cost", format="$%.4g"),
                                "close_price": st.column_config.NumberColumn("Price", format="$%.4g"),
                                "cost_basis": st.column_config.NumberColumn("Cost basis", format="dollar"),
                                "market_value": st.column_config.NumberColumn("Market value", format="dollar"),
                                "unrealised_pnl": st.column_config.NumberColumn("Unrealised P&L", format="dollar"),
                                "weight": st.column_config.ProgressColumn("Allocation", format="percent", min_value=0, max_value=1),
                                "price_status": "Valuation price"})

    left, right = st.columns(2)
    with left:
        theme.section("Asset allocation", "Share of current market value.")
        a = view.dropna(subset=["weight"]).sort_values("weight")
        fig = go.Figure(go.Bar(x=a["weight"], y=a["name"], orientation="h", marker_color=[ctx.colors.get(x) for x in a["asset_id"]],
                               text=[theme.fmt_pct(v) for v in a["weight"]], textposition="outside",
                               hovertemplate="%{y}: %{x:.1%}<extra></extra>"))
        fig.update_xaxes(tickformat=".0%")
        fig.update_layout(hovermode="closest")
        st.plotly_chart(theme.style_fig(fig, 300, x_title="Allocation"), width="stretch")
    with right:
        theme.section("Contribution to P&L", "Unrealised + realised − other fees, by asset (incl. closed positions).")
        contrib = pos.merge(hold[["asset_id", "unrealised_pnl"]], on="asset_id", how="left").fillna({"unrealised_pnl": 0})
        contrib["total"] = contrib["unrealised_pnl"] + contrib["realised_pnl"] - contrib["other_fees"]
        contrib = contrib.merge(ctx.assets[["asset_id", "name"]], on="asset_id").sort_values("total")
        fig = go.Figure(go.Bar(x=contrib["total"], y=contrib["name"], orientation="h",
                               marker_color=["#2a78d6" if v >= 0 else "#d03b3b" for v in contrib["total"]],
                               hovertemplate="%{y}: $%{x:,.0f}<extra></extra>"))
        fig.update_xaxes(tickformat=",.0f", zeroline=True, zerolinecolor=theme.HAIRLINE)
        fig.update_layout(hovermode="closest")
        st.plotly_chart(theme.style_fig(fig, 300, x_title="P&L contribution (USD)"), width="stretch")

    theme.section("Value vs cost basis", "Daily market value of holdings against open cost basis (USD). Days with an "
                                         "unvalued position are left blank.")
    d = daily.copy()
    d["snapshot_date"] = pd.to_datetime(d["snapshot_date"])
    d.loc[d["unvalued_positions"] > 0, "market_value"] = None
    d = d[(d["snapshot_date"].dt.date >= ctx.start) & (d["snapshot_date"].dt.date <= ctx.end)]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=d["snapshot_date"], y=d["market_value"], name="Market value", connectgaps=False,
                             line=dict(color=theme.SERIES[0], width=2), hovertemplate="$%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=d["snapshot_date"], y=d["cost_basis"], name="Cost basis",
                             line=dict(color=theme.INK_3, width=1.6, shape="hv"), hovertemplate="$%{y:,.0f}"))
    fig.update_yaxes(tickprefix="$")
    st.plotly_chart(theme.style_fig(fig, 320, y_title="USD"), width="stretch")

    _ledger_editor(ctx)
