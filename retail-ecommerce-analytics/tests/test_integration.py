"""Full pipeline -> DuckDB integration and SQL view reconciliation."""
import pandas as pd
import pytest

from analytics import kpis
from database import db


def test_pipeline_succeeds_and_logs(built_db, con):
    s = built_db["summary"]
    assert s["status"] == "succeeded"
    runs = db.query(con, "SELECT * FROM ops_pipeline_runs")
    assert len(runs) == 1 and runs.loc[0, "rows_loaded"] > 0
    assert db.query(con, "SELECT count(*) n FROM ops_load_failures").loc[0, "n"] == s["rejected"]
    assert db.query(con, "SELECT count(*) n FROM v_dq_latest WHERE status = 'fail'").loc[0, "n"] == 0


def test_grain_is_unique(con):
    for table, keys in {"fact_orders": "order_id", "fact_order_items": "order_item_id", "fact_returns": "return_id",
                        "fact_marketing_spend": "spend_date, campaign_id", "bridge_order_attribution": "order_id",
                        "dim_customer": "customer_id", "dim_product": "product_id", "dim_date": "date_key"}.items():
        dup = db.query(con, f"SELECT count(*) n FROM (SELECT {keys} FROM {table} GROUP BY ALL HAVING count(*) > 1)")
        assert dup.loc[0, "n"] == 0, table


def test_net_revenue_reconciles_across_facts_and_views(con):
    facts = db.query(con, """
        SELECT sum(li.net_sales_gbp) - sum(li.refund_gbp) AS net
        FROM fact_order_items li JOIN fact_orders o USING (order_id) WHERE NOT o.is_cancelled""").loc[0, "net"]
    refunds_events = db.query(con, """
        SELECT sum(r.refund_gbp) AS r FROM fact_returns r JOIN fact_orders o USING (order_id)
        WHERE NOT o.is_cancelled""").loc[0, "r"]
    line_refunds = db.query(con, """
        SELECT sum(li.refund_gbp) AS r FROM fact_order_items li JOIN fact_orders o USING (order_id)
        WHERE NOT o.is_cancelled""").loc[0, "r"]
    summary = db.query(con, "SELECT * FROM v_kpi_summary").iloc[0]
    daily = db.query(con, "SELECT sum(net_revenue_gbp) n FROM v_sales_daily").loc[0, "n"]
    customers = db.query(con, "SELECT sum(net_revenue_gbp) n FROM v_customer_value").loc[0, "n"]
    assert summary["net_revenue_gbp"] == pytest.approx(facts, rel=1e-9)
    assert daily == pytest.approx(facts, rel=1e-9)
    assert customers == pytest.approx(facts, rel=1e-9)
    assert line_refunds == pytest.approx(refunds_events, rel=1e-9)
    assert summary["gross_sales_gbp"] - summary["discount_gbp"] - summary["refund_gbp"] == pytest.approx(facts, rel=1e-9)


def test_python_kpis_match_sql(con):
    lines = db.query(con, "SELECT * FROM v_order_lines")
    orders = db.query(con, "SELECT * FROM v_order_value")
    k = kpis.commercial_kpis(lines, orders)
    s = db.query(con, "SELECT * FROM v_kpi_summary").iloc[0]
    assert k["net_revenue"] == pytest.approx(s["net_revenue_gbp"], rel=1e-9)
    assert k["orders"] == s["orders"]
    assert k["aov"] == pytest.approx(s["aov_gbp"], rel=1e-9)


def test_marketing_view_preserves_spend_and_attributed_revenue(con):
    spend_fact = db.query(con, "SELECT sum(spend_gbp) s FROM fact_marketing_spend").loc[0, "s"]
    mk = db.query(con, "SELECT sum(spend_gbp) s, sum(attributed_net_revenue_gbp) r, sum(attributed_orders) o "
                       "FROM v_marketing_daily").iloc[0]
    bridge = db.query(con, """SELECT count(*) o, sum(v.net_revenue_gbp) r FROM bridge_order_attribution b
                              JOIN v_order_value v USING (order_id)""").iloc[0]
    total = db.query(con, "SELECT net_revenue_gbp FROM v_kpi_summary").iloc[0, 0]
    assert mk["s"] == pytest.approx(spend_fact, rel=1e-9)        # spend not duplicated by the join
    assert mk["r"] == pytest.approx(bridge["r"], rel=1e-9)       # attributed revenue not duplicated
    assert mk["o"] == bridge["o"]
    assert mk["r"] <= total                                      # attributed <= total revenue


def test_gross_profit_only_on_costed_products(con):
    bad = db.query(con, """SELECT count(*) n FROM v_order_lines
                           WHERE NOT has_reliable_cost AND gross_profit_gbp IS NOT NULL""").loc[0, "n"]
    assert bad == 0


def test_pipeline_is_idempotent(built_db, tmp_path, monkeypatch):
    monkeypatch.setenv("RETAIL_AS_OF", "2026-10-31")
    from pipeline import run_pipeline
    c = db.connect(built_db["db_path"], read_only=True)
    before = db.query(c, "SELECT count(*) n, sum(net_revenue_gbp) r FROM v_order_value").iloc[0]
    c.close()
    copy = tmp_path / "copy.duckdb"
    run_pipeline(db_path=copy, raw_dir=built_db["raw_dir"], generate_data=False)
    run_pipeline(db_path=copy, raw_dir=built_db["raw_dir"], generate_data=False)
    c = db.connect(copy, read_only=True)
    after = db.query(c, "SELECT count(*) n, sum(net_revenue_gbp) r FROM v_order_value").iloc[0]
    runs = db.query(c, "SELECT count(*) n FROM ops_pipeline_runs").loc[0, "n"]
    c.close()
    assert after["n"] == before["n"] and after["r"] == pytest.approx(before["r"])
    assert runs == 2  # history is appended, facts are replaced


def test_dim_date_covers_all_fact_dates(con):
    gaps = db.query(con, """SELECT count(*) n FROM fact_orders o
                            LEFT JOIN dim_date d ON d.date_key = o.order_date WHERE d.date_key IS NULL""")
    assert gaps.loc[0, "n"] == 0
    fy = db.query(con, "SELECT fiscal_year, fiscal_quarter FROM dim_date WHERE date_key = DATE '2026-04-01'").iloc[0]
    assert fy["fiscal_year"] == 2027 and fy["fiscal_quarter"] == 1
