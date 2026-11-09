-- Crypto Market Intelligence - analytical star schema (DuckDB).
-- All timestamps are TIMESTAMPTZ stored in UTC.

CREATE TABLE IF NOT EXISTS dim_source (
    source_id        VARCHAR PRIMARY KEY,
    provider         VARCHAR NOT NULL,
    endpoint         VARCHAR,
    data_kind        VARCHAR NOT NULL,      -- synthetic | provider_api | user_entered
    description      VARCHAR,
    attribution      VARCHAR,
    status           VARCHAR,               -- active | degraded | inactive
    last_success_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS dim_asset (
    asset_id           VARCHAR PRIMARY KEY, -- canonical provider ID, never the ticker
    symbol             VARCHAR NOT NULL,    -- NOT unique (collisions are expected)
    name               VARCHAR NOT NULL,
    category           VARCHAR,
    is_stablecoin      BOOLEAN DEFAULT FALSE,
    source_id          VARCHAR NOT NULL,
    provider_asset_id  VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_date (
    date_key      DATE PRIMARY KEY,
    year          INTEGER,
    quarter       INTEGER,
    month         INTEGER,
    month_name    VARCHAR,
    year_month    VARCHAR,
    iso_week      INTEGER,
    day_of_week   INTEGER,     -- 1 = Monday
    day_name      VARCHAR,
    is_weekend    BOOLEAN,     -- crypto trades every day; kept for calendar analysis
    is_month_end  BOOLEAN
);

-- Grain: asset x quote currency x interval x observation timestamp.
CREATE TABLE IF NOT EXISTS fact_market_price (
    asset_id          VARCHAR NOT NULL,
    quote_currency    VARCHAR NOT NULL,
    interval          VARCHAR NOT NULL,
    price_date        DATE NOT NULL,
    observed_at       TIMESTAMPTZ NOT NULL,   -- market observation time (provider)
    open              DOUBLE,                 -- NULL where the provider does not supply OHLC
    high              DOUBLE,
    low               DOUBLE,
    close             DOUBLE NOT NULL,
    volume            DOUBLE,
    market_cap        DOUBLE,
    source_id         VARCHAR NOT NULL,
    retrieved_at      TIMESTAMPTZ NOT NULL,   -- when we fetched it
    ingestion_run_id  VARCHAR,
    PRIMARY KEY (asset_id, quote_currency, interval, observed_at)
);

-- Grain: one row per portfolio transaction.
CREATE TABLE IF NOT EXISTS fact_transactions (
    transaction_id    VARCHAR PRIMARY KEY,
    portfolio_id      VARCHAR NOT NULL,
    asset_id          VARCHAR NOT NULL,
    txn_type          VARCHAR NOT NULL,       -- BUY | SELL | TRANSFER_IN | TRANSFER_OUT | FEE
    quantity          DOUBLE NOT NULL,        -- always positive; direction comes from txn_type
    unit_price        DOUBLE,                 -- execution price (BUY/SELL) or cost basis per unit (TRANSFER_IN)
    fee               DOUBLE DEFAULT 0,       -- in quote currency
    quote_currency    VARCHAR NOT NULL,
    executed_at       TIMESTAMPTZ NOT NULL,
    reference         VARCHAR,
    entry_source      VARCHAR NOT NULL,       -- demo_ledger | manual | csv_import
    loaded_at         TIMESTAMPTZ NOT NULL
);

-- Grain: one row per disposal lot-match (realised P&L audit trail, FIFO).
CREATE TABLE IF NOT EXISTS fact_disposals (
    transaction_id    VARCHAR NOT NULL,
    portfolio_id      VARCHAR NOT NULL,
    asset_id          VARCHAR NOT NULL,
    disposed_at       TIMESTAMPTZ NOT NULL,
    quantity          DOUBLE NOT NULL,
    proceeds          DOUBLE NOT NULL,        -- net of the allocated sale fee
    cost_basis        DOUBLE NOT NULL,
    realised_pnl      DOUBLE NOT NULL,
    holding_days      INTEGER
);

-- Grain: one row per portfolio x asset x snapshot date (aggregates to portfolio level in views).
CREATE TABLE IF NOT EXISTS fact_portfolio_snapshot (
    portfolio_id        VARCHAR NOT NULL,
    asset_id            VARCHAR NOT NULL,
    snapshot_date       DATE NOT NULL,
    units_held          DOUBLE NOT NULL,
    cost_basis          DOUBLE NOT NULL,
    close_price         DOUBLE,              -- NULL when no permitted price exists
    price_date          DATE,                -- date of the price actually used
    price_is_stale      BOOLEAN NOT NULL,    -- TRUE when the documented fallback was used
    market_value        DOUBLE,
    external_flow       DOUBLE NOT NULL,     -- value moved in (+) / out (-) of the portfolio that day
    valuation_currency  VARCHAR NOT NULL,
    PRIMARY KEY (portfolio_id, asset_id, snapshot_date)
);

-- Grain: one row per triggered alert.
CREATE TABLE IF NOT EXISTS fact_alert_events (
    alert_id        VARCHAR PRIMARY KEY,
    rule_id         VARCHAR NOT NULL,
    rule_name       VARCHAR,
    rule_type       VARCHAR NOT NULL,
    asset_id        VARCHAR NOT NULL,
    threshold       DOUBLE,
    observed_value  DOUBLE,
    observed_at     TIMESTAMPTZ,
    triggered_at    TIMESTAMPTZ NOT NULL,
    channels        VARCHAR,
    message         VARCHAR
);

-- ------------------------------------------------------------------ operations
CREATE TABLE IF NOT EXISTS ops_ingestion_runs (
    run_id              VARCHAR NOT NULL,
    batch_id            VARCHAR NOT NULL,
    source_id           VARCHAR NOT NULL,
    endpoint            VARCHAR,
    asset_id            VARCHAR,
    started_at          TIMESTAMPTZ NOT NULL,
    finished_at         TIMESTAMPTZ,
    status              VARCHAR NOT NULL,     -- success | failed
    http_status         INTEGER,
    attempts            INTEGER,
    rows_received       INTEGER,
    rows_loaded         INTEGER,
    duplicates_dropped  INTEGER,
    invalid_rows        INTEGER,
    raw_path            VARCHAR,
    error_message       VARCHAR
);

CREATE TABLE IF NOT EXISTS ops_missing_intervals (
    asset_id        VARCHAR NOT NULL,
    quote_currency  VARCHAR NOT NULL,
    interval        VARCHAR NOT NULL,
    gap_start       DATE NOT NULL,
    gap_end         DATE NOT NULL,
    missing_points  INTEGER NOT NULL,
    detected_at     TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ops_pipeline_runs (
    run_id        VARCHAR PRIMARY KEY,
    pipeline      VARCHAR NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL,
    finished_at   TIMESTAMPTZ,
    status        VARCHAR NOT NULL,
    rows_loaded   INTEGER,
    message       VARCHAR,
    code_version  VARCHAR
);

CREATE TABLE IF NOT EXISTS ops_dq_results (
    run_id       VARCHAR NOT NULL,
    check_name   VARCHAR NOT NULL,
    table_name   VARCHAR NOT NULL,
    status       VARCHAR NOT NULL,
    failed_rows  INTEGER,
    detail       VARCHAR,
    checked_at   TIMESTAMPTZ NOT NULL
);
