from datetime import date, datetime, timezone

import pandas as pd
import pytest

AS_OF = date(2026, 10, 31)
NOW = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def built_db(tmp_path_factory):
    """Full pipeline run into a temporary DuckDB file with a pinned as-of date."""
    from pipeline import build
    tmp = tmp_path_factory.mktemp("crypto")
    path = tmp / "crypto_test.duckdb"
    out = build(db_path=path, as_of=AS_OF, seed=42, now=NOW, raw_dir=tmp / "raw", rebuild=True)
    return path, out


def tx(rows):
    """Helper: build a transactions frame from tuples
    (id, type, asset, qty, price, fee, timestamp)."""
    return pd.DataFrame([{"transaction_id": r[0], "portfolio_id": "p1", "txn_type": r[1], "asset_id": r[2],
                          "quantity": r[3], "unit_price": r[4], "fee": r[5],
                          "executed_at": pd.Timestamp(r[6], tz="UTC")} for r in rows])
