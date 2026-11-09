"""Cleansing and standardisation rules (unit of measure, currency, duplicates, orphans)."""
from __future__ import annotations

import json

import pandas as pd


class StandardisationError(ValueError):
    pass


def uom_factor(uom: pd.Series, sku: pd.Series, case_pack: pd.Series | dict, fixed: dict[str, float],
               product_uoms: list[str]) -> pd.Series:
    """Factor that converts a source quantity to base units (EA).

    fixed: UoMs with a constant factor (EA=1, DZ=12). product_uoms: UoMs whose factor is
    the product's case pack (CS). Unknown UoMs raise rather than being silently treated as EA.
    """
    packs = sku.map(case_pack)
    factor = uom.map(fixed).astype(float)
    is_pack = uom.isin(product_uoms)
    factor = factor.where(~is_pack, packs.astype(float))
    unknown = factor.isna()
    if unknown.any():
        bad = sorted(set(uom[unknown].astype(str)))
        raise StandardisationError(f"Unknown unit of measure or missing case pack: {bad}")
    return factor


def to_usd(amount: pd.Series, currency: pd.Series, fx_to_usd: dict[str, float]) -> pd.Series:
    rate = currency.map(fx_to_usd)
    if rate.isna().any():
        raise StandardisationError(f"No FX rate for currencies: {sorted(set(currency[rate.isna()]))}")
    return amount * rate


def dedupe(df: pd.DataFrame, keys: list[str]) -> tuple[pd.DataFrame, int]:
    """Drop rows repeating a natural key, keeping the first occurrence."""
    mask = df.duplicated(subset=keys, keep="first")
    return df.loc[~mask].reset_index(drop=True), int(mask.sum())


def quarantine_orphans(df: pd.DataFrame, checks: dict[str, set], entity: str, run_id: str
                       ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split rows whose foreign keys do not resolve. checks: column -> set of valid keys."""
    bad = pd.Series(False, index=df.index)
    reasons = pd.Series("", index=df.index)
    for col, valid in checks.items():
        miss = ~df[col].isin(valid)
        reasons = reasons.where(~miss, reasons + f"unknown {col}; ")
        bad |= miss
    rejected = pd.DataFrame({
        "run_id": run_id,
        "source_entity": entity,
        "reason": reasons[bad].str.strip("; "),
        "row_json": [json.dumps(r, default=str) for r in df.loc[bad].to_dict("records")],
    })
    return df.loc[~bad].reset_index(drop=True), rejected
