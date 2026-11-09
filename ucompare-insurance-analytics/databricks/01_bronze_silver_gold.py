# Databricks notebook source
# MAGIC %md
# MAGIC # Ucompare.ie – Medallion Architecture
# MAGIC Excel is the replaceable business input. A controlled Python ingestion job validates
# MAGIC the workbook and stages sheets as CSV/Parquet in approved cloud storage. Databricks
# MAGIC reads those staged files; Spark does not natively parse .xlsx without extra libraries.

# COMMAND ----------
from pyspark.sql import functions as F
CATALOG = "jad_ucompare"
SCHEMA = "analytics"
STAGED_PATH = "/Volumes/<catalog>/<schema>/<volume>/validated_staging"
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")

# COMMAND ----------
tables = ["customers","quote_requests","provider_quotes","purchases","commissions","website_events","marketing_spend"]
for name in tables:
    df = (spark.read.option("header", True).option("inferSchema", True)
          .csv(f"{STAGED_PATH}/{name}/"))
    df = df.withColumn("_ingested_at",F.current_timestamp()).withColumn("_source_table",F.lit(name))
    df.write.format("delta").mode("append").saveAsTable(f"{CATALOG}.bronze_{name}")

# COMMAND ----------
# Silver: deduplicate business keys, normalise provider/product strings and cast dates.
keys={"customers":"customer_id","quote_requests":"quote_request_id",
      "provider_quotes":"provider_quote_id","purchases":"purchase_id","commissions":"commission_id"}
for name,key in keys.items():
    df=spark.table(f"{CATALOG}.bronze_{name}").dropDuplicates([key])
    if "insurance_type" in df.columns:
        df=df.withColumn("insurance_type",F.initcap(F.trim("insurance_type")))
    if "provider_name" in df.columns:
        df=df.withColumn("provider_name",F.trim("provider_name"))
    df.write.format("delta").mode("overwrite").saveAsTable(f"{CATALOG}.silver_{name}")

# COMMAND ----------
q=spark.table(f"{CATALOG}.silver_quote_requests")
pq=spark.table(f"{CATALOG}.silver_provider_quotes")
pu=spark.table(f"{CATALOG}.silver_purchases")
cm=spark.table(f"{CATALOG}.silver_commissions")
gold_product=(q.groupBy("insurance_type").agg(
    F.countDistinct("quote_request_id").alias("quote_requests"),
    F.countDistinct(F.when(F.col("quote_submitted")==True,F.col("quote_request_id"))).alias("submitted_requests"))
    .join(pu.groupBy("insurance_type").agg(
        F.countDistinct("purchase_id").alias("confirmed_purchases"),
        F.sum("annual_premium_eur").alias("gross_premium_eur")),"insurance_type","left"))
gold_product.write.format("delta").mode("overwrite").saveAsTable(f"{CATALOG}.gold_product_performance")

gold_provider=(pq.groupBy("insurance_type","provider_name").agg(
    F.countDistinct("provider_quote_id").alias("provider_quote_records"),
    F.countDistinct(F.when(F.col("quote_status")=="Returned",F.col("provider_quote_id"))).alias("quotes_returned"),
    F.avg(F.when(F.col("quote_status")=="Returned",F.col("annual_premium_eur"))).alias("average_returned_premium_eur")))
gold_provider.write.format("delta").mode("overwrite").saveAsTable(f"{CATALOG}.gold_provider_performance")

display(gold_product)
display(gold_provider)
