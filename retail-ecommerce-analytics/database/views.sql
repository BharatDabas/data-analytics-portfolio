-- Analytical / semantic views shared by the Streamlit dashboard and the Power BI model.
-- KPI rules (see config/business_rules.toml and docs/kpi_catalogue.md):
--   * cancelled orders are excluded from every sales measure
--   * tax and shipping are excluded from revenue
--   * refunds are attributed to the original order date
--   * COGS / gross profit only for lines whose product has a reliable unit cost;
--     returned units are assumed restocked (their cost is reversed)

-- Order line, enriched with order and product attributes (grain: order line).
CREATE OR REPLACE VIEW v_order_lines AS
SELECT
    li.order_item_id, li.order_id, o.customer_id, li.product_id,
    o.order_ts, o.order_date, o.channel, o.status, o.is_cancelled, o.country, o.region,
    o.customer_order_seq, o.is_new_customer_order,
    p.sku, p.product_name, p.category, p.brand, p.lifecycle_status, p.has_reliable_cost,
    li.quantity, li.unit_price_gbp, li.gross_sales_gbp, li.discount_gbp, li.net_sales_gbp, li.is_discounted,
    li.returned_quantity, li.refund_gbp,
    li.net_sales_gbp - li.refund_gbp                                                    AS net_revenue_gbp,
    CASE WHEN p.has_reliable_cost THEN (li.quantity - li.returned_quantity) * li.unit_cost_gbp END AS cogs_gbp,
    CASE WHEN p.has_reliable_cost THEN li.net_sales_gbp - li.refund_gbp END                       AS net_revenue_costed_gbp,
    CASE WHEN p.has_reliable_cost
         THEN (li.net_sales_gbp - li.refund_gbp) - (li.quantity - li.returned_quantity) * li.unit_cost_gbp END
                                                                                         AS gross_profit_gbp
FROM fact_order_items li
JOIN fact_orders o  ON o.order_id = li.order_id
JOIN dim_product p  ON p.product_id = li.product_id;

-- Order-level revenue (grain: order). Basis for AOV, customer value and attribution.
CREATE OR REPLACE VIEW v_order_value AS
SELECT
    o.order_id, o.customer_id, o.order_date, o.order_ts, o.channel, o.country, o.region, o.status, o.is_cancelled,
    o.customer_order_seq, o.is_new_customer_order, o.shipping_gbp, o.tax_gbp,
    COALESCE(SUM(l.gross_sales_gbp), 0)  AS gross_sales_gbp,
    COALESCE(SUM(l.discount_gbp), 0)     AS discount_gbp,
    COALESCE(SUM(l.refund_gbp), 0)       AS refund_gbp,
    COALESCE(SUM(l.net_revenue_gbp), 0)  AS net_revenue_gbp,
    COALESCE(SUM(l.gross_profit_gbp), 0) AS gross_profit_gbp,
    COALESCE(SUM(l.quantity), 0)         AS units,
    COALESCE(SUM(l.returned_quantity), 0) AS returned_units
FROM fact_orders o
LEFT JOIN v_order_lines l ON l.order_id = o.order_id
GROUP BY ALL;

-- Daily sales by channel / geography / category (grain: date x channel x country x category).
CREATE OR REPLACE VIEW v_sales_daily AS
SELECT
    order_date, channel, country, region, category,
    SUM(gross_sales_gbp)        AS gross_sales_gbp,
    SUM(discount_gbp)           AS discount_gbp,
    SUM(refund_gbp)             AS refund_gbp,
    SUM(net_revenue_gbp)        AS net_revenue_gbp,
    SUM(cogs_gbp)               AS cogs_gbp,
    SUM(net_revenue_costed_gbp) AS net_revenue_costed_gbp,
    SUM(gross_profit_gbp)       AS gross_profit_gbp,
    SUM(quantity)               AS units,
    SUM(returned_quantity)      AS returned_units
FROM v_order_lines
WHERE NOT is_cancelled
GROUP BY ALL;

-- Customer value to date (grain: customer). OBSERVED value only - no modelling here.
CREATE OR REPLACE VIEW v_customer_value AS
SELECT
    c.customer_id, c.country, c.region, c.acquisition_channel, c.first_order_date, c.cohort_month,
    MAX(v.order_date)                           AS last_order_date,
    COUNT(v.order_id)                           AS orders,
    COALESCE(SUM(v.net_revenue_gbp), 0)         AS net_revenue_gbp,
    COALESCE(SUM(v.gross_profit_gbp), 0)        AS gross_profit_gbp
FROM dim_customer c
LEFT JOIN v_order_value v ON v.customer_id = c.customer_id AND NOT v.is_cancelled
GROUP BY ALL;

-- Marketing performance (grain: campaign x date).
-- Spend and attributed outcomes are aggregated to the SAME grain before joining,
-- so neither spend nor revenue can be duplicated.
CREATE OR REPLACE VIEW v_marketing_daily AS
WITH spend AS (
    SELECT spend_date AS activity_date, campaign_id,
           SUM(spend_gbp) AS spend_gbp, SUM(impressions) AS impressions, SUM(clicks) AS clicks
    FROM fact_marketing_spend GROUP BY ALL
), outcome AS (
    SELECT b.order_date AS activity_date, b.campaign_id,
           SUM(b.weight)                                         AS attributed_orders,
           SUM(CASE WHEN b.is_new_customer THEN b.weight END)    AS attributed_new_customers,
           SUM(b.weight * v.net_revenue_gbp)                     AS attributed_net_revenue_gbp
    FROM bridge_order_attribution b
    JOIN v_order_value v ON v.order_id = b.order_id
    GROUP BY ALL
)
SELECT
    COALESCE(s.activity_date, a.activity_date) AS activity_date,
    COALESCE(s.campaign_id, a.campaign_id)     AS campaign_id,
    c.campaign_name, c.channel, c.objective,
    COALESCE(s.spend_gbp, 0)                   AS spend_gbp,
    COALESCE(s.impressions, 0)                 AS impressions,
    COALESCE(s.clicks, 0)                      AS clicks,
    COALESCE(a.attributed_orders, 0)           AS attributed_orders,
    COALESCE(a.attributed_new_customers, 0)    AS attributed_new_customers,
    COALESCE(a.attributed_net_revenue_gbp, 0)  AS attributed_net_revenue_gbp
FROM spend s
FULL OUTER JOIN outcome a ON a.activity_date = s.activity_date AND a.campaign_id = s.campaign_id
JOIN dim_campaign c ON c.campaign_id = COALESCE(s.campaign_id, a.campaign_id);

-- Returns enriched (grain: return event).
CREATE OR REPLACE VIEW v_returns AS
SELECT r.return_id, r.order_item_id, r.order_id, r.product_id, r.return_date, r.quantity, r.reason, r.refund_gbp,
       o.order_date, o.channel, o.country, o.region, p.category, p.brand, p.product_name,
       date_diff('day', CAST(o.delivered_ts AS DATE), r.return_date) AS days_delivery_to_return
FROM fact_returns r
JOIN fact_orders o ON o.order_id = r.order_id
JOIN dim_product p ON p.product_id = r.product_id;

-- Fulfilment indicators (grain: completed order).
CREATE OR REPLACE VIEW v_fulfilment AS
SELECT order_id, order_date, channel, country, region,
       date_diff('hour', order_ts, shipped_ts) / 24.0                   AS days_to_ship,
       date_diff('hour', shipped_ts, delivered_ts) / 24.0               AS days_in_transit,
       delivered_ts IS NOT NULL                                         AS is_delivered,
       CAST(delivered_ts AS DATE) <= promised_delivery_date             AS delivered_on_time
FROM fact_orders WHERE NOT is_cancelled;

-- Orders whose source header total does not match loaded lines + shipping + tax.
CREATE OR REPLACE VIEW v_order_reconciliation AS
SELECT order_id, order_date, header_total_gbp,
       items_net_gbp + shipping_gbp + tax_gbp                         AS rebuilt_total_gbp,
       header_total_gbp - (items_net_gbp + shipping_gbp + tax_gbp)    AS difference_gbp
FROM fact_orders WHERE NOT reconciles_to_items;

-- Headline KPI totals (grain: one row). Used for reconciliation tests and Power BI validation.
CREATE OR REPLACE VIEW v_kpi_summary AS
SELECT
    SUM(gross_sales_gbp)                                         AS gross_sales_gbp,
    SUM(discount_gbp)                                            AS discount_gbp,
    SUM(refund_gbp)                                              AS refund_gbp,
    SUM(net_revenue_gbp)                                         AS net_revenue_gbp,
    COUNT(*)                                                     AS orders,
    SUM(units)                                                   AS units,
    SUM(net_revenue_gbp) / NULLIF(COUNT(*), 0)                   AS aov_gbp
FROM v_order_value WHERE NOT is_cancelled;

-- Latest data-quality results.
CREATE OR REPLACE VIEW v_dq_latest AS
SELECT d.* FROM ops_dq_results d
WHERE d.run_id = (SELECT run_id FROM ops_pipeline_runs WHERE status LIKE 'succeeded%' ORDER BY finished_at DESC LIMIT 1);
