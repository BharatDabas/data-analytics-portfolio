"""Retail Commercial Intelligence - Streamlit entry point.

    streamlit run app.py
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import streamlit as st

from config import settings
from dashboards import theme
from dashboards.data import Context, load_frames
from dashboards.pages import (cohorts, customers, data_quality, executive, forecast, marketing, products,
                              returns)

BRAND = theme.Brand(name="Retail Commercial Intelligence", tagline="Sales · Customers · Margin · Marketing",
                    accent="#0f766e", accent_soft="#d5f5ef", icon="R")
PAGES = {
    "Executive Trading Overview": executive,
    "Product & Category": products,
    "Customer Intelligence": customers,
    "Cohort & Retention": cohorts,
    "Marketing Performance": marketing,
    "Returns & Operations": returns,
    "Forecast & Planning": forecast,
    "Data Quality & Pipeline": data_quality,
}

# URL slugs for deep links, e.g. ?page=cohort-retention (same order as PAGES)
SLUGS = ["executive-overview", "product-category", "customer-intelligence", "cohort-retention",
         "marketing-performance", "returns-operations", "forecast-planning", "data-quality"]

st.set_page_config(page_title="Retail Commercial Intelligence", page_icon="🛍️", layout="wide")
theme.register_plotly_template()
theme.inject_css(BRAND)

db_path = Path(os.getenv("RETAIL_DB_PATH", str(settings.DB_PATH)))
if not db_path.exists():
    with st.spinner("First run: generating the synthetic demo dataset and building the DuckDB warehouse…"):
        from pipeline import run_pipeline
        run_pipeline(db_path=db_path)


@st.cache_data(show_spinner="Loading analytical model…")
def _frames(path: str, mtime: float):
    return load_frames(path)


try:
    frames = _frames(str(db_path), db_path.stat().st_mtime)
except Exception as exc:  # surface a clear message instead of a stack trace
    st.error(f"Could not read the analytical database at `{db_path}`: {exc}. "
             "Rebuild it with `python pipeline.py`.")
    st.stop()

orders = frames["orders"]
if orders.empty:
    theme.empty_state("The warehouse contains no orders. Run `python pipeline.py` to load data.")
    st.stop()

as_of = max(orders["order_date"])
min_date = min(orders["order_date"])
rules = settings.load_business_rules()

theme.sidebar_brand(BRAND)
slug = st.query_params.get("page", "")
page_name = st.sidebar.radio("Navigate", list(PAGES), label_visibility="collapsed",
                             index=SLUGS.index(slug) if slug in SLUGS else 0)
st.sidebar.markdown("---")
st.sidebar.caption("FILTERS")
default_start = max(min_date, as_of - timedelta(days=364))
dates = st.sidebar.date_input("Order date range", (default_start, as_of), min_value=min_date, max_value=as_of)
start, end = (dates if isinstance(dates, (list, tuple)) and len(dates) == 2 else (default_start, as_of))
channels = st.sidebar.multiselect("Sales channel", sorted(orders["channel"].unique()), placeholder="All channels")
regions = st.sidebar.multiselect("Region", sorted(orders["region"].unique()), placeholder="All regions")
st.sidebar.markdown("---")
last_run = frames["runs"].iloc[0] if not frames["runs"].empty else None
st.sidebar.caption(f"Last refresh {last_run['finished_at']:%d %b %Y %H:%M} UTC" if last_run is not None else "")

ctx = Context(frames=frames, as_of=as_of, rules=rules, start=start, end=end, channels=channels, regions=regions,
              data_label=settings.DATA_LABEL, db_path=str(db_path))
PAGES[page_name].render(ctx)
