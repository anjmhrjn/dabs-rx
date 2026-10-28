"""Job task: load injected labels, compute precision@k and recall@k for peer-grouped vs baseline ranks.

The only code that reads labels. Writes eval.injected_labels (replaced) and appends to eval.metrics.
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
from metrics import evaluate

METRICS_SCHEMA = T.StructType(
    [
        T.StructField("run_id", T.StringType()),
        T.StructField("evaluated_at", T.TimestampType()),
        T.StructField("report_month", T.StringType()),
        T.StructField("method", T.StringType()),
        T.StructField("k_fraction", T.DoubleType()),
        T.StructField("k", T.LongType()),
        T.StructField("positives", T.LongType()),
        T.StructField("true_positives", T.LongType()),
        T.StructField("precision", T.DoubleType()),
        T.StructField("recall", T.DoubleType()),
    ]
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", required=True)
    p.add_argument("--gold-schema", required=True)
    p.add_argument("--eval-schema", required=True)
    p.add_argument("--labels-path", required=True, help="folder in the eval volume holding injected_labels.csv")
    p.add_argument("--top-k-fractions", required=True, help="comma-separated, e.g. 0.05,0.10")
    p.add_argument("--run-id", required=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    spark = SparkSession.builder.getOrCreate()
    gold, ev = f"`{args.catalog}`.{args.gold_schema}", f"`{args.catalog}`.{args.eval_schema}"

    labels_df = spark.read.option("header", "true").csv(args.labels_path)
    labels_df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{ev}.injected_labels")
    labels = [r.asDict() for r in labels_df.collect()]

    ranks: dict = defaultdict(dict)
    risk = spark.read.table(f"{gold}.user_risk_monthly").filter("scored").collect()
    for method, col in (("peer", "peer_rank"), ("baseline", "baseline_rank")):
        by_month = defaultdict(list)
        for r in risk:
            by_month[r["report_month"]].append((r[col], r["user_id"]))
        for month, pairs in by_month.items():
            ranks[month][method] = [u for _, u in sorted(pairs)]

    fractions = [float(f) for f in args.top_k_fractions.split(",")]
    rows = evaluate(ranks, labels, fractions)
    now = datetime.now(UTC).replace(tzinfo=None)
    data = [(args.run_id, now, *(r[f.name] for f in METRICS_SCHEMA.fields[2:])) for r in rows]
    spark.createDataFrame(data, METRICS_SCHEMA).write.mode("append").saveAsTable(f"{ev}.metrics")

    print(f"{'month':8} {'method':9} {'top':>4} {'k':>3} {'pos':>4} {'hits':>4} {'precision':>9} {'recall':>7}")
    for r in rows:
        print(
            f"{r['report_month'][:7]:8} {r['method']:9} {r['k_fraction'] * 100:3.0f}% {r['k']:3} {r['positives']:4} "
            f"{r['true_positives']:4} {r['precision']:9.4f} {r['recall']:7.4f}"
        )


if __name__ == "__main__":
    main()
