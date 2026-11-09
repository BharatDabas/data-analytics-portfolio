"""Cached data access for the dashboard. Every number shown on a page comes from
these queries (DuckDB) and the analytics package - nothing is hardcoded."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb
import pandas as pd
import streamlit as st

from dashboards import theme


@st.cache_resource(show_spinner=False)
def get_connection(path: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    con.execute("SET TimeZone = 'UTC'")
    return con


def _q(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> pd.DataFrame:
    cur = con.cursor()
    cur.execute("SET TimeZone = 'UTC'")  # cursors do not inherit the parent's session settings
    return cur.execute(sql, params or []).df()


@st.cache_data(show_spinner=False)
def load(_con: duckdb.DuckDBPyConnection, sql: str, params: tuple = (), version: int = 0) -> pd.DataFrame:
    """Cached query; `version` is bumped after writes to invalidate."""
    return _q(_con, sql, list(params))


@dataclass
class Ctx:
    con: duckdb.DuckDBPyConnection
    cfg: dict
    source_id: str
    data_kind: str
    assets: pd.DataFrame        # dim_asset rows for the selected source
    colors: dict                # asset_id -> fixed categorical colour
    start: date
    end: date
    as_of: date | None
    version: int
    portfolio_id: str | None

    def q(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        return load(self.con, sql, tuple(params), self.version)

    def label(self, asset_id: str) -> str:
        row = self.assets[self.assets["asset_id"] == asset_id]
        return f"{row['name'].iloc[0]} ({row['symbol'].iloc[0]})" if len(row) else asset_id

    def chips(self) -> list[tuple[str, str]]:
        chips = [("Synthetic demo data", "warn") if self.data_kind == "synthetic"
                 else (f"Source: {self.source_id}", "accent")]
        chips.append((f"Data as of {self.as_of:%d %b %Y} UTC" if self.as_of else "No data", ""))
        return chips

    def prices(self, asset_ids: list[str] | None = None, full_history: bool = False) -> pd.DataFrame:
        ids = asset_ids or self.assets["asset_id"].tolist()
        placeholders = ",".join("?" * len(ids))
        sql = f"""SELECT * FROM v_market_daily WHERE source_id = ? AND asset_id IN ({placeholders})"""
        params: list = [self.source_id, *ids]
        if not full_history:
            sql += " AND price_date BETWEEN ? AND ?"
            params += [self.start, self.end]
        df = self.q(sql + " ORDER BY asset_id, price_date", tuple(params))
        df["price_date"] = pd.to_datetime(df["price_date"])
        return df

    def returns_wide(self, asset_ids: list[str] | None = None) -> pd.DataFrame:
        df = self.prices(asset_ids)
        if df.empty:
            return pd.DataFrame()
        return df.pivot_table(index="price_date", columns="asset_id", values="daily_return", aggfunc="last")


def color_map(asset_ids: list[str]) -> dict:
    """Colour follows the entity: assigned once by sorted canonical ID, never by rank."""
    # Beyond 8 entities colours are not generated or cycled; extras share a neutral grey.
    return {a: theme.SERIES[i] if i < len(theme.SERIES) else theme.INK_3 for i, a in enumerate(sorted(asset_ids))}
