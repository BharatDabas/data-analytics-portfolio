"""Ingestion contract, cleansing, dedupe, rejects and reconciliation on small hand-built extracts."""
from datetime import date

import pandas as pd
import pytest

from config import settings
from ingestion.sources import CsvSource, FrameSource
from ingestion.synthetic import generate
from transformations import quality as q
from transformations.build import quality_checks, transform
from transformations.cleanse import SchemaError, standardise, to_gbp

RULES = settings.load_business_rules()


def test_standardise_trims_casts_and_dedupes():
    raw = pd.DataFrame({"order_item_id": ["A", "A", "B", "C", "D"], "order_id": ["O1", "O1", "O1", "O2", "O2"],
                        "product_id": [" P1 ", " P1 ", "P2", "", "P3"], "quantity": ["1", "1", "2.5", "1", "x"],
                        "unit_price": ["10", "10", "5", "5", "5"], "discount_amount": ["0"] * 5,
                        "currency": ["gbp ", "gbp ", "GBP", "GBP", "GBP"]})
    clean, rej, dups = standardise(raw, "order_items")
    assert clean["order_item_id"].tolist() == ["A"]
    assert clean.loc[0, "product_id"] == "P1" and clean.loc[0, "currency"] == "GBP"
    assert dups == 1
    reasons = set(rej["reason"])
    assert "quantity: unparseable int" in reasons and "product_id: missing value" in reasons
    assert len(rej) == 3  # B (2.5), C (missing product), D (x)


def test_missing_column_raises_schema_error():
    with pytest.raises(SchemaError):
        standardise(pd.DataFrame({"order_id": ["1"]}), "orders")


def test_to_gbp_rejects_unknown_currency():
    assert to_gbp(pd.Series([100.0]), pd.Series(["EUR"]), {"EUR": 0.86}).iloc[0] == pytest.approx(86.0)
    with pytest.raises(ValueError):
        to_gbp(pd.Series([1.0]), pd.Series(["JPY"]), {"EUR": 0.86})


@pytest.fixture(scope="module")
def small_build():
    raw = generate(date(2026, 6, 30), seed=7, n_customers=600, days=400)
    tables, rejects, stats = transform({k: FrameSource(raw).read(k) for k in raw}, RULES)
    return raw, tables, rejects, stats


def test_dirty_records_are_rejected_with_reasons(small_build):
    _, t, rej, stats = small_build
    reasons = rej.groupby(["entity", "reason"]).size()
    assert reasons[("orders", "customer_id: missing value")] == 6
    assert reasons[("order_items", "product_id: missing value")] == 15
    assert reasons[("order_items", "quantity must be positive")] == 10
    assert reasons[("returns", "returned quantity exceeds quantity sold")] == 4
    st = stats.set_index("entity")
    # every record is accounted for: loaded + rejected + de-duplicated = read
    assert (st["rows_read"] == st["rows_loaded"] + st["rows_rejected"] + st["duplicates_removed"]).all()
    assert st.loc["orders", "duplicates_removed"] >= 20  # 25 injected; a few may also be rejected first
    assert st.loc["marketing_spend", "duplicates_removed"] == 5
    assert t["fact_orders"]["order_id"].is_unique
    assert (t["fact_orders"]["currency_local"].isin(["GBP", "EUR", "USD"])).all()


def test_lines_of_rejected_orders_cascade(small_build):
    _, t, rej, _ = small_build
    assert t["fact_order_items"]["order_id"].isin(t["fact_orders"]["order_id"]).all()
    assert (rej["reason"] == "order_id not found in orders (orphan line)").sum() >= 8


def test_order_reconciliation_flags_orders_with_rejected_lines(small_build):
    _, t, rej, _ = small_build
    o = t["fact_orders"]
    bad_line_orders = set(rej.loc[rej["entity"] == "order_items", "raw_record"].str.extract(r'"order_id":"(O\d+)"')[0])
    flagged = set(o.loc[~o["reconciles_to_items"], "order_id"])
    assert flagged and flagged <= bad_line_orders


def test_quality_checks_pass_on_clean_model(small_build):
    _, t, _, _ = small_build
    res = {r.check: r for r in quality_checks(t, RULES, now=pd.Timestamp("2026-07-01", tz="UTC"))}
    assert all(r.status != q.FAIL for r in res.values())
    assert res["reconcile:order_net=line_net"].status == q.PASS
    assert res["coverage:product_cost"].status == q.WARN  # some products lack cost by design


def test_returned_quantity_never_exceeds_sold(small_build):
    _, t, _, _ = small_build
    it = t["fact_order_items"]
    assert (it["returned_quantity"] <= it["quantity"]).all()
    assert it["refund_gbp"].sum() == pytest.approx(t["fact_returns"]["refund_gbp"].sum())


def test_marketing_spend_not_duplicated_by_attribution(small_build):
    _, t, _, _ = small_build
    sp, br = t["fact_marketing_spend"], t["bridge_order_attribution"]
    assert not sp.duplicated(["spend_date", "campaign_id"]).any()
    assert br["order_id"].is_unique  # each order credited at most once
    assert br["order_id"].isin(t["fact_orders"].loc[~t["fact_orders"]["is_cancelled"], "order_id"]).all()


def test_csv_source_roundtrip(tmp_path):
    pd.DataFrame({"a": ["1", ""]}).to_csv(tmp_path / "x.csv", index=False)
    df = CsvSource(tmp_path).read("x")
    assert df["a"].tolist() == ["1", ""]
    with pytest.raises(FileNotFoundError):
        CsvSource(tmp_path).read("missing")


def test_quality_helpers_edge_cases():
    df = pd.DataFrame({"k": [1, 1, None], "v": [1, -1, 5]})
    assert q.check_unique(df, ["k"], "t").failed_rows == 1
    assert q.check_not_null(df, ["k"], "t").failed_rows == 1
    assert q.check_range(df, "v", "t", min_value=0).failed_rows == 1
    assert q.check_freshness(None, 24, "t").status == q.FAIL
    assert q.check_reconciliation(100, 100.4, "x", "t", 0.005).status == q.PASS
    assert q.check_reconciliation(100, 110, "x", "t", 0.005).status == q.FAIL


def test_committed_sample_templates_load_cleanly():
    sample = settings.SAMPLE_DIR
    from transformations.cleanse import CONTRACTS
    t, rej, st = transform({e: CsvSource(sample).read(e) for e in CONTRACTS}, RULES)
    assert rej.empty
    assert len(t["fact_orders"]) > 0
