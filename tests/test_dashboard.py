"""Dashboard JSON (dashboards/): generated file is current, widgets are consistent, data comes from reporting views."""

import importlib.util
import json
import re

from generator.common import REPO_ROOT

DASH_DIR = REPO_ROOT / "dashboards"
spec = importlib.util.spec_from_file_location("build_dashboard", DASH_DIR / "build_dashboard.py")
build_dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_dashboard)
DASH = json.loads((DASH_DIR / "rx_surveillance.lvdash.json").read_text())
REPORTING_VIEWS = set(
    re.findall(
        r"CREATE OR REPLACE VIEW \$\{reporting\}\.(\w+)",
        (REPO_ROOT / "sql/governance/04_reporting_views.sql").read_text(),
    )
)
VERSIONS = {
    "counter": 2,
    "table": 1,
    "bar": 3,
    "line": 3,
    "filter-multi-select": 2,
    "filter-single-select": 2,
    "filter-date-range-picker": 2,
}


def widgets():
    for page in DASH["pages"]:
        for item in page["layout"]:
            yield page, item


def test_committed_json_matches_builder() -> None:
    assert (DASH_DIR / "rx_surveillance.lvdash.json").read_text() == build_dashboard.render()


def test_required_pages_and_synthetic_header() -> None:
    assert [p["displayName"] for p in DASH["pages"]] == ["Daily", "Monthly", "Data quality"]
    for page in DASH["pages"]:
        texts = [i["widget"] for i in page["layout"] if "multilineTextboxSpec" in i["widget"]]
        assert any("synthetic" in "".join(w["multilineTextboxSpec"]["lines"]) for w in texts), page["name"]


def test_datasets_read_only_reporting_views() -> None:
    for ds in DASH["datasets"]:
        sql = " ".join(ds["queryLines"])
        tables = set(re.findall(r"\bFROM\s+([\w.`-]+)", sql, flags=re.IGNORECASE))
        assert tables and tables <= REPORTING_VIEWS, (ds["name"], tables)
        assert not re.search(r"silver|gold|bronze|_eval|labels", sql), ds["name"]


def test_widget_versions_fields_and_datasets() -> None:
    names = {ds["name"] for ds in DASH["datasets"]}
    seen = set()
    for page, item in widgets():
        w = item["widget"]
        assert w["name"] not in seen
        seen.add(w["name"])
        p = item["position"]
        assert 0 <= p["x"] and p["x"] + p["width"] <= 6, w["name"]  # 6-column grid
        if "spec" not in w:
            continue
        kind = w["spec"]["widgetType"]
        assert w["spec"]["version"] == VERSIONS[kind], w["name"]
        fields_by_query = {q["name"]: {f["name"] for f in q["query"]["fields"]} for q in w["queries"]}
        assert all(q["query"]["datasetName"] in names for q in w["queries"]), w["name"]
        enc = w["spec"]["encodings"]
        if kind.startswith("filter-"):
            for f in enc["fields"]:
                assert f["fieldName"] in fields_by_query[f["queryName"]], w["name"]
        elif kind == "table":
            assert [c["fieldName"] for c in enc["columns"]] == [f["name"] for f in w["queries"][0]["query"]["fields"]]
        else:
            all_fields = fields_by_query["main_query"]
            for channel in enc.values():
                assert channel["fieldName"] in all_fields, (w["name"], channel["fieldName"])


def test_review_list_is_a_table_for_export() -> None:
    review = [i["widget"] for _, i in widgets() if i["widget"]["name"] == "review_list_table"]
    assert review and review[0]["spec"]["widgetType"] == "table"
    assert "Excel" in review[0]["spec"]["frame"]["description"]
