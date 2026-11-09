-- Databricks SQL / Delta Lake starter schema.
-- Set catalog and schema to your organisation-approved locations.
CREATE SCHEMA IF NOT EXISTS jad_ucompare.analytics;

CREATE TABLE IF NOT EXISTS jad_ucompare.analytics.dim_customer (
  customer_id STRING, created_date DATE, age_band STRING, county STRING,
  acquisition_channel STRING, consent_status STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS jad_ucompare.analytics.fact_quote_request (
  quote_request_id STRING, customer_id STRING, quote_date TIMESTAMP,
  insurance_type STRING, quote_submitted BOOLEAN, session_source STRING,
  cover_amount_eur DECIMAL(12,2), trip_type STRING, destination_region STRING,
  bike_count INT, bike_value_eur DECIMAL(12,2)
) USING DELTA;

CREATE TABLE IF NOT EXISTS jad_ucompare.analytics.fact_provider_quote (
  provider_quote_id STRING, quote_request_id STRING, provider_name STRING,
  insurance_type STRING, provider_priority BOOLEAN, quote_status STRING,
  annual_premium_eur DECIMAL(12,2), quote_timestamp TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS jad_ucompare.analytics.fact_purchase (
  purchase_id STRING, quote_request_id STRING, provider_quote_id STRING,
  customer_id STRING, provider_name STRING, insurance_type STRING,
  purchase_date DATE, annual_premium_eur DECIMAL(12,2), purchase_status STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS jad_ucompare.analytics.fact_commission (
  commission_id STRING, purchase_id STRING, commission_date DATE,
  commission_amount_eur DECIMAL(12,2), commission_status STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS jad_ucompare.analytics.fact_website_event (
  event_id STRING, customer_id STRING, quote_request_id STRING,
  event_date TIMESTAMP, event_name STRING, insurance_type STRING, channel STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS jad_ucompare.analytics.fact_marketing_spend (
  month_start DATE, channel STRING, spend_eur DECIMAL(12,2)
) USING DELTA;
