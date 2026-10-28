"""Job task: append each month's top-ranked nurses and reason codes to gold.monthly_audit_snapshot.

This is the list pharmacy leadership reviews. The table is a regular Delta table and is only ever appended to,
keyed by report_month and run_id, so Delta time travel answers "what did the report show on date X".
"""

import argparse

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", required=True)
    p.add_argument("--gold-schema", required=True)
    p.add_argument("--audit-fraction", type=float, required=True)
    p.add_argument("--run-id", required=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    spark = SparkSession.builder.getOrCreate()
    gold = f"`{args.catalog}`.{args.gold_schema}"
    risk = spark.read.table(f"{gold}.user_risk_monthly").filter("scored")
    # Top fraction of scored nurses that month, rounded up, at least 1.
    k = F.greatest(F.lit(1), F.ceil(F.col("scored_users") * F.lit(args.audit_fraction) - F.lit(1e-9)))
    snapshot = risk.filter(F.col("peer_rank") <= k).select(
        "report_month",
        F.lit(args.run_id).alias("run_id"),
        F.current_timestamp().alias("snapshot_ts"),
        F.col("peer_rank").alias("rank"),
        "user_id",
        "facility_id",
        "unit_id",
        "shift",
        "pulls",
        "peer_score",
        F.col("peer_reason_codes").alias("reason_codes"),
    )
    snapshot.write.mode("append").saveAsTable(f"{gold}.monthly_audit_snapshot")
    for row in snapshot.groupBy("report_month").count().orderBy("report_month").collect():
        print(f"{row['report_month']}: {row['count']} nurses on the review list")


if __name__ == "__main__":
    main()
