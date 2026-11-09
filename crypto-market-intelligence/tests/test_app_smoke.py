"""Smoke test: the Streamlit app starts and every page renders without an exception."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parent.parent / "app.py")
PAGES = ["Market Overview", "Asset Explorer", "Portfolio Overview", "Risk & Diversification",
         "Performance & Benchmark", "Alerts & Data Operations"]


@pytest.fixture(scope="module")
def app(built_db, monkeypatch_module):
    monkeypatch_module.setenv("CRYPTO_DB_PATH", str(built_db[0]))
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    return at


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(app, page):
    assert app.sidebar.radio[0].options == PAGES
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception, [e.value for e in app.exception]
    assert not app.error, [e.value for e in app.error]
    assert any("Synthetic demo data" in m.value for m in app.markdown)


def test_manual_transaction_is_validated_and_persisted(tmp_path, monkeypatch):
    """Write path: a valid BUY entered on the dashboard lands in the ledger and recalculates holdings;
    an oversell is rejected with a message and nothing is written."""
    import duckdb
    from pipeline import build
    from tests.conftest import AS_OF, NOW
    path = tmp_path / "write.duckdb"
    build(db_path=path, as_of=AS_OF, seed=3, now=NOW, raw_dir=tmp_path / "raw", days=120)
    monkeypatch.setenv("CRYPTO_DB_PATH", str(path))
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.sidebar.radio[0].set_value("Portfolio Overview").run()

    def submit(ttype, qty, price):
        [s for s in at.selectbox if s.label == "Type"][0].set_value(ttype)
        [s for s in at.selectbox if s.label == "Asset (canonical ID)"][0].set_value("demo-aurora")
        [n for n in at.number_input if n.label == "Quantity"][0].set_value(qty)
        [n for n in at.number_input if n.label.startswith("Unit price")][0].set_value(price)
        [b for b in at.button if b.label == "Add to ledger"][0].click().run()

    submit("SELL", 1_000_000.0, 1.0)
    assert any("insufficient units" in e.value for e in at.error)
    submit("BUY", 0.5, 20_000.0)
    assert not at.exception
    con = duckdb.connect(str(path))
    try:
        rows = con.execute("SELECT txn_type, quantity FROM fact_transactions WHERE entry_source = 'manual'").fetchall()
        assert rows == [("BUY", 0.5)]
        units = con.execute("SELECT units_held FROM v_current_holdings WHERE asset_id = 'demo-aurora'").fetchall()
        assert units and units[0][0] >= 0.5
    finally:
        con.close()
