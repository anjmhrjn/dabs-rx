#!/usr/bin/env bash
# Upload simulated daily files (synthetic data) and reference files to the bundle's landing volume.
#
# Usage: DATABRICKS_CONFIG_PROFILE=<profile> generator/upload.sh <first_day> <last_day>
#   TARGET=dev (default) or prod picks the bundle target, and so the volume.
#
# Daily files are never overwritten, so Auto Loader only ever sees new files.
# Reference files are small and are replaced on every upload.
# Labels (data/labels/) are never uploaded.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <first_day> <last_day>" >&2
  exit 1
fi
first=$1
last=$2
target=${TARGET:-dev}
cd "$(dirname "$0")/.."

volume=$(databricks bundle validate -t "$target" -o json \
  | uv run python -c 'import json, sys; print(json.load(sys.stdin)["resources"]["volumes"]["landing"]["volume_path"])')
echo "target $target, landing volume $volume"

staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
uv run python -m generator.upload_files "$first" "$last" "$staging"

databricks fs cp -r --concurrency 16 "$staging/daily" "dbfs:$volume"
databricks fs cp -r --overwrite "$staging/reference" "dbfs:$volume"
echo "uploaded to dbfs:$volume"
