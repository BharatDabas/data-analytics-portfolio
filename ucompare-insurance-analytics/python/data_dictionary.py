"""Canonical source-to-model field definitions for the starter project."""
DATA_DICTIONARY = {
 "customers": {
  "customer_id":"Pseudonymous customer key; never use name/email as an analytics key.",
  "created_date":"Customer record creation date.",
  "age_band":"Age band only; avoid exact date of birth in BI.",
  "county":"Coarse geographic region.",
  "acquisition_channel":"First/attributed acquisition channel, as defined by source system.",
  "consent_status":"Consent/permission indicator; not a substitute for legal review."
 },
 "quote_requests": {
  "quote_request_id":"One customer quote journey/request.",
  "customer_id":"Pseudonymous customer key.",
  "quote_date":"Timestamp/date request was created.",
  "insurance_type":"Life, Travel or Bike.",
  "quote_submitted":"Whether the customer submitted the quote form.",
  "session_source":"Channel attribution recorded for the session.",
  "cover_amount_eur":"Life cover amount, where applicable.",
  "trip_type":"Travel trip type, where applicable.",
  "destination_region":"Travel destination grouping, where applicable.",
  "bike_count":"Number of bikes insured, where applicable.",
  "bike_value_eur":"Declared bike value, where applicable."
 },
 "provider_quotes": {
  "provider_quote_id":"One provider response/quote record.",
  "quote_request_id":"Parent quote request.",
  "provider_name":"Provider from the approved provider master.",
  "insurance_type":"Life, Travel or Bike.",
  "provider_priority":"Business-defined priority flag; currently ATI, Multitrip, Switched On.",
  "quote_status":"Returned, No Quote or Not Submitted.",
  "annual_premium_eur":"Quoted premium; null when no quote returned."
 },
 "purchases": {
  "purchase_id":"Confirmed purchase transaction key.",
  "quote_request_id":"Quote journey associated with purchase.",
  "provider_quote_id":"Provider quote associated with purchase.",
  "customer_id":"Pseudonymous customer key.",
  "provider_name":"Selected provider.",
  "insurance_type":"Product category.",
  "purchase_date":"Confirmed purchase date.",
  "annual_premium_eur":"Premium amount shown in source; validate whether annualised/gross/net."
 },
 "commissions": {
  "commission_id":"Commission ledger record key.",
  "purchase_id":"Associated purchase.",
  "commission_date":"Commission earned/posted date.",
  "commission_amount_eur":"Commission amount, not gross premium.",
  "commission_status":"Earned, Pending, Reversed or other source status."
 }
}
