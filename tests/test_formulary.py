"""Formulary build logic, tested on small inline fixtures. No network."""

import pytest

from generator.build_formulary import (
    FormularyError,
    fallback_cost,
    latest_nadac,
    normalize_ndc,
    select_package,
    shortage_rows,
)
from generator.common import load_config

ITEM = {
    "drug_key": "lorazepam_tab_1mg",
    "generic_names": ["lorazepam"],
    "route": "ORAL",
    "dosage_forms": ["TABLET"],
    "strengths": ["1 mg/1"],
    "form_group": "oral_solid",
    "expected_schedule": "CIV",
    "shortage_names": ["Lorazepam Tablet"],
}
FALLBACK = {"oral_solid": {"pricing_unit": "EA", "min": 0.10, "max": 2.50}}


def product(ndcs: list[str], schedule: str = "CIV", strength: str = "1 mg/1", **overrides) -> dict:
    p = {
        "generic_name": "Lorazepam",
        "route": ["ORAL"],
        "dosage_form": "TABLET",
        "active_ingredients": [{"name": "LORAZEPAM", "strength": strength}],
        "product_type": "HUMAN PRESCRIPTION DRUG",
        "dea_schedule": schedule,
        "packaging": [{"package_ndc": n, "description": "", "sample": False} for n in ndcs],
    }
    p.update(overrides)
    return p


def nadac_row(ndc: str, price: str, as_of: str, cls: str = "G") -> dict:
    return {
        "ndc": ndc,
        "nadac_per_unit": price,
        "pricing_unit": "EA",
        "as_of_date": as_of,
        "classification_for_rate_setting": cls,
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0093-3426-01", "00093342601"), ("67296-223-82", "67296022382"), ("51079-4523-1", "51079452301")],
)
def test_normalize_ndc_pads_each_layout(raw: str, expected: str) -> None:
    assert normalize_ndc(raw) == expected


@pytest.mark.parametrize("raw", ["0093342601", "123-45-6", "0093-3426-0A"])
def test_normalize_ndc_rejects_bad_input(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_ndc(raw)


def test_latest_nadac_keeps_most_recent_row() -> None:
    rows = [
        nadac_row("1", "0.05", "2026-10-14"),
        nadac_row("1", "0.04", "2026-10-28"),
        nadac_row("2", "", "2026-10-28"),
    ]
    assert latest_nadac(rows) == {
        "1": {"price": 0.04, "pricing_unit": "EA", "as_of_date": "2026-10-28", "classification": "G"}
    }


def test_select_prefers_priced_generic_then_lowest_ndc() -> None:
    products = [product(["00009-001-01", "00093-001-01", "00228-001-01"]), product(["00001-001-01"])]
    nadac = latest_nadac(
        [nadac_row("00009000101", "5.00", "2026-10-28", cls="B"), nadac_row("00228000101", "0.02", "2026-10-28")]
    )
    # 00001 and 00093 are unpriced, 00009 is a priced brand, 00228 is a priced generic.
    assert select_package(ITEM, products, nadac)["ndc"] == "00228000101"
    assert select_package(ITEM, products, {})["ndc"] == "00001000101"


def test_select_filters_strength_route_and_samples() -> None:
    products = [
        product(["00001-001-01"], strength="2 mg/1"),
        product(["00002-001-01"], route=["INTRAVENOUS"]),
        product(["00003-001-01"], product_type="HUMAN OTC DRUG"),
        product(["00005-001-01"]),
    ]
    products[3]["packaging"].insert(0, {"package_ndc": "00004-001-01", "sample": True})
    assert select_package(ITEM, products, {})["ndc"] == "00005000101"


def test_select_raises_on_schedule_mismatch() -> None:
    with pytest.raises(FormularyError, match="dea_schedule CII"):
        select_package(ITEM, [product(["00001-001-01"], schedule="CII")], {})


def test_select_raises_when_nothing_matches() -> None:
    with pytest.raises(FormularyError, match="no openFDA product"):
        select_package(ITEM, [product(["00001-001-01"], strength="2 mg/1")], {})


def test_fallback_cost_is_seeded_and_in_range() -> None:
    a = fallback_cost(ITEM, FALLBACK, seed=7)
    assert a == fallback_cost(ITEM, FALLBACK, seed=7)
    assert a != fallback_cost(ITEM, FALLBACK, seed=8)
    assert 0.10 <= a[0] <= 2.50 and a[1] == "EA"


def test_shortage_rows_match_exact_name_only() -> None:
    records = [
        {"generic_name": "Lorazepam Tablet", "package_ndc": "0093-3426-01", "status": "Current"},
        {"generic_name": "Lorazepam Tablet, Extended Release", "package_ndc": "bad", "status": "Current"},
    ]
    rows = shortage_rows(records, [ITEM])
    assert [(r["drug_key"], r["ndc"]) for r in rows] == [("lorazepam_tab_1mg", "00093342601"), ("", "")]


def test_config_formulary_is_well_formed() -> None:
    cfg = load_config()["reference"]
    items = cfg["formulary"]
    assert 18 <= len(items) <= 22
    assert len({i["drug_key"] for i in items}) == len(items)
    assert {i["expected_schedule"] for i in items} <= {"CII", "CIII", "CIV", "CV"}
    assert {i["form_group"] for i in items} <= set(cfg["fallback_cost"])
    for name in ("lorazepam", "clonazepam", "diazepam", "ketamine", "phenobarbital", "pregabalin", "tramadol"):
        assert any(name in g for i in items for g in i["generic_names"]), name
