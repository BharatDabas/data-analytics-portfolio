from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)
SOURCE_XLSX = DATA / "Ucompare_Synthetic_Source.xlsx"

SHEETS = ["customers","quote_requests","provider_quotes","purchases","commissions","website_events","marketing_spend"]
if SOURCE_XLSX.exists():
    data = pd.read_excel(SOURCE_XLSX, sheet_name=SHEETS)
else:
    data = {name: pd.read_csv(DATA/"raw"/f"{name}.csv") for name in SHEETS}

customers, quotes, providers, purchases, commissions, events, spend = [data[s] for s in SHEETS]
for df, col in [(quotes,"quote_date"),(purchases,"purchase_date"),(commissions,"commission_date"),(spend,"month_start")]:
    df[col] = pd.to_datetime(df[col], errors="coerce")

# Minimal schema and key validation; production version should fail fast on missing required columns.
required = {
 "customers":{"customer_id","created_date","acquisition_channel"},
 "quote_requests":{"quote_request_id","customer_id","quote_date","insurance_type","quote_submitted"},
 "provider_quotes":{"provider_quote_id","quote_request_id","provider_name","quote_status"},
 "purchases":{"purchase_id","quote_request_id","provider_name","insurance_type","purchase_status","annual_premium_eur"},
 "commissions":{"commission_id","purchase_id","commission_status","commission_amount_eur"}
}
for name, cols in required.items():
    missing = cols - set(data[name].columns)
    if missing: raise ValueError(f"{name}: missing required columns: {sorted(missing)}")

submitted = quotes[quotes["quote_submitted"].astype(str).str.lower().isin(["true","1"])]
returned = providers[providers.quote_status.eq("Returned")]
confirmed = purchases[purchases.purchase_status.eq("Confirmed")]
earned = commissions[commissions.commission_status.eq("Earned")]

quality = []
for name, df, key in [
 ("customers",customers,"customer_id"),("quote_requests",quotes,"quote_request_id"),
 ("provider_quotes",providers,"provider_quote_id"),("purchases",purchases,"purchase_id"),
 ("commissions",commissions,"commission_id")]:
    quality.append({"table":name,"rows":len(df),"duplicate_primary_keys":int(df[key].duplicated().sum()),
                    "missing_primary_keys":int(df[key].isna().sum())})
pd.DataFrame(quality).to_csv(OUT/"data_quality_checks.csv",index=False)

kpis = pd.DataFrame([
 ("Unique Customers",customers.customer_id.nunique()),
 ("Quote Requests",quotes.quote_request_id.nunique()),
 ("Submitted Quote Requests",submitted.quote_request_id.nunique()),
 ("Provider Quotes Returned",returned.provider_quote_id.nunique()),
 ("Confirmed Purchases",confirmed.purchase_id.nunique()),
 ("Gross Premium EUR",confirmed.annual_premium_eur.sum()),
 ("Earned Commission EUR",earned.commission_amount_eur.sum()),
 ("Quote Submission Rate",submitted.quote_request_id.nunique()/max(quotes.quote_request_id.nunique(),1)),
 ("Submitted-to-Purchase Rate",confirmed.quote_request_id.nunique()/max(submitted.quote_request_id.nunique(),1))
],columns=["metric","value"])
kpis.to_csv(OUT/"executive_kpis.csv",index=False)

product = quotes.groupby("insurance_type").agg(
 quote_requests=("quote_request_id","nunique"),
 submitted_requests=("quote_request_id",lambda x: quotes.loc[x.index].query("quote_submitted == True").quote_request_id.nunique())
).reset_index()
pp = confirmed.groupby("insurance_type").agg(
 confirmed_purchases=("purchase_id","nunique"),gross_premium_eur=("annual_premium_eur","sum")
).reset_index()
cc = earned.merge(confirmed[["purchase_id","insurance_type"]],on="purchase_id",how="left").groupby("insurance_type").commission_amount_eur.sum().reset_index(name="earned_commission_eur")
product = product.merge(pp,on="insurance_type",how="left").merge(cc,on="insurance_type",how="left").fillna(0)
product.to_csv(OUT/"product_performance.csv",index=False)

provider_perf = providers.groupby(["insurance_type","provider_name","provider_priority"]).agg(
 provider_quote_records=("provider_quote_id","nunique"),
 quotes_returned=("quote_status",lambda s:(s=="Returned").sum()),
 average_returned_premium_eur=("annual_premium_eur","mean")
).reset_index()
pc = confirmed.groupby(["insurance_type","provider_name"]).agg(
 confirmed_purchases=("purchase_id","nunique"),gross_premium_eur=("annual_premium_eur","sum")
).reset_index()
provider_perf = provider_perf.merge(pc,on=["insurance_type","provider_name"],how="left").fillna(0)
provider_perf.to_csv(OUT/"provider_performance.csv",index=False)

quotes["month"] = quotes.quote_date.dt.to_period("M").astype(str)
confirmed["month"] = confirmed.purchase_date.dt.to_period("M").astype(str)
monthly = quotes.groupby(["month","insurance_type"]).quote_request_id.nunique().reset_index(name="quote_requests")
m2 = confirmed.groupby(["month","insurance_type"]).agg(confirmed_purchases=("purchase_id","nunique"),gross_premium_eur=("annual_premium_eur","sum")).reset_index()
monthly = monthly.merge(m2,on=["month","insurance_type"],how="left").fillna(0)
monthly.to_csv(OUT/"monthly_performance.csv",index=False)

travel_priority = provider_perf[(provider_perf.insurance_type=="Travel") &
 provider_perf.provider_name.isin(["ATI","Multitrip","Switched On"])]
travel_priority.to_csv(OUT/"travel_priority_providers.csv",index=False)

# Charts
fig, ax = plt.subplots(figsize=(8,4.5))
product.set_index("insurance_type")[["quote_requests","confirmed_purchases"]].plot(kind="bar",ax=ax)
ax.set_title("Quote Requests vs Confirmed Purchases – Synthetic Prototype")
ax.set_ylabel("Count"); ax.set_xlabel("Insurance category")
plt.tight_layout(); fig.savefig(OUT/"product_funnel.png",dpi=160); plt.close(fig)
fig, ax = plt.subplots(figsize=(9,4.5))
provider_perf.sort_values("quotes_returned").plot(x="provider_name",y="quotes_returned",kind="barh",ax=ax,legend=False)
ax.set_title("Provider Quotes Returned – Synthetic Prototype")
ax.set_xlabel("Returned provider quotes")
plt.tight_layout(); fig.savefig(OUT/"provider_quotes.png",dpi=160); plt.close(fig)
print("Analysis complete. All values are synthetic demonstration data.")
