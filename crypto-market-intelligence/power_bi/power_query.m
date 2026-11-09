// Power Query (M) examples - Crypto Market Intelligence
// Requires the DuckDB ODBC driver with a DSN named "crypto_duckdb" pointing at data/processed/crypto.duckdb.

// ---- Market Price (from the gap-aware view) ----
let
    Source = Odbc.Query("dsn=crypto_duckdb",
        "SELECT asset_id, quote_currency, price_date, observed_at, retrieved_at, open, high, low, close,
                volume, market_cap, source_id, data_kind, daily_return
         FROM v_market_daily"),
    Typed = Table.TransformColumnTypes(Source, {
        {"price_date", type date}, {"observed_at", type datetimezone}, {"retrieved_at", type datetimezone},
        {"open", type number}, {"high", type number}, {"low", type number}, {"close", type number},
        {"volume", type number}, {"market_cap", type number}, {"daily_return", Percentage.Type}})
in
    Typed

// ---- Transactions (adds executed_date for the Date relationship) ----
let
    Source = Odbc.Query("dsn=crypto_duckdb", "SELECT * FROM fact_transactions"),
    Typed = Table.TransformColumnTypes(Source, {{"executed_at", type datetimezone}, {"quantity", type number},
                                                {"unit_price", type number}, {"fee", type number}}),
    AddDate = Table.AddColumn(Typed, "executed_date",
                              each Date.From(DateTimeZone.SwitchZone([executed_at], 0)), type date)
in
    AddDate

// ---- Portfolio Daily with TWR ratio (for the DAX TWR Index measure) ----
let
    Source = Odbc.Query("dsn=crypto_duckdb",
        "SELECT snapshot_date, market_value, external_flow,
                market_value / NULLIF(LAG(market_value, 1, 0) OVER (ORDER BY snapshot_date) + external_flow, 0) AS twr_ratio
         FROM v_portfolio_daily WHERE unvalued_positions = 0"),
    Typed = Table.TransformColumnTypes(Source, {{"snapshot_date", type date}, {"twr_ratio", type number}})
in
    Typed

// ---- Production variant (Databricks) ----
// Source = Databricks.Catalogs("adb-xxxx.azuredatabricks.net", "/sql/1.0/warehouses/xxxx", [Catalog="analytics"]),
// Gold = Source{[Name="analytics"]}[Data]{[Name="gold"]}[Data],
// MarketPrice = Gold{[Name="v_market_daily"]}[Data]
