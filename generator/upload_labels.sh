#!/usr/bin/env bash
# Upload the injected labels (synthetic) to the eval schema's labels volume.
# This is the only way labels reach Databricks. Only src/eval/evaluate.py reads them.
#
# Usage: DATABRICKS_CONFIG_PROFILE=<profile> generator/upload_labels.sh   (TARGET=dev by default)
set -euo pipefail
target=${TARGET:-dev}
cd "$(dirname "$0")/.."
volume=$(databricks bundle validate -t "$target" -o json \
  | uv run python -c 'import json, sys; print(json.load(sys.stdin)["resources"]["volumes"]["labels"]["volume_path"])')
databricks fs cp --overwrite data/labels/injected_labels.csv "dbfs:$volume/injected_labels.csv"
echo "uploaded labels to dbfs:$volume/injected_labels.csv"
