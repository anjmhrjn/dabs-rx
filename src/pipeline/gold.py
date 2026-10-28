"""Gold layer: report-ready marts over silver (synthetic data).

Reads silver tables and bronze ref_* tables only. Never reads the eval schema or labels.

Pipeline configuration (resources/pipeline.yml):
  rx.catalog, rx.silver_schema, rx.gold_schema
  rx.window_start           first simulated day (inventory calendar starts here)
  rx.usage_lookback_days    trailing days for average daily usage and days on hand
"""

from pyspark import pipelines as dp
from pyspark.sql import Column, DataFrame, SparkSession, Window
from pyspark.sql import functions as F

spark = SparkSession.getActiveSession()
CATALOG = spark.conf.get("rx.catalog")
SILVER = spark.conf.get("rx.silver_schema")
GOLD = spark.conf.get("rx.gold_schema")
WINDOW_START = spark.conf.get("rx.window_start")
USAGE_LOOKBACK_DAYS = int(spark.conf.get("rx.usage_lookback_days"))

PULL_TYPES = ["PULL", "OVERRIDE"]
# Signed effect of each event type on cabinet stock.
STOCK_SIGN = {"RESTOCK": 1, "RETURN": 1, "CANCEL": 1, "PULL": -1, "OVERRIDE": -1}
FLAGS = [
    "is_override",
    "is_unwitnessed_waste",
    "is_delayed_waste",
    "is_pull_without_active_order",
    "is_pull_after_discharge",
    "is_pull_after_transfer",
]


def silver(table: str) -> str:
    return f"`{CATALOG}`.{SILVER}.{table}"


def gold(table: str) -> str:
    return f"`{CATALOG}`.{GOLD}.{table}"


def ratio(num: Column, den: Column) -> Column:
    return F.when(den > 0, num / den)


def events() -> DataFrame:
    return spark.read.table(silver("adc_events"))


def formulary() -> DataFrame:
    return spark.read.table("ref_formulary").select(
        "drug_key",
        "ndc",
        "display_name",
        "dea_schedule",
        F.col("unit_cost").cast("double").alias("unit_cost"),
        "pricing_unit",
        "cost_source",
    )


def flag(col: str) -> Column:
    return F.sum(F.col(col).cast("int"))


@dp.materialized_view(
    name=gold("daily_discrepancies"),
    comment="Cabinet count discrepancies by day, facility, cabinet, and drug, with resolution status (synthetic).",
)
def daily_discrepancies() -> DataFrame:
    ev = events()
    counts = ev.filter((F.col("event_type") == "COUNT") & (F.col("quantity") != F.col("expected_quantity")))
    resolutions = ev.filter(F.col("event_type") == "DISCREPANCY_RESOLVED").select(
        F.col("related_event_id").alias("_count_id"),
        F.col("event_ts").alias("_resolved_ts"),
        F.col("resolution_code").alias("_resolution_code"),
    )
    hours = (F.unix_timestamp("_resolved_ts") - F.unix_timestamp("event_ts")) / 3600.0
    return (
        counts.join(resolutions, F.col("event_id") == F.col("_count_id"), "left")
        .groupBy(F.col("event_date").alias("report_date"), "facility_id", "cabinet_id", "cabinet_unit_id", "ndc")
        .agg(
            F.count("*").alias("discrepancies"),
            F.count("_count_id").alias("resolved"),
            (F.count("*") - F.count("_count_id")).alias("unresolved"),
            F.sum(F.col("quantity") - F.col("expected_quantity")).alias("net_variance_units"),
            F.round(F.percentile(hours, F.lit(0.5)), 1).alias("median_hours_to_resolve"),
            F.array_sort(F.collect_set("_resolution_code")).alias("resolution_codes"),
        )
        .join(formulary().select("ndc", "drug_key", "display_name", "dea_schedule"), "ndc", "left")
    )


@dp.materialized_view(
    name=gold("daily_compliance_events"),
    comment="Flagged ADC events by day, facility, unit, and flag type (synthetic).",
)
def daily_compliance_events() -> DataFrame:
    stack = ", ".join(f"'{f.removeprefix('is_')}', {f}" for f in FLAGS)
    return (
        events()
        .select(
            F.col("event_date").alias("report_date"),
            "facility_id",
            F.col("cabinet_unit_id").alias("unit_id"),
            "user_id",
            "patient_id",
            F.expr(f"stack({len(FLAGS)}, {stack}) AS (flag_type, flagged)"),
        )
        .filter("flagged")
        .groupBy("report_date", "facility_id", "unit_id", "flag_type")
        .agg(
            F.count("*").alias("events"),
            F.countDistinct("user_id").alias("users"),
            F.countDistinct("patient_id").alias("patients"),
        )
    )


@dp.materialized_view(
    name=gold("user_activity_monthly"),
    comment=(
        "Per nurse, per month, per peer group (facility, unit, shift at event time) activity features used for "
        "screening. Synthetic data. A screening aid, not a determination."
    ),
)
def user_activity_monthly() -> DataFrame:
    keys = [
        "user_id",
        "report_month",
        F.col("assigned_facility_id").alias("facility_id"),
        F.col("assigned_unit_id").alias("unit_id"),
        F.col("assigned_shift").alias("shift"),
    ]
    ev = events().filter(F.col("user_role") == "RN").withColumn("report_month", F.trunc("event_date", "month"))
    is_pull = F.col("event_type").isin(PULL_TYPES)
    is_waste = F.col("event_type") == "WASTE"

    base = ev.groupBy(*keys).agg(
        F.sum(is_pull.cast("int")).alias("pulls"),
        F.sum(F.when(is_pull, F.col("quantity"))).alias("pulled_units"),
        F.countDistinct(F.when(is_pull, F.concat_ws("|", "patient_id", "event_date"))).alias("patient_days"),
        F.sum(is_waste.cast("int")).alias("wastes"),
        F.sum(F.when(is_waste, F.col("quantity"))).alias("wasted_units"),
        flag("is_unwitnessed_waste").alias("unwitnessed_wastes"),
        F.percentile(F.when(is_waste, F.col("minutes_to_waste")), F.lit(0.5)).alias("median_minutes_to_waste"),
        flag("is_delayed_waste").alias("delayed_wastes"),
        flag("is_override").alias("overrides"),
        F.sum((F.col("event_type") == "CANCEL").cast("int")).alias("cancels"),
        flag("is_pull_without_active_order").alias("pulls_without_active_order"),
        flag("is_pull_after_discharge").alias("post_discharge_pulls"),
        flag("is_pull_after_transfer").alias("post_transfer_pulls"),
    )

    group_cols = ["user_id", "report_month", "facility_id", "unit_id", "shift"]
    by_witness = (
        ev.filter(is_waste & F.col("witness_id").isNotNull())
        .groupBy(*keys, "witness_id")
        .agg(F.count("*").alias("n"))
        .groupBy(*group_cols)
        .agg(
            (F.max("n") / F.sum("n")).alias("witness_concentration"),
            F.max_by("witness_id", F.struct("n", "witness_id")).alias("top_witness_id"),
            F.count("*").alias("distinct_witnesses"),
        )
    )

    return base.join(by_witness, group_cols, "left").select(
        *group_cols,
        "pulls",
        "patient_days",
        F.round(ratio(F.col("pulls"), F.col("patient_days")), 3).alias("pulls_per_patient_day"),
        "wastes",
        F.round(ratio(F.col("wasted_units"), F.col("pulled_units")), 4).alias("waste_ratio"),
        "unwitnessed_wastes",
        F.round(ratio(F.col("unwitnessed_wastes"), F.col("wastes")), 4).alias("unwitnessed_waste_rate"),
        F.round("median_minutes_to_waste", 1).alias("median_minutes_to_waste"),
        "delayed_wastes",
        "overrides",
        F.round(ratio(F.col("overrides"), F.col("pulls")), 4).alias("override_rate"),
        "cancels",
        F.round(ratio(F.col("cancels"), F.col("pulls")), 4).alias("cancel_rate"),
        "pulls_without_active_order",
        F.round(ratio(F.col("pulls_without_active_order"), F.col("pulls")), 4).alias("no_active_order_rate"),
        "post_discharge_pulls",
        "post_transfer_pulls",
        F.round(ratio(F.col("post_discharge_pulls") + F.col("post_transfer_pulls"), F.col("pulls")), 4).alias(
            "pulls_after_patient_left_rate"
        ),
        F.round("witness_concentration", 4).alias("witness_concentration"),
        "top_witness_id",
        F.coalesce("distinct_witnesses", F.lit(0)).alias("distinct_witnesses"),
    )


@dp.materialized_view(
    name=gold("inventory_daily"),
    comment=(
        "End-of-day on hand, usage, days on hand, and below-par flag per cabinet pocket (synthetic). On hand comes "
        "from the cabinet's last system count of the day plus later movements."
    ),
)
def inventory_daily() -> DataFrame:
    ev = events()
    pockets = spark.read.table("ref_cabinet_par").select(
        "cabinet_id",
        "ndc",
        "drug_key",
        F.col("par_level").cast("int").alias("par_level"),
        F.col("restock_below").cast("int").alias("restock_below"),
    )
    cabinets = spark.read.table("ref_cabinets").select("cabinet_id", "facility_id", "unit_id")
    calendar = ev.agg(F.max("event_date").alias("_last")).select(
        F.explode(F.sequence(F.to_date(F.lit(WINDOW_START)), F.col("_last"))).alias("report_date")
    )

    sign = F.create_map(*[x for k, v in STOCK_SIGN.items() for x in (F.lit(k), F.lit(v))])
    moves = ev.filter(F.col("event_type").isin(list(STOCK_SIGN))).withColumn(
        "delta", F.col("quantity") * sign[F.col("event_type")]
    )
    counts = ev.filter(F.col("event_type") == "COUNT")
    last_count = counts.groupBy("cabinet_id", "ndc", "event_date").agg(
        F.max_by("expected_quantity", F.struct("event_ts", "event_id")).alias("_count_qty"),
        F.max(F.struct("event_ts", "event_id")).alias("_count_key"),
    )
    pocket_day = ["cabinet_id", "ndc", "event_date"]
    daily_moves = (
        moves.join(last_count, pocket_day, "left")
        .groupBy(*pocket_day)
        .agg(
            F.sum("delta").alias("_day_delta"),
            F.sum(F.when(F.struct("event_ts", "event_id") > F.col("_count_key"), F.col("delta")).otherwise(0)).alias(
                "_after_count"
            ),
            F.sum(F.when(F.col("event_type").isin(PULL_TYPES), F.col("quantity")).otherwise(0)).alias("_pulled"),
            F.sum(F.when(F.col("event_type").isin("RETURN", "CANCEL"), F.col("quantity")).otherwise(0)).alias("_back"),
        )
    )

    grid = (
        pockets.crossJoin(calendar)
        .join(daily_moves.withColumnRenamed("event_date", "report_date"), ["cabinet_id", "ndc", "report_date"], "left")
        .join(last_count.withColumnRenamed("event_date", "report_date"), ["cabinet_id", "ndc", "report_date"], "left")
        .fillna(0, ["_day_delta", "_after_count", "_pulled", "_back"])
    )
    w = Window.partitionBy("cabinet_id", "ndc").orderBy("report_date")
    # on_hand(d) = K(d) + cum(d), where K is the last (count-derived on hand - cum) seen up to day d.
    cum = F.sum("_day_delta").over(w.rowsBetween(Window.unboundedPreceding, 0))
    from_count = F.col("_count_qty") + F.col("_after_count")
    usage = F.col("_pulled") - F.col("_back")
    avg_usage = F.avg(usage).over(w.rowsBetween(-(USAGE_LOOKBACK_DAYS - 1), 0))
    with_on_hand = (
        grid.withColumn("_cum", cum)
        .withColumn("_k", F.last(from_count - F.col("_cum"), ignorenulls=True).over(w))
        .withColumn("on_hand", F.col("_k") + F.col("_cum"))
        .withColumn("daily_usage", usage)
        .withColumn("avg_daily_usage", F.round(avg_usage, 3))
    )
    return with_on_hand.join(cabinets, "cabinet_id").select(
        "report_date",
        "facility_id",
        "unit_id",
        "cabinet_id",
        "ndc",
        "drug_key",
        "on_hand",
        "daily_usage",
        "avg_daily_usage",
        F.round(ratio(F.col("on_hand"), F.col("avg_daily_usage")), 1).alias("days_on_hand"),
        "par_level",
        "restock_below",
        (F.col("on_hand") < F.col("restock_below")).alias("below_par"),
    )


@dp.materialized_view(
    name=gold("shortage_exposure"),
    comment="Formulary items on the current FDA shortage list, with on hand and days on hand per facility.",
)
def shortage_exposure() -> DataFrame:
    shortages = (
        spark.read.table("ref_shortages")
        .filter((F.col("status") == "Current") & F.col("drug_key").isNotNull())
        .groupBy("drug_key")
        .agg(
            F.count("*").alias("shortage_records"),
            F.array_sort(F.collect_set("availability")).alias("availability"),
            F.max(F.to_date("update_date", "MM/dd/yyyy")).alias("latest_update_date"),
        )
    )
    inv = spark.read.table(gold("inventory_daily"))
    latest = inv.agg(F.max("report_date").alias("as_of_date"))
    stock = (
        inv.join(latest, F.col("report_date") == F.col("as_of_date"))
        .groupBy("as_of_date", "facility_id", "drug_key")
        .agg(F.sum("on_hand").alias("on_hand_units"), F.sum("avg_daily_usage").alias("avg_daily_usage"))
    )
    return (
        shortages.join(stock, "drug_key")
        .join(formulary().select("drug_key", "ndc", "display_name", "dea_schedule"), "drug_key")
        .select(
            "as_of_date",
            "facility_id",
            "drug_key",
            "display_name",
            "ndc",
            "dea_schedule",
            "shortage_records",
            "availability",
            "latest_update_date",
            "on_hand_units",
            F.round("avg_daily_usage", 2).alias("avg_daily_usage"),
            F.round(ratio(F.col("on_hand_units"), F.col("avg_daily_usage")), 1).alias("days_on_hand"),
        )
    )


@dp.materialized_view(
    name=gold("procurement_monthly"),
    comment="Restocked units and estimated spend by month, facility, drug, and schedule (synthetic volumes, NADAC costs).",
)
def procurement_monthly() -> DataFrame:
    dispense = spark.read.table("ref_dispense_units").select(
        "drug_key", "unit_of_issue", F.col("unit_volume_ml").cast("double").alias("unit_volume_ml")
    )
    costs = (
        formulary()
        .join(dispense, "drug_key")
        .withColumn(
            "unit_cost_per_issue_unit",
            F.col("unit_cost") * F.when(F.col("pricing_unit") == "ML", F.col("unit_volume_ml")).otherwise(F.lit(1.0)),
        )
    )
    return (
        events()
        .filter(F.col("event_type") == "RESTOCK")
        .withColumn("report_month", F.trunc("event_date", "month"))
        .join(costs.drop("ndc", "dea_schedule"), "drug_key")
        .groupBy(
            "report_month",
            "facility_id",
            "drug_key",
            "display_name",
            "ndc",
            "dea_schedule",
            "unit_of_issue",
            "unit_cost_per_issue_unit",
            "cost_source",
        )
        .agg(F.sum("quantity").alias("restocked_units"), F.count("*").alias("restock_events"))
        .withColumn("est_spend", F.round(F.col("restocked_units") * F.col("unit_cost_per_issue_unit"), 2))
        .withColumn("unit_cost_per_issue_unit", F.round("unit_cost_per_issue_unit", 4))
    )
