"""Silver data quality rules against simulator output: exactly the corrupted rows fail, one rule each."""

import copy

from rules import adc_rules_sql, failed_rules_py

from generator.common import load_config
from generator.simulate import run_simulation
from tests.test_simulator import fake_formulary

CONFIG = load_config()
# Simulator corruption rule -> silver rule it must trip.
EXPECTED_RULE = {
    "null_user_id": "user_id_not_null",
    "null_cabinet_id": "cabinet_id_not_null",
    "negative_quantity": "quantity_not_negative",
    "ts_outside_window": "event_ts_in_window",
    "unknown_ndc": "ndc_known",
    "unknown_cabinet": "cabinet_known",
}


def run(rate: float) -> list[dict]:
    sim = copy.deepcopy(CONFIG["simulation"])
    sim["days"] = 30
    sim["corruption"]["rate"] = rate
    return run_simulation(sim, fake_formulary()).sources["adc_events"]


def test_sql_and_python_rules_have_the_same_names() -> None:
    assert set(adc_rules_sql("2026-03-01", "2026-03-31")) == set(EXPECTED_RULE.values()) | {"event_id_not_null"}


def test_only_corrupted_rows_fail_and_each_fails_one_rule() -> None:
    clean = {r["event_id"]: r for r in run(0.0)}
    dirty = run(0.005)
    ndcs = {r["ndc"] for r in fake_formulary().values()}
    cabinets = {r["cabinet_id"] for r in clean.values()}
    failed = {}
    for r in dirty:
        rules = failed_rules_py(r, ndcs, cabinets, "2026-03-01", "2026-03-31")
        if rules:
            failed[r["event_id"]] = rules
    corrupted = {r["event_id"] for r in dirty if r != clean[r["event_id"]]}
    assert set(failed) == corrupted
    assert all(len(v) == 1 for v in failed.values())
    assert {v[0] for v in failed.values()} == set(EXPECTED_RULE.values())
