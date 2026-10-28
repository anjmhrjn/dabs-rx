"""Job task: score nurses per month against peers and against everyone, write gold.user_risk_monthly.

Reads gold.user_activity_monthly only. Never reads the eval schema or labels (tests/test_label_isolation.py).
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime

from pyspark.sql import SparkSession
from pyspark.sql import types as T

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError:  # __file__ is not always set when a job runs a Python file
    pass
from scoring import ScoreConfig, score_month

SCHEMA = T.StructType(
    [
        T.StructField("report_month", T.DateType()),
        T.StructField("user_id", T.StringType()),
        T.StructField("facility_id", T.StringType()),
        T.StructField("unit_id", T.StringType()),
        T.StructField("shift", T.StringType()),
        T.StructField("pulls", T.LongType()),
        T.StructField("scored", T.BooleanType()),
        T.StructField("scored_users", T.LongType()),
        T.StructField("peer_n", T.LongType()),
        T.StructField("peer_score", T.DoubleType()),
        T.StructField("peer_rank", T.LongType()),
        T.StructField("peer_reason_codes", T.ArrayType(T.StringType())),
        T.StructField("baseline_score", T.DoubleType()),
        T.StructField("baseline_rank", T.LongType()),
        T.StructField("baseline_reason_codes", T.ArrayType(T.StringType())),
        T.StructField("feature_z", T.MapType(T.StringType(), T.DoubleType())),
        T.StructField("run_id", T.StringType()),
        T.StructField("scored_at", T.TimestampType()),
    ]
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", required=True)
    p.add_argument("--gold-schema", required=True)
    p.add_argument("--features", required=True, help="comma-separated feature columns")
    p.add_argument("--z-cap", type=float, required=True)
    p.add_argument("--min-pulls", type=int, required=True)
    p.add_argument("--min-wastes", type=int, required=True)
    p.add_argument("--min-peers", type=int, required=True)
    p.add_argument("--reason-min-z", type=float, required=True)
    p.add_argument("--run-id", required=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    spark = SparkSession.builder.getOrCreate()
    gold = f"`{args.catalog}`.{args.gold_schema}"
    cfg = ScoreConfig(
        features=tuple(f.strip() for f in args.features.split(",")),
        z_cap=args.z_cap,
        min_pulls=args.min_pulls,
        min_wastes=args.min_wastes,
        min_peers=args.min_peers,
        reason_min_z=args.reason_min_z,
    )
    months: dict = defaultdict(list)
    for row in spark.read.table(f"{gold}.user_activity_monthly").collect():
        months[row["report_month"]].append(row.asDict())

    scored_at = datetime.now(UTC).replace(tzinfo=None)
    out = []
    for month in sorted(months):
        for r in score_month(months[month], cfg):
            out.append({**r, "report_month": month, "run_id": args.run_id, "scored_at": scored_at})
    df = spark.createDataFrame([tuple(r.get(f.name) for f in SCHEMA) for r in out], SCHEMA)
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{gold}.user_risk_monthly")

    print(f"scored {sum(r['scored'] for r in out)} user-months, {sum(not r['scored'] for r in out)} below the floor")
    for month in sorted(months):
        top = [r for r in out if r["report_month"] == month and r["scored"]][:3]
        print(f"{month}: top 3 by peer score: " + ", ".join(f"{r['user_id']} {r['peer_score']:.1f}" for r in top))


if __name__ == "__main__":
    main()
