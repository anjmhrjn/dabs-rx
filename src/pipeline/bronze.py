"""Bronze layer: raw synthetic feeds as ingested, plus reference tables.

Daily feeds are streaming tables fed by Auto Loader from the landing volume. Every column stays a string so bad
values reach silver's data quality checks unchanged. Reference files are small and load as batch materialized views.

Pipeline configuration (set in resources/pipeline.yml):
  rx.landing_path   landing volume path, e.g. /Volumes/<catalog>/<bronze schema>/landing
"""

from pyspark import pipelines as dp
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

spark = SparkSession.getActiveSession()
LANDING = spark.conf.get("rx.landing_path")

FEEDS = {
    "adc_events": "Automated dispensing cabinet transactions (synthetic).",
    "adt_events": "Admit, transfer, and discharge events (synthetic).",
    "order_events": "Controlled substance order events: new, verify, discontinue (synthetic).",
    "staff_assignments": "Staff unit and shift assignment changes, CDC feed for SCD2 (synthetic).",
}

REFERENCE_CSV = {
    "ref_formulary": ("formulary", "Controlled substance formulary: NDC, DEA schedule, unit cost (openFDA, NADAC)."),
    "ref_shortages": ("shortages", "FDA drug shortage records (openFDA)."),
    "ref_units": ("units", "Simulated hospital units (synthetic)."),
    "ref_cabinets": ("cabinets", "Simulated dispensing cabinets (synthetic)."),
    "ref_cabinet_par": ("cabinet_par", "Par levels per cabinet and drug (synthetic)."),
    "ref_dispense_units": ("dispense_units", "Unit of issue, mg and mL per unit for each formulary item."),
}


# The generator writes CSV with Python's csv module: quotes are escaped by doubling, and free-text fields (FDA
# shortage presentations) can contain newlines inside quotes.
CSV_OPTIONS = {"header": "true", "multiLine": "true", "escape": '"'}


def _with_ingest_columns(df: DataFrame) -> DataFrame:
    return df.withColumn("_ingested_at", F.current_timestamp()).withColumn("_source_file", F.col("_metadata.file_path"))


def _feed_table(source: str, comment: str) -> None:
    @dp.table(name=source, comment=comment)
    def _feed() -> DataFrame:
        raw = (
            spark.readStream.format("cloudFiles")
            .option("cloudFiles.format", "csv")
            .option("cloudFiles.inferColumnTypes", "false")
            .options(**CSV_OPTIONS)
            .load(f"{LANDING}/{source}/")
        )
        return _with_ingest_columns(raw)


def _reference_csv(table: str, folder: str, comment: str) -> None:
    @dp.materialized_view(name=table, comment=comment)
    def _ref() -> DataFrame:
        return _with_ingest_columns(spark.read.options(**CSV_OPTIONS).csv(f"{LANDING}/reference/{folder}/"))


for _source, _comment in FEEDS.items():
    _feed_table(_source, _comment)

for _table, (_folder, _comment) in REFERENCE_CSV.items():
    _reference_csv(_table, _folder, _comment)


@dp.materialized_view(name="ref_nadac_prices", comment="Recent NADAC weekly prices for candidate formulary NDCs (CMS).")
def ref_nadac_prices() -> DataFrame:
    raw = spark.read.option("multiLine", "true").json(f"{LANDING}/reference/nadac_prices/")
    return (
        raw.select(F.explode("results").alias("r"), F.col("_metadata.file_path").alias("_source_file"))
        .select("r.*", "_source_file")
        .withColumn("_ingested_at", F.current_timestamp())
    )
