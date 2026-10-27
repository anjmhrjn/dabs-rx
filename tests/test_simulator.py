"""Simulator tests. No network: the formulary is built from config with fake NDCs."""

import copy
import csv
import hashlib
import statistics
from datetime import datetime
from pathlib import Path

import pytest

from generator.common import load_config
from generator.simulate import SOURCES, SimOutput, _user_metrics, run_simulation, write_outputs

CONFIG = load_config()
PATTERNS = list(CONFIG["simulation"]["injection"]["patterns"])


def fake_formulary() -> dict[str, dict[str, str]]:
    return {
        item["drug_key"]: {"drug_key": item["drug_key"], "ndc": f"{i + 1:011d}", "dea_schedule": item["expected_schedule"]}
        for i, item in enumerate(CONFIG["reference"]["formulary"])
    }


def small_config(seed: int | None = None) -> dict:
    sim = copy.deepcopy(CONFIG["simulation"])
    sim["days"] = 21
    sim["injection"]["start_day_range"] = [2, 5]
    if seed is not None:
        sim["seed"] = seed
    return sim


@pytest.fixture(scope="module")
def full() -> SimOutput:
    return run_simulation(copy.deepcopy(CONFIG["simulation"]), fake_formulary())


@pytest.fixture(scope="module")
def small_files(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("small")
    write_outputs(run_simulation(small_config(), fake_formulary()), root, 0)
    return root


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.csv")):
        h.update(str(p.relative_to(root)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def read(root: Path, source: str) -> list[dict[str, str]]:
    rows = []
    for p in sorted((root / "landing" / source).glob("*.csv")):
        with p.open() as f:
            rows += list(csv.DictReader(f))
    return rows


def test_same_seed_gives_identical_files(small_files: Path, tmp_path: Path) -> None:
    write_outputs(run_simulation(small_config(), fake_formulary()), tmp_path, 0)
    assert tree_hash(tmp_path) == tree_hash(small_files)


def test_different_seed_gives_different_files(small_files: Path, tmp_path: Path) -> None:
    write_outputs(run_simulation(small_config(seed=7), fake_formulary()), tmp_path, 0)
    assert tree_hash(tmp_path) != tree_hash(small_files)


def test_one_file_per_day_per_source(small_files: Path) -> None:
    for source, columns in SOURCES.items():
        files = sorted((small_files / "landing" / source).glob("*.csv"))
        assert [p.name for p in files] == [f"{source}_2026-03-{d:02d}.csv" for d in range(1, 22)]
        for p in files:
            assert p.read_text().splitlines()[0] == ",".join(columns)


def test_full_run_event_count_in_target_range(full: SimOutput) -> None:
    assert 150_000 <= len(full.sources["adc_events"]) <= 250_000
    assert 2_700 <= len(full.admissions) <= 3_300


def test_injected_rate_and_patterns(full: SimOutput) -> None:
    n_rn = sum(s.role == "RN" for s in full.staff)
    assert abs(len(full.injected) - 0.03 * n_rn) <= 1
    assert all(1 <= len(i.patterns) <= 2 for i in full.injected.values())
    assert sorted({p for i in full.injected.values() for p in i.patterns}) == sorted(PATTERNS)
    assert {r["user_id"] for r in full.labels} == set(full.injected)


def test_injected_users_stand_out_from_peers(full: SimOutput) -> None:
    home = {s.user_id: (s.unit_id, s.shift) for s in full.staff if s.role == "RN"}
    for inj in full.injected.values():
        metrics = _user_metrics(full, inj.start_ts)
        peers = [u for u in home if home[u] == home[inj.user_id] and u not in full.injected]
        for p in inj.patterns:
            peer_vals = sorted(metrics[u][p] for u in peers)
            assert metrics[inj.user_id][p] > 2 * statistics.median(peer_vals), (inj.user_id, p)
            assert metrics[inj.user_id][p] > peer_vals[int(0.75 * len(peer_vals))], (inj.user_id, p)


def test_injected_users_and_colluders_do_not_transfer(full: SimOutput) -> None:
    movers = {a.user_id for a in full.history if a.change_type == "TRANSFER"}
    assert movers
    assert not movers & (set(full.injected) | {i.colluder for i in full.injected.values()})


def test_corruption_rate_and_rules(full: SimOutput) -> None:
    n_bad = sum(full.corruption.values())
    assert n_bad / len(full.sources["adc_events"]) == pytest.approx(0.005, abs=0.0005)
    assert set(full.corruption) == set(CONFIG["simulation"]["corruption"]["rules"])


def test_clean_rows_reference_known_ids(full: SimOutput) -> None:
    sim = CONFIG["simulation"]
    users = {s.user_id for s in full.staff}
    cabinets = {c["cabinet_id"] for c in full.hospital.cabinets}
    ndcs = {r["ndc"] for r in fake_formulary().values()}
    start, end = full.tl.start, full.tl.end
    rows = full.sources["adc_events"]
    by_id = {r["event_id"]: r for r in rows}
    bad = 0
    for r in rows:
        ts = datetime.fromisoformat(r["event_ts"])
        clean = (
            r["user_id"] in users
            and r["cabinet_id"] in cabinets
            and r["ndc"] in ndcs
            and start <= ts < end
            and float(r["quantity"]) >= 0
        )
        if not clean:
            bad += 1
            continue
        assert r["event_type"] in {"PULL", "WASTE", "RETURN", "CANCEL", "OVERRIDE", "RESTOCK", "COUNT", "DISCREPANCY_RESOLVED"}
        if r["event_type"] in ("WASTE", "CANCEL", "RETURN"):
            parent = by_id[r["related_event_id"]]
            assert parent["event_type"] in ("PULL", "OVERRIDE")
            assert parent["event_id"] < r["event_id"]
        if r["event_type"] == "DISCREPANCY_RESOLVED":
            assert by_id[r["related_event_id"]]["event_type"] == "COUNT"
        if r["event_type"] == "WASTE" and r["witness_id"]:
            assert r["witness_id"] != r["user_id"]
    assert bad == sum(full.corruption.values())
    assert sim["corruption"]["unknown_ndc"] not in ndcs


def test_labels_only_under_labels_dir(small_files: Path) -> None:
    labels = (small_files / "labels" / "injected_labels.csv").read_text()
    assert all(p in PATTERNS for p in [line.split(",")[1] for line in labels.splitlines()[1:]])
    for p in small_files.rglob("*"):
        if p.is_file() and "labels" not in p.parts:
            text = p.read_text()
            assert not any(pattern in text for pattern in PATTERNS), p


def test_ed_has_more_overrides_than_med_surg(full: SimOutput) -> None:
    unit_of = {c["cabinet_id"]: c["unit_id"] for c in full.hospital.cabinets}
    rate = {}
    for unit in ("MAIN-ED", "MAIN-MSA"):
        pulls = [t for t in full.events if t.event_type in ("PULL", "OVERRIDE") and unit_of[t.cabinet_id] == unit]
        rate[unit] = sum(t.event_type == "OVERRIDE" for t in pulls) / len(pulls)
    assert rate["MAIN-ED"] > 5 * rate["MAIN-MSA"]


def test_adt_and_orders_cover_every_admission(small_files: Path) -> None:
    adt = read(small_files, "adt_events")
    orders = read(small_files, "order_events")
    admitted = {r["admission_id"] for r in adt if r["event_type"] == "ADMIT"}
    assert {r["admission_id"] for r in adt} == admitted
    assert {r["admission_id"] for r in orders} <= admitted
    pulls = [r for r in read(small_files, "adc_events") if r["event_type"] in ("PULL", "OVERRIDE") and r["admission_id"]]
    assert {r["admission_id"] for r in pulls} <= admitted
