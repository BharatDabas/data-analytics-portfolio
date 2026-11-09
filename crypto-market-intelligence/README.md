# Crypto Market Intelligence & Portfolio Analytics

## Development Period
December 2025 – March 2026

## Project objective
A cryptocurrency market and portfolio analytics application that collects market-price data, maintains a portfolio transaction ledger, and presents asset performance, portfolio valuation, profit/loss, risk, diversification and data-ingestion health. The default mode uses clearly labelled synthetic demonstration data; live CoinGecko ingestion is optional. This application does not execute trades or provide investment advice.

## Technology
Python, pandas, DuckDB, SQL, Streamlit and Plotly.

## Run locally
Prerequisites: Python 3.11 or newer.

```bash
cd crypto-market-intelligence
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

Open the local URL printed by Streamlit (normally `http://localhost:8501`). On first launch, the app builds its local DuckDB database from the synthetic demo data. To rebuild the database manually, run `python pipeline.py --rebuild` from this project folder.

Optional live market-data ingestion (requires internet access and is subject to provider limits):

```bash
python pipeline.py --source coingecko --assets bitcoin,ethereum --days 365
```
