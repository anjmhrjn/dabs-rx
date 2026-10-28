"""Precision@k and recall@k of the screening ranks against injected labels. Pure Python.

Only src/eval/ may read labels. A nurse counts as a positive in a month when one of their injected patterns started
on or before the last day of that month.
"""

import calendar
import math
from datetime import date


def k_for(n_users: int, fraction: float) -> int:
    """Number of nurses in the top `fraction` of n_users, rounded up, at least 1."""
    return max(1, math.ceil(n_users * fraction - 1e-9))


def month_end(month: date) -> date:
    return month.replace(day=calendar.monthrange(month.year, month.month)[1])


def positives_for_month(labels: list[dict[str, str]], month: date) -> set[str]:
    end = month_end(month)
    return {r["user_id"] for r in labels if date.fromisoformat(str(r["start_date"])[:10]) <= end}


def at_k(ranked: list[str], positives: set[str], k: int) -> dict[str, float | int | None]:
    hits = len(set(ranked[:k]) & positives)
    return {
        "k": k,
        "positives": len(positives),
        "true_positives": hits,
        "precision": round(hits / k, 4),
        "recall": round(hits / len(positives), 4) if positives else None,
    }


def evaluate(
    ranks: dict[date, dict[str, list[str]]], labels: list[dict[str, str]], fractions: list[float]
) -> list[dict]:
    """ranks: {month: {method: [user_id in rank order]}}. Months with no positives are skipped.

    Returns one row per month, method, and fraction, plus pooled 'ALL' rows (sums of hits, k, and positives).
    """
    rows = []
    pooled: dict[tuple[str, float], dict[str, int]] = {}
    for month in sorted(ranks):
        positives = positives_for_month(labels, month)
        if not positives:
            continue
        for method, ranked in sorted(ranks[month].items()):
            for frac in fractions:
                r = at_k(ranked, positives, k_for(len(ranked), frac))
                rows.append({"report_month": month.isoformat(), "method": method, "k_fraction": frac, **r})
                p = pooled.setdefault((method, frac), {"k": 0, "positives": 0, "true_positives": 0})
                for key in p:
                    p[key] += r[key]
    for (method, frac), p in sorted(pooled.items()):
        rows.append(
            {
                "report_month": "ALL",
                "method": method,
                "k_fraction": frac,
                **p,
                "precision": round(p["true_positives"] / p["k"], 4),
                "recall": round(p["true_positives"] / p["positives"], 4),
            }
        )
    return rows
