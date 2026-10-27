"""Units, cabinets, par levels, staff, and unit transfers."""

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


@dataclass
class Unit:
    unit_id: str
    facility_id: str
    name: str
    kind: str
    nurse_ratio: int
    cabinet_ids: list[str]
    drug_keys: list[str]


@dataclass
class Staff:
    user_id: str
    display_name: str
    role: str  # RN, RPH, TECH
    facility_id: str
    unit_id: str
    shift: str
    high_acuity: bool = False


@dataclass
class Assignment:
    user_id: str
    display_name: str
    role: str
    facility_id: str
    unit_id: str
    shift: str
    effective_ts: datetime
    sequence_num: int
    change_type: str  # INITIAL or TRANSFER


@dataclass
class Hospital:
    units: dict[str, Unit]
    cabinets: list[dict[str, str]]
    par: dict[tuple[str, str], int] = field(default_factory=dict)  # (cabinet_id, drug_key) -> par level


def build_hospital(sim: dict[str, Any]) -> Hospital:
    units: dict[str, Unit] = {}
    cabinets: list[dict[str, str]] = []
    par: dict[tuple[str, str], int] = {}
    for u in sim["units"]:
        kind = sim["unit_kinds"][u["kind"]]
        cab_ids = [f"{u['unit_id']}-ADC{i + 1}" for i in range(u["cabinets"])]
        drug_keys = list(kind["drugs"])
        units[u["unit_id"]] = Unit(
            u["unit_id"], u["facility_id"], u["name"], u["kind"], kind["nurse_ratio"], cab_ids, drug_keys
        )
        for cab in cab_ids:
            cabinets.append({"cabinet_id": cab, "facility_id": u["facility_id"], "unit_id": u["unit_id"]})
            for dk in drug_keys:
                par[(cab, dk)] = sim["inventory"]["par_units"][sim["drugs"][dk]["unit_of_issue"]]
    return Hospital(units, cabinets, par)


def _names(sim: dict[str, Any], rng: random.Random, n: int) -> list[str]:
    combos = [f"{f} {last}" for f in sim["names"]["first"] for last in sim["names"]["last"]]
    if n > len(combos):
        raise ValueError(f"need {n} unique names, name list gives {len(combos)}")
    rng.shuffle(combos)
    return combos[:n]


def build_staff(sim: dict[str, Any], hospital: Hospital, rng: random.Random) -> list[Staff]:
    """RNs per unit split evenly between day and night, then pharmacists and techs per facility."""
    plan: list[tuple[str, str, str, str]] = []  # role, facility, unit, shift
    for u in sim["units"]:
        for i in range(u["nurses"]):
            plan.append(("RN", u["facility_id"], u["unit_id"], "day" if i % 2 == 0 else "night"))
    for fac in sim["facilities"]:
        plan += [("RPH", fac, f"{fac}-PHARM", "day")] * sim["pharmacists_per_facility"]
        plan += [("TECH", fac, f"{fac}-PHARM", "day")] * sim["techs_per_facility"]
    names = _names(sim, rng, len(plan))
    return [Staff(f"U{i + 1:04d}", names[i], *p) for i, p in enumerate(plan)]


def build_transfers(
    sim: dict[str, Any],
    hospital: Hospital,
    staff: list[Staff],
    start: datetime,
    rng: random.Random,
    exclude: frozenset[str] = frozenset(),
) -> list[Assignment]:
    """Initial assignment for everyone, then one transfer for a sample of RNs, ordered by effective time.

    `exclude` keeps injected users and their colluders on their home unit, so a transfer doesn't split a pattern.
    """
    history = [
        Assignment(s.user_id, s.display_name, s.role, s.facility_id, s.unit_id, s.shift, start, i + 1, "INITIAL")
        for i, s in enumerate(staff)
    ]
    rns = [s for s in staff if s.role == "RN"]
    cfg = sim["staffing"]
    eligible = [s for s in rns if s.user_id not in exclude]
    movers = rng.sample(eligible, round(cfg["transfer_rate"] * len(rns)))
    hour = sim["shifts"]["day"]["start_hour"]
    changes = []
    for s in movers:
        day = rng.randint(*cfg["transfer_day_range"])
        same_fac = [u for u in hospital.units.values() if u.facility_id == s.facility_id and u.unit_id != s.unit_id]
        if same_fac and rng.random() < 0.4:
            unit_id, shift = rng.choice(same_fac).unit_id, s.shift
        else:
            unit_id, shift = s.unit_id, "night" if s.shift == "day" else "day"
        changes.append((start + timedelta(days=day, hours=hour), s, unit_id, shift))
    changes.sort(key=lambda c: (c[0], c[1].user_id))
    seq = len(history)
    for ts, s, unit_id, shift in changes:
        seq += 1
        history.append(
            Assignment(s.user_id, s.display_name, s.role, s.facility_id, unit_id, shift, ts, seq, "TRANSFER")
        )
    return history
