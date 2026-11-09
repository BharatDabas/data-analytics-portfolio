from datetime import date

import pandas as pd
import pytest
import requests

from database import db
from ingestion.engine import ingest_assets
from ingestion.http_client import (CallStats, IngestionError, RateLimitedClient, RetryPolicy, TransientError,
                                   call_with_retry)
from ingestion.providers import CoinGeckoProvider, SyntheticProvider


def test_retry_succeeds_after_transient_errors_and_honours_retry_after():
    calls, waits = [], []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise TransientError("429", 429, retry_after=7)
        return "ok"

    stats = CallStats()
    assert call_with_retry(fn, RetryPolicy(max_retries=5), stats, sleep=waits.append) == "ok"
    assert stats.attempts == 3 and waits == [7, 7]


def test_exponential_backoff_and_exhaustion():
    waits = []
    policy = RetryPolicy(max_retries=3, backoff_base=2, jitter=0)
    with pytest.raises(IngestionError) as exc:
        call_with_retry(lambda: (_ for _ in ()).throw(TransientError("503", 503)), policy, sleep=waits.append)
    assert waits == [1, 2, 4]
    assert exc.value.attempts == 4 and exc.value.status == 503


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers, self.text = status, body, headers or {}, str(body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls, self.headers = list(responses), [], {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make_client(responses, waits):
    return RateLimitedClient("https://api.example", 600, session=FakeSession(responses), sleep=waits.append,
                             clock=lambda: 0.0)


def test_client_maps_status_codes():
    waits = []
    c = make_client([FakeResponse(429, headers={"Retry-After": "3"}), FakeResponse(404, "nope"),
                     requests.Timeout()], waits)
    with pytest.raises(TransientError) as e:
        c.get_json("/x")
    assert e.value.retry_after == 3
    with pytest.raises(IngestionError):
        c.get_json("/x")
    with pytest.raises(TransientError):
        c.get_json("/x")


def test_client_throttles_between_calls():
    waits = []
    c = make_client([FakeResponse(200, {}), FakeResponse(200, {})], waits)
    c.get_json("/a")
    c.get_json("/b")
    assert waits == [pytest.approx(0.1)]  # 600 rpm -> 0.1s spacing, clock frozen


def test_coingecko_pagination_stops_on_short_page():
    page1 = [{"id": f"c{i}", "symbol": "x", "name": "X"} for i in range(2)]
    page2 = [{"id": "c9", "symbol": "usdt", "name": "Tether"}]
    waits = []
    client = make_client([FakeResponse(200, page1), FakeResponse(200, page2)], waits)
    assets = CoinGeckoProvider(client, page_size=2).list_assets(limit=10)
    assert [a.asset_id for a in assets] == ["c0", "c1", "c9"]
    assert assets[-1].is_stablecoin
    assert [p["page"] for _, p in client.session.calls] == [1, 2]


def test_coingecko_history_marks_ohlc_unsupported():
    client = make_client([FakeResponse(200, {"prices": [[0, 1]], "market_caps": [], "total_volumes": []})], [])
    assert CoinGeckoProvider(client).fetch_history("bitcoin", 30)["ohlc"] is None


def test_engine_logs_failed_run_then_recovers(tmp_path):
    con = db.connect(":memory:")
    from pipeline import init_db
    init_db(con)
    provider = SyntheticProvider(date(2026, 10, 31), 42, 60, outage_calls=3)
    db.upsert(con, "dim_asset", pd.DataFrame([{"asset_id": "demo-flux-network", "symbol": "FLX", "name": "Flux Network",
                                               "category": "x", "is_stablecoin": False, "source_id": "synthetic_demo",
                                               "provider_asset_id": "demo-flux-network"}]), ["asset_id"])
    policy = RetryPolicy(max_retries=2)
    first = ingest_assets(con, provider, ["demo-flux-network"], 60, tmp_path, policy=policy, sleep=lambda s: None)
    assert first.failed == ["demo-flux-network"]
    second = ingest_assets(con, provider, first.failed, 60, tmp_path, policy=policy, sleep=lambda s: None)
    assert second.succeeded == ["demo-flux-network"] and second.rows_loaded == 60
    runs = db.query(con, "SELECT status, attempts, http_status FROM ops_ingestion_runs ORDER BY started_at")
    assert runs["status"].tolist() == ["failed", "success"]
    assert runs["attempts"].tolist() == [3, 1] and runs["http_status"].iloc[0] == 503
    # idempotent: re-ingesting does not duplicate rows
    ingest_assets(con, provider, ["demo-flux-network"], 60, tmp_path, policy=policy, sleep=lambda s: None)
    assert db.query(con, "SELECT COUNT(*) AS n FROM fact_market_price")["n"].iloc[0] == 60
    assert any(tmp_path.rglob("demo-flux-network.json"))  # raw payload preserved
