"""Build the controlled substance formulary and shortage file from fetched reference data.

Offline step. Reads only data/reference/ (written by fetch_reference.py) and generator/config.yaml.
Writes data/reference/formulary.csv and data/reference/shortages.csv.

Run: uv run python -m generator.build_formulary
"""

import csv
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

from generator.common import REFERENCE_DIR, load_config

FORMULARY_COLUMNS = [
    "drug_key",
    "display_name",
    "ndc",
    "package_ndc",
    "package_description",
    "generic_name",
    "strength",
    "dosage_form",
    "route",
    "dea_schedule",
    "therapeutic_class",
    "psych_relevant",
    "unit_cost",
    "pricing_unit",
    "cost_source",
    "nadac_as_of_date",
    "labeler_name",
    "in_shortage",
]

SHORTAGE_COLUMNS = [
    "package_ndc",
    "ndc",
    "generic_name",
    "dosage_form",
    "presentation",
    "company_name",
    "status",
    "availability",
    "initial_posting_date",
    "update_date",
    "drug_key",
]


class FormularyError(ValueError):
    """Raised when a formulary item can't be matched cleanly to openFDA data."""


def normalize_ndc(package_ndc: str) -> str:
    """Convert a hyphenated 10-digit NDC (4-4-2, 5-3-2 or 5-4-1) to the 11-digit 5-4-2 form, no dashes."""
    parts = package_ndc.strip().split("-")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a hyphenated NDC: {package_ndc!r}")
    labeler, product, package = parts
    if (len(labeler), len(product), len(package)) not in {(4, 4, 2), (5, 3, 2), (5, 4, 1)}:
        raise ValueError(f"unexpected NDC segment lengths: {package_ndc!r}")
    return labeler.zfill(5) + product.zfill(4) + package.zfill(2)


def product_strength(product: dict[str, Any]) -> str:
    return "; ".join(a.get("strength", "") for a in product.get("active_ingredients", []))


def matching_products(item: dict[str, Any], products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """openFDA NDC products that fit the item spec (exact name, route, form, strength, prescription drug)."""
    names = {n.lower() for n in item["generic_names"]}
    return [
        p
        for p in products
        if p.get("generic_name", "").lower() in names
        and item["route"] in p.get("route", [])
        and p.get("dosage_form") in item["dosage_forms"]
        and product_strength(p) in item["strengths"]
        and p.get("product_type") == "HUMAN PRESCRIPTION DRUG"
        and p.get("dea_schedule")
    ]


def candidate_packages(item: dict[str, Any], products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten matching products to non-sample packages, each with its 11-digit NDC."""
    out = []
    for p in matching_products(item, products):
        for pkg in p.get("packaging", []):
            if pkg.get("sample"):
                continue
            try:
                ndc = normalize_ndc(pkg["package_ndc"])
            except (KeyError, ValueError):
                continue
            out.append({"ndc": ndc, "package": pkg, "product": p})
    return out


def latest_nadac(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Most recent NADAC row per NDC: {ndc: {"price", "pricing_unit", "as_of_date", "classification"}}.

    classification is NADAC's classification_for_rate_setting: G (generic), B (brand), B-ANDA.
    """
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        ndc, as_of = r.get("ndc"), r.get("as_of_date")
        if not ndc or not as_of or r.get("nadac_per_unit") in (None, ""):
            continue
        if ndc not in latest or as_of > latest[ndc]["as_of_date"]:
            latest[ndc] = {
                "price": float(r["nadac_per_unit"]),
                "pricing_unit": r["pricing_unit"],
                "as_of_date": as_of,
                "classification": r.get("classification_for_rate_setting", ""),
            }
    return latest


def select_package(
    item: dict[str, Any], products: list[dict[str, Any]], nadac: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Pick one package deterministically.

    Order: NADAC-priced generic, then NADAC-priced brand, then unpriced, with lowest 11-digit NDC as the tie-break.
    Generic first because a hospital buys the generic when one exists.
    """
    candidates = candidate_packages(item, products)
    if not candidates:
        raise FormularyError(f"{item['drug_key']}: no openFDA product matches the spec")

    def rank(c: dict[str, Any]) -> tuple[int, str]:
        price = nadac.get(c["ndc"])
        tier = 2 if price is None else (0 if price["classification"] == "G" else 1)
        return tier, c["ndc"]

    chosen = min(candidates, key=rank)
    schedule = chosen["product"]["dea_schedule"]
    if schedule != item["expected_schedule"]:
        raise FormularyError(
            f"{item['drug_key']}: openFDA dea_schedule {schedule} != expected {item['expected_schedule']}"
        )
    return chosen


def fallback_cost(item: dict[str, Any], fallback: dict[str, dict[str, Any]], seed: int) -> tuple[float, str]:
    """Seeded cost per pricing unit. Seeded per drug_key so the value doesn't depend on list order."""
    spec = fallback[item["form_group"]]
    rng = random.Random(f"{seed}:{item['drug_key']}")
    return round(rng.uniform(spec["min"], spec["max"]), 4), spec["pricing_unit"]


def shortage_rows(records: list[dict[str, Any]], formulary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten FDA shortage records. drug_key is set when the shortage generic_name matches a formulary item."""
    by_name = {n.lower(): item["drug_key"] for item in formulary for n in item["shortage_names"]}
    out = []
    for r in records:
        try:
            ndc = normalize_ndc(r.get("package_ndc", ""))
        except ValueError:
            ndc = ""
        out.append(
            {
                "package_ndc": r.get("package_ndc", ""),
                "ndc": ndc,
                "generic_name": r.get("generic_name", ""),
                "dosage_form": r.get("dosage_form", ""),
                "presentation": r.get("presentation", ""),
                "company_name": r.get("company_name", ""),
                "status": r.get("status", ""),
                "availability": r.get("availability") or "",
                "initial_posting_date": r.get("initial_posting_date", ""),
                "update_date": r.get("update_date", ""),
                "drug_key": by_name.get(r.get("generic_name", "").lower(), ""),
            }
        )
    return out


def build_formulary_rows(
    cfg: dict[str, Any],
    products_by_key: dict[str, list[dict[str, Any]]],
    nadac: dict[str, dict[str, Any]],
    short_keys: set[str],
) -> list[dict[str, Any]]:
    rows = []
    for item in cfg["formulary"]:
        chosen = select_package(item, products_by_key[item["drug_key"]], nadac)
        product, ndc = chosen["product"], chosen["ndc"]
        if ndc in nadac:
            price = nadac[ndc]
            cost, unit, source, as_of = price["price"], price["pricing_unit"], "nadac", price["as_of_date"]
        else:
            (cost, unit), source, as_of = fallback_cost(item, cfg["fallback_cost"], cfg["seed"]), "fallback", ""
        rows.append(
            {
                "drug_key": item["drug_key"],
                "display_name": item["display_name"],
                "ndc": ndc,
                "package_ndc": chosen["package"]["package_ndc"],
                "package_description": chosen["package"].get("description", ""),
                "generic_name": product["generic_name"].lower(),
                "strength": product_strength(product),
                "dosage_form": product["dosage_form"],
                "route": item["route"],
                "dea_schedule": product["dea_schedule"],
                "therapeutic_class": item["therapeutic_class"],
                "psych_relevant": str(item["psych_relevant"]).lower(),
                "unit_cost": cost,
                "pricing_unit": unit,
                "cost_source": source,
                "nadac_as_of_date": as_of,
                "labeler_name": product.get("labeler_name", ""),
                "in_shortage": str(item["drug_key"] in short_keys).lower(),
            }
        )
    return rows


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    cfg = load_config()["reference"]
    ndc_dir = REFERENCE_DIR / "openfda_ndc"
    products_by_key = {
        item["drug_key"]: json.loads((ndc_dir / f"{item['drug_key']}.json").read_text())["results"]
        for item in cfg["formulary"]
    }
    nadac = latest_nadac(json.loads((REFERENCE_DIR / "nadac_subset.json").read_text())["results"])
    shortages = shortage_rows(json.loads((REFERENCE_DIR / "shortages.json").read_text())["results"], cfg["formulary"])
    short_keys = {s["drug_key"] for s in shortages if s["drug_key"] and s["status"] == "Current"}

    rows = build_formulary_rows(cfg, products_by_key, nadac, short_keys)
    _write_csv(REFERENCE_DIR / "formulary.csv", FORMULARY_COLUMNS, rows)
    _write_csv(REFERENCE_DIR / "shortages.csv", SHORTAGE_COLUMNS, shortages)

    print(f"formulary.csv: {len(rows)} rows")
    print("  by schedule:", dict(sorted(Counter(r["dea_schedule"] for r in rows).items())))
    print("  by cost_source:", dict(Counter(r["cost_source"] for r in rows)))
    print(f"  {'drug_key':28} {'ndc':11}  sched  {'unit_cost':>9} unit  source    short")
    for r in rows:
        print(
            f"  {r['drug_key']:28} {r['ndc']:11}  {r['dea_schedule']:5}  {r['unit_cost']:9.4f} {r['pricing_unit']:4}"
            f"  {r['cost_source']:8}  {r['in_shortage']}"
        )
    print(f"shortages.csv: {len(shortages)} rows, by status {dict(Counter(s['status'] for s in shortages))}")
    print(f"  formulary items currently in shortage: {sorted(short_keys)}")


if __name__ == "__main__":
    main()
