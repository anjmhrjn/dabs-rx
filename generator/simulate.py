"""Simulate synthetic ADC, ADT, order, and staff assignment feeds for the two-hospital system.

Local only. Reads generator/config.yaml and data/reference/formulary.csv (from build_formulary.py).
Writes one CSV per day per source under data/landing/, static reference CSVs under data/reference/,
injected labels under data/labels/, and data/sim_manifest.json. All records are synthetic.

Run: uv run python -m generator.simulate [--days N] [--seed N] [--out DIR]
"""

import argparse
import csv
import json
import random
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from generator.common import DATA_DIR, REFERENCE_DIR, load_config, write_csv
from generator.sim.adc import AdcSimulator, Injected, Tx, pick_injected
from generator.sim.clinical import Admission, Order, simulate_admissions, simulate_orders
from generator.sim.corrupt import corrupt_rows
from generator.sim.hospital import Assignment, Hospital, Staff, build_hospital, build_staff, build_transfers
from generator.sim.timeline import Timeline, fmt

ADC_COLUMNS = [
    "event_id",
    "event_ts",
    "event_type",
    "facility_id",
    "cabinet_id",
    "ndc",
    "user_id",
    "witness_id",
    "patient_id",
    "admission_id",
    "order_id",
    "quantity",
    "expected_quantity",
    "related_event_id",
    "resolution_code",
]
ADT_COLUMNS = [
    "adt_event_id",
    "event_type",
    "event_ts",
    "admission_id",
    "patient_id",
    "facility_id",
    "unit_id",
    "from_unit_id",
]
ORDER_COLUMNS = [
    "order_event_id",
    "event_type",
    "event_ts",
    "order_id",
    "admission_id",
    "patient_id",
    "facility_id",
    "unit_id",
    "ndc",
    "dose_mg",
    "is_prn",
    "interval_h",
    "pharmacist_id",
]
STAFF_COLUMNS = [
    "user_id",
    "display_name",
    "role",
    "facility_id",
    "unit_id",
    "shift",
    "effective_ts",
    "sequence_num",
    "change_type",
]
SOURCES = {
    "adc_events": ADC_COLUMNS,
    "adt_events": ADT_COLUMNS,
    "order_events": ORDER_COLUMNS,
    "staff_assignments": STAFF_COLUMNS,
}


@dataclass
class SimOutput:
    tl: Timeline
    hospital: Hospital
    staff: list[Staff]
    history: list[Assignment]
    admissions: list[Admission]
    orders: list[Order]
    injected: dict[str, Injected]
    high_acuity: list[str]
    events: list[Tx]
    sources: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # rows carry a "_day" key for file placement
    corruption: Counter = field(default_factory=Counter)
    reference: dict[str, tuple[list[str], list[dict[str, Any]]]] = field(default_factory=dict)
    labels: list[dict[str, Any]] = field(default_factory=list)
    confounders: list[dict[str, Any]] = field(default_factory=list)


def load_formulary(path: Path = REFERENCE_DIR / "formulary.csv") -> dict[str, dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"{path} missing. Run generator.fetch_reference and generator.build_formulary first.")
    with path.open() as f:
        return {r["drug_key"]: r for r in csv.DictReader(f)}


def _stream(seed: int, name: str) -> random.Random:
    """Independent random stream per component, so tuning one part doesn't shift the others."""
    return random.Random(f"{seed}:{name}")


def run_simulation(sim: dict[str, Any], formulary: dict[str, dict[str, str]]) -> SimOutput:
    seed = sim["seed"]
    tl = Timeline.from_config(sim)
    hospital = build_hospital(sim)
    staff = build_staff(sim, hospital, _stream(seed, "staff"))
    injected, high_acuity = pick_injected(sim, staff, tl, _stream(seed, "injected"))
    keep_home = frozenset(injected) | frozenset(i.colluder for i in injected.values() if i.colluder)
    history = build_transfers(sim, hospital, staff, tl.start, _stream(seed, "transfers"), keep_home)
    admissions = simulate_admissions(sim, hospital, tl, _stream(seed, "admissions"))
    orders = simulate_orders(sim, admissions, staff, tl, _stream(seed, "orders"))
    schedules = {k: formulary[k]["dea_schedule"] for k in sim["drugs"]}
    adc = AdcSimulator(
        sim, tl, hospital, staff, history, admissions, orders, injected, high_acuity, schedules, _stream(seed, "adc")
    ).run()
    out = SimOutput(tl, hospital, staff, history, admissions, orders, injected, high_acuity, adc.events)
    out.sources["adc_events"] = _adc_rows(adc.events, hospital, formulary)
    out.corruption = corrupt_rows(
        out.sources["adc_events"], sim["corruption"], tl.start, tl.end, _stream(seed, "corrupt")
    )
    out.sources["adt_events"] = _adt_rows(admissions, tl)
    out.sources["order_events"] = _order_rows(orders, formulary, tl)
    out.sources["staff_assignments"] = _staff_rows(history)
    out.reference = _reference_tables(sim, hospital, formulary)
    out.labels = [
        {"user_id": i.user_id, "pattern": p, "start_date": i.start_ts.date().isoformat()}
        for i in sorted(injected.values(), key=lambda x: x.user_id)
        for p in i.patterns
    ]
    out.confounders = [{"user_id": u, "confounder": "high_acuity"} for u in high_acuity] + [
        {"user_id": i.colluder, "confounder": "collusion_witness"} for i in injected.values() if i.colluder
    ]
    return out


def _adc_rows(events: list[Tx], hospital: Hospital, formulary: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    cab_fac = {c["cabinet_id"]: c["facility_id"] for c in hospital.cabinets}
    ordered = sorted(events, key=lambda t: (t.ts, t.seq))
    ids = {t.k: f"E{i + 1:07d}" for i, t in enumerate(ordered)}
    return [
        {
            "_day": t.ts.date().isoformat(),
            "event_id": ids[t.k],
            "event_ts": fmt(t.ts),
            "event_type": t.event_type,
            "facility_id": cab_fac[t.cabinet_id],
            "cabinet_id": t.cabinet_id,
            "ndc": formulary[t.drug_key]["ndc"],
            "user_id": t.user_id,
            "witness_id": t.witness_id,
            "patient_id": t.patient_id,
            "admission_id": t.admission_id,
            "order_id": t.order_id,
            "quantity": _num(t.quantity),
            "expected_quantity": "" if t.expected_quantity is None else _num(t.expected_quantity),
            "related_event_id": ids.get(t.related, "") if t.related else "",
            "resolution_code": t.resolution_code,
        }
        for t in ordered
    ]


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def _adt_rows(admissions: list[Admission], tl: Timeline) -> list[dict[str, Any]]:
    rows = []
    for adm in admissions:
        for i, st in enumerate(adm.stays):
            kind, prev = ("ADMIT", "") if i == 0 else ("TRANSFER", adm.stays[i - 1].unit_id)
            rows.append((st.start, kind, adm, st.unit_id, prev))
        if adm.discharge_ts < tl.end:
            rows.append((adm.discharge_ts, "DISCHARGE", adm, adm.stays[-1].unit_id, ""))
    rows = [r for r in rows if r[0] < tl.end]
    rows.sort(key=lambda r: (r[0], r[2].admission_id, r[1]))
    return [
        {
            "_day": ts.date().isoformat(),
            "adt_event_id": f"T{i + 1:07d}",
            "event_type": kind,
            "event_ts": fmt(ts),
            "admission_id": adm.admission_id,
            "patient_id": adm.patient_id,
            "facility_id": adm.facility_id,
            "unit_id": unit,
            "from_unit_id": prev,
        }
        for i, (ts, kind, adm, unit, prev) in enumerate(rows)
    ]


def _order_rows(orders: list[Order], formulary: dict[str, dict[str, str]], tl: Timeline) -> list[dict[str, Any]]:
    rows = []
    for o in orders:
        rows.append((o.start, "NEW", o, ""))
        rows.append((o.verify_ts, "VERIFY", o, o.pharmacist_id))
        if o.stop < tl.end:
            rows.append((o.stop, "DISCONTINUE", o, ""))
    rows = [r for r in rows if r[0] < tl.end]
    rows.sort(key=lambda r: (r[0], r[2].order_id, r[1]))
    return [
        {
            "_day": ts.date().isoformat(),
            "order_event_id": f"R{i + 1:07d}",
            "event_type": kind,
            "event_ts": fmt(ts),
            "order_id": o.order_id,
            "admission_id": o.stay.admission_id,
            "patient_id": o.stay.patient_id,
            "facility_id": o.stay.facility_id,
            "unit_id": o.stay.unit_id,
            "ndc": formulary[o.drug_key]["ndc"],
            "dose_mg": _num(o.dose_mg),
            "is_prn": str(o.is_prn).lower(),
            "interval_h": _num(o.interval_h),
            "pharmacist_id": rph,
        }
        for i, (ts, kind, o, rph) in enumerate(rows)
    ]


def _staff_rows(history: list[Assignment]) -> list[dict[str, Any]]:
    return [
        {
            "_day": a.effective_ts.date().isoformat(),
            "user_id": a.user_id,
            "display_name": a.display_name,
            "role": a.role,
            "facility_id": a.facility_id,
            "unit_id": a.unit_id,
            "shift": a.shift,
            "effective_ts": fmt(a.effective_ts),
            "sequence_num": a.sequence_num,
            "change_type": a.change_type,
        }
        for a in history
    ]


def _reference_tables(
    sim: dict[str, Any], hospital: Hospital, formulary: dict[str, dict[str, str]]
) -> dict[str, tuple[list[str], list[dict[str, Any]]]]:
    units = [
        {
            "unit_id": u.unit_id,
            "facility_id": u.facility_id,
            "unit_name": u.name,
            "unit_kind": u.kind,
            "nurse_ratio": u.nurse_ratio,
        }
        for u in hospital.units.values()
    ]
    below = sim["inventory"]["restock_below_fraction"]
    par = [
        {
            "cabinet_id": cab,
            "drug_key": dk,
            "ndc": formulary[dk]["ndc"],
            "par_level": p,
            "restock_below": round(p * below),
        }
        for (cab, dk), p in hospital.par.items()
    ]
    dispense = [
        {
            "drug_key": dk,
            "ndc": formulary[dk]["ndc"],
            "unit_of_issue": d["unit_of_issue"],
            "mg_per_unit": _num(d["mg_per_unit"]),
            "unit_volume_ml": _num(d["unit_volume_ml"]) if "unit_volume_ml" in d else "",
        }
        for dk, d in sim["drugs"].items()
    ]
    return {
        "units.csv": (list(units[0]), units),
        "cabinets.csv": (["cabinet_id", "facility_id", "unit_id"], hospital.cabinets),
        "cabinet_par.csv": (list(par[0]), par),
        "dispense_units.csv": (list(dispense[0]), dispense),
    }


def write_outputs(out: SimOutput, root: Path, seed: int) -> dict[str, Any]:
    """Write every file and the manifest. Returns the manifest."""
    days = [(out.tl.start.date() + timedelta(days=d)).isoformat() for d in range(out.tl.days)]
    files: dict[str, int] = {}
    for source, columns in SOURCES.items():
        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in out.sources[source]:
            by_day[r["_day"]].append(r)
        for day in days:
            write_csv(root / "landing" / source / f"{source}_{day}.csv", columns, by_day.get(day, []))
        files[source] = len(days)
    for name, (columns, rows) in out.reference.items():
        write_csv(root / "reference" / name, columns, rows)
    write_csv(root / "labels" / "injected_labels.csv", ["user_id", "pattern", "start_date"], out.labels)
    write_csv(root / "labels" / "confounders.csv", ["user_id", "confounder"], out.confounders)
    manifest = {
        "note": "All records are synthetic.",
        "seed": seed,
        "window": {"start": fmt(out.tl.start), "end_exclusive": fmt(out.tl.end), "days": out.tl.days},
        "rows": {s: len(out.sources[s]) for s in SOURCES},
        "files": files,
        "corrupted_adc_rows": dict(sorted(out.corruption.items())),
        "staff": dict(Counter(s.role for s in out.staff)),
        "admissions": len(out.admissions),
        "orders": len(out.orders),
    }
    (root / "sim_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def summarize(out: SimOutput) -> list[str]:
    """Plain-text summary. Computed on events before corruption."""
    ev, tl = out.events, out.tl
    unit_of = {c["cabinet_id"]: c["unit_id"] for c in out.hospital.cabinets}
    n_rn = sum(s.role == "RN" for s in out.staff)
    n_bad = sum(out.corruption.values())
    patterns = dict(Counter(p for i in out.injected.values() for p in i.patterns))
    lines = [
        f"window {tl.start.date()} to {tl.end.date()} (exclusive), {tl.days} days",
        f"staff {dict(Counter(s.role for s in out.staff))}, transfers {sum(a.change_type == 'TRANSFER' for a in out.history)}",
        f"admissions {len(out.admissions)}, stays {sum(len(a.stays) for a in out.admissions)}, orders {len(out.orders)}",
        f"ADC events {len(ev)}: {dict(Counter(t.event_type for t in ev).most_common())}",
        f"rows per source: { {s: len(r) for s, r in out.sources.items()} }",
        f"corrupted ADC rows {n_bad} ({n_bad / len(ev):.2%}): {dict(sorted(out.corruption.items()))}",
        f"injected users {len(out.injected)} of {n_rn} RNs ({len(out.injected) / n_rn:.1%}), patterns {patterns}",
        f"high-acuity nurses {len(out.high_acuity)}",
    ]
    by_unit = Counter(
        (unit_of[t.cabinet_id], tl.shift_name(tl.shift_index(t.ts))) for t in ev if t.event_type in ("PULL", "OVERRIDE")
    )
    lines.append("pulls by unit and shift: " + ", ".join(f"{u}/{s} {n}" for (u, s), n in sorted(by_unit.items())))

    lines += pattern_check(out)
    lines += confounder_check(out)
    return lines


def confounder_check(out: SimOutput) -> list[str]:
    """Benign differences the peer grouping has to absorb: unit and shift rates, high-acuity volume."""
    tl = out.tl
    unit_of = {c["cabinet_id"]: c["unit_id"] for c in out.hospital.cabinets}
    c: dict[tuple[str, str], Counter] = defaultdict(Counter)
    pt_days: dict[tuple[str, str], set] = defaultdict(set)
    for t in out.events:
        if t.event_type not in ("PULL", "OVERRIDE", "WASTE"):
            continue
        key = (unit_of[t.cabinet_id], tl.shift_name(tl.shift_index(t.ts)))
        if t.event_type == "WASTE":
            c[key]["wastes"] += 1
            continue
        c[key]["pulls"] += 1
        c[key]["overrides"] += t.event_type == "OVERRIDE"
        pt_days[key].add((t.patient_id, t.ts.date()))
    lines = ["unit/shift          pulls  pulls/pt-day  override/pull  waste/pull"]
    for key in sorted(c):
        k = c[key]
        lines.append(
            f"  {key[0]:9} {key[1]:5} {k['pulls']:7} {k['pulls'] / len(pt_days[key]):11.2f} "
            f"{k['overrides'] / k['pulls']:14.3f} {k['wastes'] / k['pulls']:11.3f}"
        )
    pulls = Counter(t.user_id for t in out.events if t.event_type in ("PULL", "OVERRIDE"))
    rn = [s.user_id for s in out.staff if s.role == "RN"]
    others = [pulls[u] for u in rn if u not in out.high_acuity and u not in out.injected]
    lines.append(
        f"pulls per RN: median {statistics.median(pulls[u] for u in rn)}, "
        f"high-acuity median {statistics.median(pulls[u] for u in out.high_acuity)} vs others {statistics.median(others)}"
    )
    return lines


def _user_metrics(out: SimOutput, since: datetime) -> dict[str, dict[str, float]]:
    """Per-user metric for each injected pattern, over events at or after `since`."""
    stay_end = {(st.admission_id, st.unit_id): st.end for a in out.admissions for st in a.stays}
    unit_of = {c["cabinet_id"]: c["unit_id"] for c in out.hospital.cabinets}
    by_k = {t.k: t for t in out.events}
    c: dict[str, Counter] = defaultdict(Counter)
    delays: dict[str, list[float]] = defaultdict(list)
    witnesses: dict[str, Counter] = defaultdict(Counter)
    for t in out.events:
        if t.ts < since:
            continue
        u = c[t.user_id]
        if t.event_type in ("PULL", "OVERRIDE"):
            u["pulls"] += 1
            u["no_order"] += not t.order_id
            end = stay_end.get((t.admission_id, unit_of[t.cabinet_id]))
            u["after_left"] += bool(end and t.ts > end)
        elif t.event_type == "WASTE":
            u["wastes"] += 1
            u["unwitnessed"] += not t.witness_id
            delays[t.user_id].append((t.ts - by_k[t.related].ts).total_seconds() / 60)
            if t.witness_id:
                witnesses[t.user_id][t.witness_id] += 1
        elif t.event_type == "CANCEL":
            u["cancels"] += 1

    def ratio(user: str, num: str, den: str) -> float:
        return c[user][num] / c[user][den] if c[user][den] else 0.0

    users = [s.user_id for s in out.staff if s.role == "RN"]
    return {
        user: {
            "high_waste_ratio": ratio(user, "wastes", "pulls"),
            "unwitnessed_waste": ratio(user, "unwitnessed", "wastes"),
            "witness_collusion": (max(witnesses[user].values()) / sum(witnesses[user].values()))
            if witnesses[user]
            else 0.0,
            "delayed_waste": statistics.median(delays[user]) if delays[user] else 0.0,
            "pull_no_order": ratio(user, "no_order", "pulls"),
            "pull_after_discharge_or_transfer": ratio(user, "after_left", "pulls"),
            "high_cancel": ratio(user, "cancels", "pulls"),
        }
        for user in users
    }


def pattern_check(out: SimOutput) -> list[str]:
    """Each injected user's pattern metric after their start date vs the median of their home unit-shift peers."""
    home = {s.user_id: (s.unit_id, s.shift) for s in out.staff if s.role == "RN"}
    lines = ["injected pattern check (after start date)        user    peer median"]
    for inj in sorted(out.injected.values(), key=lambda i: i.user_id):
        metrics = _user_metrics(out, inj.start_ts)
        peers = [u for u in home if home[u] == home[inj.user_id] and u not in out.injected]
        for p in inj.patterns:
            peer = statistics.median(metrics[u][p] for u in peers)
            lines.append(
                f"  {inj.user_id} {home[inj.user_id][0]:9} {home[inj.user_id][1]:5} {p:33} {metrics[inj.user_id][p]:7.3f} {peer:9.3f}"
            )
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, help="override simulation length")
    parser.add_argument("--seed", type=int, help="override seed")
    parser.add_argument("--out", type=Path, default=DATA_DIR, help="output root (default: data/)")
    args = parser.parse_args()
    sim = load_config()["simulation"]
    if args.days:
        sim["days"] = args.days
    if args.seed is not None:
        sim["seed"] = args.seed
    out = run_simulation(sim, load_formulary())
    manifest = write_outputs(out, args.out, sim["seed"])
    print("\n".join(summarize(out)))
    print(f"files per source: {manifest['files']}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
