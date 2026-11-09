"""Deterministic synthetic retail dataset generator.

ALL DATA PRODUCED HERE IS SYNTHETIC. Brands, products, customers, campaigns and
amounts are invented for demonstration and do not describe any real business.

The generator writes "source-system style" CSV extracts to data/raw, including
multi-currency amounts and a small, deliberate set of dirty records (duplicates,
missing keys, invalid quantities, orphan rows, untidy codes) so that the
validation and cleansing layer has something real to do.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

FX_TO_GBP = {"GBP": 1.0, "EUR": 0.86, "USD": 0.79}

COUNTRIES = {  # country: (weight, currency, region, vat/sales-tax rate)
    "United Kingdom": (0.55, "GBP", "UK & Ireland", 0.20),
    "Ireland": (0.05, "EUR", "UK & Ireland", 0.23),
    "Germany": (0.12, "EUR", "Europe", 0.19),
    "France": (0.10, "EUR", "Europe", 0.20),
    "Netherlands": (0.06, "EUR", "Europe", 0.21),
    "United States": (0.12, "USD", "North America", 0.07),
}

CATEGORIES = {  # category: (price range GBP, cost ratio range, return probability, n products)
    "Apparel": ((18, 90), (0.35, 0.50), 0.17, 30),
    "Footwear": ((45, 140), (0.40, 0.55), 0.14, 18),
    "Accessories": ((12, 70), (0.30, 0.45), 0.06, 20),
    "Home": ((15, 160), (0.45, 0.60), 0.05, 22),
    "Beauty": ((8, 45), (0.25, 0.40), 0.03, 18),
    "Electronics": ((30, 250), (0.60, 0.75), 0.07, 12),
}
BRANDS = ["Alder & Co", "Brightline", "Corvo", "Duneworks", "Elmstead", "Fenwick Studio", "Greyhaven"]
NOUNS = {
    "Apparel": ["Linen Shirt", "Merino Crew", "Chino", "Rain Jacket", "Hoodie", "Tee", "Overshirt", "Knit Dress"],
    "Footwear": ["Runner", "Chelsea Boot", "Loafer", "Trail Shoe", "Canvas Sneaker", "Sandal"],
    "Accessories": ["Tote Bag", "Wool Scarf", "Leather Belt", "Cap", "Card Wallet", "Sunglasses"],
    "Home": ["Throw Blanket", "Ceramic Mug Set", "Table Lamp", "Linen Cushion", "Glass Carafe", "Planter"],
    "Beauty": ["Face Serum", "Hand Cream", "Body Wash", "Lip Balm", "Shampoo Bar", "Candle"],
    "Electronics": ["Wireless Earbuds", "Desk Speaker", "Power Bank", "Smart Plug", "Charging Dock"],
}
ACQ_CHANNELS = {"Paid Search": 0.30, "Paid Social": 0.25, "Organic": 0.20, "Affiliate": 0.10,
                "Direct": 0.10, "Display": 0.05}
PAID_CHANNELS = ["Paid Search", "Paid Social", "Display", "Affiliate", "Email"]
RETURN_REASONS = {
    "Apparel": ["Size / fit", "Size / fit", "Not as described", "Changed mind", "Damaged"],
    "Footwear": ["Size / fit", "Size / fit", "Damaged", "Changed mind"],
}
DEFAULT_REASONS = ["Damaged", "Not as described", "Changed mind", "Late delivery"]


def _season(month: np.ndarray) -> np.ndarray:
    return np.select([month == 11, month == 12, month == 1, month == 7], [1.5, 1.75, 0.8, 0.9], 1.0)


def generate(as_of: date, seed: int = 42, n_customers: int = 7000, days: int = 730) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    start = as_of - timedelta(days=days - 1)
    day_index = pd.date_range(start, as_of, freq="D")

    # ---------------- products ----------------
    prods = []
    pid = 1
    for cat, ((lo, hi), (clo, chi), _, n) in CATEGORIES.items():
        for _ in range(n):
            price = float(np.round(rng.uniform(lo, hi)) - 0.01)
            cost = round(price * rng.uniform(clo, chi), 2)
            launch = start - timedelta(days=int(rng.integers(30, 900))) if rng.random() < 0.85 \
                else start + timedelta(days=int(rng.integers(0, days - 60)))
            status = "new" if launch > as_of - timedelta(days=180) else ("discontinued" if rng.random() < 0.08 else "active")
            brand = str(rng.choice(BRANDS))
            prods.append({
                "product_id": f"P{pid:04d}", "sku": f" {cat[:3].upper()}-{pid:04d} ",  # untidy whitespace on purpose
                "product_name": f"{brand} {rng.choice(NOUNS[cat])}",
                "category": cat, "brand": brand,
                "list_price_gbp": price,
                "unit_cost_gbp": cost if rng.random() > 0.06 else None,  # ~6% of products lack reliable cost
                "lifecycle_status": status, "launch_date": launch.isoformat(),
            })
            pid += 1
    products = pd.DataFrame(prods)
    popularity = rng.zipf(1.6, len(products)).clip(1, 50).astype(float)
    launch_dates = pd.to_datetime(products["launch_date"]).dt.date.to_numpy()

    # ---------------- campaigns ----------------
    camps = []
    cid = 1
    for ch, n in [("Paid Search", 3), ("Paid Social", 3), ("Display", 2), ("Affiliate", 2), ("Email", 3)]:
        for k in range(n):
            c_start = start + timedelta(days=int(k * days / (n + 0.5)))
            c_end = min(as_of, c_start + timedelta(days=int(days / n) + 120)) if k < n - 1 else as_of
            camps.append({"campaign_id": f"CMP{cid:03d}", "campaign_name": f"{ch} – {['Always-on', 'Seasonal', 'Brand', 'Prospecting'][k % 4]} {k + 1}",
                          "channel": ch, "objective": "retention" if ch == "Email" else "acquisition",
                          "start_date": c_start.isoformat(), "end_date": c_end.isoformat()})
            cid += 1
    campaigns = pd.DataFrame(camps)

    camp_recs = campaigns.to_dict("records")

    def active_campaign(channel: str, d: date) -> str | None:
        ds = d.isoformat()
        live = [c["campaign_id"] for c in camp_recs if c["channel"] == channel and c["start_date"] <= ds <= c["end_date"]]
        return None if not live else live[int(rng.integers(0, len(live)))]

    # ---------------- customers ----------------
    t = np.linspace(0, 1, len(day_index))
    w = (1 + 0.7 * t) * _season(day_index.month.to_numpy())
    acq_days = rng.choice(len(day_index), size=n_customers, p=w / w.sum())
    countries = list(COUNTRIES)
    cw = np.array([COUNTRIES[c][0] for c in countries])
    cust_country = rng.choice(countries, size=n_customers, p=cw / cw.sum())
    cust_channel = rng.choice(list(ACQ_CHANNELS), size=n_customers, p=list(ACQ_CHANNELS.values()))
    customers = pd.DataFrame({
        "customer_id": [f"C{i:06d}" for i in range(1, n_customers + 1)],
        "signup_date": [(day_index[d] - timedelta(days=int(rng.integers(0, 20)))).date().isoformat() for d in acq_days],
        "country": cust_country, "acquisition_channel": cust_channel,
    })

    # ---------------- orders, items, returns ----------------
    orders, items, returns = [], [], []
    oid = iid = rid = 1
    prod_recs = products.to_dict("records")
    cust_recs = customers.to_dict("records")
    for i in range(n_customers):
        cust = cust_recs[i]
        country = cust["country"]
        _, cur, _, tax_rate = COUNTRIES[country]
        fx = FX_TO_GBP[cur]
        repeat_p = rng.beta(2.2, 3.0)
        mean_gap = rng.uniform(35, 120)
        d = day_index[acq_days[i]].date()
        seq = 1
        while d <= as_of:
            ts = datetime.combine(d, time(0), tzinfo=timezone.utc) + timedelta(seconds=int(rng.integers(6 * 3600, 23 * 3600)))
            channel = str(rng.choice(["web", "app", "marketplace"], p=[0.6, 0.25, 0.15]))
            cancelled = rng.random() < 0.04
            # marketing touch (last click before the order)
            touch_c, touch_ts = None, None
            if seq == 1 and cust["acquisition_channel"] in PAID_CHANNELS:
                touch_c = active_campaign(cust["acquisition_channel"], d)
                lag = rng.uniform(0, 6.5) if rng.random() < 0.9 else rng.uniform(8, 20)  # some clicks fall outside window
            elif seq > 1 and rng.random() < 0.45:
                touch_c = active_campaign("Email" if rng.random() < 0.7 else "Paid Search", d)
                lag = rng.uniform(0, 9)
            if touch_c:
                touch_ts = ts - timedelta(days=float(lag))
            # lines
            n_lines = int(min(5, 1 + rng.poisson(0.7)))
            eligible = np.where(launch_dates <= d)[0]
            pw = popularity[eligible] / popularity[eligible].sum()
            chosen = rng.choice(eligible, size=min(n_lines, len(eligible)), replace=False, p=pw)
            promo_p = 0.45 if d.month in (11, 12) else 0.22
            items_net = 0.0
            order_lines = []
            for pidx in chosen:
                p = prod_recs[pidx]
                qty = 1 + int(rng.random() < 0.2) + int(rng.random() < 0.05)
                unit_local = round(p["list_price_gbp"] / fx, 2)
                disc_pct = float(rng.choice([0.1, 0.2, 0.3])) if rng.random() < promo_p else 0.0
                disc = round(qty * unit_local * disc_pct, 2)
                items_net += qty * unit_local - disc
                order_lines.append((iid, pidx, qty, unit_local, disc))
                items.append({"order_item_id": f"OI{iid:07d}", "order_id": f"O{oid:07d}", "product_id": p["product_id"],
                              "quantity": qty, "unit_price": unit_local, "discount_amount": disc, "currency": cur})
                iid += 1
            shipping = 0.0 if items_net >= 50 / fx else round(4.99 / fx, 2)
            tax = round((items_net + shipping) * tax_rate, 2)
            shipped = ts + timedelta(hours=float(rng.uniform(6, 72)))
            transit = rng.uniform(1, 5) + (2 if cur == "USD" else 0) + (rng.uniform(2, 6) if rng.random() < 0.07 else 0)
            delivered = shipped + timedelta(days=float(transit))
            promised = d + timedelta(days=8 if cur == "USD" else 5)
            orders.append({
                "order_id": f"O{oid:07d}", "customer_id": cust["customer_id"], "order_ts": ts.isoformat().replace("+00:00", "Z"),
                "channel": channel, "status": "cancelled" if cancelled else "completed", "country": country, "currency": cur,
                "shipping_amount": shipping, "tax_amount": tax, "order_total": round(items_net + shipping + tax, 2),
                "last_touch_campaign_id": touch_c, "last_touch_ts": touch_ts.isoformat().replace("+00:00", "Z") if touch_ts else None,
                "shipped_ts": None if cancelled else shipped.isoformat().replace("+00:00", "Z"),
                "delivered_ts": None if cancelled or delivered.date() > as_of else delivered.isoformat().replace("+00:00", "Z"),
                "promised_delivery_date": promised.isoformat(),
            })
            # returns
            if not cancelled:
                for (line_id, pidx, qty, unit_local, disc) in order_lines:
                    cat = prod_recs[pidx]["category"]
                    if rng.random() < CATEGORIES[cat][2] * (1.4 if transit > 6 else 1.0):
                        rqty = int(rng.integers(1, qty + 1))
                        rdate = delivered.date() + timedelta(days=int(rng.integers(2, 26)))
                        if rdate > as_of:
                            continue
                        net_unit = (qty * unit_local - disc) / qty
                        refund = round(rqty * net_unit * (0.5 if rng.random() < 0.08 else 1.0), 2)
                        returns.append({"return_id": f"R{rid:06d}", "order_item_id": f"OI{line_id:07d}",
                                        "return_date": rdate.isoformat(), "quantity": rqty,
                                        "reason": str(rng.choice(RETURN_REASONS.get(cat, DEFAULT_REASONS))),
                                        "refund_amount": refund, "currency": cur})
                        rid += 1
            oid += 1
            seq += 1
            if rng.random() > repeat_p:
                break
            d = d + timedelta(days=max(3, int(rng.exponential(mean_gap))))

    orders = pd.DataFrame(orders)
    items = pd.DataFrame(items)
    returns = pd.DataFrame(returns)

    # ---------------- marketing spend (campaign x day) ----------------
    spend_rows = []
    base = {"Paid Search": 150.0, "Paid Social": 115.0, "Display": 45.0, "Affiliate": 40.0, "Email": 9.0}
    cpm = {"Paid Search": 9.0, "Paid Social": 6.0, "Display": 2.5, "Affiliate": 4.0, "Email": 0.4}
    ctr = {"Paid Search": 0.012, "Paid Social": 0.004, "Display": 0.0012, "Affiliate": 0.006, "Email": 0.008}
    for c in campaigns.itertuples():
        for dd in pd.date_range(c.start_date, c.end_date, freq="D"):
            n_live = ((campaigns.channel == c.channel) & (campaigns.start_date <= dd.date().isoformat())
                      & (campaigns.end_date >= dd.date().isoformat())).sum()
            prog = (dd.date() - start).days / days
            spend = base[c.channel] / n_live * (1 + 0.6 * prog) * float(_season(np.array([dd.month]))[0]) * rng.uniform(0.75, 1.25)
            impressions = int(spend / cpm[c.channel] * 1000 * rng.uniform(0.85, 1.15))
            spend_rows.append({"date": dd.date().isoformat(), "campaign_id": c.campaign_id, "spend": round(spend, 2),
                               "currency": "GBP", "impressions": impressions,
                               "clicks": int(impressions * ctr[c.channel] * rng.uniform(0.8, 1.2))})
    spend = pd.DataFrame(spend_rows)
    # US prospecting is billed in USD in the ad platform
    us_mask = (spend.campaign_id == campaigns.loc[campaigns.channel == "Paid Social", "campaign_id"].iloc[-1])
    spend.loc[us_mask, "spend"] = (spend.loc[us_mask, "spend"] / FX_TO_GBP["USD"]).round(2)
    spend.loc[us_mask, "currency"] = "USD"

    # ---------------- inventory snapshots (weekly, last 12 weeks, single DC) ----------------
    inv_rows = []
    weeks = pd.date_range(as_of - timedelta(weeks=11), as_of, freq="7D")
    for p in products.itertuples():
        level = int(rng.integers(20, 400))
        for wk in weeks:
            level = max(0, level + int(rng.integers(-60, 70)))
            inv_rows.append({"snapshot_date": wk.date().isoformat(), "product_id": p.product_id, "location_id": "DC-01",
                             "on_hand": level, "reserved": int(level * rng.uniform(0, 0.15))})
    inventory = pd.DataFrame(inv_rows)

    data = {"customers": customers, "products": products, "campaigns": campaigns, "orders": orders,
            "order_items": items, "returns": returns, "marketing_spend": spend, "inventory_snapshots": inventory}
    _inject_dirty_records(data, rng)
    return data


def _inject_dirty_records(data: dict[str, pd.DataFrame], rng: np.random.Generator) -> None:
    """Deliberate, documented data defects (see docs/sample_data.md)."""
    o, it, r, s = data["orders"], data["order_items"], data["returns"], data["marketing_spend"]
    # 1. exact duplicate order headers (re-sent by the source system)
    o = pd.concat([o, o.sample(25, random_state=1)], ignore_index=True)
    # 2. orders with a missing customer key
    o.loc[o.sample(6, random_state=2).index, "customer_id"] = None
    # 3. untidy currency codes
    o.loc[o.sample(40, random_state=3).index, "currency"] = o["currency"].str.lower() + " "
    # 4. order lines with a missing product key / invalid quantities
    it.loc[it.sample(15, random_state=4).index, "product_id"] = None
    it.loc[it.sample(10, random_state=5).index, "quantity"] = rng.choice([0, -1], 10)
    # 5. orphan order lines (order never arrived in the extract)
    orphans = it.sample(8, random_state=6).copy()
    orphans["order_item_id"] = [f"OX{i:06d}" for i in range(len(orphans))]
    orphans["order_id"] = [f"O9{i:06d}" for i in range(len(orphans))]
    it = pd.concat([it, orphans], ignore_index=True)
    # 6. returns with more units than were sold
    bad = r.sample(4, random_state=7).index
    r.loc[bad, "quantity"] = 99
    # 7. duplicated spend rows
    s = pd.concat([s, s.sample(5, random_state=8)], ignore_index=True)
    data.update(orders=o, order_items=it, returns=r, marketing_spend=s)


def write_raw(data: dict[str, pd.DataFrame], raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name, df in data.items():
        df.to_csv(raw_dir / f"{name}.csv", index=False)
