-- Supply Chain & Inventory Analytics: dimensional model (DuckDB dialect).
-- Every fact keeps its own grain. Facts share conformed dimensions
-- (date, product, supplier, location) and are never joined row-to-row.
-- Quantities are stored in the base unit of measure (EA); money in USD.

-- ---------- dimensions (rebuilt every run) ----------
CREATE OR REPLACE TABLE dim_date (
    date_key        DATE PRIMARY KEY,
    year            INTEGER,
    quarter         INTEGER,
    month           INTEGER,
    month_name      VARCHAR,
    iso_week        INTEGER,
    week_start      DATE,
    day_of_week     INTEGER,      -- 1 = Monday
    is_working_day  BOOLEAN,      -- Mon-Fri; holiday calendar not modelled in the demo
    fiscal_year     INTEGER,      -- fiscal year starts 1 April (assumption)
    fiscal_period   INTEGER
);

CREATE OR REPLACE TABLE dim_product (
    sku                 VARCHAR PRIMARY KEY,
    product_name        VARCHAR,
    category            VARCHAR,
    base_uom            VARCHAR,
    case_pack           INTEGER,
    unit_cost_usd       DOUBLE,
    lifecycle_status    VARCHAR,
    primary_supplier_id VARCHAR,
    moq_units           INTEGER,
    launch_date         DATE,
    abc_class           VARCHAR,     -- populated by the classification step
    xyz_class           VARCHAR,
    annual_consumption_value DOUBLE,
    demand_cv           DOUBLE
);

CREATE OR REPLACE TABLE dim_supplier (
    supplier_id             VARCHAR PRIMARY KEY,
    supplier_name           VARCHAR,
    region                  VARCHAR,
    country                 VARCHAR,
    currency                VARCHAR,
    payment_terms_days      INTEGER,
    approved_status         VARCHAR,
    category                VARCHAR,
    nominal_lead_time_days  INTEGER
);

CREATE OR REPLACE TABLE dim_location (
    location_id     VARCHAR PRIMARY KEY,
    location_name   VARCHAR,
    region          VARCHAR,
    location_type   VARCHAR,
    capacity_units  INTEGER
);

-- ---------- facts (rebuilt every run) ----------
-- Grain: one row per SKU, location and snapshot timestamp.
CREATE OR REPLACE TABLE fact_inventory_snapshot (
    snapshot_ts         TIMESTAMP,
    snapshot_date       DATE,
    sku                 VARCHAR,
    location_id         VARCHAR,
    on_hand_qty         DOUBLE,
    reserved_qty        DOUBLE,
    available_qty       DOUBLE,     -- on_hand - reserved (may be negative when on_hand is wrong)
    in_transit_qty      DOUBLE,
    is_negative_on_hand BOOLEAN,    -- traceable flag: value retained as reported, excluded from valuation
    PRIMARY KEY (sku, location_id, snapshot_ts)
);

-- Grain: one row per stock movement document line.
CREATE OR REPLACE TABLE fact_inventory_movement (
    movement_id     VARCHAR PRIMARY KEY,
    movement_ts     TIMESTAMP,
    movement_date   DATE,
    sku             VARCHAR,
    location_id     VARCHAR,
    movement_type   VARCHAR,        -- RECEIPT, ISSUE, TRANSFER_IN, TRANSFER_OUT, ADJUSTMENT
    quantity        DOUBLE,         -- signed, base UoM
    source_uom      VARCHAR,
    source_quantity DOUBLE,
    reference       VARCHAR
);

-- Grain: one row per SKU, location and demand date (complete daily grid incl. zero-demand days).
CREATE OR REPLACE TABLE fact_demand (
    demand_date     DATE,
    sku             VARCHAR,
    location_id     VARCHAR,
    requested_qty   DOUBLE,
    fulfilled_qty   DOUBLE,
    backordered_qty DOUBLE,
    PRIMARY KEY (sku, location_id, demand_date)
);

-- Grain: one row per purchase-order line.
CREATE OR REPLACE TABLE fact_purchase_order_line (
    po_number           VARCHAR,
    po_line             INTEGER,
    supplier_id         VARCHAR,
    sku                 VARCHAR,
    location_id         VARCHAR,
    order_date          DATE,
    approval_date       DATE,       -- release/confirmation date; lead-time clock starts here
    promised_date       DATE,
    ordered_qty         DOUBLE,     -- base UoM
    source_uom          VARCHAR,
    unit_cost_source    DOUBLE,     -- per source UoM, source currency
    currency            VARCHAR,
    unit_cost_usd       DOUBLE,     -- per EA, USD
    line_value_usd      DOUBLE,
    status              VARCHAR,
    PRIMARY KEY (po_number, po_line)
);

-- Grain: one row per goods-receipt line.
CREATE OR REPLACE TABLE fact_receipt_line (
    receipt_id          VARCHAR PRIMARY KEY,
    po_number           VARCHAR,
    po_line             INTEGER,
    receipt_ts          TIMESTAMP,
    receipt_date        DATE,
    received_qty        DOUBLE,
    accepted_qty        DOUBLE,
    rejected_qty        DOUBLE,
    source_uom          VARCHAR,
    inspection_outcome  VARCHAR
);

-- Grain: one row per SKU, location, forecast run and target week.
CREATE OR REPLACE TABLE fact_forecast (
    forecast_run_id VARCHAR,
    run_type        VARCHAR,        -- 'backtest' or 'production'
    origin_week     DATE,           -- last week of history available to the model
    target_week     DATE,
    horizon_weeks   INTEGER,
    sku             VARCHAR,
    location_id     VARCHAR,
    method          VARCHAR,
    predicted_qty   DOUBLE,
    lower_qty       DOUBLE,
    upper_qty       DOUBLE,
    model_version   VARCHAR,
    PRIMARY KEY (forecast_run_id, sku, location_id, target_week)
);

-- Build metadata: as-of date and policy parameters consumed by the SQL views.
CREATE OR REPLACE TABLE meta_build (
    run_id                  VARCHAR,
    data_as_of              DATE,
    built_at                TIMESTAMPTZ,
    demand_lookback_days    INTEGER,
    in_full_tolerance       DOUBLE,
    slow_moving_days        INTEGER,
    carrying_cost_rate      DOUBLE,
    snapshot_max_age_hours  DOUBLE,
    data_label              VARCHAR
);

-- FIFO stock ageing of current on-hand (computed in Python from inbound movements).
CREATE OR REPLACE TABLE mart_stock_ageing (
    sku VARCHAR, location_id VARCHAR, age_bucket VARCHAR, bucket_order INTEGER, qty DOUBLE, value_usd DOUBLE
);

-- Replenishment recommendations for the default policy (decision support only).
CREATE OR REPLACE TABLE mart_replenishment (
    sku VARCHAR, location_id VARCHAR, abc_class VARCHAR, service_level DOUBLE,
    avg_daily_demand DOUBLE, demand_std DOUBLE, lead_time_days DOUBLE, lead_time_std DOUBLE,
    on_hand_qty DOUBLE, available_qty DOUBLE, on_order_qty DOUBLE, inventory_position DOUBLE,
    safety_stock DOUBLE, reorder_point DOUBLE, target_stock DOUBLE, suggested_order_qty DOUBLE,
    days_of_cover DOUBLE, projected_stockout_date DATE, urgency VARCHAR,
    unit_cost_usd DOUBLE, stock_value_usd DOUBLE, excess_qty DOUBLE, excess_value_usd DOUBLE,
    expected_cover_after_order DOUBLE,
    PRIMARY KEY (sku, location_id)
);

-- Forecast method selection per series (from backtest).
CREATE OR REPLACE TABLE mart_forecast_accuracy (
    sku VARCHAR, location_id VARCHAR, method VARCHAR, wape DOUBLE, mae DOUBLE, bias DOUBLE,
    actual_qty DOUBLE, selected BOOLEAN, demand_pattern VARCHAR
);

-- Rows that failed referential checks are quarantined, not silently dropped.
CREATE OR REPLACE TABLE ops_rejected_rows (
    run_id VARCHAR, source_entity VARCHAR, reason VARCHAR, row_json VARCHAR
);

-- ---------- persistent operational tables (survive rebuilds) ----------
CREATE TABLE IF NOT EXISTS ops_pipeline_runs (
    run_id          VARCHAR PRIMARY KEY,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    status          VARCHAR,
    data_as_of      DATE,
    rows_loaded     BIGINT,
    pipeline_version VARCHAR,
    message         VARCHAR
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

-- Planner decisions on replenishment recommendations. Recording a decision never creates a PO.
CREATE TABLE IF NOT EXISTS planner_review (
    sku                 VARCHAR,
    location_id         VARCHAR,
    recommended_qty     DOUBLE,
    status              VARCHAR,    -- Pending, Approved, Rejected, Deferred
    note                VARCHAR,
    reviewed_by         VARCHAR,
    reviewed_at         TIMESTAMPTZ,
    PRIMARY KEY (sku, location_id)
);
