import pandas as pd
import pytest

from analytics.ledger import LedgerError, positions_frame, run_ledger, validate_transactions
from tests.conftest import tx


def pos(result, asset="A"):
    return result.positions[("p1", asset)]


def test_buy_fee_is_capitalised_into_cost_basis():
    r = run_ledger(tx([("t1", "BUY", "A", 2, 100, 10, "2026-01-01")]))
    assert pos(r).units == 2
    assert pos(r).cost_basis == pytest.approx(210)
    assert pos(r).capitalised_fees == 10


def test_fifo_partial_disposal_across_two_lots():
    r = run_ledger(tx([
        ("t1", "BUY", "A", 1, 100, 0, "2026-01-01"),
        ("t2", "BUY", "A", 1, 200, 0, "2026-01-02"),
        ("t3", "SELL", "A", 1.5, 300, 3, "2026-01-03"),
    ]))
    # proceeds 450 - 3 = 447 ; FIFO cost = 100 + 0.5*200 = 200
    assert pos(r).realised_pnl == pytest.approx(247)
    assert pos(r).units == pytest.approx(0.5)
    assert pos(r).cost_basis == pytest.approx(100)  # remaining half of the 200 lot
    assert len(r.disposals) == 2
    assert r.disposals["proceeds"].sum() == pytest.approx(447)
    assert r.disposals["cost_basis"].sum() == pytest.approx(200)
    assert r.disposals["holding_days"].tolist() == [2, 1]


def test_sale_fee_reduces_realised_pnl_not_cost():
    r = run_ledger(tx([("t1", "BUY", "A", 1, 100, 0, "2026-01-01"),
                       ("t2", "SELL", "A", 1, 100, 5, "2026-01-02")]))
    assert pos(r).realised_pnl == pytest.approx(-5)
    assert pos(r).sale_fees == 5


def test_transfer_out_is_not_a_sale():
    r = run_ledger(tx([("t1", "BUY", "A", 2, 100, 0, "2026-01-01"),
                       ("t2", "TRANSFER_OUT", "A", 1, None, 1.5, "2026-01-02")]))
    assert pos(r).realised_pnl == 0
    assert r.disposals.empty
    assert pos(r).units == 1
    assert pos(r).cost_basis == pytest.approx(100)
    assert pos(r).transferred_out_cost == pytest.approx(100)
    assert pos(r).other_fees == 1.5


def test_transfer_in_uses_carried_cost_not_market_value():
    r = run_ledger(tx([("t1", "TRANSFER_IN", "A", 1, 50, 2, "2026-01-01"),
                       ("t2", "SELL", "A", 1, 80, 0, "2026-01-05")]))
    assert pos(r).realised_pnl == pytest.approx(30)
    assert pos(r).other_fees == 2  # transfer fee expensed separately, not in cost basis


def test_transfer_in_without_cost_basis_is_rejected():
    with pytest.raises(LedgerError, match="cost basis"):
        run_ledger(tx([("t1", "TRANSFER_IN", "A", 1, None, 0, "2026-01-01")]))


def test_overselling_is_rejected():
    with pytest.raises(LedgerError, match="insufficient units"):
        run_ledger(tx([("t1", "BUY", "A", 1, 100, 0, "2026-01-01"),
                       ("t2", "SELL", "A", 2, 100, 0, "2026-01-02")]))


def test_transfer_out_more_than_held_is_rejected():
    with pytest.raises(LedgerError):
        run_ledger(tx([("t1", "TRANSFER_OUT", "A", 1, None, 0, "2026-01-02")]))


def test_standalone_fee_is_other_fee_and_does_not_change_units():
    r = run_ledger(tx([("t1", "BUY", "A", 1, 100, 0, "2026-01-01"),
                       ("t2", "FEE", "A", 0, None, 7, "2026-01-02")]))
    assert pos(r).units == 1 and pos(r).other_fees == 7 and pos(r).realised_pnl == 0


def test_order_is_chronological_regardless_of_input_order():
    r = run_ledger(tx([("t2", "SELL", "A", 1, 150, 0, "2026-01-02"),
                       ("t1", "BUY", "A", 1, 100, 0, "2026-01-01")]))
    assert pos(r).realised_pnl == pytest.approx(50)


def test_assets_are_tracked_independently():
    r = run_ledger(tx([("t1", "BUY", "A", 1, 100, 0, "2026-01-01"),
                       ("t2", "BUY", "B", 1, 10, 0, "2026-01-01"),
                       ("t3", "SELL", "B", 1, 20, 0, "2026-01-02")]))
    pf = positions_frame(r).set_index("asset_id")
    assert pf.loc["A", "realised_pnl"] == 0 and pf.loc["B", "realised_pnl"] == pytest.approx(10)


def test_validation_messages():
    bad = tx([("x1", "SWAP", "A", 1, 1, 0, "2026-01-01"),
              ("x2", "BUY", "A", -1, 1, 0, "2026-01-01"),
              ("x3", "SELL", "A", 1, None, 0, "2026-01-01"),
              ("x4", "BUY", "A", 1, 1, -2, "2026-01-01"),
              ("x4", "TRANSFER_IN", "A", 1, None, 0, "2026-01-01")])
    errors = " | ".join(validate_transactions(bad))
    for frag in ["unknown txn_type", "quantity must be positive", "requires a positive unit_price",
                 "fee cannot be negative", "carried-over cost basis", "duplicate transaction_id"]:
        assert frag in errors


def test_validation_requires_columns():
    assert "missing columns" in validate_transactions(pd.DataFrame({"a": [1]}))[0]
