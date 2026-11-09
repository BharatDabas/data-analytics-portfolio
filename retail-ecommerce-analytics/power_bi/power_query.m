// Power Query M examples (specification; not validated in a .pbix)
// Option A: read the curated views from DuckDB through the DuckDB ODBC driver (DSN "RetailDuckDB").
// Option B (production): Databricks.Catalogs(...) against the Gold schema with identical view names.

// ---- shared parameter ----
// DatabasePath = "C:\analytics-portfolio\retail-ecommerce-analytics\data\processed\retail.duckdb"

// ---- Order Line (from the semantic view) ----
let
    Source = Odbc.Query(
        "dsn=RetailDuckDB;database=" & DatabasePath,
        "SELECT * FROM v_order_lines"
    ),
    Typed = Table.TransformColumnTypes(
        Source,
        {
            {"order_date", type date}, {"quantity", Int64.Type},
            {"gross_sales_gbp", Currency.Type}, {"discount_gbp", Currency.Type},
            {"refund_gbp", Currency.Type}, {"net_revenue_gbp", Currency.Type},
            {"cogs_gbp", Currency.Type}, {"gross_profit_gbp", Currency.Type},
            {"net_revenue_costed_gbp", Currency.Type},
            {"is_cancelled", type logical}, {"has_reliable_cost", type logical}
        }
    )
in
    Typed

// ---- Marketing Spend (campaign x day; keep separate from orders) ----
let
    Source = Odbc.Query("dsn=RetailDuckDB;database=" & DatabasePath, "SELECT * FROM fact_marketing_spend"),
    Typed = Table.TransformColumnTypes(
        Source,
        {{"spend_date", type date}, {"spend_gbp", Currency.Type}, {"impressions", Int64.Type}, {"clicks", Int64.Type}}
    )
in
    Typed

// ---- Direct CSV import of a raw extract with the same cleansing rules as cleanse.py ----
// (useful for ad-hoc validation of a new extract against the contract)
let
    Source = Csv.Document(File.Contents("C:\extracts\orders.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Trimmed = Table.TransformColumns(Promoted, {}, Text.Trim),
    UpperCurrency = Table.TransformColumns(Trimmed, {{"currency", Text.Upper, type text}}),
    Deduped = Table.Distinct(UpperCurrency, {"order_id"}),
    NoMissingCustomer = Table.SelectRows(Deduped, each [customer_id] <> null and [customer_id] <> ""),
    FxTable = #table({"currency", "fx_to_gbp"}, {{"GBP", 1.0}, {"EUR", 0.86}, {"USD", 0.79}}),
    WithFx = Table.NestedJoin(NoMissingCustomer, {"currency"}, FxTable, {"currency"}, "fx", JoinKind.Inner),
    Expanded = Table.ExpandTableColumn(WithFx, "fx", {"fx_to_gbp"}),
    Typed = Table.TransformColumnTypes(Expanded, {{"order_total", type number}, {"fx_to_gbp", type number}}),
    WithGbp = Table.AddColumn(Typed, "header_total_gbp", each [order_total] * [fx_to_gbp], Currency.Type)
in
    WithGbp
