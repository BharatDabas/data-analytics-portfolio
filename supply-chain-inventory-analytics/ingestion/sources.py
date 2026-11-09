"""Source adapters. Each adapter returns raw, source-shaped DataFrames per entity.

The pipeline depends only on the SourceAdapter protocol, so an ERP/WMS API or
database connector can replace CsvSource without touching transformations.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pandas as pd

# entity -> (required columns, timestamp/date columns)
SOURCE_SCHEMAS: dict[str, tuple[list[str], list[str]]] = {
    "products": (["sku", "product_name", "category", "base_uom", "case_pack", "unit_cost_usd",
                  "primary_supplier_id", "moq_units", "lifecycle_status", "launch_date"], ["launch_date"]),
    "suppliers": (["supplier_id", "supplier_name", "region", "country", "currency", "payment_terms_days",
                   "approved_status", "category", "nominal_lead_time_days"], []),
    "locations": (["location_id", "location_name", "region", "location_type", "capacity_units"], []),
    "inventory_snapshots": (["snapshot_ts", "sku", "location_id", "on_hand_qty", "reserved_qty",
                             "in_transit_qty", "uom"], ["snapshot_ts"]),
    "inventory_movements": (["movement_id", "movement_ts", "sku", "location_id", "movement_type", "quantity",
                             "uom", "reference"], ["movement_ts"]),
    "demand": (["demand_date", "sku", "location_id", "requested_qty", "fulfilled_qty", "backordered_qty", "uom"],
               ["demand_date"]),
    "purchase_orders": (["po_number", "po_line", "supplier_id", "sku", "location_id", "order_date", "approval_date",
                         "promised_date", "ordered_qty", "uom", "unit_cost", "currency", "status"],
                        ["order_date", "approval_date", "promised_date"]),
    "receipts": (["receipt_id", "po_number", "po_line", "receipt_ts", "received_qty", "accepted_qty",
                  "rejected_qty", "uom", "inspection_outcome"], ["receipt_ts"]),
}


class SourceAdapter(Protocol):
    name: str

    def read(self, entity: str) -> pd.DataFrame: ...


class CsvSource:
    """Reads <entity>.csv files exported from ERP / WMS / procurement systems."""

    def __init__(self, directory: str | Path, name: str = "csv_extract"):
        self.directory = Path(directory)
        self.name = name

    def read(self, entity: str) -> pd.DataFrame:
        path = self.directory / f"{entity}.csv"
        if not path.exists():
            raise FileNotFoundError(f"Source extract missing: {path}")
        df = pd.read_csv(path)
        for col in SOURCE_SCHEMAS[entity][1]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        return df
