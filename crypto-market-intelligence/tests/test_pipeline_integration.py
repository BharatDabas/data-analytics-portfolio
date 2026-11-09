"""Integration tests: full pipeline into DuckDB, then grain, reconciliation and DQ checks."""
import duckdb
import pytest

from config import settings
from ingestion.csv_import import parse_transactions_csv
from tests.conftest import AS_OF, NOW


@pytest.fixture(scope="module")
def con(built_db):
    c = duckdb.connect(str(built_db[0]))  # same config as the app connection in this process
    yield c
    c.close()


def scalar(con, sql):
    return con.execute(sql).fetchone()[0]


def test_no_failing_quality_checks(built_db):
    dq = built_db[1]["dq"]
    assert (dq["status"] == "fail").sum() == 0, dq[dq["status"] == "fail"]


def test_market_price_grain_is_unique(con):
    assert scalar(con, """SELECT COUNT(*) FROM (SELECT asset_id, quote_currency, interval, observed_at, COUNT(*) c
                          FROM fact_market_price GROUP BY ALL HAVING c > 1)""") == 0


def test_planted_defects_are_detected_not_hidden(con):
    assert scalar(con, "SELECT SUM(missing_points) FROM ops_missing_intervals") == 6
    assert scalar(con, "SELECT SUM(duplicates_dropped) FROM ops_ingestion_runs") == 1
    assert scalar(con, "SELECT COUNT(*) FROM ops_ingestion_runs WHERE status = 'failed'") == 1
    assert scalar(con, """SELECT COUNT(*) FROM ops_ingestion_runs WHERE asset_id = 'demo-flux-network'
                          AND status = 'success'""") == 1
    # no forward-filled rows for missing days
    assert scalar(con, "SELECT COUNT(*) FROM fact_market_price WHERE asset_id = 'demo-dune'") == 727
    assert scalar(con, "SELECT COUNT(*) FROM fact_alert_events WHERE rule_type = 'stale_data'") == 1


def test_symbol_collision_resolved_by_canonical_id(con):
    assert scalar(con, "SELECT COUNT(*) FROM dim_asset WHERE symbol = 'FLX'") == 2
    assert scalar(con, "SELECT COUNT(DISTINCT asset_id) FROM fact_market_price WHERE asset_id LIKE 'demo-flux%'") == 2


def test_timestamps_are_utc_and_observation_differs_from_retrieval(con):
    assert scalar(con, "SELECT COUNT(*) FROM fact_market_price WHERE observed_at = retrieved_at") == 0
    assert scalar(con, "SELECT MAX(price_date) FROM fact_market_price") == AS_OF


def test_all_synthetic_rows_are_labelled(con):
    assert scalar(con, """SELECT COUNT(*) FROM fact_market_price p JOIN dim_source s USING (source_id)
                          WHERE s.data_kind <> 'synthetic'""") == 0


def test_portfolio_reconciles(con):
    mv = scalar(con, "SELECT SUM(market_value) FROM v_current_holdings")
    latest = scalar(con, "SELECT market_value FROM v_portfolio_daily ORDER BY snapshot_date DESC LIMIT 1")
    assert mv == pytest.approx(latest)
    assert scalar(con, "SELECT COUNT(*) FROM fact_disposals") > 0
    assert scalar(con, "SELECT COUNT(*) FROM fact_transactions WHERE txn_type LIKE 'TRANSFER%'") == 2


def test_views_exist(con):
    for v in ["v_market_daily", "v_latest_market", "v_portfolio_daily", "v_realised_pnl", "v_current_holdings",
              "v_data_freshness"]:
        assert scalar(con, f"SELECT COUNT(*) FROM {v}") > 0


def test_rerun_is_idempotent(tmp_path):
    from pipeline import build
    path = tmp_path / "idem.duckdb"
    build(db_path=path, as_of=AS_OF, seed=1, now=NOW, raw_dir=tmp_path / "raw", days=90)
    c = duckdb.connect(str(path), read_only=True)
    n1 = scalar(c, "SELECT COUNT(*) FROM fact_market_price"), scalar(c, "SELECT COUNT(*) FROM fact_transactions")
    c.close()
    build(db_path=path, as_of=AS_OF, seed=1, now=NOW, raw_dir=tmp_path / "raw", days=90)
    c = duckdb.connect(str(path), read_only=True)
    n2 = scalar(c, "SELECT COUNT(*) FROM fact_market_price"), scalar(c, "SELECT COUNT(*) FROM fact_transactions")
    c.close()
    assert n1 == n2


def test_csv_template_imports_cleanly():
    res = parse_transactions_csv((settings.SAMPLE_DIR / "transactions_template.csv").read_bytes(),
                                 known_assets={"demo-aurora", "demo-borealis"})
    assert res.ok, res.errors
    assert len(res.frame) == 6 and str(res.frame["executed_at"].dt.tz) == "UTC"


def test_csv_rejects_tickers_and_bad_rows():
    csv = ("transaction_id,asset_id,txn_type,quantity,unit_price,fee,executed_at\n"
           "a,AUR,BUY,1,10,0,2026-01-01\n")
    assert "canonical" in " ".join(parse_transactions_csv(csv, "p", known_assets={"demo-aurora"}).errors)
    bad = "transaction_id,asset_id,txn_type,quantity,unit_price,fee,executed_at\na,demo-aurora,BUY,1,10,0,notadate\n"
    assert not parse_transactions_csv(bad, "p").ok
