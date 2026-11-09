import pandas as pd
import pytest

from transformations.standardise import StandardisationError, dedupe, quarantine_orphans, to_usd, uom_factor

FIXED = {"EA": 1, "DZ": 12}


def test_uom_conversion_each_case_dozen():
    f = uom_factor(pd.Series(["EA", "CS", "DZ"]), pd.Series(["A", "B", "A"]), {"A": 6, "B": 24}, FIXED, ["CS"])
    assert f.tolist() == [1, 24, 12]


def test_unknown_uom_raises_instead_of_assuming_each():
    with pytest.raises(StandardisationError):
        uom_factor(pd.Series(["PAL"]), pd.Series(["A"]), {"A": 6}, FIXED, ["CS"])


def test_case_uom_without_case_pack_raises():
    with pytest.raises(StandardisationError):
        uom_factor(pd.Series(["CS"]), pd.Series(["UNKNOWN"]), {"A": 6}, FIXED, ["CS"])


def test_currency_conversion_and_missing_rate():
    assert to_usd(pd.Series([10.0]), pd.Series(["EUR"]), {"EUR": 1.08}).iloc[0] == pytest.approx(10.8)
    with pytest.raises(StandardisationError):
        to_usd(pd.Series([10.0]), pd.Series(["JPY"]), {"EUR": 1.08})


def test_dedupe_counts_and_keeps_first():
    df = pd.DataFrame({"id": [1, 1, 2], "v": ["a", "b", "c"]})
    out, n = dedupe(df, ["id"])
    assert n == 1 and out["v"].tolist() == ["a", "c"]


def test_quarantine_orphans_keeps_reason():
    df = pd.DataFrame({"sku": ["A", "X"], "loc": ["L1", "L9"]})
    clean, rej = quarantine_orphans(df, {"sku": {"A"}, "loc": {"L1"}}, "demand", "run-1")
    assert clean["sku"].tolist() == ["A"]
    assert rej.iloc[0]["reason"] == "unknown sku; unknown loc"
