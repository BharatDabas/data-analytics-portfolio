"""Source contracts, cleansing and standardisation for raw retail extracts.

Raw extracts are read as text. Each entity has a contract (required columns and
target types). Records that cannot satisfy the contract are not dropped
silently: they are returned as rejects with a reason and written to
ops_load_failures by the build step.
"""
from __future__ import annotations

import pandas as pd

CONTRACTS: dict[str, dict[str, str]] = {
    "customers": {"customer_id": "str", "signup_date": "date", "country": "str", "acquisition_channel": "str"},
    "products": {"product_id": "str", "sku": "str", "product_name": "str", "category": "str", "brand": "str",
                 "list_price_gbp": "float", "unit_cost_gbp": "float?", "lifecycle_status": "str", "launch_date": "date"},
    "campaigns": {"campaign_id": "str", "campaign_name": "str", "channel": "str", "objective": "str",
                  "start_date": "date", "end_date": "date"},
    "orders": {"order_id": "str", "customer_id": "str", "order_ts": "ts", "channel": "str", "status": "str",
               "country": "str", "currency": "currency", "shipping_amount": "float", "tax_amount": "float",
               "order_total": "float", "last_touch_campaign_id": "str?", "last_touch_ts": "ts?",
               "shipped_ts": "ts?", "delivered_ts": "ts?", "promised_delivery_date": "date"},
    "order_items": {"order_item_id": "str", "order_id": "str", "product_id": "str", "quantity": "int",
                    "unit_price": "float", "discount_amount": "float", "currency": "currency"},
    "returns": {"return_id": "str", "order_item_id": "str", "return_date": "date", "quantity": "int",
                "reason": "str", "refund_amount": "float", "currency": "currency"},
    "marketing_spend": {"date": "date", "campaign_id": "str", "spend": "float", "currency": "currency",
                        "impressions": "int", "clicks": "int"},
    "inventory_snapshots": {"snapshot_date": "date", "product_id": "str", "location_id": "str",
                            "on_hand": "int", "reserved": "int"},
}

PRIMARY_KEYS = {
    "customers": ["customer_id"], "products": ["product_id"], "campaigns": ["campaign_id"],
    "orders": ["order_id"], "order_items": ["order_item_id"], "returns": ["return_id"],
    "marketing_spend": ["date", "campaign_id"], "inventory_snapshots": ["snapshot_date", "product_id", "location_id"],
}


class SchemaError(ValueError):
    """Raised when a source extract is missing required columns (load cannot continue)."""


def reject(df: pd.DataFrame, mask: pd.Series, entity: str, key_cols: list[str], reason: str) -> pd.DataFrame:
    rows = df[mask]
    if rows.empty:
        return pd.DataFrame(columns=["entity", "record_key", "reason", "raw_record"])
    return pd.DataFrame({
        "entity": entity,
        "record_key": rows[key_cols].astype(str).agg("|".join, axis=1).to_numpy(),
        "reason": reason,
        "raw_record": rows.astype(str).to_json(orient="records", lines=True).splitlines(),
    })


def standardise(df: pd.DataFrame, entity: str) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Validate the contract, trim text, cast types, drop exact/keyed duplicates.

    Returns (clean_df, rejects_df, duplicates_removed).
    """
    contract = CONTRACTS[entity]
    missing = [c for c in contract if c not in df.columns]
    if missing:
        raise SchemaError(f"{entity}: missing required columns {missing}")
    out = df[list(contract)].copy()
    rejects = []
    for col, kind in contract.items():
        optional = kind.endswith("?")
        kind = kind.rstrip("?")
        s = out[col].astype("string").str.strip().replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
        if kind == "currency":
            s = s.str.upper()
        if kind in ("str", "currency"):
            out[col] = s
            parsed_bad = pd.Series(False, index=out.index)
        elif kind == "float":
            out[col] = pd.to_numeric(s, errors="coerce")
            parsed_bad = s.notna() & out[col].isna()
        elif kind == "int":
            num = pd.to_numeric(s, errors="coerce")
            parsed_bad = s.notna() & (num.isna() | (num % 1 != 0))
            out[col] = num.where(~parsed_bad).astype("Int64")
        elif kind == "date":
            out[col] = pd.to_datetime(s, errors="coerce").dt.date
            parsed_bad = s.notna() & out[col].isna()
        elif kind == "ts":
            out[col] = pd.to_datetime(s, errors="coerce", utc=True)
            parsed_bad = s.notna() & out[col].isna()
        else:  # pragma: no cover - contract typo
            raise ValueError(kind)
        missing_mask = out[col].isna() & ~parsed_bad if not optional else pd.Series(False, index=out.index)
        for mask, reason in ((parsed_bad, f"{col}: unparseable {kind}"), (missing_mask, f"{col}: missing value")):
            if mask.any():
                rejects.append(reject(df.loc[out.index], mask, entity, PRIMARY_KEYS[entity], reason))
        out = out[~(parsed_bad | missing_mask)]
    before = len(out)
    out = out.drop_duplicates()  # exact re-sent records
    out = out.drop_duplicates(subset=PRIMARY_KEYS[entity], keep="first")
    dups = before - len(out)
    rej = pd.concat(rejects, ignore_index=True) if rejects else reject(df, pd.Series(False, index=df.index), entity, [], "")
    return out.reset_index(drop=True), rej, dups


def to_gbp(amount: pd.Series, currency: pd.Series, fx_to_gbp: dict[str, float]) -> pd.Series:
    """Convert local amounts to GBP. Unknown currencies raise rather than silently pass through."""
    unknown = set(currency.dropna().unique()) - set(fx_to_gbp)
    if unknown:
        raise ValueError(f"no FX rate configured for {sorted(unknown)}")
    return (amount.astype(float) * currency.map(fx_to_gbp).astype(float)).round(4)
