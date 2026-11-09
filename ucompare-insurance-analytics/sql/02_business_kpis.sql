-- Executive KPI queries. Respect grain: quote requests, provider responses,
-- purchases and commission ledger entries are different entities.

-- 1. Product funnel
WITH q AS (
 SELECT insurance_type, COUNT(DISTINCT quote_request_id) quote_requests,
        COUNT(DISTINCT CASE WHEN quote_submitted THEN quote_request_id END) submitted_requests
 FROM jad_ucompare.analytics.fact_quote_request GROUP BY insurance_type
), p AS (
 SELECT insurance_type, COUNT(DISTINCT purchase_id) confirmed_purchases,
        COUNT(DISTINCT quote_request_id) confirmed_quote_requests,
        SUM(annual_premium_eur) gross_premium_eur
 FROM jad_ucompare.analytics.fact_purchase
 WHERE purchase_status='Confirmed' GROUP BY insurance_type
)
SELECT q.insurance_type, quote_requests, submitted_requests,
       COALESCE(confirmed_purchases,0) confirmed_purchases,
       COALESCE(confirmed_quote_requests,0) confirmed_quote_requests,
       COALESCE(gross_premium_eur,0) gross_premium_eur,
       submitted_requests / NULLIF(quote_requests,0) quote_submission_rate,
       COALESCE(confirmed_quote_requests,0) / NULLIF(submitted_requests,0) submitted_to_purchase_rate
FROM q LEFT JOIN p USING (insurance_type);

-- 2. Provider scorecard: quote availability is not sales.
SELECT insurance_type, provider_name, provider_priority,
       COUNT(*) provider_quote_records,
       SUM(CASE WHEN quote_status='Returned' THEN 1 ELSE 0 END) quotes_returned,
       AVG(CASE WHEN quote_status='Returned' THEN annual_premium_eur END) avg_quoted_premium
FROM jad_ucompare.analytics.fact_provider_quote
GROUP BY insurance_type, provider_name, provider_priority;

-- 3. Travel priority-provider comparison
SELECT provider_name,
       COUNT(*) provider_quote_records,
       SUM(CASE WHEN quote_status='Returned' THEN 1 ELSE 0 END) quotes_returned,
       AVG(CASE WHEN quote_status='Returned' THEN annual_premium_eur END) avg_annual_premium_eur
FROM jad_ucompare.analytics.fact_provider_quote
WHERE insurance_type='Travel'
  AND provider_name IN ('ATI','Multitrip','Switched On')
GROUP BY provider_name;

-- 4. Earned commission by month and product
SELECT DATE_TRUNC('MONTH', c.commission_date) commission_month,
       p.insurance_type, SUM(c.commission_amount_eur) earned_commission_eur
FROM jad_ucompare.analytics.fact_commission c
JOIN jad_ucompare.analytics.fact_purchase p ON c.purchase_id=p.purchase_id
WHERE c.commission_status='Earned'
GROUP BY DATE_TRUNC('MONTH', c.commission_date), p.insurance_type;

-- 5. Marketing spend by channel (CAC requires matching attributable conversions)
SELECT month_start, channel, SUM(spend_eur) marketing_spend_eur
FROM jad_ucompare.analytics.fact_marketing_spend
GROUP BY month_start, channel;
