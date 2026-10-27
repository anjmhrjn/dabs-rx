"""Data quality rules for ADC events, shared by silver.py (as SQL) and the local tests (as Python).

Each rule describes valid data. A row that fails any rule goes to silver.adc_events_quarantine with the names of
the rules it failed. Rules are written so a row with one defect fails exactly one rule.
No pyspark import here, so tests can load this module without Databricks.
"""

from datetime import datetime
from typing import Any


def adc_rules_sql(window_start: str, window_end: str) -> dict[str, str]:
    """Rule name -> SQL boolean over the typed ADC columns. Never NULL, so NOT(rule) is safe."""
    return {
        "event_id_not_null": "event_id IS NOT NULL",
        "user_id_not_null": "user_id IS NOT NULL",
        "cabinet_id_not_null": "cabinet_id IS NOT NULL",
        "quantity_not_negative": "coalesce(quantity >= 0, false)",
        "event_ts_in_window": (
            f"coalesce(event_ts >= TIMESTAMP '{window_start} 00:00:00' AND event_ts < TIMESTAMP '{window_end} 00:00:00', false)"
        ),
        "ndc_known": "coalesce(_ndc_known, false)",
        # A NULL cabinet already fails cabinet_id_not_null; don't count it twice.
        "cabinet_known": "cabinet_id IS NULL OR coalesce(_cabinet_known, false)",
    }


def failed_rules_py(
    row: dict[str, Any], known_ndcs: set[str], known_cabinets: set[str], window_start: str, window_end: str
) -> list[str]:
    """Python twin of adc_rules_sql for one raw CSV row (empty string = NULL). Used by local tests."""

    def val(k: str) -> str | None:
        return row.get(k) or None

    try:
        ts = datetime.strptime(row["event_ts"], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        ts = None
    try:
        qty = float(row["quantity"])
    except (TypeError, ValueError):
        qty = None
    start, end = datetime.fromisoformat(window_start), datetime.fromisoformat(window_end)
    checks = {
        "event_id_not_null": val("event_id") is not None,
        "user_id_not_null": val("user_id") is not None,
        "cabinet_id_not_null": val("cabinet_id") is not None,
        "quantity_not_negative": qty is not None and qty >= 0,
        "event_ts_in_window": ts is not None and start <= ts < end,
        "ndc_known": val("ndc") in known_ndcs,
        "cabinet_known": val("cabinet_id") is None or val("cabinet_id") in known_cabinets,
    }
    return [name for name, ok in checks.items() if not ok]
