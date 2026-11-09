from datetime import date, datetime, timezone

import pandas as pd

from transformations.market import deduplicate, detect_missing_intervals, parse_market_chart, validate_prices

DAY = 86_400_000
T0 = int(datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)  # midnight -> close of 1 Jan


def payload(n=3, dup=None, intraday=False):
    ts = [T0 + i * DAY for i in range(n)]
    if dup is not None:
        ts.insert(dup, ts[dup])
    p = {"prices": [[t, 10.0 + i] for i, t in enumerate(ts)],
         "market_caps": [[t, 1000.0] for t in ts],
         "total_volumes": [[t, 50.0] for t in ts],
         "ohlc": None}
    if intraday:
        p["prices"].append([ts[-1] + 3_600_000 * 5, 99.0])
    return p


def test_midnight_observation_maps_to_previous_day_close():
    df = parse_market_chart(payload(), "a", "src", today=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert df["price_date"].tolist() == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    assert str(df["observed_at"].dt.tz) == "UTC"
    assert df["open"].isna().all()  # OHLC not supplied -> NULL, never invented


def test_retrieval_time_kept_separately():
    ret = datetime(2026, 2, 1, 12, tzinfo=timezone.utc)
    df = parse_market_chart(payload(), "a", "src", retrieved_at=ret, today=ret)
    assert (df["retrieved_at"] == pd.Timestamp(ret)).all()
    assert (df["observed_at"] != df["retrieved_at"]).all()


def test_intraday_point_on_current_day_is_dropped():
    today = datetime(2026, 1, 4, 12, tzinfo=timezone.utc)
    df = parse_market_chart(payload(intraday=True), "a", "src", today=today)
    assert 99.0 not in df["close"].tolist()


def test_duplicate_timestamps_are_removed_keeping_latest_retrieval():
    df = parse_market_chart(payload(dup=1), "a", "src", today=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert len(df) == 4
    clean, n = deduplicate(df)
    assert n == 1 and len(clean) == 3
    assert clean["observed_at"].is_unique


def test_missing_intervals_collapsed_into_ranges_and_tail_reported():
    days = [date(2026, 1, d) for d in (1, 2, 5, 6, 8)]
    df = pd.DataFrame({"asset_id": "a", "quote_currency": "usd", "price_date": days})
    gaps = detect_missing_intervals(df, expected_end=pd.Timestamp("2026-01-10"))
    assert gaps[["gap_start", "gap_end", "missing_points"]].values.tolist() == [
        [date(2026, 1, 3), date(2026, 1, 4), 2], [date(2026, 1, 7), date(2026, 1, 7), 1],
        [date(2026, 1, 9), date(2026, 1, 10), 2]]


def test_no_gaps_returns_empty():
    df = pd.DataFrame({"asset_id": "a", "quote_currency": "usd",
                       "price_date": [date(2026, 1, d) for d in (1, 2, 3)]})
    assert detect_missing_intervals(df).empty


def test_validate_prices_flags_bad_rows():
    df = pd.DataFrame({"close": [10, -1, 10, 10], "open": [9, 9, 9, None], "high": [11, 11, 9.5, None],
                       "low": [8, 8, 8, None], "volume": [1, 1, 1, -5]})
    assert validate_prices(df).tolist() == [True, False, False, False]
