"""Shared fixtures: tiny hand-built frames for unit tests and one full synthetic build for integration tests."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

AS_OF = "2026-10-31"


@pytest.fixture(scope="session")
def built_db(tmp_path_factory):
    """Full pipeline run (synthetic generation -> DuckDB) in a temp folder, fixed as-of date."""
    mp = pytest.MonkeyPatch()
    mp.setenv("RETAIL_AS_OF", AS_OF)
    from pipeline import run_pipeline
    root = tmp_path_factory.mktemp("retail")
    db_path = root / "retail.duckdb"
    summary = run_pipeline(db_path=db_path, raw_dir=root / "raw")
    yield {"db_path": db_path, "raw_dir": root / "raw", "summary": summary}
    mp.undo()


@pytest.fixture
def con(built_db):
    from database import db
    c = db.connect(built_db["db_path"], read_only=True)
    yield c
    c.close()


def make_lines(rows: list[dict]) -> pd.DataFrame:
    """Order lines in the shape of v_order_lines with sensible defaults."""
    base = {"is_cancelled": False, "has_reliable_cost": True, "discount_gbp": 0.0, "refund_gbp": 0.0,
            "returned_quantity": 0, "is_discounted": False, "order_date": date(2026, 1, 1), "category": "A",
            "product_name": "P", "channel": "web", "region": "UK & Ireland"}
    out = []
    for i, r in enumerate(rows):
        d = {**base, "order_item_id": f"L{i}", **r}
        d["gross_sales_gbp"] = d["quantity"] * d["unit_price_gbp"]
        d["net_sales_gbp"] = d["gross_sales_gbp"] - d["discount_gbp"]
        d["net_revenue_gbp"] = d["net_sales_gbp"] - d["refund_gbp"]
        if d["has_reliable_cost"]:
            d["cogs_gbp"] = (d["quantity"] - d["returned_quantity"]) * d["unit_cost_gbp"]
            d["net_revenue_costed_gbp"] = d["net_revenue_gbp"]
            d["gross_profit_gbp"] = d["net_revenue_gbp"] - d["cogs_gbp"]
        else:
            d["cogs_gbp"] = d["net_revenue_costed_gbp"] = d["gross_profit_gbp"] = float("nan")
        d["is_discounted"] = d["discount_gbp"] > 0
        out.append(d)
    return pd.DataFrame(out)


def orders_from_lines(lines: pd.DataFrame, extra: dict | None = None) -> pd.DataFrame:
    g = lines.groupby("order_id").agg(customer_id=("customer_id", "first"), order_date=("order_date", "first"),
                                      is_cancelled=("is_cancelled", "first"), net_revenue_gbp=("net_revenue_gbp", "sum"),
                                      returned_units=("returned_quantity", "sum")).reset_index()
    g["shipping_gbp"] = 0.0
    g["tax_gbp"] = 0.0
    g["order_ts"] = pd.to_datetime(g["order_date"]).dt.tz_localize("UTC")
    return g
