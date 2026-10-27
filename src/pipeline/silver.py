"""Silver layer: typed and validated synthetic events, quarantine, compliance flags, SCD2 staff history.

Two kinds of rules, kept apart:
  1. Data quality expectations (bad data). Rows that fail go to adc_events_quarantine with the failed rule names.
  2. Compliance flags (valid but suspicious events). Boolean columns on adc_events. The events stay in the table.

Pipeline configuration (resources/pipeline.yml):
  rx.catalog, rx.silver_schema      where silver tables are published (bronze is the pipeline default schema)
  rx.window_start, rx.window_end    simulation window, end exclusive
  rx.waste_delay_minutes            threshold for is_delayed_waste
"""

from pyspark import pipelines as dp
from pyspark.sql import Column, DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from rules import adc_rules_sql

spark = SparkSession.getActiveSession()
CATALOG = spark.conf.get("rx.catalog")
SILVER = spark.conf.get("rx.silver_schema")
WINDOW_START = spark.conf.get("rx.window_start")
WINDOW_END = spark.conf.get("rx.window_end")
WASTE_DELAY_MINUTES = int(spark.conf.get("rx.waste_delay_minutes"))

ADC_RULES = adc_rules_sql(WINDOW_START, WINDOW_END)
PULL_TYPES = ["PULL", "OVERRIDE"]
TS_FORMAT = "yyyy-MM-dd HH:mm:ss"


def silver(table: str) -> str:
    return f"`{CATALOG}`.{SILVER}.{table}"


def ts(col: str) -> Column:
    return F.expr(f"try_to_timestamp({col}, '{TS_FORMAT}')")


def num(col: str) -> Column:
    return F.expr(f"try_cast({col} AS DOUBLE)")


# ADC events: typing and data quality


@dp.materialized_view(name="adc_events_checked", private=True, comment="Typed ADC events with failed rule names.")
@dp.expect_all(ADC_RULES)
def adc_events_checked() -> DataFrame:
    formulary = spark.read.table("ref_formulary").select(
        "ndc", "drug_key", "dea_schedule", F.lit(True).alias("_ndc_known")
    )
    cabinets = spark.read.table("ref_cabinets").select(
        "cabinet_id", F.col("unit_id").alias("cabinet_unit_id"), F.lit(True).alias("_cabinet_known")
    )
    typed = spark.read.table("adc_events").select(
        "event_id",
        F.col("event_ts").alias("event_ts_raw"),
        ts("event_ts").alias("event_ts"),
        "event_type",
        "facility_id",
        "cabinet_id",
        "ndc",
        "user_id",
        "witness_id",
        "patient_id",
        "admission_id",
        "order_id",
        F.col("quantity").alias("quantity_raw"),
        num("quantity").alias("quantity"),
        num("expected_quantity").alias("expected_quantity"),
        "related_event_id",
        "resolution_code",
        "_source_file",
        "_ingested_at",
    )
    checked = typed.join(formulary, "ndc", "left").join(cabinets, "cabinet_id", "left")
    failed = F.array(*[F.when(~F.expr(sql), F.lit(name)) for name, sql in ADC_RULES.items()])
    return checked.withColumn("failed_rules", F.filter(failed, lambda x: x.isNotNull()))


@dp.materialized_view(
    name=silver("adc_events_quarantine"),
    comment="ADC rows that failed a data quality rule, with the rule names. Raw values kept as received (synthetic).",
)
def adc_events_quarantine() -> DataFrame:
    return (
        spark.read.table("adc_events_checked")
        .filter(F.size("failed_rules") > 0)
        .select(
            "failed_rules",
            "event_id",
            "event_ts_raw",
            "event_type",
            "facility_id",
            "cabinet_id",
            "ndc",
            "user_id",
            "witness_id",
            "patient_id",
            "admission_id",
            "order_id",
            "quantity_raw",
            "related_event_id",
            "_source_file",
            "_ingested_at",
        )
    )


# Context: patient stays, orders, staff assignment history


@dp.materialized_view(
    name=silver("patient_stays"), comment="One row per admission per unit stay, from ADT (synthetic)."
)
@dp.expect("stay_end_after_start", "end_ts IS NULL OR end_ts >= start_ts")
def patient_stays() -> DataFrame:
    adt = spark.read.table("adt_events").select(
        "adt_event_id",
        "event_type",
        ts("event_ts").alias("event_ts"),
        "admission_id",
        "patient_id",
        "facility_id",
        "unit_id",
    )
    w = Window.partitionBy("admission_id").orderBy("event_ts", "adt_event_id")
    return (
        adt.withColumn("end_ts", F.lead("event_ts").over(w))
        .withColumn("end_reason", F.lead("event_type").over(w))
        .filter(F.col("event_type").isin("ADMIT", "TRANSFER"))
        .select(
            "admission_id",
            "patient_id",
            "facility_id",
            "unit_id",
            F.col("event_ts").alias("start_ts"),
            "end_ts",
            "end_reason",
        )
    )


@dp.materialized_view(name=silver("orders"), comment="One row per controlled substance order (synthetic).")
@dp.expect("order_has_start", "new_ts IS NOT NULL")
def orders() -> DataFrame:
    ev = spark.read.table("order_events").withColumn("event_ts", ts("event_ts"))

    def at(kind: str, col: str) -> Column:
        return F.max(F.when(F.col("event_type") == kind, F.col(col)))

    return ev.groupBy("order_id").agg(
        at("NEW", "admission_id").alias("admission_id"),
        at("NEW", "patient_id").alias("patient_id"),
        at("NEW", "facility_id").alias("facility_id"),
        at("NEW", "unit_id").alias("unit_id"),
        at("NEW", "ndc").alias("ndc"),
        at("NEW", "dose_mg").cast("double").alias("dose_mg"),
        (at("NEW", "is_prn") == "true").alias("is_prn"),
        at("NEW", "interval_h").cast("double").alias("interval_h"),
        at("NEW", "event_ts").alias("new_ts"),
        at("VERIFY", "event_ts").alias("verify_ts"),
        at("VERIFY", "pharmacist_id").alias("pharmacist_id"),
        at("DISCONTINUE", "event_ts").alias("stop_ts"),
    )


@dp.temporary_view(name="staff_assignments_typed")
def staff_assignments_typed() -> DataFrame:
    return spark.readStream.table("staff_assignments").select(
        "user_id",
        "display_name",
        "role",
        "facility_id",
        "unit_id",
        "shift",
        ts("effective_ts").alias("effective_ts"),
        F.col("sequence_num").cast("int").alias("sequence_num"),
        "change_type",
    )


dp.create_streaming_table(
    name=silver("staff_unit_scd2"),
    comment="Staff unit and shift history, SCD Type 2. __START_AT/__END_AT are effective timestamps (synthetic).",
)
dp.create_auto_cdc_flow(
    target=silver("staff_unit_scd2"),
    source="staff_assignments_typed",
    keys=["user_id"],
    sequence_by="effective_ts",
    stored_as_scd_type=2,
)


# ADC events: valid rows with event-time context and compliance flags


@dp.materialized_view(
    name=silver("adc_events"),
    comment="Valid ADC events with the staff assignment at event time and compliance flags (synthetic).",
)
def adc_events() -> DataFrame:
    valid = spark.read.table("adc_events_checked").filter(F.size("failed_rules") == 0)
    window_end = F.lit(f"{WINDOW_END} 00:00:00").cast("timestamp")
    is_pull = F.col("event_type").isin(PULL_TYPES)

    pulls = valid.filter(is_pull).select(F.col("event_id").alias("_pull_id"), F.col("event_ts").alias("_pull_ts"))
    orders_ = spark.read.table(silver("orders")).select(
        F.col("order_id").alias("_order_id"),
        F.col("ndc").alias("_order_ndc"),
        F.col("new_ts").alias("_order_start"),
        F.col("stop_ts").alias("_order_stop"),
    )
    stays = spark.read.table(silver("patient_stays"))
    discharges = (
        stays.filter(F.col("end_reason") == "DISCHARGE")
        .groupBy("admission_id")
        .agg(F.max("end_ts").alias("_discharge_ts"))
    )
    # When the patient last left each unit. NULL while they are still there.
    left_unit = stays.groupBy("admission_id", F.col("unit_id").alias("cabinet_unit_id")).agg(
        F.when(F.count(F.when(F.col("end_ts").isNull(), 1)) > 0, F.lit(None).cast("timestamp"))
        .otherwise(F.max("end_ts"))
        .alias("_left_unit_ts")
    )
    scd = spark.read.table(silver("staff_unit_scd2")).select(
        F.col("user_id").alias("_scd_user"),
        F.col("facility_id").alias("assigned_facility_id"),
        F.col("unit_id").alias("assigned_unit_id"),
        F.col("shift").alias("assigned_shift"),
        F.col("role").alias("user_role"),
        F.col("__START_AT").alias("_scd_start"),
        F.coalesce(F.col("__END_AT"), window_end).alias("_scd_end"),
    )

    ev = (
        valid.join(pulls, F.col("related_event_id") == F.col("_pull_id"), "left")
        .join(orders_, F.col("order_id") == F.col("_order_id"), "left")
        .join(discharges, "admission_id", "left")
        .join(left_unit, ["admission_id", "cabinet_unit_id"], "left")
        .join(
            scd,
            (F.col("user_id") == F.col("_scd_user"))
            & (F.col("event_ts") >= F.col("_scd_start"))
            & (F.col("event_ts") < F.col("_scd_end")),
            "left",
        )
    )
    minutes_to_waste = F.when(
        F.col("event_type") == "WASTE",
        (F.unix_timestamp("event_ts") - F.unix_timestamp("_pull_ts")) / 60.0,
    )
    order_active = (
        F.col("_order_id").isNotNull()
        & (F.col("_order_ndc") == F.col("ndc"))
        & (F.col("event_ts") >= F.col("_order_start"))
        & (F.col("event_ts") < F.coalesce(F.col("_order_stop"), window_end))
    )
    after_discharge = is_pull & F.col("_discharge_ts").isNotNull() & (F.col("event_ts") > F.col("_discharge_ts"))
    return ev.select(
        "event_id",
        "event_ts",
        F.to_date("event_ts").alias("event_date"),
        "event_type",
        "facility_id",
        "cabinet_id",
        "cabinet_unit_id",
        "ndc",
        "drug_key",
        "dea_schedule",
        "user_id",
        "user_role",
        "assigned_facility_id",
        "assigned_unit_id",
        "assigned_shift",
        "witness_id",
        "patient_id",
        "admission_id",
        "order_id",
        "quantity",
        "expected_quantity",
        "related_event_id",
        "resolution_code",
        F.round(minutes_to_waste, 1).alias("minutes_to_waste"),
        (F.col("event_type") == "OVERRIDE").alias("is_override"),
        ((F.col("event_type") == "WASTE") & F.col("witness_id").isNull()).alias("is_unwitnessed_waste"),
        F.coalesce(minutes_to_waste > WASTE_DELAY_MINUTES, F.lit(False)).alias("is_delayed_waste"),
        (is_pull & ~F.coalesce(order_active, F.lit(False))).alias("is_pull_without_active_order"),
        F.coalesce(after_discharge, F.lit(False)).alias("is_pull_after_discharge"),
        F.coalesce(
            is_pull
            & ~after_discharge
            & F.col("_left_unit_ts").isNotNull()
            & (F.col("event_ts") > F.col("_left_unit_ts")),
            F.lit(False),
        ).alias("is_pull_after_transfer"),
        "_source_file",
    )
