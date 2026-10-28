"""Build dashboards/rx_surveillance.lvdash.json, the AI/BI dashboard for this project (synthetic data).

The dashboard JSON is generated so field names in widget encodings always match their query fields, and so the
layout is readable as code. Dataset queries use unqualified reporting view names; the bundle sets the catalog and
schema per target (resources/dashboard.yml).

Run: uv run python dashboards/build_dashboard.py
"""

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("rx_surveillance.lvdash.json")

HEADER = [
    "## Controlled Substance Surveillance\n",
    (
        "**All data on this dashboard is synthetic.** It comes from a simulated two-hospital system (MAIN and COMM). "
        "No real patients, staff, or cabinets are shown.\n"
    ),
    (
        "Screening scores and flags are a review aid for pharmacy, not a finding of diversion. "
        "Patient IDs are hidden unless you are in the rx_privileged group, and you see only the facilities you have access to.\n"
    ),
]

DATASETS = {
    "discrepancies": (
        "Count discrepancies",
        (
            "SELECT report_date, facility_id, cabinet_id, display_name, dea_schedule, discrepancies, resolved, unresolved, "
            "net_variance_units, median_hours_to_resolve FROM daily_discrepancies"
        ),
    ),
    "compliance": (
        "Compliance events",
        (
            "SELECT report_date, CAST(date_trunc('MONTH', report_date) AS DATE) AS report_month, facility_id, unit_id, "
            "flag_type, events, users FROM daily_compliance_events"
        ),
    ),
    "below_par": (
        "Below-par pockets (latest day)",
        (
            "SELECT report_date, facility_id, unit_id, cabinet_id, drug_key, on_hand, restock_below, par_level, "
            "avg_daily_usage, days_on_hand FROM inventory_daily "
            "WHERE below_par AND report_date = (SELECT max(report_date) FROM inventory_daily)"
        ),
    ),
    "shortages": (
        "Shortage exposure",
        (
            "SELECT as_of_date, facility_id, display_name, dea_schedule, shortage_records, "
            "array_join(availability, ', ') AS availability, latest_update_date, on_hand_units, avg_daily_usage, "
            "days_on_hand FROM shortage_exposure"
        ),
    ),
    "review_list": (
        "Monthly review list",
        "SELECT report_month, rank, user_id, facility_id, unit_id, shift, pulls, score, reasons FROM monthly_review_list",
    ),
    "procurement": (
        "Procurement",
        (
            "SELECT report_month, facility_id, dea_schedule, display_name, restocked_units, est_spend, cost_source "
            "FROM procurement_monthly"
        ),
    ),
    "quarantine": ("Quarantined rows", "SELECT file_date, rule, rows FROM quarantine_summary"),
}


def pos(x: int, y: int, w: int, h: int) -> dict[str, int]:
    return {"x": x, "y": y, "width": w, "height": h}


def field(name: str, expression: str | None = None) -> dict[str, str]:
    return {"name": name, "expression": expression or f"`{name}`"}


def query(dataset: str, fields: list[dict[str, str]], disaggregated: bool, name: str = "main_query") -> dict:
    return {"name": name, "query": {"datasetName": dataset, "fields": fields, "disaggregated": disaggregated}}


def frame(title: str, description: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"showTitle": True, "title": title}
    if description:
        out.update(showDescription=True, description=description)
    return out


def text(name: str, lines: list[str], position: dict) -> dict:
    return {"widget": {"name": name, "multilineTextboxSpec": {"lines": lines}}, "position": position}


def counter(name: str, dataset: str, value: str, expression: str, title: str, position: dict, desc: str = "") -> dict:
    return {
        "widget": {
            "name": name,
            "queries": [query(dataset, [field(value, expression)], disaggregated=False)],
            "spec": {
                "version": 2,
                "widgetType": "counter",
                "encodings": {"value": {"fieldName": value, "displayName": title}},
                "frame": frame(title, desc),
            },
        },
        "position": position,
    }


def chart(
    kind: str,
    name: str,
    dataset: str,
    x: tuple[str, str, str],
    y: tuple[str, str, str],
    title: str,
    position: dict,
    color: tuple[str, str] | None = None,
    desc: str = "",
) -> dict:
    """kind is 'bar' or 'line'. x = (field, scale type, label); y = (field, aggregate expression, label)."""
    fields = [field(x[0]), field(y[0], y[1])] + ([field(color[0])] if color else [])
    encodings: dict[str, Any] = {
        "x": {"fieldName": x[0], "scale": {"type": x[1]}, "displayName": x[2]},
        "y": {"fieldName": y[0], "scale": {"type": "quantitative"}, "displayName": y[2]},
    }
    if color:
        encodings["color"] = {"fieldName": color[0], "scale": {"type": "categorical"}, "displayName": color[1]}
    return {
        "widget": {
            "name": name,
            "queries": [query(dataset, fields, disaggregated=False)],
            "spec": {"version": 3, "widgetType": kind, "encodings": encodings, "frame": frame(title, desc)},
        },
        "position": position,
    }


def table(
    name: str, dataset: str, columns: list[tuple[str, str, str]], title: str, position: dict, desc: str = ""
) -> dict:
    """columns = (field, label, type) with type one of string, integer, float, datetime.

    Column specs copy every key a table exported from the Databricks UI carries (table version 1). Leaving keys out,
    or using a type the UI doesn't write (such as "date"), shows "Invalid widget definition".
    """
    display = {"string": "string", "integer": "number", "float": "number", "datetime": "datetime"}
    cols = []
    for i, (fname, label, ctype) in enumerate(columns):
        col: dict[str, Any] = {
            "fieldName": fname,
            "displayName": label,
            "title": label,
            "type": ctype,
            "displayAs": display[ctype],
            "visible": True,
            "order": 100000 + i,
            "alignContent": "left" if ctype == "string" else "right",
            "allowSearch": ctype == "string",
            "allowHTML": False,
            "highlightLinks": False,
            "useMonospaceFont": False,
            "preserveWhitespace": False,
            "booleanValues": ["false", "true"],
            "imageUrlTemplate": "{{ @ }}",
            "imageTitleTemplate": "{{ @ }}",
            "imageWidth": "",
            "imageHeight": "",
            "linkUrlTemplate": "{{ @ }}",
            "linkTextTemplate": "{{ @ }}",
            "linkTitleTemplate": "{{ @ }}",
            "linkOpenInNewTab": True,
        }
        if ctype == "datetime":
            col["dateTimeFormat"] = "YYYY-MM-DD"
        elif ctype == "integer":
            col["numberFormat"] = "0"
        elif ctype == "float":
            col["numberFormat"] = "0.00"
        cols.append(col)
    return {
        "widget": {
            "name": name,
            "queries": [query(dataset, [field(c[0]) for c in columns], disaggregated=True)],
            "spec": {
                "version": 1,
                "widgetType": "table",
                "encodings": {"columns": cols},
                "invisibleColumns": [],
                "allowHTMLByDefault": False,
                "itemsPerPage": 25,
                "paginationSize": "default",
                "condensed": True,
                "withRowNumber": False,
                "frame": frame(title, desc),
            },
        },
        "position": position,
    }


def filter_widget(kind: str, name: str, fname: str, datasets: list[str], title: str, position: dict) -> dict:
    """kind is 'filter-multi-select', 'filter-single-select', or 'filter-date-range-picker'."""
    assoc = field(f"{fname}_associativity", "COUNT_IF(`associative_filter_predicate_group`)")
    queries = [query(ds, [field(fname), assoc], disaggregated=False, name=f"{name}_{ds}") for ds in datasets]
    return {
        "widget": {
            "name": name,
            "queries": queries,
            "spec": {
                "version": 2,
                "widgetType": kind,
                "encodings": {
                    "fields": [{"fieldName": fname, "displayName": title, "queryName": q["name"]} for q in queries]
                },
                "frame": frame(title),
            },
        },
        "position": position,
    }


def page(name: str, title: str, layout: list[dict]) -> dict:
    return {"name": name, "displayName": title, "layout": layout, "pageType": "PAGE_TYPE_CANVAS"}


def daily_page() -> dict:
    return page(
        "daily",
        "Daily",
        [
            text("daily_header", HEADER, pos(0, 0, 6, 3)),
            filter_widget(
                "filter-multi-select",
                "daily_facility",
                "facility_id",
                ["discrepancies", "compliance", "below_par", "shortages"],
                "Facility",
                pos(0, 3, 2, 2),
            ),
            filter_widget(
                "filter-date-range-picker",
                "daily_dates",
                "report_date",
                ["discrepancies", "compliance"],
                "Report date",
                pos(2, 3, 2, 2),
            ),
            counter(
                "open_discrepancies",
                "discrepancies",
                "open_discrepancies",
                "SUM(`unresolved`)",
                "Unresolved count discrepancies",
                pos(0, 5, 2, 3),
            ),
            counter(
                "flagged_events",
                "compliance",
                "flagged_events",
                "SUM(`events`)",
                "Flagged cabinet events",
                pos(2, 5, 2, 3),
            ),
            counter(
                "below_par_pockets",
                "below_par",
                "below_par_pockets",
                "COUNT(`drug_key`)",
                "Pockets below restock level today",
                pos(4, 5, 2, 3),
            ),
            table(
                "discrepancy_table",
                "discrepancies",
                [
                    ("report_date", "Date", "datetime"),
                    ("facility_id", "Facility", "string"),
                    ("cabinet_id", "Cabinet", "string"),
                    ("display_name", "Drug", "string"),
                    ("dea_schedule", "Schedule", "string"),
                    ("discrepancies", "Discrepancies", "integer"),
                    ("unresolved", "Unresolved", "integer"),
                    ("net_variance_units", "Counted minus expected", "float"),
                    ("median_hours_to_resolve", "Hours to resolve (median)", "float"),
                ],
                "Count discrepancies",
                pos(0, 8, 6, 6),
                "A blind count that did not match the cabinet's expected quantity. Unresolved ones need follow-up.",
            ),
            chart(
                "bar",
                "compliance_by_day",
                "compliance",
                ("report_date", "temporal", "Date"),
                ("events_sum", "SUM(`events`)", "Events"),
                "Flagged events by day",
                pos(0, 14, 6, 6),
                color=("flag_type", "Flag"),
            ),
            table(
                "below_par_table",
                "below_par",
                [
                    ("facility_id", "Facility", "string"),
                    ("cabinet_id", "Cabinet", "string"),
                    ("drug_key", "Drug", "string"),
                    ("on_hand", "On hand", "float"),
                    ("restock_below", "Restock level", "integer"),
                    ("avg_daily_usage", "Average daily use", "float"),
                    ("days_on_hand", "Days on hand", "float"),
                ],
                "Below restock level (latest day)",
                pos(0, 20, 3, 7),
            ),
            table(
                "shortage_table",
                "shortages",
                [
                    ("facility_id", "Facility", "string"),
                    ("display_name", "Drug", "string"),
                    ("dea_schedule", "Schedule", "string"),
                    ("availability", "FDA availability", "string"),
                    ("on_hand_units", "Cabinet on hand", "float"),
                    ("days_on_hand", "Days on hand", "float"),
                ],
                "On the FDA shortage list",
                pos(3, 20, 3, 7),
                "Days on hand counts cabinet stock only. Central pharmacy stock is not modeled.",
            ),
        ],
    )


def monthly_page() -> dict:
    return page(
        "monthly",
        "Monthly",
        [
            text("monthly_header", HEADER, pos(0, 0, 6, 3)),
            filter_widget(
                "filter-single-select",
                "monthly_month",
                "report_month",
                ["review_list"],
                "Month",
                pos(0, 3, 2, 2),
            ),
            filter_widget(
                "filter-multi-select",
                "monthly_facility",
                "facility_id",
                ["review_list", "compliance", "procurement"],
                "Facility",
                pos(2, 3, 2, 2),
            ),
            table(
                "review_list_table",
                "review_list",
                [
                    ("report_month", "Month", "datetime"),
                    ("rank", "Rank", "integer"),
                    ("user_id", "Nurse", "string"),
                    ("facility_id", "Facility", "string"),
                    ("unit_id", "Unit", "string"),
                    ("shift", "Shift", "string"),
                    ("pulls", "Pulls", "integer"),
                    ("score", "Score", "float"),
                    ("reasons", "Reasons", "string"),
                ],
                "Monthly review list",
                pos(0, 5, 6, 9),
                "Top 10% of nurses each month compared with peers on the same unit and shift. "
                "Use the table menu to download as CSV or Excel.",
            ),
            chart(
                "bar",
                "unit_trends",
                "compliance",
                ("report_month", "temporal", "Month"),
                ("events_sum", "SUM(`events`)", "Flagged events"),
                "Flagged events by unit",
                pos(0, 14, 3, 6),
                color=("unit_id", "Unit"),
            ),
            chart(
                "bar",
                "spend_by_schedule",
                "procurement",
                ("report_month", "temporal", "Month"),
                ("spend_sum", "SUM(`est_spend`)", "Estimated spend (USD)"),
                "Estimated restock spend by schedule",
                pos(3, 14, 3, 6),
                color=("dea_schedule", "Schedule"),
                desc="Restocked units times NADAC unit cost (fallback cost where NADAC has no price).",
            ),
            table(
                "procurement_table",
                "procurement",
                [
                    ("report_month", "Month", "datetime"),
                    ("facility_id", "Facility", "string"),
                    ("display_name", "Drug", "string"),
                    ("dea_schedule", "Schedule", "string"),
                    ("restocked_units", "Units restocked", "float"),
                    ("est_spend", "Estimated spend (USD)", "float"),
                    ("cost_source", "Cost source", "string"),
                ],
                "Procurement by drug",
                pos(0, 20, 6, 7),
            ),
        ],
    )


def quality_page() -> dict:
    return page(
        "data_quality",
        "Data quality",
        [
            text(
                "quality_header",
                HEADER
                + ["Rows that fail a data quality rule are kept in quarantine with the rule name, not dropped.\n"],
                pos(0, 0, 6, 3),
            ),
            counter(
                "quarantined_rows", "quarantine", "quarantined_rows", "SUM(`rows`)", "Quarantined rows", pos(0, 3, 2, 3)
            ),
            chart(
                "bar",
                "quarantine_by_rule",
                "quarantine",
                ("rule", "categorical", "Rule"),
                ("rows_sum", "SUM(`rows`)", "Rows"),
                "Quarantined rows by rule",
                pos(2, 3, 4, 6),
            ),
            chart(
                "line",
                "quarantine_by_day",
                "quarantine",
                ("file_date", "temporal", "File date"),
                ("rows_sum", "SUM(`rows`)", "Rows"),
                "Quarantined rows by file date",
                pos(0, 9, 6, 6),
            ),
        ],
    )


def build() -> dict:
    return {
        "datasets": [
            {"name": name, "displayName": display, "queryLines": [sql]} for name, (display, sql) in DATASETS.items()
        ],
        "pages": [daily_page(), monthly_page(), quality_page()],
        "uiSettings": {"theme": {"widgetHeaderAlignment": "ALIGNMENT_UNSPECIFIED"}},
    }


def render() -> str:
    return json.dumps(build(), indent=2) + "\n"


if __name__ == "__main__":
    OUT.write_text(render())
    print(f"wrote {OUT}")
