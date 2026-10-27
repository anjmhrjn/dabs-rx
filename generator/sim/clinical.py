"""Admissions, unit stays, controlled substance orders, and the dose times each order produces."""

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from generator.sim.hospital import Hospital, Staff
from generator.sim.timeline import Timeline


@dataclass
class Stay:
    """One admission's time on one unit. An ED visit admitted to Med/Surg has two stays."""

    stay_id: int
    admission_id: str
    patient_id: str
    facility_id: str
    unit_id: str
    start: datetime
    end: datetime
    ends_in: str  # TRANSFER or DISCHARGE


@dataclass
class Admission:
    admission_id: str
    patient_id: str
    facility_id: str
    stays: list[Stay]

    @property
    def admit_ts(self) -> datetime:
        return self.stays[0].start

    @property
    def discharge_ts(self) -> datetime:
        return self.stays[-1].end


@dataclass
class Dose:
    ts: datetime
    late: bool = False  # benign pull after the order stopped (documentation lag)


@dataclass
class Order:
    order_id: str
    stay: Stay
    drug_key: str
    dose_mg: float
    is_prn: bool
    interval_h: float
    start: datetime
    stop: datetime
    verify_ts: datetime
    pharmacist_id: str
    stopped_early: bool
    doses: list[Dose] = field(default_factory=list)


def poisson(rng: random.Random, lam: float) -> int:
    threshold, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= threshold:
            return k
        k += 1


def weighted(rng: random.Random, weights: dict[Any, float]) -> Any:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def _los(rng: random.Random, bounds: list[float]) -> timedelta:
    lo, hi = bounds
    return timedelta(hours=rng.triangular(lo, hi, lo + (hi - lo) * 0.3))


def simulate_admissions(sim: dict[str, Any], hospital: Hospital, tl: Timeline, rng: random.Random) -> list[Admission]:
    """Arrivals per entry unit per day (Poisson). ED visits may continue to Med/Surg as a second stay."""
    raw = []
    for day in range(tl.days):
        for entry in sim["arrivals"]:
            unit = hospital.units[entry["unit_id"]]
            for _ in range(poisson(rng, entry["per_day"])):
                hour = rng.uniform(0, 24) if unit.kind == "ED" else rng.uniform(8, 20)
                raw.append((tl.start + timedelta(days=day, hours=hour), unit.unit_id))
    raw.sort()

    admissions: list[Admission] = []
    discharged: dict[str, list[tuple[datetime, str]]] = {f: [] for f in sim["facilities"]}
    stay_id = 0
    for i, (ts, unit_id) in enumerate(raw):
        unit = hospital.units[unit_id]
        fac = unit.facility_id
        prior = [p for d, p in discharged[fac] if d < ts]
        if prior and rng.random() < sim["readmission_rate"]:
            patient_id = rng.choice(prior)
        else:
            patient_id = f"P{i + 1:06d}"
        admission_id = f"A{i + 1:06d}"
        segments = [(unit_id, ts, ts + _los(rng, sim["unit_kinds"][unit.kind]["los_hours"]))]
        if unit.kind == "ED" and rng.random() < sim["ed_admit_rate"]:
            next_unit = weighted(rng, sim["ed_admit_units"])
            seg_start = segments[0][2]
            kind = hospital.units[next_unit].kind
            segments.append((next_unit, seg_start, seg_start + _los(rng, sim["unit_kinds"][kind]["los_hours"])))
        stays = []
        for j, (u, s, e) in enumerate(segments):
            stay_id += 1
            ends_in = "TRANSFER" if j < len(segments) - 1 else "DISCHARGE"
            stays.append(Stay(stay_id, admission_id, patient_id, fac, u, s, e, ends_in))
        admissions.append(Admission(admission_id, patient_id, fac, stays))
        discharged[fac].append((stays[-1].end, patient_id))
    return admissions


def _dose_times(
    sim: dict[str, Any], tl: Timeline, rng: random.Random, kind: dict[str, Any], drug: dict[str, Any], o: Order
) -> list[Dose]:
    end = min(o.stop, tl.end)
    if rng.random() < kind["first_dose_before_verify"]:
        first = o.start + timedelta(minutes=rng.uniform(0, 15))
    else:
        first = max(o.start, o.verify_ts) + timedelta(minutes=rng.uniform(5, 60))
    doses: list[Dose] = []
    if drug.get("night_only"):
        day = first.replace(hour=21, minute=0, second=0, microsecond=0)
        while day < end:
            t = day + timedelta(minutes=rng.uniform(0, 240))
            if first <= t < end and rng.random() < drug["use_prob"]:
                doses.append(Dose(t))
            day += timedelta(days=1)
        return doses
    t = first
    while t < end:
        if not drug["prn"] or not doses:
            doses.append(Dose(t))
        else:
            night = tl.shift_index(t) % 2 == 1
            p = drug["use_prob"] * kind["prn_use_mult"] * (sim["night_prn_mult"] if night else 1.0)
            if rng.random() < min(p, 0.95):
                doses.append(Dose(t))
        jitter = rng.uniform(1.0, 1.3) if drug["prn"] else 1.0 + rng.uniform(-0.05, 0.05)
        t += timedelta(hours=drug["interval_h"] * jitter)
    return doses


def simulate_orders(
    sim: dict[str, Any], admissions: list[Admission], staff: list[Staff], tl: Timeline, rng: random.Random
) -> list[Order]:
    """0 to 3 controlled substance orders per stay, each with its dose times."""
    pharmacists = {f: [s.user_id for s in staff if s.role == "RPH" and s.facility_id == f] for f in sim["facilities"]}
    unit_kind = {u["unit_id"]: u["kind"] for u in sim["units"]}
    base = sim["base_rates"]
    orders: list[Order] = []
    for adm in admissions:
        for stay in adm.stays:
            kind = sim["unit_kinds"][unit_kind[stay.unit_id]]
            n = min(int(weighted(rng, kind["orders_per_stay"])), len(kind["drugs"]))
            pool = dict(kind["drugs"])
            for _ in range(n):
                drug_key = weighted(rng, pool)
                del pool[drug_key]
                drug = sim["drugs"][drug_key]
                start = stay.start + timedelta(minutes=rng.uniform(*kind["order_start_lag_min"]))
                if start >= stay.end:
                    start = stay.start
                stop, early = stay.end, False
                if unit_kind[stay.unit_id] != "ED" and rng.random() < 0.3:
                    span = (stay.end - start).total_seconds()
                    stop, early = start + timedelta(seconds=rng.uniform(0.3, 0.9) * span), True
                lag = kind["verify_lag_min_median"] * math.exp(rng.gauss(0, 0.6))
                o = Order(
                    order_id=f"O{len(orders) + 1:07d}",
                    stay=stay,
                    drug_key=drug_key,
                    dose_mg=float(weighted(rng, drug["doses_mg"])),
                    is_prn=drug["prn"],
                    interval_h=drug["interval_h"],
                    start=start,
                    stop=stop,
                    verify_ts=start + timedelta(minutes=lag),
                    pharmacist_id=rng.choice(pharmacists[stay.facility_id]),
                    stopped_early=early,
                )
                o.doses = _dose_times(sim, tl, rng, kind, drug, o)
                # Benign: a dose documented shortly after the order stopped.
                if o.doses and rng.random() < min(base["pull_after_stop"] * len(o.doses), 0.5):
                    late = o.stop + timedelta(minutes=rng.uniform(*base["pull_after_stop_min"]))
                    if late < tl.end:
                        o.doses.append(Dose(late, late=True))
                orders.append(o)
    return orders
