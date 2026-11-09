"""Data access for the dashboard: read the semantic views once, filter in memory per interaction."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from database import db

QUERIES = {
    "lines": "SELECT * FROM v_order_lines",
    "orders": "SELECT * FROM v_order_value",
    "marketing": "SELECT * FROM v_marketing_daily",
    "returns": "SELECT * FROM v_returns",
    "fulfilment": "SELECT * FROM v_fulfilment",
    "customers": "SELECT * FROM v_customer_value",
    "products": "SELECT * FROM dim_product",
    "inventory": "SELECT * FROM fact_inventory",
    "runs": "SELECT * FROM ops_pipeline_runs ORDER BY started_at DESC",
    "dq": "SELECT * FROM v_dq_latest",
    "failures": "SELECT * FROM ops_load_failures WHERE run_id = (SELECT max(run_id) FROM ops_load_failures)",
    "source_files": "SELECT * FROM ops_source_files WHERE run_id = (SELECT max(run_id) FROM ops_source_files)",
    "reconciliation": "SELECT * FROM v_order_reconciliation ORDER BY abs(difference_gbp) DESC",
}


def load_frames(db_path: str | Path) -> dict[str, pd.DataFrame]:
    con = db.connect(db_path, read_only=True)
    try:
        frames = {k: con.execute(sql).df() for k, sql in QUERIES.items()}
    finally:
        con.close()
    for k, cols in {"lines": ["order_date"], "orders": ["order_date"], "marketing": ["activity_date"],
                    "returns": ["return_date", "order_date"], "fulfilment": ["order_date"],
                    "customers": ["first_order_date", "cohort_month", "last_order_date"]}.items():
        for c in cols:
            frames[k][c] = pd.to_datetime(frames[k][c]).dt.date
    # marketing channels (Paid Search...) are a different dimension from sales channels (web/app/marketplace)
    frames["marketing"] = frames["marketing"].rename(columns={"channel": "marketing_channel"})
    return frames


@dataclass
class Context:
    frames: dict[str, pd.DataFrame]
    as_of: date
    rules: dict
    start: date
    end: date
    channels: list[str]
    regions: list[str]
    data_label: str
    db_path: str
    filtered: dict[str, pd.DataFrame] = field(default_factory=dict)

    def _mask(self, df: pd.DataFrame, date_col: str, start: date, end: date) -> pd.Series:
        m = (df[date_col] >= start) & (df[date_col] <= end)
        if "channel" in df and self.channels:
            m &= df["channel"].isin(self.channels)
        if "region" in df and self.regions:
            m &= df["region"].isin(self.regions)
        return m

    def window(self, name: str, date_col: str = "order_date", start: date | None = None,
               end: date | None = None) -> pd.DataFrame:
        df = self.frames[name]
        return df[self._mask(df, date_col, start or self.start, end or self.end)]

    def all_dates(self, name: str) -> pd.DataFrame:
        """Channel/region filters applied, but full history (cohorts, RFM, forecasting need it)."""
        df = self.frames[name]
        m = pd.Series(True, index=df.index)
        if "channel" in df and self.channels:
            m &= df["channel"].isin(self.channels)
        if "region" in df and self.regions:
            m &= df["region"].isin(self.regions)
        return df[m]
