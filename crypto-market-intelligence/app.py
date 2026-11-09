"""Crypto Market Intelligence & Portfolio Analytics - Streamlit entry point.

Run:  streamlit run app.py
The DuckDB database is built automatically from synthetic demo data on first start.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from config import settings
from dashboards import theme
from dashboards.data import Ctx, color_map, get_connection, load
from dashboards.pages import alerts_ops, asset_explorer, market_overview, performance, portfolio_overview, risk

BRAND = theme.Brand(name="Crypto Market Intelligence", tagline="Market · Portfolio · Risk analytics",
                    accent="#6d4aff", accent_soft="#ece8ff", icon="◆")
PAGES = {
    "Market Overview": market_overview,
    "Asset Explorer": asset_explorer,
    "Portfolio Overview": portfolio_overview,
    "Risk & Diversification": risk,
    "Performance & Benchmark": performance,
    "Alerts & Data Operations": alerts_ops,
}

st.set_page_config(page_title=BRAND.name, page_icon="◆", layout="wide", initial_sidebar_state="expanded")
theme.register_plotly_template()
theme.inject_css(BRAND)


def ensure_database(path: Path) -> None:
    if path.exists():
        return
    with st.spinner("First run: generating the synthetic demo dataset and building the DuckDB model…"):
        from pipeline import build
        build(db_path=path)


db_file = settings.db_path()
try:
    ensure_database(db_file)
    con = get_connection(str(db_file))
except Exception as exc:  # surface a clear, actionable error instead of a stack trace
    st.error(f"Could not open or build the analytics database at `{db_file}`: {exc}\n\n"
             "Try `python pipeline.py --rebuild` from the project folder.")
    st.stop()

st.session_state.setdefault("data_version", 0)
version = st.session_state["data_version"]
cfg = settings.load_settings()

theme.sidebar_brand(BRAND)
SLUGS = {"market-overview": 0, "asset-explorer": 1, "portfolio-overview": 2, "risk": 3, "performance": 4, "alerts-ops": 5}
# Deep links: ?page=<slug> opens a specific page (used for sharing and screenshots).
page_name = st.sidebar.radio("Navigate", list(PAGES), label_visibility="collapsed",
                             index=SLUGS.get(st.query_params.get("page", ""), 0))

sources = load(con, """SELECT s.source_id, s.data_kind, s.provider FROM dim_source s
                       WHERE EXISTS (SELECT 1 FROM fact_market_price p WHERE p.source_id = s.source_id)
                       ORDER BY s.data_kind DESC""", (), version)
if sources.empty:
    st.warning("No market data loaded yet. Run `python pipeline.py` to ingest data.")
    st.stop()

st.sidebar.markdown("---")
source_id = st.sidebar.selectbox("Market data source", sources["source_id"].tolist(),
                                 format_func=lambda s: sources.set_index("source_id").loc[s, "provider"])
data_kind = sources.set_index("source_id").loc[source_id, "data_kind"]
assets = load(con, "SELECT * FROM dim_asset WHERE source_id = ? ORDER BY asset_id", (source_id,), version)
bounds = load(con, "SELECT MIN(price_date) AS lo, MAX(price_date) AS hi FROM fact_market_price WHERE source_id = ?",
              (source_id,), version)
lo, hi = pd.Timestamp(bounds["lo"].iloc[0]).date(), pd.Timestamp(bounds["hi"].iloc[0]).date()

preset = st.sidebar.selectbox("Period", ["Last 90 days", "Last 180 days", "Last 365 days", "Year to date",
                                         "All history", "Custom"], index=2)
if preset == "Custom":
    picked = st.sidebar.date_input("Date range", (max(lo, hi - timedelta(days=365)), hi), min_value=lo, max_value=hi)
    start, end = (picked if isinstance(picked, tuple) and len(picked) == 2 else (lo, hi))
else:
    days = {"Last 90 days": 90, "Last 180 days": 180, "Last 365 days": 365}.get(preset)
    start = hi - timedelta(days=days - 1) if days else (hi.replace(month=1, day=1) if preset == "Year to date" else lo)
    start, end = max(start, lo), hi

portfolios = load(con, "SELECT DISTINCT portfolio_id FROM fact_transactions ORDER BY 1", (), version)["portfolio_id"].tolist()
portfolio_id = st.sidebar.selectbox("Portfolio", portfolios) if portfolios else None

st.sidebar.markdown("---")
st.sidebar.caption(
    "Analytics and decision support only — no trade execution and no investment advice. "
    + ("Prices shown are **synthetic** and belong to fictional demo assets." if data_kind == "synthetic" else
       "Market data © its provider; check licence terms before redistribution."))

ctx = Ctx(con=con, cfg=cfg, source_id=source_id, data_kind=data_kind, assets=assets,
          colors=color_map(assets["asset_id"].tolist()), start=start, end=end, as_of=hi, version=version,
          portfolio_id=portfolio_id)

try:
    PAGES[page_name].render(ctx)
except Exception as exc:
    st.error(f"This page failed to render: {exc}")
    st.exception(exc)
