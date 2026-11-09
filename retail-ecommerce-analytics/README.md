# Retail & E-commerce Commercial Intelligence

## Development Period
January 2025 – February 2025

## Project objective
An end-to-end retail analytics application that transforms order, order-line, customer, product, returns and marketing data into a commercial reporting model. It provides sales and margin analysis, product/category performance, customer segmentation, cohort retention, marketing effectiveness, returns analysis, forecasting and data-quality monitoring. The included demonstration dataset is synthetic.

## Technology
Python, pandas, NumPy, DuckDB, SQL, Streamlit and Plotly.

## Run locally
Prerequisites: Python 3.11 or newer.

```bash
cd retail-ecommerce-analytics
python -m venv .venv
```

Activate the environment:

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

Install and launch:

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Open the local URL printed by Streamlit (normally `http://localhost:8501`). On first launch, the app generates the synthetic source extracts and builds the local DuckDB analytics database automatically. To rebuild the data and model manually, run `python pipeline.py` from this project folder.
