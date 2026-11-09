# Ucompare Insurance Analytics Prototype

**Development Period:** April 2024 – June 2024

An end-to-end insurance comparison analytics prototype built around the Ucompare / JAD Marketing business context. It demonstrates how quote journeys, insurer responses, confirmed purchases, commission records and marketing activity can be modelled and analysed across Life, Travel and Bike insurance.

> **Synthetic-data notice:** Every record and metric in this repository is dummy demonstration data. It is not actual Ucompare or JAD Marketing performance, and this repository is not a live production integration or an endorsement by either organisation.

## Business objective

Provide a consistent analytical view of the insurance comparison journey so business stakeholders can examine:

- Quote demand and submission across Life, Travel and Bike products.
- Provider quote availability, returned premiums and provider-level performance.
- Confirmed purchases and gross premium, separately from provider quotes and redirects.
- Earned commission from the commission ledger, rather than treating gross premium as platform revenue.
- Monthly performance, acquisition activity and data-quality issues.
- Travel priority-provider performance for ATI, Multitrip and Switched On.

The prototype keeps quote requests, provider responses, purchases and commission ledger entries at separate grains to reduce double-counting in reporting.

## Analytical scope

| Area | Coverage |
|---|---|
| Insurance products | Life, Travel and Bike |
| Life providers | Royal London, Zurich, Aviva, Irish Life, New Ireland |
| Travel providers | Cover for You, ATI, Multitrip, AA Travel Insurance, Ireland Go, Switched On |
| Priority Travel providers | ATI, Multitrip, Switched On |
| Bike provider | Bikmo |

## Key outputs

- Customer, quote request, submitted request, returned provider quote and confirmed purchase counts.
- Quote submission and submitted-to-purchase rates.
- Gross premium and earned commission, reported as distinct measures.
- Product, provider and monthly performance tables.
- Travel priority-provider comparison.
- Primary-key data-quality checks.
- Product funnel and provider quote charts.

**Metric interpretation:** A provider quote is not a sale; a redirect/click is not proof of purchase; gross premium is not commission revenue. Provider response rate requires a complete eligible-provider-opportunity denominator, which this starter dataset does not fully model. Conversion rates should be interpreted with a consistent cohort and attribution window.

## Technology

- **Python:** pandas, NumPy, Matplotlib, openpyxl
- **SQL:** Databricks SQL / Delta Lake table definitions, KPI queries and Gold views
- **Databricks:** Bronze / Silver / Gold notebook template
- **Excel:** source workbook, analysis workbook and management dashboard
- **Power BI:** PBIP report and semantic model, DAX measures, Power Query model sources

## Run locally (Windows)

Use Python 3.11 or 3.12 (64-bit).

1. Open Command Prompt in this project folder.
2. Create and activate a virtual environment:

   ```cmd
   py -3.12 -m venv .venv
   .venv\Scripts\activate
   ```

   If `py -3.12` is unavailable, use `py -3` with an installed compatible Python version.

3. Install dependencies and run the analysis:

   ```cmd
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   python python\run_analysis.py
   ```

4. Review the generated CSV tables and charts in `outputs/`.

### Run locally (macOS / Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python python/run_analysis.py
```

The analysis reads `data/Ucompare_Synthetic_Source.xlsx` by worksheet name. If that workbook is absent, it falls back to the corresponding CSV files in `data/raw/`. The script recreates the analysis outputs in `outputs/`.

## Excel deliverables

- `excel/JAD_Ucompare_Analysis_Workbook.xlsx` — executive summary and source-data tabs.
- `excel/JAD_Ucompare_Professional_Dashboard.xlsx` — management dashboard, product/provider/monthly views and source tabs.

Open these workbooks in Microsoft Excel or a compatible spreadsheet application.

## Power BI

Power BI Desktop is optional for the Python/Excel workflow.

1. Run the Python analysis first.
2. Run `generate_powerbi_report.bat` on Windows, or `python generate_powerbi_report.py`.
3. Open `JAD Marketing Analysis Power BI.pbip` in Power BI Desktop.
4. Refresh the model if prompted.

The generator updates the semantic model's CSV source paths for the current local folder. Those paths are machine-specific; rerun the generator after moving or cloning the project. The report uses the curated CSV files in `outputs/`.

## Databricks / SQL assets

- `sql/01_databricks_ddl.sql` — Delta table definitions.
- `sql/02_business_kpis.sql` — business KPI queries.
- `sql/03_data_quality.sql` — key, orphan and premium checks.
- `sql/04_gold_views.sql` — reporting views.
- `databricks/01_bronze_silver_gold.py` — notebook template for Bronze/Silver/Gold processing.

The notebook expects validated CSV files to be staged in an organisation-approved Databricks location. It is a template, not a preconfigured connection to a live workspace.

## Repository structure

```text
ucompare-insurance-analytics/
├── data/                 # Synthetic Excel source and CSV extracts
├── databricks/           # Bronze / Silver / Gold notebook template
├── excel/                # Analysis workbook and dashboard
├── outputs/              # Curated CSV results and generated charts
├── powerbi/              # DAX measures and report theme
├── JAD Marketing Analysis Power BI.Report/
├── JAD Marketing Analysis Power BI.SemanticModel/
├── python/               # Analysis script and data dictionary
├── sql/                  # DDL, KPI, quality and Gold-view SQL
├── generate_powerbi_report.py
└── requirements.txt
```

## Data governance

Only the supplied synthetic demonstration data is intended for this public repository. Do not add actual customer records, company exports, credentials, provider contracts or other confidential material. Any future use of live data requires the organisation's approval, appropriate access controls and privacy/security review.
