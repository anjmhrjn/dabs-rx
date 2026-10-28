"""Job task: create the access table, mask function, row filter view, and reporting views (sql/governance/).

Runs the SQL files in name order. ${catalog}, ${silver}, ${gold}, and ${reporting} are replaced with fully
qualified names. Statements end with a semicolon at the end of a line; full-line -- comments are dropped first.
"""

import argparse
import os
import sys
from pathlib import Path

from pyspark.sql import SparkSession

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
except NameError:  # __file__ is not always set when a job runs a Python file
    pass
from sql_files import render, statements


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", required=True)
    p.add_argument("--silver-schema", required=True)
    p.add_argument("--gold-schema", required=True)
    p.add_argument("--reporting-schema", required=True)
    p.add_argument("--sql-dir", required=True, help="folder with the numbered governance SQL files")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    spark = SparkSession.builder.getOrCreate()
    schemas = {"silver": args.silver_schema, "gold": args.gold_schema, "reporting": args.reporting_schema}
    for path in sorted(Path(args.sql_dir).glob("*.sql")):
        for stmt in statements(render(path.read_text(), args.catalog, schemas)):
            spark.sql(stmt)
            print(f"{path.name}: {' '.join(stmt.split()[:6])} ...")


if __name__ == "__main__":
    main()
