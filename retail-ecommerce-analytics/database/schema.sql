-- Retail Commercial Intelligence: star schema (DuckDB dialect, portable to Databricks SQL).
-- All monetary columns are in the reporting currency (GBP) unless suffixed _local.
-- Grain is stated for every table; facts at different grains are never joined directly.

-- ============ dimensions ============
CREATE TABLE IF NOT EXISTS dim_date (            -- one row per calendar day
    date_key        DATE PRIMARY KEY,
    year            INTEGER,
    quarter         INTEGER,
    month           INTEGER,
    month_name      VARCHAR,
    month_start     DATE,
    week_start      DATE,                        -- ISO week, Monday start
    iso_week        INTEGER,
    day_of_week     INTEGER,                     -- 1 = Monday
    is_weekend      BOOLEAN,
    fiscal_year     INTEGER,                     -- UK fiscal year starting 1 April (FY named by end year)
    fiscal_quarter  INTEGER
);

CREATE TABLE IF NOT EXISTS dim_customer (        -- one row per pseudonymous customer
    customer_id          VARCHAR PRIMARY KEY,
    signup_date          DATE,
    first_order_date     DATE,                   -- first eligible (non-cancelled) order
    country              VARCHAR,
    region               VARCHAR,
    acquisition_channel  VARCHAR,
    cohort_month         DATE
);

CREATE TABLE IF NOT EXISTS dim_product (         -- one row per product / SKU
    product_id       VARCHAR PRIMARY KEY,
    sku              VARCHAR,
    product_name     VARCHAR,
    category         VARCHAR,
    brand            VARCHAR,
    list_price_gbp   DOUBLE,
    unit_cost_gbp    DOUBLE,                     -- NULL when no reliable cost exists
    has_reliable_cost BOOLEAN,
    lifecycle_status VARCHAR,
    launch_date      DATE
);

CREATE TABLE IF NOT EXISTS dim_campaign (        -- one row per marketing campaign
    campaign_id   VARCHAR PRIMARY KEY,
    campaign_name VARCHAR,
    channel       VARCHAR,
    objective     VARCHAR,                       -- acquisition | retention
    start_date    DATE,
    end_date      DATE
);

-- ============ facts ============
CREATE TABLE IF NOT EXISTS fact_orders (         -- one row per order
    order_id                VARCHAR PRIMARY KEY,
    customer_id             VARCHAR,
    order_ts                TIMESTAMPTZ,
    order_date              DATE,
    channel                 VARCHAR,
    status                  VARCHAR,             -- completed | cancelled
    is_cancelled            BOOLEAN,
    country                 VARCHAR,
    region                  VARCHAR,
    currency_local          VARCHAR,
    fx_rate_to_gbp          DOUBLE,
    shipping_gbp            DOUBLE,
    tax_gbp                 DOUBLE,
    header_total_gbp        DOUBLE,              -- total reported by the source header (items + shipping + tax)
    items_gross_gbp         DOUBLE,
    items_discount_gbp      DOUBLE,
    items_net_gbp           DOUBLE,              -- after discounts, before refunds
    units                   INTEGER,
    reconciles_to_items     BOOLEAN,             -- header total = loaded lines + shipping + tax
    customer_order_seq      INTEGER,             -- 1 = first eligible order of the customer
    is_new_customer_order   BOOLEAN,
    last_touch_campaign_id  VARCHAR,
    last_touch_ts           TIMESTAMPTZ,
    shipped_ts              TIMESTAMPTZ,
    delivered_ts            TIMESTAMPTZ,
    promised_delivery_date  DATE
);

CREATE TABLE IF NOT EXISTS fact_order_items (    -- one row per order line
    order_item_id     VARCHAR PRIMARY KEY,
    order_id          VARCHAR,
    product_id        VARCHAR,
    order_date        DATE,                      -- degenerate copy of the order date for partitioning
    quantity          INTEGER,
    unit_price_gbp    DOUBLE,
    gross_sales_gbp   DOUBLE,                    -- quantity x unit price
    discount_gbp      DOUBLE,
    net_sales_gbp     DOUBLE,                    -- gross - discount (before refunds)
    is_discounted     BOOLEAN,
    unit_cost_gbp     DOUBLE,                    -- NULL when product cost is unreliable
    returned_quantity INTEGER,                   -- sum of fact_returns.quantity for this line
    refund_gbp        DOUBLE                     -- sum of fact_returns.refund_gbp for this line
);

CREATE TABLE IF NOT EXISTS fact_returns (        -- one row per return / refund event
    return_id      VARCHAR PRIMARY KEY,
    order_item_id  VARCHAR,
    order_id       VARCHAR,
    product_id     VARCHAR,
    return_date    DATE,
    quantity       INTEGER,
    reason         VARCHAR,
    refund_gbp     DOUBLE
);

CREATE TABLE IF NOT EXISTS fact_marketing_spend ( -- one row per campaign per day
    spend_date     DATE,
    campaign_id    VARCHAR,
    channel        VARCHAR,
    spend_gbp      DOUBLE,
    currency_local VARCHAR,
    impressions    BIGINT,
    clicks         BIGINT,
    PRIMARY KEY (spend_date, campaign_id)
);

-- Attribution bridge: one row per attributed order (last-click, window from config).
-- Spend is NEVER joined to order lines; both sides are aggregated to campaign x day first.
CREATE TABLE IF NOT EXISTS bridge_order_attribution (
    order_id             VARCHAR PRIMARY KEY,
    campaign_id          VARCHAR,
    channel              VARCHAR,
    attribution_model    VARCHAR,
    window_days          INTEGER,
    touch_ts             TIMESTAMPTZ,
    order_date           DATE,
    days_touch_to_order  DOUBLE,
    weight               DOUBLE,
    is_new_customer      BOOLEAN
);

CREATE TABLE IF NOT EXISTS fact_inventory (      -- one row per product, location, snapshot date
    snapshot_date DATE,
    product_id    VARCHAR,
    location_id   VARCHAR,
    on_hand       INTEGER,
    reserved      INTEGER,
    available     INTEGER,
    PRIMARY KEY (snapshot_date, product_id, location_id)
);

-- ============ operations / data quality ============
CREATE TABLE IF NOT EXISTS ops_pipeline_runs (
    run_id       VARCHAR PRIMARY KEY,
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    status       VARCHAR,
    data_as_of   DATE,
    source       VARCHAR,
    rows_loaded  BIGINT,
    message      VARCHAR
);

CREATE TABLE IF NOT EXISTS ops_source_files (
    run_id        VARCHAR,
    entity        VARCHAR,
    rows_read     BIGINT,
    rows_loaded   BIGINT,
    rows_rejected BIGINT,
    duplicates_removed BIGINT,
    max_record_ts TIMESTAMPTZ,
    loaded_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ops_dq_results (
    run_id      VARCHAR,
    check_name  VARCHAR,
    table_name  VARCHAR,
    status      VARCHAR,
    failed_rows BIGINT,
    detail      VARCHAR,
    checked_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ops_load_failures (
    run_id      VARCHAR,
    entity      VARCHAR,
    record_key  VARCHAR,
    reason      VARCHAR,
    raw_record  VARCHAR,
    logged_at   TIMESTAMPTZ
);
