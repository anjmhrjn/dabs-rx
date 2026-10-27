"""Deliberately bad ADC rows, so the pipeline's data quality expectations and quarantine have something to catch.

Each picked row gets exactly one defect. Rows carry no marker; per-rule counts go to the simulation manifest only.
"""

import random
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from generator.sim.timeline import fmt


def corrupt_rows(
    rows: list[dict[str, Any]], cfg: dict[str, Any], start: datetime, end: datetime, rng: random.Random
) -> Counter:
    """Mutate about cfg['rate'] of rows in place, cycling through the rules. Returns counts per rule."""
    n = round(cfg["rate"] * len(rows))
    picked = sorted(rng.sample(range(len(rows)), n))
    rules = list(cfg["rules"])
    counts: Counter = Counter()
    for i, idx in enumerate(picked):
        rule = rules[i % len(rules)]
        row = rows[idx]
        if rule == "null_user_id":
            row["user_id"] = ""
        elif rule == "null_cabinet_id":
            row["cabinet_id"] = ""
        elif rule == "negative_quantity":
            row["quantity"] = -abs(float(row["quantity"] or 1))
        elif rule == "ts_outside_window":
            ts = (
                start - timedelta(days=rng.uniform(1, 60))
                if rng.random() < 0.5
                else end + timedelta(days=rng.uniform(1, 60))
            )
            row["event_ts"] = fmt(ts)
        elif rule == "unknown_ndc":
            row["ndc"] = cfg["unknown_ndc"]
        elif rule == "unknown_cabinet":
            row["cabinet_id"] = cfg["unknown_cabinet"]
        else:
            raise ValueError(f"unknown corruption rule {rule!r}")
        counts[rule] += 1
    return counts
