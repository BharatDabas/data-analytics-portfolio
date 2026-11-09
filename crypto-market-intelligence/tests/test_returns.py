import numpy as np
import pandas as pd
import pytest

from analytics import returns as R


def series(values, start="2026-01-01", drop=()):
    idx = pd.date_range(start, periods=len(values), freq="D")
    s = pd.Series(values, index=idx, dtype=float)
    return s.drop(idx[list(drop)])


def test_simple_returns_do_not_span_gaps():
    s = series([100, 110, 121, 133.1, 146.41], drop=[2])  # day 3 missing
    r = R.simple_returns(s)
    assert r.iloc[1] == pytest.approx(0.10)
    assert np.isnan(r.iloc[2]) and np.isnan(r.iloc[3])  # no fill, no gap-spanning return
    assert r.iloc[4] == pytest.approx(0.10)


def test_cumulative_return_compounds():
    assert R.cumulative_return(pd.Series([0.1, -0.1, np.nan])) == pytest.approx(1.1 * 0.9 - 1)
    assert np.isnan(R.cumulative_return(pd.Series([np.nan])))


def test_annualised_volatility_scaling():
    r = pd.Series([0.01, -0.01] * 50)
    assert R.annualised_volatility(r, 365) == pytest.approx(r.std(ddof=1) * np.sqrt(365))
    assert np.isnan(R.annualised_volatility(pd.Series([0.01]), 365))


def test_max_drawdown_known_path():
    v = series([100, 120, 90, 95, 130, 65])
    mdd, peak, trough = R.max_drawdown(v)
    assert mdd == pytest.approx(65 / 130 - 1)
    assert peak == v.index[4] and trough == v.index[5]


def test_drawdown_never_positive():
    assert (R.drawdown_series(series([1, 2, 3, 2, 4])) <= 0).all()


def test_sharpe_requires_enough_data_and_volatility():
    assert R.sharpe_ratio(pd.Series([0.01] * 10)) is None
    assert R.sharpe_ratio(pd.Series([0.0] * 100)) is None
    r = pd.Series([0.02, 0.0] * 50)
    expected = (r.mean() - 0.05 / 365) / r.std(ddof=1) * np.sqrt(365)
    assert R.sharpe_ratio(r, 0.05, 365) == pytest.approx(expected)


def test_correlation_respects_min_overlap():
    rng = np.random.default_rng(0)
    a = pd.Series(rng.normal(size=100))
    df = pd.DataFrame({"a": a, "b": a * 2 + 0.0, "c": [np.nan] * 90 + list(rng.normal(size=10))})
    c = R.correlation_matrix(df, min_overlap=60)
    assert c.loc["a", "b"] == pytest.approx(1.0)
    assert np.isnan(c.loc["a", "c"])


def test_period_return_uses_last_observation_on_or_before_boundary():
    s = series([100, 101, 102, 103, 104, 105, 106, 107, 108], drop=[1])
    end = s.index[-1]
    assert R.period_return(s, end, days=7) == pytest.approx(108 / 100 - 1)  # boundary day missing -> earlier obs
    assert np.isnan(R.period_return(s, end, days=30))


def test_herfindahl_bounds():
    assert R.herfindahl(pd.Series([1.0])) == 1
    assert R.herfindahl(pd.Series([0.25] * 4)) == pytest.approx(0.25)


def test_moving_average_requires_full_window():
    ma = R.moving_average(series([1, 2, 3, 4]), 3)
    assert np.isnan(ma.iloc[1]) and ma.iloc[2] == 2
