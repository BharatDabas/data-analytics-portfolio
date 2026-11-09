# Supply Chain & Inventory Optimisation Intelligence

## Development Period
April 2025 – June 2025

## Project objective
An operational analytics application for inventory, demand, procurement, suppliers and warehouse performance. It analyses stock availability and ageing, stockout and excess-inventory risk, supplier delivery performance, purchase orders, demand forecasts and replenishment recommendations. The included demonstration dataset is synthetic; replenishment outputs are decision support and do not automatically place orders.

## Technology
Python, pandas, NumPy, DuckDB, SQL, Streamlit and Plotly.

## Run locally
Prerequisites: Python 3.11 or newer.

```bash
cd supply-chain-inventory-analytics
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

Open the local URL printed by Streamlit (normally `http://localhost:8501`). On first launch, the app generates the synthetic source data and builds the local DuckDB model automatically. To regenerate the data and rebuild the model manually, run `python pipeline.py` from this project folder.
