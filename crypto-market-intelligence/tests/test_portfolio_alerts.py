from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from analytics.alerts import evaluate_rules
from analytics.backtest import backtest
from analytics.portfolio import build_snapshots, portfolio_daily
from tests.conftest import tx


def prices(asset, closes, start="2026-01-01", drop=()):
    d = pd.date_range(start, periods=len(closes), freq="D")
    df = pd.DataFrame({"asset_id": asset, "price_date": d.date, "close": closes,
                       "observed_at": d + pd.Timedelta(days=1), "volume": 100.0})
    return df.drop(index=list(drop)).reset_index(drop=True)


def test_stale_fallback_is_flagged_and_bounded():
    px = prices("A", [10, 11, 12, 13, 14, 15, 16, 17], drop=[2, 3, 4, 5])  # 3..6 Jan missing
    snaps, _ = build_snapshots(tx([("t1", "BUY", "A", 1, 10, 0, "2026-01-01")]), px,
                               pd.Timestamp("2026-01-08"), max_stale_days=3)
    s = snaps.set_index("snapshot_date")
    assert not s.loc[date(2026, 1, 2), "price_is_stale"]
    assert s.loc[date(2026, 1, 3), "price_is_stale"] and s.loc[date(2026, 1, 3), "close_price"] == 11
    assert s.loc[date(2026, 1, 5), "price_is_stale"]                      # 3 days old: allowed
    assert np.isnan(s.loc[date(2026, 1, 6), "market_value"])              # 4 days old: unvalued
    assert s.loc[date(2026, 1, 7), "close_price"] == 16 and not s.loc[date(2026, 1, 7), "price_is_stale"]


def test_twr_removes_external_flows():
    px = prices("A", [100, 100, 110, 110])
    t = tx([("t1", "BUY", "A", 1, 100, 0, "2026-01-01"),
            ("t2", "BUY", "A", 1, 100, 0, "2026-01-02")])   # adding money is not performance
    snaps, _ = build_snapshots(t, px, pd.Timestamp("2026-01-04"))
    d = portfolio_daily(snaps).set_index("snapshot_date")
    assert d["twr_return"].iloc[0] == pytest.approx(0.0)
    assert d["twr_return"].iloc[1] == pytest.approx(0.0)
    assert d["twr_return"].iloc[2] == pytest.approx(0.10)
    assert d["twr_index"].iloc[-1] == pytest.approx(110)


def test_transfers_are_flows_valued_at_market():
    px = prices("A", [100, 120, 120])
    t = tx([("t1", "TRANSFER_IN", "A", 1, 50, 0, "2026-01-02")])
    snaps, disp = build_snapshots(t, px, pd.Timestamp("2026-01-03"))
    assert snaps["external_flow"].iloc[0] == pytest.approx(120)  # market value, not carried cost
    assert snaps["cost_basis"].iloc[0] == pytest.approx(50)
    assert disp.empty


def test_snapshot_units_reconcile_to_ledger():
    px = prices("A", [100] * 5)
    t = tx([("t1", "BUY", "A", 3, 100, 0, "2026-01-01"), ("t2", "SELL", "A", 1, 100, 0, "2026-01-03"),
            ("t3", "TRANSFER_OUT", "A", 1, None, 0, "2026-01-04")])
    snaps, disp = build_snapshots(t, px, pd.Timestamp("2026-01-05"))
    assert snaps.set_index("snapshot_date")["units_held"].tolist() == [3, 3, 2, 1, 1]
    assert len(disp) == 1


RULE = {"rule_id": "R", "name": "r", "asset_id": "*", "channels": ["dashboard"]}


def test_threshold_alert_fires_only_on_crossing():
    px = prices("A", [90, 105, 110, 95, 120])
    ev = evaluate_rules(px, [{**RULE, "type": "price_above", "threshold": 100}])
    assert len(ev) == 2


def test_pct_move_alert_ignores_gap_spanning_moves():
    px = prices("A", [100, 100, 200, 100], drop=[1])
    assert evaluate_rules(px, [{**RULE, "type": "pct_move", "threshold": 0.4}])["observed_value"].tolist() == [-0.5]


def test_volume_spike_and_stale_rules():
    px = prices("A", [1.0] * 40)
    px.loc[35, "volume"] = 1000
    ev = evaluate_rules(px, [{**RULE, "type": "volume_spike", "threshold": 3}])
    assert len(ev) == 1 and ev["observed_value"].iloc[0] == pytest.approx(10)
    now = datetime(2026, 3, 1, tzinfo=timezone.utc)
    stale = evaluate_rules(px, [{**RULE, "type": "stale_data", "threshold": 36}], now=now)
    assert len(stale) == 1


def test_alert_ids_are_deterministic():
    px = prices("A", [90, 105])
    r = [{**RULE, "type": "price_above", "threshold": 100}]
    assert evaluate_rules(px, r)["alert_id"].tolist() == evaluate_rules(px, r)["alert_id"].tolist()


def test_backtest_costs_reduce_value():
    idx = pd.date_range("2026-01-01", periods=60, freq="D")
    rets = pd.DataFrame({"A": [0.01, -0.01] * 30, "B": [-0.01, 0.01] * 30}, index=idx)
    free = backtest(rets, {"A": 0.5, "B": 0.5}, "M", 0, 0)
    costly = backtest(rets, {"A": 0.5, "B": 0.5}, "M", 50, 50)
    assert costly["value"].iloc[-1] < free["value"].iloc[-1]
    assert free["turnover"].sum() > 0
