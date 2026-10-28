"""Reporting view SQL (sql/governance/): facility filter and ID masking are applied where they must be."""

import re

from sql_files import render, statements

from generator.common import REPO_ROOT

SQL_DIR = REPO_ROOT / "sql" / "governance"
FACILITY_SOURCES = (
    "daily_discrepancies",
    "daily_compliance_events",
    "adc_events",
    "inventory_daily",
    "shortage_exposure",
    "procurement_monthly",
    "user_risk_monthly",
    "monthly_audit_snapshot",
)
UNFILTERED_OK = {"quarantine_summary", "my_facilities"}


def views() -> dict[str, str]:
    out = {}
    for path in sorted(SQL_DIR.glob("*.sql")):
        for stmt in statements(render(path.read_text(), "c", {"silver": "s", "gold": "g", "reporting": "r"})):
            m = re.match(r"CREATE OR REPLACE VIEW `c`\.r\.(\w+)", stmt)
            if m:
                out[m.group(1)] = stmt
    return out


def test_statement_split_and_placeholders() -> None:
    all_sql = [
        stmt
        for p in sorted(SQL_DIR.glob("*.sql"))
        for stmt in statements(render(p.read_text(), "c", {"silver": "s", "gold": "g", "reporting": "r"}))
    ]
    assert len(all_sql) == 13
    assert not [s for s in all_sql if "${" in s]
    assert any("array_join(s.reason_codes, '; ')" in s for s in all_sql)  # a ';' inside a string is not a split point


def test_facility_views_filter_on_my_facilities() -> None:
    v = views()
    assert set(v) >= {"daily_discrepancies", "compliance_event_detail", "monthly_review_list", "quarantine_summary"}
    for name, sql in v.items():
        if name in UNFILTERED_OK:
            continue
        assert any(f".{src}" in sql for src in FACILITY_SOURCES), name
        assert "facility_id IN (SELECT facility_id FROM `c`.r.my_facilities)" in sql, name


def test_patient_and_admission_ids_are_masked() -> None:
    for name, sql in views().items():
        for col in ("patient_id", "admission_id"):
            for use in re.findall(rf"[^\n,]*\b{col}\b[^\n,]*", sql.split(" FROM ")[0]):
                assert "mask_id(" in use, (name, use)
    mask = (SQL_DIR / "02_column_masks.sql").read_text()
    assert "is_member('rx_privileged')" in mask and "sha2(id, 256)" in mask


def test_access_seed_has_no_user_names() -> None:
    text = (SQL_DIR / "01_access_tables.sql").read_text()
    assert "current_user()" in text
    assert not re.search(r"[\w.+-]+@[\w-]+\.\w+", text)
