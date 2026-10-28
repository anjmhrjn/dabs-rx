"""Precision@k and recall@k (src/eval/metrics.py)."""

from datetime import date

from metrics import at_k, evaluate, k_for, positives_for_month


def test_k_rounds_up_and_is_at_least_one() -> None:
    assert k_for(120, 0.05) == 6
    assert k_for(120, 0.10) == 12
    assert k_for(118, 0.05) == 6
    assert k_for(3, 0.05) == 1


def test_positive_when_pattern_started_by_month_end() -> None:
    labels = [{"user_id": "A", "start_date": "2026-04-30"}, {"user_id": "B", "start_date": "2026-05-01"}]
    assert positives_for_month(labels, date(2026, 4, 1)) == {"A"}
    assert positives_for_month(labels, date(2026, 5, 1)) == {"A", "B"}


def test_at_k() -> None:
    r = at_k(["A", "X", "B", "Y"], {"A", "B", "C"}, 2)
    assert r == {"k": 2, "positives": 3, "true_positives": 1, "precision": 0.5, "recall": 0.3333}


def test_evaluate_skips_months_without_positives_and_pools() -> None:
    labels = [{"user_id": "A", "start_date": "2026-04-10"}]
    ranks = {
        date(2026, 3, 1): {"peer": ["A", "B"], "baseline": ["B", "A"]},
        date(2026, 4, 1): {"peer": ["A", "B"], "baseline": ["B", "A"]},
        date(2026, 5, 1): {"peer": ["B", "A"], "baseline": ["A", "B"]},
    }
    rows = evaluate(ranks, labels, [0.5])
    assert {r["report_month"] for r in rows} == {"2026-04-01", "2026-05-01", "ALL"}
    pooled = {r["method"]: r for r in rows if r["report_month"] == "ALL"}
    assert pooled["peer"]["true_positives"] == 1 and pooled["peer"]["k"] == 2 and pooled["peer"]["recall"] == 0.5
    assert pooled["baseline"]["precision"] == 0.5
