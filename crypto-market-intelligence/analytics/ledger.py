"""Portfolio transaction ledger with FIFO cost basis.

Accounting rules (documented in docs/kpi_catalogue.md):
- BUY          opens a lot. Cost basis = quantity x price + fee (fee is capitalised).
- SELL         is the only disposal. Units are matched against open lots first-in-first-out.
               Proceeds = quantity x price - fee. Realised P&L = proceeds - matched cost basis.
- TRANSFER_IN  units arrive from a wallet outside this portfolio. They open a lot at the
               carried-over cost basis per unit (unit_price is REQUIRED; never market value).
               Not a purchase: no P&L, no cash flow. Any network fee is an "other fee".
- TRANSFER_OUT units leave to a wallet outside this portfolio. FIFO lots are removed together
               with their cost basis. Not a sale: no realised P&L. Any fee is an "other fee".
- FEE          standalone fee in quote currency (e.g. custody/withdrawal fee): "other fee".
Selling or transferring out more units than are held is rejected (LedgerError).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import pandas as pd

TXN_TYPES = ("BUY", "SELL", "TRANSFER_IN", "TRANSFER_OUT", "FEE")
REQUIRED_COLUMNS = ["transaction_id", "portfolio_id", "asset_id", "txn_type", "quantity", "unit_price", "fee",
                    "executed_at"]
EPS = 1e-12


class LedgerError(ValueError):
    pass


@dataclass
class Lot:
    quantity: float
    unit_cost: float
    acquired_at: pd.Timestamp


@dataclass
class AssetPosition:
    lots: deque = field(default_factory=deque)
    realised_pnl: float = 0.0
    capitalised_fees: float = 0.0   # buy fees inside cost basis
    sale_fees: float = 0.0          # deducted from proceeds
    other_fees: float = 0.0         # transfer / standalone fees (expensed separately)
    transferred_out_cost: float = 0.0

    @property
    def units(self) -> float:
        return sum(l.quantity for l in self.lots)

    @property
    def cost_basis(self) -> float:
        return sum(l.quantity * l.unit_cost for l in self.lots)

    def take_fifo(self, qty: float) -> list[tuple[float, float, pd.Timestamp]]:
        """Remove qty units FIFO; returns [(qty, unit_cost, acquired_at), ...]."""
        if qty > self.units + 1e-9:
            raise LedgerError(f"insufficient units: requested {qty}, held {self.units}")
        taken = []
        remaining = qty
        while remaining > EPS:
            lot = self.lots[0]
            q = min(lot.quantity, remaining)
            taken.append((q, lot.unit_cost, lot.acquired_at))
            lot.quantity -= q
            remaining -= q
            if lot.quantity <= 1e-12:
                self.lots.popleft()
        return taken


@dataclass
class LedgerResult:
    positions: dict          # (portfolio_id, asset_id) -> AssetPosition
    disposals: pd.DataFrame  # one row per lot match
    states: pd.DataFrame     # position after each transaction (for daily snapshots)


def validate_transactions(df: pd.DataFrame) -> list[str]:
    """Row-level validation used for CSV import and manual entry. Returns error messages."""
    errors: list[str] = []
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        return [f"missing columns: {missing}"]
    for i, r in df.reset_index(drop=True).iterrows():
        row = f"row {i + 1} ({r['transaction_id']})"
        t = str(r["txn_type"]).upper()
        if t not in TXN_TYPES:
            errors.append(f"{row}: unknown txn_type '{r['txn_type']}'")
            continue
        q = pd.to_numeric(r["quantity"], errors="coerce")
        p = pd.to_numeric(r["unit_price"], errors="coerce")
        fee = pd.to_numeric(r["fee"], errors="coerce")
        if pd.isna(q) or q < 0 or (t != "FEE" and q == 0):
            errors.append(f"{row}: quantity must be positive")
        if t in ("BUY", "SELL") and (pd.isna(p) or p <= 0):
            errors.append(f"{row}: {t} requires a positive unit_price")
        if t == "TRANSFER_IN" and (pd.isna(p) or p < 0):
            errors.append(f"{row}: TRANSFER_IN requires the carried-over cost basis per unit in unit_price")
        if not pd.isna(fee) and fee < 0:
            errors.append(f"{row}: fee cannot be negative")
        if pd.isna(pd.to_datetime(r["executed_at"], utc=True, errors="coerce")):
            errors.append(f"{row}: executed_at is not a valid timestamp")
    if df["transaction_id"].duplicated().any():
        dups = df.loc[df["transaction_id"].duplicated(), "transaction_id"].tolist()
        errors.append(f"duplicate transaction_id: {dups[:5]}")
    return errors


def run_ledger(transactions: pd.DataFrame) -> LedgerResult:
    tx = transactions.copy()
    tx["txn_type"] = tx["txn_type"].str.upper()
    tx["executed_at"] = pd.to_datetime(tx["executed_at"], utc=True)
    tx["fee"] = pd.to_numeric(tx["fee"], errors="coerce").fillna(0.0)
    tx["quantity"] = pd.to_numeric(tx["quantity"], errors="coerce").fillna(0.0)
    tx["unit_price"] = pd.to_numeric(tx["unit_price"], errors="coerce")
    # Deterministic order: time, then transaction_id (stable for same-timestamp entries).
    tx = tx.sort_values(["executed_at", "transaction_id"], kind="mergesort")

    positions: dict[tuple[str, str], AssetPosition] = {}
    disposals, states = [], []
    for r in tx.itertuples(index=False):
        key = (r.portfolio_id, r.asset_id)
        pos = positions.setdefault(key, AssetPosition())
        q, p, fee = float(r.quantity), r.unit_price, float(r.fee)
        try:
            if r.txn_type == "BUY":
                pos.lots.append(Lot(q, (q * p + fee) / q, r.executed_at))
                pos.capitalised_fees += fee
            elif r.txn_type == "SELL":
                proceeds = q * p - fee
                pos.sale_fees += fee
                for mq, unit_cost, acquired in pos.take_fifo(q):
                    share = mq / q
                    cost = mq * unit_cost
                    pnl = proceeds * share - cost
                    pos.realised_pnl += pnl
                    disposals.append({
                        "transaction_id": r.transaction_id, "portfolio_id": r.portfolio_id,
                        "asset_id": r.asset_id, "disposed_at": r.executed_at, "quantity": mq,
                        "proceeds": proceeds * share, "cost_basis": cost, "realised_pnl": pnl,
                        "holding_days": int((r.executed_at - acquired).days)})
            elif r.txn_type == "TRANSFER_IN":
                if pd.isna(p):
                    raise LedgerError("TRANSFER_IN requires carried-over cost basis per unit")
                pos.lots.append(Lot(q, float(p), r.executed_at))
                pos.other_fees += fee
            elif r.txn_type == "TRANSFER_OUT":
                pos.transferred_out_cost += sum(mq * uc for mq, uc, _ in pos.take_fifo(q))
                pos.other_fees += fee
            elif r.txn_type == "FEE":
                pos.other_fees += fee
            else:
                raise LedgerError(f"unknown txn_type {r.txn_type}")
        except LedgerError as exc:
            raise LedgerError(f"{r.transaction_id} ({r.txn_type} {r.asset_id}): {exc}") from exc
        states.append({"portfolio_id": r.portfolio_id, "asset_id": r.asset_id, "executed_at": r.executed_at,
                       "transaction_id": r.transaction_id, "units": pos.units, "cost_basis": pos.cost_basis,
                       "realised_pnl": pos.realised_pnl})

    disp_cols = ["transaction_id", "portfolio_id", "asset_id", "disposed_at", "quantity", "proceeds",
                 "cost_basis", "realised_pnl", "holding_days"]
    return LedgerResult(positions, pd.DataFrame(disposals, columns=disp_cols), pd.DataFrame(states))


def positions_frame(result: LedgerResult) -> pd.DataFrame:
    rows = [{"portfolio_id": k[0], "asset_id": k[1], "units": p.units, "cost_basis": p.cost_basis,
             "avg_cost": p.cost_basis / p.units if p.units > EPS else None,
             "realised_pnl": p.realised_pnl, "capitalised_fees": p.capitalised_fees,
             "sale_fees": p.sale_fees, "other_fees": p.other_fees,
             "transferred_out_cost": p.transferred_out_cost}
            for k, p in result.positions.items()]
    return pd.DataFrame(rows)
