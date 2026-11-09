CREATE OR REPLACE VIEW jad_ucompare.analytics.vw_executive_product_kpis AS
WITH q AS (
 SELECT insurance_type,
        COUNT(DISTINCT quote_request_id) AS quote_requests,
        COUNT(DISTINCT CASE WHEN quote_submitted THEN quote_request_id END) AS submitted_requests
 FROM jad_ucompare.analytics.silver_quote_requests
 GROUP BY insurance_type
), p AS (
 SELECT insurance_type, COUNT(DISTINCT purchase_id) AS confirmed_purchases,
        SUM(annual_premium_eur) AS gross_premium_eur
 FROM jad_ucompare.analytics.silver_purchases
 WHERE purchase_status='Confirmed'
 GROUP BY insurance_type
)
SELECT q.insurance_type, q.quote_requests, q.submitted_requests,
       COALESCE(p.confirmed_purchases,0) AS confirmed_purchases,
       COALESCE(p.gross_premium_eur,0) AS gross_premium_eur
FROM q LEFT JOIN p ON q.insurance_type=p.insurance_type;

CREATE OR REPLACE VIEW jad_ucompare.analytics.vw_travel_priority_providers AS
SELECT provider_name,
       COUNT(DISTINCT provider_quote_id) AS provider_quote_records,
       COUNT(DISTINCT CASE WHEN quote_status='Returned' THEN provider_quote_id END) AS quotes_returned,
       AVG(CASE WHEN quote_status='Returned' THEN annual_premium_eur END) AS average_returned_premium_eur
FROM jad_ucompare.analytics.silver_provider_quotes
WHERE insurance_type='Travel'
  AND provider_name IN ('ATI','Multitrip','Switched On')
GROUP BY provider_name;