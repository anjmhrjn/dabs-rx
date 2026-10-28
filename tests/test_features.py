"""Peer-group scoring (src/scoring/scoring.py). Pure Python, no Databricks."""

import statistics

import pytest

from scoring import ScoreConfig, loo_z, score_month

FEATURES = ("waste_ratio", "override_rate")
CFG = ScoreConfig(features=FEATURES, z_cap=4.0, min_pulls=20, min_wastes=5, min_peers=3, reason_min_z=2.0)


def row(user: str, unit: str = "MSA", shift: str = "day", pulls: int = 100, wastes: int = 20, **features) -> dict:
    base = {"waste_ratio": 0.10, "override_rate": 0.02}
    base.update(features)
    return {
        "user_id": user,
        "facility_id": "MAIN",
        "unit_id": unit,
        "shift": shift,
        "pulls": pulls,
        "wastes": wastes,
        **base,
    }


def test_loo_z_compares_against_the_others() -> None:
    values = [1.0, 2.0, 3.0, 10.0]
    z = loo_z(values, min_peers=3)
    others = [1.0, 2.0, 3.0]
    assert z[3] == pytest.approx((10.0 - statistics.mean(others)) / statistics.stdev(others))
    assert z[0] < 0


def test_loo_z_handles_missing_too_few_peers_and_zero_sd() -> None:
    assert loo_z([1.0, None, 2.0], min_peers=3) == [None, None, None]
    assert loo_z([5.0, 5.0, 5.0, 9.0], min_peers=3)[3] == 0.0


def test_outlier_ranks_first_with_capped_score_and_reason() -> None:
    rows = [row(f"U{i}", waste_ratio=0.10 + i * 0.005) for i in range(10)] + [row("X", waste_ratio=0.40)]
    out = score_month(rows, CFG)
    top = out[0]
    assert top["user_id"] == "X" and top["peer_rank"] == 1
    assert top["peer_score"] == pytest.approx(4.0)  # waste ratio z is huge, capped at 4; override z is ~0
    assert top["peer_reason_codes"][0].startswith("waste ratio ")
    assert "SD above peers (0.40 vs peer median 0.12)" in top["peer_reason_codes"][0]


def test_peer_grouping_removes_unit_confounder() -> None:
    # ED nurses all waste a lot; one Med/Surg nurse wastes more than Med/Surg peers but less than ED.
    ed = [row(f"E{i}", unit="ED", waste_ratio=0.50 + i * 0.01) for i in range(8)]
    ms = [row(f"M{i}", unit="MSA", waste_ratio=0.08 + i * 0.005) for i in range(8)] + [
        row("X", unit="MSA", waste_ratio=0.30)
    ]
    out = {r["user_id"]: r for r in score_month(ed + ms, CFG)}
    assert out["X"]["peer_rank"] == 1
    assert out["X"]["baseline_rank"] > 1  # globally, ED nurses look worse
    assert "SD above all nurses" not in " ".join(out["X"]["peer_reason_codes"])


def test_floor_skips_low_activity_and_waste_features() -> None:
    rows = [row(f"U{i}") for i in range(6)] + [
        row("LOW", pulls=5, waste_ratio=0.9),
        row("FEW", wastes=2, waste_ratio=0.9),
    ]
    out = {r["user_id"]: r for r in score_month(rows, CFG)}
    assert out["LOW"]["scored"] is False and out["LOW"]["peer_score"] is None
    assert "fewer than 20 pulls" in out["LOW"]["peer_reason_codes"][0]
    assert out["FEW"]["scored"] is True
    assert "waste_ratio" not in out["FEW"]["feature_z"]  # under min_wastes, waste features don't count


def test_transfer_month_keeps_higher_peer_score() -> None:
    rows = [row(f"A{i}", unit="MSA", waste_ratio=0.09 + i * 0.01) for i in range(5)]
    rows += [row(f"B{i}", unit="MSB", waste_ratio=0.09 + i * 0.01) for i in range(5)]
    rows += [row("T", unit="MSA", waste_ratio=0.10), row("T", unit="MSB", waste_ratio=0.35)]
    out = [r for r in score_month(rows, CFG) if r["user_id"] == "T"]
    assert len(out) == 1
    assert out[0]["unit_id"] == "MSB" and out[0]["peer_rank"] == 1


def test_ranks_are_deterministic_and_complete() -> None:
    rows = [row(f"U{i:02d}") for i in range(12)]
    out = score_month(rows, CFG)
    assert [r["peer_rank"] for r in out] == list(range(1, 13))
    assert [r["user_id"] for r in out] == sorted(r["user_id"] for r in out)  # ties broken by user_id
    assert all(r["scored_users"] == 12 for r in out)


def test_reason_codes_cap_displayed_z() -> None:
    rows = [row(f"U{i}", waste_ratio=0.10 + i * 0.0001) for i in range(10)] + [row("X", waste_ratio=0.40)]
    top = score_month(rows, CFG)[0]
    assert top["user_id"] == "X"
    assert top["peer_reason_codes"][0].startswith("waste ratio more than 10 SD above peers")
