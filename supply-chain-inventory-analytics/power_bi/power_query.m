// Power Query (M) examples for the supply chain model.
// Parameter: DataFolder = folder containing Parquet exports of the DuckDB tables/views, e.g.
//   duckdb data/processed/supply_chain.duckdb -c "COPY v_po_line_status TO 'export/v_po_line_status.parquet'"

// ---- Parameter ----
// DataFolder = "C:\analytics-portfolio\supply-chain-inventory-analytics\export\" meta [IsParameterQuery = true, Type = "Text"]

// ---- Inventory Snapshot (incremental refresh on snapshot_date using RangeStart / RangeEnd) ----
let
    Source = Parquet.Document(File.Contents(DataFolder & "fact_inventory_snapshot.parquet")),
    Typed = Table.TransformColumnTypes(Source, {
        {"snapshot_ts", type datetime}, {"snapshot_date", type date}, {"sku", type text},
        {"location_id", type text}, {"on_hand_qty", type number}, {"reserved_qty", type number},
        {"available_qty", type number}, {"in_transit_qty", type number}, {"is_negative_on_hand", type logical}}),
    Incremental = Table.SelectRows(Typed, each [snapshot_ts] >= RangeStart and [snapshot_ts] < RangeEnd)
in
    Incremental

// ---- PO Line (from the lifecycle view) ----
let
    Source = Parquet.Document(File.Contents(DataFolder & "v_po_line_status.parquet")),
    Typed = Table.TransformColumnTypes(Source, {
        {"order_date", type date}, {"approval_date", type date}, {"promised_date", type date},
        {"first_receipt_date", type date}, {"last_receipt_date", type date},
        {"ordered_qty", type number}, {"received_qty", type number}, {"accepted_qty", type number},
        {"open_qty", type number}, {"unit_cost_usd", Currency.Type}, {"line_value_usd", Currency.Type},
        {"is_due", type logical}, {"is_overdue", type logical}, {"on_time", type logical},
        {"otif", type logical}, {"in_full", type logical}, {"lead_time_days", Int64.Type}})
in
    Typed

// ---- Standardising a raw supplier extract in Power Query (illustrates the pipeline rules) ----
// Converts cases to eaches using the product case pack and supplier prices to USD with an FX table.
let
    Raw = Csv.Document(File.Contents(DataFolder & "purchase_orders.csv"), [Delimiter = ",", Encoding = 65001, PromoteHeaders = true]),
    Promoted = Table.PromoteHeaders(Raw, [PromoteAllScalars = true]),
    Typed = Table.TransformColumnTypes(Promoted, {{"ordered_qty", type number}, {"unit_cost", type number}, {"po_line", Int64.Type}}),
    Dedup = Table.Distinct(Typed, {"po_number", "po_line"}),
    WithProduct = Table.NestedJoin(Dedup, {"sku"}, Product, {"sku"}, "p", JoinKind.Inner),   // inner join = orphan rows excluded
    Expanded = Table.ExpandTableColumn(WithProduct, "p", {"case_pack"}),
    Factor = Table.AddColumn(Expanded, "uom_factor", each if [uom] = "CS" then [case_pack] else if [uom] = "DZ" then 12 else 1, Int64.Type),
    FxTable = #table(type table [currency = text, fx_to_usd = number], {{"USD", 1.0}, {"EUR", 1.08}, {"GBP", 1.27}}),
    WithFx = Table.ExpandTableColumn(Table.NestedJoin(Factor, {"currency"}, FxTable, {"currency"}, "fx", JoinKind.LeftOuter), "fx", {"fx_to_usd"}),
    QtyEA = Table.AddColumn(WithFx, "ordered_qty_ea", each [ordered_qty] * [uom_factor], type number),
    CostUSD = Table.AddColumn(QtyEA, "unit_cost_usd", each [unit_cost] * [fx_to_usd] / [uom_factor], Currency.Type)
in
    CostUSD

// ---- Date table (if not importing dim_date) ----
let
    StartDate = #date(2025, 1, 1),
    EndDate = Date.AddYears(Date.From(DateTime.LocalNow()), 1),
    Dates = List.Dates(StartDate, Duration.Days(EndDate - StartDate) + 1, #duration(1, 0, 0, 0)),
    T = Table.FromList(Dates, Splitter.SplitByNothing(), {"date_key"}, null, ExtraValues.Error),
    Typed = Table.TransformColumnTypes(T, {{"date_key", type date}}),
    AddCols = Table.AddColumn(Table.AddColumn(Table.AddColumn(Typed,
        "week_start", each Date.StartOfWeek([date_key], Day.Monday), type date),
        "is_working_day", each Date.DayOfWeek([date_key], Day.Monday) < 5, type logical),
        "fiscal_year", each if Date.Month([date_key]) >= 4 then Date.Year([date_key]) + 1 else Date.Year([date_key]), Int64.Type)
in
    AddCols
