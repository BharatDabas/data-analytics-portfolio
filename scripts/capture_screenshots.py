"""Capture dashboard screenshots for the READMEs (developer utility, not a runtime dependency).

Prerequisites: `pip install playwright`. It uses the locally installed Microsoft Edge or Chrome,
so no browser download is needed. Start each app first, for example:
    streamlit run crypto-market-intelligence/app.py --server.port 8601
    streamlit run retail-ecommerce-analytics/app.py --server.port 8602
    streamlit run supply-chain-inventory-analytics/app.py --server.port 8603
Then:  python scripts/capture_screenshots.py [crypto|retail|supply ...]
Every app supports deep links via ?page=<slug>.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
APPS = {
    "crypto": ("http://localhost:8601", ["market-overview", "asset-explorer", "portfolio-overview", "risk",
                                         "performance", "alerts-ops"]),
    "retail": ("http://localhost:8602", ["executive-overview", "product-category", "customer-intelligence",
                                         "cohort-retention", "marketing-performance", "returns-operations",
                                         "forecast-planning", "data-quality"]),
    "supply": ("http://localhost:8603", ["executive-overview", "inventory-health", "demand-forecast",
                                         "supplier-performance", "procurement", "warehouse-network",
                                         "replenishment", "data-quality"]),
}


def capture(page, url: str, path: Path) -> None:
    page.goto(url, wait_until="networkidle")
    page.wait_for_selector('[data-testid="stSidebar"]', timeout=120_000)
    # wait until Streamlit has finished running the script
    page.wait_for_function("""() => !document.querySelector('[data-testid="stStatusWidget"]')
                              || document.querySelector('[data-testid="stStatusWidget"]').innerText.trim() === ''""",
                           timeout=120_000)
    page.wait_for_timeout(3500)  # let Plotly and data grids paint
    page.screenshot(path=str(path))


def main(which: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge")
        except Exception:
            browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1440, "height": 1500}, device_scale_factor=1)
        for key in which:
            base, slugs = APPS[key]
            for slug in slugs:
                path = OUT / f"{key}-{slug}.png"
                capture(page, f"{base}/?page={slug}", path)
                print("saved", path.name)
        browser.close()


if __name__ == "__main__":
    main(sys.argv[1:] or list(APPS))
