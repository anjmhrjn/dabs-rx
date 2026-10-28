"""Peer-group screening scores for nurses, per month. Pure Python, no Spark, so tests run locally.

For each feature, a nurse is compared with the other nurses in the same peer group (facility, unit, shift) that
month using a leave-one-out z-score: (value - mean of the others) / SD of the others. The composite score is the sum
of positive z-scores, each capped. A baseline score uses the same formula against all nurses that month, with no
peer grouping. Scores are a screening aid for pharmacy review, not a determination.
"""

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

FEATURE_LABELS = {
    "pulls_per_patient_day": "pulls per patient-day",
    "waste_ratio": "waste ratio",
    "unwitnessed_waste_rate": "unwitnessed waste rate",
    "median_minutes_to_waste": "median minutes from pull to waste",
    "override_rate": "override rate",
    "cancel_rate": "cancel rate",
    "no_active_order_rate": "pulls without an active order",
    "pulls_after_patient_left_rate": "pulls after discharge or transfer",
    "witness_concentration": "share of wastes with the same witness",
}
# Features that only mean something with enough wastes in the month.
WASTE_FEATURES = {"waste_ratio", "unwitnessed_waste_rate", "median_minutes_to_waste", "witness_concentration"}
PEER_KEY = ("facility_id", "unit_id", "shift")


@dataclass(frozen=True)
class ScoreConfig:
    features: tuple[str, ...]
    z_cap: float = 4.0
    min_pulls: int = 20
    min_wastes: int = 5
    min_peers: int = 3
    reason_min_z: float = 2.0
    max_reasons: int = 3
    # Above this, reason codes say "more than N SD". Tiny peer SDs (most peers at 0) give unreadable z values.
    reason_z_display_cap: float = 10.0


def loo_z(values: list[float | None], min_peers: int) -> list[float | None]:
    """Leave-one-out z for each value against the others. None where the value is missing or peers are too few.

    When every other value is identical (SD 0), a value that differs is as far from peers as possible: z is +/- inf,
    which the composite caps like any large z. Equal to the others gives 0.
    """
    out: list[float | None] = []
    for i, x in enumerate(values):
        others = [v for j, v in enumerate(values) if j != i and v is not None]
        if x is None or len(others) < min_peers:
            out.append(None)
            continue
        mean = statistics.mean(others)
        sd = statistics.stdev(others) if len(others) > 1 else 0.0
        if sd == 0:
            out.append(0.0 if x == mean else math.copysign(math.inf, x - mean))
        else:
            out.append((x - mean) / sd)
    return out


def _value(row: dict[str, Any], feature: str, cfg: ScoreConfig) -> float | None:
    if feature in WASTE_FEATURES and (row.get("wastes") or 0) < cfg.min_wastes:
        return None
    v = row.get(feature)
    return None if v is None else float(v)


def _fmt(feature: str, x: float) -> str:
    return f"{x:.0f}" if feature == "median_minutes_to_waste" else f"{x:.2f}"


def _composite(z: dict[str, float | None], cap: float) -> float:
    return round(sum(min(max(v, 0.0), cap) for v in z.values() if v is not None), 4)


# How reason codes name the comparison group: (group, median label).
COMPARED_WITH = {"peer": ("peers", "peer median"), "baseline": ("all nurses", "all-nurse median")}


def _reasons(
    z: dict[str, float | None],
    value: dict[str, float | None],
    ref: dict[str, float | None],
    cfg: ScoreConfig,
    kind: str,
) -> list[str]:
    group, median = COMPARED_WITH[kind]
    top = sorted(((v, f) for f, v in z.items() if v is not None and v >= cfg.reason_min_z), reverse=True)

    def reason(v: float, f: str) -> str:
        if math.isinf(v):  # every peer had the same value
            return f"{FEATURE_LABELS[f]} higher than every one of the {group} ({_fmt(f, value[f])}; all {group} {_fmt(f, ref[f])})"
        size = f"more than {cfg.reason_z_display_cap:.0f} SD" if v > cfg.reason_z_display_cap else f"{v:.1f} SD"
        return f"{FEATURE_LABELS[f]} {size} above {group} ({_fmt(f, value[f])} vs {median} {_fmt(f, ref[f])})"

    return [reason(v, f) for v, f in top[: cfg.max_reasons]]


def _score_set(rows: list[dict[str, Any]], cfg: ScoreConfig, kind: str) -> list[dict[str, Any]]:
    """z, composite, and reasons for each row against the other rows in the same set."""
    results: list[dict[str, Any]] = [{} for _ in rows]
    for f in cfg.features:
        vals = [_value(r, f, cfg) for r in rows]
        zs = loo_z(vals, cfg.min_peers)
        for i, (v, z) in enumerate(zip(vals, zs)):
            others = [x for j, x in enumerate(vals) if j != i and x is not None]
            results[i].setdefault("z", {})[f] = None if z is None else round(z, 3)
            results[i].setdefault("value", {})[f] = v
            results[i].setdefault("ref", {})[f] = statistics.median(others) if others else None
    for res in results:
        res["score"] = _composite(res["z"], cfg.z_cap)
        res["reasons"] = _reasons(res["z"], res["value"], res["ref"], cfg, kind)
    return results


def score_month(rows: list[dict[str, Any]], cfg: ScoreConfig) -> list[dict[str, Any]]:
    """Score one month of user_activity_monthly rows. Returns one row per nurse with ranks.

    A nurse with rows in two peer groups (transfer month) is scored in each and keeps the higher peer score.
    Nurses below min_pulls in every row are returned unscored.
    """
    eligible = [r for r in rows if (r.get("pulls") or 0) >= cfg.min_pulls]
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, r in enumerate(eligible):
        groups[tuple(r[k] for k in PEER_KEY)].append(i)
    peer: list[dict[str, Any]] = [{} for _ in eligible]
    for idx in groups.values():
        for i, res in zip(idx, _score_set([eligible[i] for i in idx], cfg, "peer")):
            res["peer_n"] = len(idx) - 1
            peer[i] = res
    base = _score_set(eligible, cfg, "baseline")

    by_user: dict[str, list[tuple[dict, dict, dict]]] = defaultdict(list)
    for r, p, b in zip(eligible, peer, base):
        by_user[r["user_id"]].append((r, p, b))
    best: dict[str, dict[str, Any]] = {}
    for user_id, cands in by_user.items():
        r, p, _ = max(cands, key=lambda c: c[1]["score"])  # peer group with the higher peer score
        b = max((c[2] for c in cands), key=lambda x: x["score"])
        best[user_id] = {
            "user_id": user_id,
            **{k: r[k] for k in PEER_KEY},
            "pulls": r["pulls"],
            "peer_n": p["peer_n"],
            "peer_score": p["score"],
            "peer_reason_codes": p["reasons"],
            "baseline_score": b["score"],
            "baseline_reason_codes": b["reasons"],
            # Clipped for storage; +/-99 means the nurse differed from peers who all had the same value.
            "feature_z": {f: max(-99.0, min(99.0, z)) for f, z in p["z"].items() if z is not None},
            "scored": True,
        }

    scored = list(best.values())
    for key, rank_col in (("peer_score", "peer_rank"), ("baseline_score", "baseline_rank")):
        for rank, row in enumerate(sorted(scored, key=lambda x: (-x[key], x["user_id"])), start=1):
            row[rank_col] = rank
    for row in scored:
        row["scored_users"] = len(scored)

    unscored: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["user_id"] not in best and r["user_id"] not in unscored:
            unscored[r["user_id"]] = {
                "user_id": r["user_id"],
                **{k: r[k] for k in PEER_KEY},
                "pulls": r.get("pulls") or 0,
                "peer_n": None,
                "peer_score": None,
                "peer_rank": None,
                "peer_reason_codes": [f"not scored: fewer than {cfg.min_pulls} pulls this month"],
                "baseline_score": None,
                "baseline_rank": None,
                "baseline_reason_codes": [],
                "feature_z": {},
                "scored": False,
                "scored_users": len(scored),
            }
    return sorted(scored, key=lambda x: x["peer_rank"]) + sorted(unscored.values(), key=lambda x: x["user_id"])
