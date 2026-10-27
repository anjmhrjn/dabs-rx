"""Fetch public reference data for the formulary. Runs locally only.

Databricks Free Edition restricts outbound internet, so nothing on Databricks calls these APIs.
This script saves raw responses under data/reference/; build_formulary.py turns them into CSVs.

Sources:
  openFDA NDC directory   https://api.fda.gov/drug/ndc.json        (NDC, dea_schedule)
  openFDA drug shortages  https://api.fda.gov/drug/shortages.json  (current FDA shortage list)
  NADAC (CMS Medicaid)    https://data.medicaid.gov                 (unit cost)

Run: uv run python -m generator.fetch_reference
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from generator.build_formulary import candidate_packages
from generator.common import REFERENCE_DIR, load_config

OPENFDA_NDC_URL = "https://api.fda.gov/drug/ndc.json"
OPENFDA_SHORTAGES_URL = "https://api.fda.gov/drug/shortages.json"
MEDICAID_SEARCH_URL = "https://data.medicaid.gov/api/1/search"
MEDICAID_QUERY_URL = "https://data.medicaid.gov/api/1/datastore/query/{dataset_id}/0"

OPENFDA_PAGE = 1000
NADAC_PAGE = 500
# Only NADAC rows from the most recent weeks are needed to get the latest price per NDC.
NADAC_LOOKBACK_DAYS = 35
RETRY_STATUS = {429, 500, 502, 503, 504}


def _request(url: str, timeout: int, body: dict[str, Any] | None = None, attempts: int = 4) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            # openFDA returns 404 when a search has no results.
            if e.code == 404 and "api.fda.gov" in url:
                return {"meta": {"results": {"total": 0}}, "results": []}
            if e.code not in RETRY_STATUS or attempt == attempts - 1:
                raise
        except urllib.error.URLError:
            if attempt == attempts - 1:
                raise
        time.sleep(2**attempt)
    raise RuntimeError("unreachable")


def fetch_openfda(base_url: str, timeout: int, search: str | None = None) -> list[dict[str, Any]]:
    """All pages of an openFDA query."""
    results: list[dict[str, Any]] = []
    while True:
        params = {"limit": OPENFDA_PAGE, "skip": len(results)}
        if search:
            params["search"] = search
        page = _request(f"{base_url}?{urllib.parse.urlencode(params)}", timeout)
        results.extend(page["results"])
        if not page["results"] or len(results) >= page["meta"]["results"]["total"]:
            return results
        time.sleep(0.3)


def find_nadac_dataset(year: int, timeout: int) -> dict[str, str]:
    title = f"NADAC (National Average Drug Acquisition Cost) {year}"
    params = urllib.parse.urlencode({"fulltext": "NADAC", "page-size": 50})
    found = _request(f"{MEDICAID_SEARCH_URL}?{params}", timeout)["results"]
    for ds in found.values():
        if ds.get("title") == title:
            return {"identifier": ds["identifier"], "title": title, "modified": ds.get("modified", "")}
    raise LookupError(f"NADAC dataset not found: {title!r}")


def _nadac_query(dataset_id: str, timeout: int, conditions: list[dict[str, Any]], limit: int, offset: int) -> list:
    body = {
        "conditions": conditions,
        "sorts": [{"property": "as_of_date", "order": "desc"}],
        "limit": limit,
        "offset": offset,
    }
    return _request(MEDICAID_QUERY_URL.format(dataset_id=dataset_id), timeout, body)["results"]


def fetch_nadac(dataset_id: str, ndcs: list[str], batch_size: int, timeout: int) -> tuple[list[dict[str, Any]], str]:
    """Recent NADAC rows for the given 11-digit NDCs. Returns (rows, latest as_of_date in the dataset)."""
    latest = _nadac_query(dataset_id, timeout, [], 1, 0)[0]["as_of_date"]
    since = (date.fromisoformat(latest) - timedelta(days=NADAC_LOOKBACK_DAYS)).isoformat()
    rows: list[dict[str, Any]] = []
    for i in range(0, len(ndcs), batch_size):
        conditions = [
            {"property": "ndc", "value": ndcs[i : i + batch_size], "operator": "in"},
            {"property": "as_of_date", "value": since, "operator": ">="},
        ]
        offset = 0
        while True:
            page = _nadac_query(dataset_id, timeout, conditions, NADAC_PAGE, offset)
            rows.extend(page)
            if len(page) < NADAC_PAGE:
                break
            offset += NADAC_PAGE
    return rows, latest


def _save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1))


def main() -> None:
    cfg = load_config()["reference"]
    timeout = cfg["fetch_timeout_s"]
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    counts: dict[str, int] = {}

    ndcs: set[str] = set()
    for item in cfg["formulary"]:
        search = f'generic_name:"{item["search_name"]}" AND route:"{item["route"]}" AND _exists_:dea_schedule'
        products = fetch_openfda(OPENFDA_NDC_URL, timeout, search)
        _save(REFERENCE_DIR / "openfda_ndc" / f"{item['drug_key']}.json", {"search": search, "results": products})
        candidates = {c["ndc"] for c in candidate_packages(item, products)}
        ndcs |= candidates
        counts[f"openfda_ndc.{item['drug_key']}"] = len(products)
        print(f"openFDA NDC {item['drug_key']}: {len(products)} products, {len(candidates)} candidate packages")
        time.sleep(0.3)

    shortages = fetch_openfda(OPENFDA_SHORTAGES_URL, timeout)
    _save(REFERENCE_DIR / "shortages.json", {"results": shortages})
    counts["shortages"] = len(shortages)
    print(f"openFDA shortages: {len(shortages)} records")

    dataset = find_nadac_dataset(cfg["nadac_year"], timeout)
    nadac_rows, nadac_latest = fetch_nadac(dataset["identifier"], sorted(ndcs), cfg["nadac_batch_size"], timeout)
    _save(REFERENCE_DIR / "nadac_subset.json", {"dataset": dataset, "results": nadac_rows})
    counts["nadac_rows"] = len(nadac_rows)
    print(f"NADAC {dataset['title']}: {len(nadac_rows)} rows for {len(ndcs)} candidate NDCs, latest {nadac_latest}")

    _save(
        REFERENCE_DIR / "manifest.json",
        {
            "fetched_at_utc": fetched_at,
            "sources": {
                "openfda_ndc": OPENFDA_NDC_URL,
                "openfda_shortages": OPENFDA_SHORTAGES_URL,
                "nadac": MEDICAID_QUERY_URL.format(dataset_id=dataset["identifier"]),
            },
            "nadac_dataset": dataset,
            "nadac_latest_as_of_date": nadac_latest,
            "nadac_lookback_days": NADAC_LOOKBACK_DAYS,
            "counts": counts,
        },
    )
    print(f"wrote {REFERENCE_DIR}")


if __name__ == "__main__":
    main()
