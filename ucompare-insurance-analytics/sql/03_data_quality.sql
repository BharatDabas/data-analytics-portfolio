-- Duplicate key checks
SELECT quote_request_id, COUNT(*) c
FROM jad_ucompare.analytics.fact_quote_request
GROUP BY quote_request_id HAVING COUNT(*) > 1;

SELECT purchase_id, COUNT(*) c
FROM jad_ucompare.analytics.fact_purchase
GROUP BY purchase_id HAVING COUNT(*) > 1;

-- Orphan quote requests in provider quote table
SELECT COUNT(*) orphan_provider_quotes
FROM jad_ucompare.analytics.fact_provider_quote p
LEFT ANTI JOIN jad_ucompare.analytics.fact_quote_request q
ON p.quote_request_id=q.quote_request_id;

-- Premium validation
SELECT COUNT(*) invalid_premium_rows
FROM jad_ucompare.analytics.fact_purchase
WHERE annual_premium_eur < 0 OR annual_premium_eur IS NULL;
