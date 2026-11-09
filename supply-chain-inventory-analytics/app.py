"""Supply Chain & Inventory Optimisation Intelligence - Streamlit entry point.

Run:  streamlit run app.py
The first run generates the synthetic demo dataset and builds data/processed/supply_chain.duckdb.
"""
from __future__ import annotations

import streamlit as st

from config import settings
from dashboards import theme
from dashboards.context import BRAND, Ctx, connection, cursor, reset_connection
from dashboards.pages import (data_quality, demand_forecast, executive, inventory_health, network, procurement,
                              replenishment, suppliers)

PAGES = {
    "Executive Overview": executive,
    "Inventory Health": inventory_health,
    "Demand & Forecast": demand_forecast,
    "Supplier Performance": suppliers,
    "Procurement & PO Tracking": procurement,
    "Warehouse & Network": network,
    "Replenishment Workbench": replenishment,
    "Data Quality & Operations": data_quality,
}

# Deep-link slugs (e.g. ?page=replenishment) in the same order as PAGES.
SLUGS = {slug: i for i, slug in enumerate(["executive-overview", "inventory-health", "demand-forecast",
                                           "supplier-performance", "procurement", "warehouse-network",
                                           "replenishment", "data-quality"])}

st.set_page_config(page_title="Supply Chain Intelligence", page_icon="📦", layout="wide",
                   initial_sidebar_state="expanded")
theme.register_plotly_template()
theme.inject_css(BRAND)
theme.sidebar_brand(BRAND)

con = connection()
meta = cursor(con).execute("SELECT data_as_of, built_at, data_label FROM meta_build").df().iloc[0]
dims = cursor(con).execute("""SELECT (SELECT list(location_id ORDER BY location_id) FROM dim_location) AS locs,
                                      (SELECT list(DISTINCT category ORDER BY category) FROM dim_product) AS cats""").fetchone()

page = st.sidebar.radio("Navigation", list(PAGES), label_visibility="collapsed",
                        index=SLUGS.get(st.query_params.get("page", ""), 0))
st.sidebar.markdown("---")
locs = st.sidebar.multiselect("Locations", dims[0], default=dims[0])
cats = st.sidebar.multiselect("Product categories", dims[1], default=dims[1])
st.sidebar.markdown("---")
st.sidebar.caption(f"**Model built:** {meta['built_at']:%d %b %Y %H:%M} UTC  \n"
                   f"**Source:** {meta['data_label']}  \n"
                   f"**Database:** `{settings.DB_PATH.name}`")
if st.sidebar.button("Rebuild demo dataset", help="Regenerates synthetic extracts and rebuilds the model"):
    from transformations.build import build

    con.close()
    reset_connection()
    with st.spinner("Rebuilding..."):
        build(settings.DB_PATH, settings.RAW_DIR, settings.default_as_of(), settings.SEED)
    st.rerun()

if not locs or not cats:
    theme.empty_state("Select at least one location and one product category in the sidebar.")
    st.stop()

ctx = Ctx(con=con, as_of=meta["data_as_of"].date(), built_at=meta["built_at"], data_label=meta["data_label"],
          locations=locs, categories=cats)
PAGES[page].render(ctx)
