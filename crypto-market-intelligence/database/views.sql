-- Reusable analytical views (consumed by the Streamlit app and documented for Power BI).

-- Daily market observations with consecutive-day simple return (no gap-spanning returns).
CREATE OR REPLACE VIEW v_market_daily AS
SELECT
    p.asset_id, a.symbol, a.name, a.category, a.is_stablecoin,
    p.quote_currency, p.price_date, p.observed_at, p.retrieved_at,
    p.open, p.high, p.low, p.close, p.volume, p.market_cap, p.source_id, s.data_kind,
    CASE WHEN LAG(p.price_date) OVER w = p.price_date - INTERVAL 1 DAY
         THEN p.close / LAG(p.close) OVER w - 1 END AS daily_return
FROM fact_market_price p
JOIN dim_asset a USING (asset_id)
JOIN dim_source s ON s.source_id = p.source_id
WHERE p.interval = '1d'
WINDOW w AS (PARTITION BY p.asset_id, p.quote_currency ORDER BY p.price_date);

-- Latest available observation per asset with point-to-point changes.
CREATE OR REPLACE VIEW v_latest_market AS
WITH ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY asset_id, quote_currency ORDER BY price_date DESC) AS rn
    FROM v_market_daily
)
SELECT
    l.asset_id, l.symbol, l.name, l.category, l.is_stablecoin, l.quote_currency, l.data_kind, l.source_id,
    l.price_date AS latest_price_date, l.observed_at, l.retrieved_at,
    l.close AS latest_close, l.volume AS latest_volume, l.market_cap AS latest_market_cap,
    l.daily_return AS change_1d,
    l.close / (SELECT d.close FROM v_market_daily d WHERE d.asset_id = l.asset_id AND d.quote_currency = l.quote_currency
               AND d.price_date <= l.price_date - INTERVAL 7 DAY ORDER BY d.price_date DESC LIMIT 1) - 1 AS change_7d,
    l.close / (SELECT d.close FROM v_market_daily d WHERE d.asset_id = l.asset_id AND d.quote_currency = l.quote_currency
               AND d.price_date <= l.price_date - INTERVAL 30 DAY ORDER BY d.price_date DESC LIMIT 1) - 1 AS change_30d
FROM ranked l
WHERE l.rn = 1;

-- Portfolio-level daily valuation (aggregated from the asset-grain snapshot).
CREATE OR REPLACE VIEW v_portfolio_daily AS
SELECT
    portfolio_id, snapshot_date,
    SUM(market_value) AS market_value,
    SUM(cost_basis) AS cost_basis,
    SUM(external_flow) AS external_flow,
    SUM(CASE WHEN market_value IS NULL AND ABS(units_held) > 1e-12 THEN 1 ELSE 0 END) AS unvalued_positions,
    SUM(CASE WHEN price_is_stale THEN 1 ELSE 0 END) AS stale_positions
FROM fact_portfolio_snapshot
GROUP BY portfolio_id, snapshot_date;

-- Realised P&L by asset (from the FIFO disposal audit trail).
CREATE OR REPLACE VIEW v_realised_pnl AS
SELECT portfolio_id, asset_id, SUM(quantity) AS units_disposed, SUM(proceeds) AS proceeds,
       SUM(cost_basis) AS cost_basis_disposed, SUM(realised_pnl) AS realised_pnl
FROM fact_disposals
GROUP BY portfolio_id, asset_id;

-- Current holdings = latest snapshot per portfolio/asset with unrealised P&L.
CREATE OR REPLACE VIEW v_current_holdings AS
WITH latest AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY portfolio_id, asset_id ORDER BY snapshot_date DESC) AS rn
    FROM fact_portfolio_snapshot
)
SELECT l.portfolio_id, l.asset_id, a.symbol, a.name, a.category, l.snapshot_date, l.units_held, l.cost_basis,
       CASE WHEN l.units_held > 1e-12 THEN l.cost_basis / l.units_held END AS avg_cost,
       l.close_price, l.price_date, l.price_is_stale, l.market_value,
       l.market_value - l.cost_basis AS unrealised_pnl
FROM latest l JOIN dim_asset a USING (asset_id)
WHERE l.rn = 1 AND l.units_held > 1e-12;

-- Data freshness per asset and source.
CREATE OR REPLACE VIEW v_data_freshness AS
SELECT p.asset_id, a.name, p.source_id, MAX(p.observed_at) AS latest_observed_at, MAX(p.retrieved_at) AS latest_retrieved_at,
       MIN(p.price_date) AS first_price_date, MAX(p.price_date) AS last_price_date, COUNT(*) AS observations,
       DATE_DIFF('day', MIN(p.price_date), MAX(p.price_date)) + 1 AS expected_days
FROM fact_market_price p JOIN dim_asset a USING (asset_id)
GROUP BY p.asset_id, a.name, p.source_id;
