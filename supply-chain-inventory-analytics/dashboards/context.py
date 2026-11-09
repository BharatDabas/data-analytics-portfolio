"""Shared page context: database connection, global filters and query helper."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import duckdb
import pandas as pd
import streamlit as st

from config import settings
from dashboards import theme
from database import db

BRAND = theme.Brand(name="Supply Chain Intelligence", tagline="Inventory · Suppliers · Demand · Replenishment",
                    accent="#3346a8", accent_soft="#e3e7fb", icon="S")
_PARAM = re.compile(r"\$([a-z_]+)")


def cursor(con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """Thread-safe cursor; cursors do not inherit session settings, so pin UTC on each."""
    cur = con.cursor()
    cur.execute("SET TimeZone = 'UTC'").fetchall()
    return cur


@dataclass
class Ctx:
    con: duckdb.DuckDBPyConnection
    as_of: date
    built_at: pd.Timestamp
    data_label: str
    locations: list[str]
    categories: list[str]
    cfg: dict = field(default_factory=lambda: settings.BUSINESS)

    def q(self, sql: str, **extra) -> pd.DataFrame:
        """Run SQL with the global filters available as $locs, $cats and $as_of (only those used are bound)."""
        params = {"locs": self.locations, "cats": self.categories, "as_of": self.as_of, **extra}
        used = {k: params[k] for k in set(_PARAM.findall(sql)) if k in params}
        return cursor(self.con).execute(sql, used).df()

    def header(self, title: str, subtitle: str) -> None:
        chips = [(f"Data as of {self.as_of:%d %b %Y}", "accent"), (self.data_label.title(), "warn")]
        stale = self.q("SELECT location_id FROM v_location_freshness WHERE is_stale AND list_contains($locs, location_id)")
        if not stale.empty:
            chips.append((f"Stale snapshot: {', '.join(stale['location_id'])}", "warn"))
        theme.page_header(title, subtitle, chips)


@st.cache_resource(show_spinner=False)
def _connection(db_path: str) -> duckdb.DuckDBPyConnection:
    from pathlib import Path

    if not Path(db_path).exists():
        from transformations.build import build

        with st.spinner("First run: generating synthetic data and building the analytical model (~10 s)..."):
            build(db_path, settings.RAW_DIR, settings.default_as_of(), settings.SEED)
    return db.connect(db_path)


def connection() -> duckdb.DuckDBPyConnection:
    return _connection(str(settings.DB_PATH))


def reset_connection() -> None:
    _connection.clear()


def show_table(df: pd.DataFrame, height: int | None = None, empty: str = "No rows match the current filters.",
               **column_config) -> None:
    if df.empty:
        theme.empty_state(empty)
        return
    st.dataframe(df, hide_index=True, width="stretch", height=height or "auto", column_config=column_config or None)
