-- Analytical / semantic views (DuckDB dialect, portable to Databricks SQL with minor changes).
-- Rule: every fact is aggregated to a shared grain (sku x location, supplier, date, ...) BEFORE
-- it is combined with another fact, so quantities are never duplicated by many-to-many joins.

-- Latest snapshot per SKU/location, with age relative to the dataset as-of date.
CREATE OR REPLACE VIEW v_latest_snapshot AS
SELECT s.*,
       m.data_as_of - s.snapshot_date AS snapshot_age_days
FROM fact_inventory_snapshot s, meta_build m
QUALIFY row_number() OVER (PARTITION BY s.sku, s.location_id ORDER BY s.snapshot_ts DESC) = 1;

-- Snapshot freshness per location.
CREATE OR REPLACE VIEW v_location_freshness AS
SELECT l.location_id, l.location_name,
       max(s.snapshot_ts) AS latest_snapshot_ts,
       date_diff('hour', max(s.snapshot_ts), CAST(m.data_as_of AS TIMESTAMP) + INTERVAL 1 DAY) AS age_hours,
       date_diff('hour', max(s.snapshot_ts), CAST(m.data_as_of AS TIMESTAMP) + INTERVAL 1 DAY)
           > m.snapshot_max_age_hours AS is_stale
FROM dim_location l
LEFT JOIN fact_inventory_snapshot s USING (location_id)
CROSS JOIN meta_build m
GROUP BY l.location_id, l.location_name, m.data_as_of, m.snapshot_max_age_hours;

-- Demand statistics over the look-back window (only days on/after product launch).
CREATE OR REPLACE VIEW v_demand_stats AS
SELECT d.sku, d.location_id,
       count(*)                                         AS days_observed,
       avg(d.requested_qty)                             AS avg_daily_demand,
       coalesce(stddev_samp(d.requested_qty), 0)        AS demand_std,
       sum(d.requested_qty)                             AS requested_qty,
       sum(d.fulfilled_qty)                             AS fulfilled_qty,
       sum(d.backordered_qty)                           AS backordered_qty,
       count(*) FILTER (WHERE d.requested_qty > 0)      AS demand_days,
       count(*) FILTER (WHERE d.backordered_qty > 0)    AS stockout_days
FROM fact_demand d
JOIN dim_product p USING (sku)
CROSS JOIN meta_build m
WHERE d.demand_date > m.data_as_of - m.demand_lookback_days
  AND d.demand_date <= m.data_as_of
  AND d.demand_date >= coalesce(p.launch_date, DATE '1900-01-01')
GROUP BY d.sku, d.location_id;

-- Purchase-order line lifecycle and delivery performance.
-- OTD  : first receipt on or before the promised date.
-- OTIF : accepted quantity received on or before the promised date >= ordered qty x (1 - tolerance).
-- Lead time: approval (release) date -> final receipt date, closed lines only.
CREATE OR REPLACE VIEW v_po_line_status AS
WITH r AS (
    SELECT po_number, po_line,
           min(receipt_date) AS first_receipt_date,
           max(receipt_date) AS last_receipt_date,
           count(*)          AS receipt_count,
           sum(received_qty) AS received_qty,
           sum(accepted_qty) AS accepted_qty,
           sum(rejected_qty) AS rejected_qty
    FROM fact_receipt_line GROUP BY po_number, po_line
), by_promise AS (
    SELECT r.po_number, r.po_line, sum(r.accepted_qty) AS accepted_by_promised
    FROM fact_receipt_line r
    JOIN fact_purchase_order_line l USING (po_number, po_line)
    WHERE r.receipt_date <= l.promised_date
    GROUP BY r.po_number, r.po_line
)
SELECT l.*,
       r.first_receipt_date, r.last_receipt_date,
       coalesce(r.receipt_count, 0)            AS receipt_count,
       coalesce(r.received_qty, 0)             AS received_qty,
       coalesce(r.accepted_qty, 0)             AS accepted_qty,
       coalesce(r.rejected_qty, 0)             AS rejected_qty,
       coalesce(b.accepted_by_promised, 0)     AS accepted_by_promised,
       CASE WHEN l.status IN ('Open', 'Partially Received')
            THEN greatest(l.ordered_qty - coalesce(r.received_qty, 0), 0) ELSE 0 END AS open_qty,
       (l.status <> 'Cancelled' AND l.promised_date <= m.data_as_of)            AS is_due,
       (l.status IN ('Open', 'Partially Received') AND l.promised_date < m.data_as_of) AS is_overdue,
       coalesce(l.status <> 'Cancelled' AND l.promised_date <= m.data_as_of
                AND r.first_receipt_date <= l.promised_date, FALSE)               AS on_time,
       coalesce(l.status <> 'Cancelled' AND l.promised_date <= m.data_as_of
                AND coalesce(b.accepted_by_promised, 0) >= l.ordered_qty * (1 - m.in_full_tolerance), FALSE) AS otif,
       coalesce(l.status <> 'Cancelled' AND l.promised_date <= m.data_as_of
                AND coalesce(r.accepted_qty, 0) >= l.ordered_qty * (1 - m.in_full_tolerance), FALSE) AS in_full,
       CASE WHEN l.status = 'Closed' THEN r.last_receipt_date - l.approval_date END AS lead_time_days,
       r.first_receipt_date - l.approval_date                                      AS first_receipt_lead_time_days,
       CASE WHEN r.first_receipt_date IS NOT NULL THEN r.first_receipt_date - l.promised_date
            WHEN l.status IN ('Open', 'Partially Received') AND l.promised_date < m.data_as_of
                 THEN m.data_as_of - l.promised_date END                          AS days_late,
       m.data_as_of - l.order_date                                                 AS order_age_days,
       CASE
           WHEN l.status = 'Cancelled' THEN 'Cancelled'
           WHEN l.status = 'Closed' AND r.first_receipt_date <= l.promised_date THEN 'Received on time'
           WHEN l.status = 'Closed' THEN 'Received late'
           WHEN l.status = 'Partially Received' AND l.promised_date < m.data_as_of THEN 'Partial - overdue'
           WHEN l.status = 'Partially Received' THEN 'Partial - not yet due'
           WHEN l.promised_date < m.data_as_of THEN 'Open - overdue'
           ELSE 'Open - not yet due'
       END AS delivery_status
FROM fact_purchase_order_line l
LEFT JOIN r USING (po_number, po_line)
LEFT JOIN by_promise b USING (po_number, po_line)
CROSS JOIN meta_build m;

-- Open purchase-order quantity per SKU/location (aggregated before joining to inventory).
CREATE OR REPLACE VIEW v_open_po_by_sku_location AS
SELECT sku, location_id, sum(open_qty) AS on_order_qty, count(*) FILTER (WHERE open_qty > 0) AS open_lines,
       min(promised_date) FILTER (WHERE open_qty > 0) AS next_promised_date
FROM v_po_line_status
GROUP BY sku, location_id;

-- Supplier scorecard (due lines only for delivery KPIs; all non-cancelled lines for spend).
CREATE OR REPLACE VIEW v_supplier_scorecard AS
WITH lines AS (
    SELECT p.*, d.unit_cost_usd AS std_cost_usd
    FROM v_po_line_status p JOIN dim_product d USING (sku)
)
SELECT s.supplier_id, s.supplier_name, s.region, s.country, s.currency, s.approved_status, s.category,
       s.nominal_lead_time_days,
       count(*) FILTER (WHERE l.status <> 'Cancelled')                  AS po_lines,
       count(*) FILTER (WHERE l.is_due)                                 AS due_lines,
       avg(CASE WHEN l.on_time THEN 1.0 ELSE 0.0 END) FILTER (WHERE l.is_due)  AS otd_rate,
       avg(CASE WHEN l.in_full THEN 1.0 ELSE 0.0 END) FILTER (WHERE l.is_due)  AS in_full_rate,
       avg(CASE WHEN l.otif THEN 1.0 ELSE 0.0 END) FILTER (WHERE l.is_due)     AS otif_rate,
       sum(l.accepted_qty) FILTER (WHERE l.is_due) / nullif(sum(l.ordered_qty) FILTER (WHERE l.is_due), 0) AS fill_rate,
       sum(l.rejected_qty) / nullif(sum(l.received_qty), 0)            AS defect_rate,
       avg(l.lead_time_days)                                            AS lead_time_mean,
       stddev_samp(l.lead_time_days)                                    AS lead_time_std,
       quantile_cont(l.lead_time_days, 0.9)                             AS lead_time_p90,
       count(l.lead_time_days)                                          AS lead_time_obs,
       sum(l.line_value_usd) FILTER (WHERE l.status <> 'Cancelled')    AS spend_usd,
       sum((l.unit_cost_usd - l.std_cost_usd) * l.ordered_qty) FILTER (WHERE l.status <> 'Cancelled')
           / nullif(sum(l.std_cost_usd * l.ordered_qty) FILTER (WHERE l.status <> 'Cancelled'), 0) AS price_variance_pct,
       count(*) FILTER (WHERE l.is_overdue)                             AS overdue_lines,
       sum(l.open_qty * l.unit_cost_usd)                                AS open_value_usd
FROM dim_supplier s
LEFT JOIN lines l USING (supplier_id)
GROUP BY ALL;

-- Current inventory position per SKU/location: latest snapshot + demand stats + open POs.
CREATE OR REPLACE VIEW v_inventory_position AS
SELECT s.sku, s.location_id, p.product_name, p.category, p.lifecycle_status, p.abc_class, p.xyz_class,
       p.primary_supplier_id AS supplier_id, p.unit_cost_usd,
       s.snapshot_ts, s.snapshot_age_days, s.is_negative_on_hand,
       s.on_hand_qty, s.reserved_qty, s.available_qty, s.in_transit_qty,
       coalesce(o.on_order_qty, 0)                         AS on_order_qty,
       greatest(s.on_hand_qty, 0) * p.unit_cost_usd        AS stock_value_usd,
       coalesce(d.avg_daily_demand, 0)                     AS avg_daily_demand,
       coalesce(d.demand_std, 0)                           AS demand_std,
       CASE WHEN coalesce(d.avg_daily_demand, 0) > 0
            THEN greatest(s.available_qty, 0) / d.avg_daily_demand END AS days_of_cover,
       coalesce(d.requested_qty, 0) AS requested_qty_lookback,
       coalesce(d.fulfilled_qty, 0) AS fulfilled_qty_lookback,
       coalesce(d.backordered_qty, 0) AS backordered_qty_lookback,
       coalesce(d.stockout_days, 0) AS stockout_days_lookback,
       coalesce(d.demand_days, 0)   AS demand_days_lookback,
       lo.last_outbound_date,
       m.data_as_of - lo.last_outbound_date AS days_since_last_outbound
FROM v_latest_snapshot s
JOIN dim_product p USING (sku)
LEFT JOIN v_open_po_by_sku_location o USING (sku, location_id)
LEFT JOIN v_demand_stats d USING (sku, location_id)
LEFT JOIN (SELECT sku, location_id, max(movement_date) AS last_outbound_date
           FROM fact_inventory_movement WHERE movement_type IN ('ISSUE', 'TRANSFER_OUT')
           GROUP BY sku, location_id) lo USING (sku, location_id)
CROSS JOIN meta_build m;

-- Daily inventory value by location and category (negative on-hand excluded from valuation).
CREATE OR REPLACE VIEW v_inventory_value_daily AS
SELECT s.snapshot_date, s.location_id, p.category,
       sum(greatest(s.on_hand_qty, 0))                   AS on_hand_qty,
       sum(greatest(s.on_hand_qty, 0) * p.unit_cost_usd) AS stock_value_usd
FROM fact_inventory_snapshot s JOIN dim_product p USING (sku)
GROUP BY ALL;

-- Daily demand service by location and category.
CREATE OR REPLACE VIEW v_service_daily AS
SELECT d.demand_date, d.location_id, p.category,
       sum(d.requested_qty)   AS requested_qty,
       sum(d.fulfilled_qty)   AS fulfilled_qty,
       sum(d.backordered_qty) AS backordered_qty,
       sum(d.fulfilled_qty * p.unit_cost_usd)   AS cogs_usd,
       sum(d.backordered_qty * p.unit_cost_usd) AS unfilled_value_usd,
       count(*) FILTER (WHERE d.requested_qty > 0)   AS eligible_sku_days,
       count(*) FILTER (WHERE d.backordered_qty > 0) AS stockout_sku_days
FROM fact_demand d JOIN dim_product p USING (sku)
GROUP BY ALL;

-- Weekly demand per SKU/location (week starts Monday).
CREATE OR REPLACE VIEW v_demand_weekly AS
SELECT date_trunc('week', demand_date)::DATE AS week_start, sku, location_id,
       sum(requested_qty) AS requested_qty, sum(fulfilled_qty) AS fulfilled_qty, count(*) AS days_in_week
FROM fact_demand GROUP BY ALL;

-- Backtest forecasts versus actuals (actuals aggregated to week before joining).
CREATE OR REPLACE VIEW v_forecast_vs_actual AS
SELECT f.*, w.requested_qty AS actual_qty,
       f.predicted_qty - w.requested_qty AS error_qty
FROM fact_forecast f
LEFT JOIN v_demand_weekly w ON w.sku = f.sku AND w.location_id = f.location_id AND w.week_start = f.target_week;

-- Movements per day, location and type.
CREATE OR REPLACE VIEW v_movement_daily AS
SELECT movement_date, location_id, movement_type, count(*) AS movement_lines,
       sum(quantity) AS net_qty, sum(abs(quantity)) AS gross_qty
FROM fact_inventory_movement GROUP BY ALL;

-- Snapshot-to-movement reconciliation: reported on-hand vs previous snapshot + net movements.
CREATE OR REPLACE VIEW v_snapshot_reconciliation AS
WITH mv AS (
    SELECT sku, location_id, movement_date, sum(quantity) AS net_qty
    FROM fact_inventory_movement GROUP BY ALL
), mv_cum AS (
    SELECT *, sum(net_qty) OVER (PARTITION BY sku, location_id ORDER BY movement_date) AS cum_qty
    FROM mv
), s AS (
    SELECT sku, location_id, snapshot_date, on_hand_qty,
           lag(on_hand_qty)   OVER w AS prev_on_hand,
           lag(snapshot_date) OVER w AS prev_snapshot_date
    FROM fact_inventory_snapshot
    WINDOW w AS (PARTITION BY sku, location_id ORDER BY snapshot_ts)
), cum_at AS (
    -- cumulative movements up to each snapshot date (ASOF join to the latest movement day)
    SELECT s.*, coalesce(c.cum_qty, 0) AS cum_at_snapshot
    FROM s ASOF LEFT JOIN mv_cum c
      ON s.sku = c.sku AND s.location_id = c.location_id AND s.snapshot_date >= c.movement_date
)
SELECT sku, location_id, snapshot_date, prev_snapshot_date, prev_on_hand, on_hand_qty,
       cum_at_snapshot - lag(cum_at_snapshot) OVER w2 AS net_movement_qty,
       prev_on_hand + (cum_at_snapshot - lag(cum_at_snapshot) OVER w2) AS expected_on_hand,
       on_hand_qty - (prev_on_hand + (cum_at_snapshot - lag(cum_at_snapshot) OVER w2)) AS variance_qty
FROM cum_at
WINDOW w2 AS (PARTITION BY sku, location_id ORDER BY snapshot_date)
QUALIFY prev_on_hand IS NOT NULL;
