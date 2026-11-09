import math

import pandas as pd

from analytics.classification import abc_classify, coefficient_of_variation, xyz_classify


def test_abc_boundaries():
    v = pd.Series([50, 30, 15, 5], index=list("abcd"))
    # cumulative share before item: 0, .5, .8, .95 -> A, A, B, C
    assert abc_classify(v).tolist() == ["A", "A", "B", "C"]


def test_abc_first_item_always_a_and_order_preserved():
    v = pd.Series([1, 99], index=["small", "big"])
    out = abc_classify(v)
    assert out.to_dict() == {"small": "C", "big": "A"}  # 99% share before "small"


def test_abc_zero_values_and_zero_total():
    assert abc_classify(pd.Series([0.0, 0.0])).tolist() == ["C", "C"]
    assert abc_classify(pd.Series([10.0, 0.0])).tolist() == ["A", "C"]


def test_xyz_thresholds_inclusive_and_undefined_is_z():
    cv = pd.Series([0.5, 0.51, 1.0, 1.2, float("nan")])
    assert xyz_classify(cv).tolist() == ["X", "Y", "Y", "Z", "Z"]


def test_cv_no_demand_is_nan():
    assert math.isnan(coefficient_of_variation(pd.Series([0, 0, 0])))
    assert coefficient_of_variation(pd.Series([10, 10, 10])) == 0
