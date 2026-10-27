"""ADC transactions: staffing, nurse assignment, pulls and follow-ups, injected patterns, inventory, counts.

Two passes:
  1. Shift loop. Staff each unit-shift, assign patients to nurses, turn each due dose into a PULL or OVERRIDE plus any
     WASTE, CANCEL, or RETURN, and add the extra pulls injected users make.
  2. Inventory pass in time order. Track on-hand per cabinet and drug, add RESTOCK and COUNT events, discrepancies,
     and DISCREPANCY_RESOLVED events.
"""

import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from generator.sim.clinical import Admission, Order, Stay, weighted
from generator.sim.hospital import Assignment, Hospital, Staff
from generator.sim.timeline import Timeline


@dataclass
class Injected:
    user_id: str
    patterns: list[str]
    start_ts: datetime
    colluder: str = ""


@dataclass
class Tx:
    """One ADC event before event_ids are assigned. related points at another Tx.k."""

    k: int
    ts: datetime
    event_type: str
    cabinet_id: str
    drug_key: str
    user_id: str
    witness_id: str = ""
    patient_id: str = ""
    admission_id: str = ""
    order_id: str = ""
    quantity: float = 0.0
    expected_quantity: float | None = None
    related: int | None = None
    resolution_code: str = ""
    count_on_access: bool = False


@dataclass
class AdcResult:
    events: list[Tx]
    shifts_worked: Counter = field(default_factory=Counter)


class AdcSimulator:
    def __init__(
        self,
        sim: dict[str, Any],
        tl: Timeline,
        hospital: Hospital,
        staff: list[Staff],
        history: list[Assignment],
        admissions: list[Admission],
        orders: list[Order],
        injected: dict[str, Injected],
        high_acuity: list[str],
        schedules: dict[str, str],
        rng: random.Random,
    ) -> None:
        self.sim, self.tl, self.h, self.rng = sim, tl, hospital, rng
        self.staff = {s.user_id: s for s in staff}
        self.rn_ids = [s.user_id for s in staff if s.role == "RN"]
        self.techs = {
            f: [s.user_id for s in staff if s.role == "TECH" and s.facility_id == f] for f in sim["facilities"]
        }
        self.rphs = {f: [s.user_id for s in staff if s.role == "RPH" and s.facility_id == f] for f in sim["facilities"]}
        self.history, self.admissions, self.orders = history, admissions, orders
        self.injected, self.high_acuity = injected, set(high_acuity)
        self.schedules = schedules
        self.base = sim["base_rates"]
        self.pat = sim["injection"]["patterns"]
        self.cab_fac = {c["cabinet_id"]: c["facility_id"] for c in hospital.cabinets}
        self.cab_unit = {c["cabinet_id"]: c["unit_id"] for c in hospital.cabinets}
        self.count_schedules = sim["inventory"]["count_on_access_schedules"]
        self.txs: list[Tx] = []
        self._k = 0
        self.working: dict[tuple[int, str], list[str]] = {}
        self.last_nurse: dict[int, str] = {}
        self.orders_by_stay: dict[int, list[Order]] = defaultdict(list)
        for o in orders:
            self.orders_by_stay[o.stay.stay_id].append(o)
        self.stays_by_id: dict[int, Stay] = {}
        self.stays_by_unit: dict[str, list[Stay]] = defaultdict(list)
        for adm in admissions:
            for st in adm.stays:
                self.stays_by_id[st.stay_id] = st
                self.stays_by_unit[st.unit_id].append(st)

    # helpers

    def _next_k(self) -> int:
        self._k += 1
        return self._k

    def _add(self, ts: datetime, event_type: str, cabinet_id: str, drug_key: str, user_id: str, **kw: Any) -> Tx:
        tx = Tx(self._next_k(), ts, event_type, cabinet_id, drug_key, user_id, **kw)
        self.txs.append(tx)
        return tx

    def active(self, user_id: str, pattern: str, ts: datetime) -> bool:
        inj = self.injected.get(user_id)
        return bool(inj and pattern in inj.patterns and ts >= inj.start_ts)

    def _minutes(self, bounds: list[float]) -> timedelta:
        return timedelta(minutes=self.rng.uniform(*bounds))

    # pass 1: shift loop

    def run(self) -> AdcResult:
        tl = self.tl
        present: dict[tuple[int, str], list[Stay]] = defaultdict(list)
        for adm in self.admissions:
            for stay in adm.stays:
                last = min(stay.end, tl.end) - timedelta(seconds=1)
                for s in range(max(0, tl.shift_index(stay.start)), min(tl.n_shifts - 1, tl.shift_index(last)) + 1):
                    present[(s, stay.unit_id)].append(stay)
        doses: dict[tuple[int, str], list[tuple[datetime, str, Order, bool]]] = defaultdict(list)
        for o in self.orders:
            for d in o.doses:
                s = tl.shift_index(d.ts)
                if 0 <= s < tl.n_shifts:
                    doses[(s, o.stay.unit_id)].append((d.ts, o.order_id, o, d.late))

        current = {}
        transfers = []
        for a in self.history:
            if a.change_type == "INITIAL":
                current[a.user_id] = (a.unit_id, a.shift)
            else:
                transfers.append(a)
        t_idx = 0
        worked: Counter = Counter()
        staffing = self.sim["staffing"]

        for s in range(tl.n_shifts):
            s_start, s_end, shift = tl.shift_start(s), tl.shift_end(s), tl.shift_name(s)
            while t_idx < len(transfers) and transfers[t_idx].effective_ts <= s_start:
                a = transfers[t_idx]
                current[a.user_id] = (a.unit_id, a.shift)
                t_idx += 1
            for unit in self.h.units.values():
                pool = [u for u in self.rn_ids if current[u] == (unit.unit_id, shift)]
                stays = sorted(present[(s, unit.unit_id)], key=lambda st: st.stay_id)
                need = max(staffing["min_nurses_per_shift"], math.ceil(len(stays) / unit.nurse_ratio))
                ranked = sorted(pool, key=lambda u: (worked[u] + self.rng.random() * 2, u))
                working = ranked[: min(need, len(pool))]
                # A colluding witness works the same shifts as the injected user when they share a pool.
                for u in list(working):
                    inj = self.injected.get(u)
                    if inj and inj.colluder in pool and inj.colluder not in working:
                        working.append(inj.colluder)
                for u in working:
                    worked[u] += 1
                self.working[(s, unit.unit_id)] = working
                if not working:
                    continue

                assigned = self._assign(stays, working)
                for ts, _, o, late in sorted(doses[(s, unit.unit_id)], key=lambda x: (x[0], x[1])):
                    nurse = assigned.get(o.stay.stay_id) or self.last_nurse.get(o.stay.stay_id)
                    if nurse not in working:
                        nurse = self.rng.choice(working)
                    self.dispense(nurse, o, ts, working, unit.cabinet_ids)
                    # High-acuity nurses' patients ask for PRN doses more often.
                    extra_dose = self.rng.random() < staffing["high_acuity_use_mult"] - 1
                    if nurse in self.high_acuity and not late and o.is_prn and extra_dose:
                        extra = ts + timedelta(hours=self.rng.uniform(1, 2))
                        if extra < min(o.stop, s_end, tl.end):
                            self.dispense(nurse, o, extra, working, unit.cabinet_ids)
                for stay_id, nurse in assigned.items():
                    self.last_nurse[stay_id] = nurse
                self._injected_extras(
                    s, s_start, s_end, unit.unit_id, unit.cabinet_ids, unit.drug_keys, working, assigned
                )

        events = self._inventory_pass()
        return AdcResult(events, worked)

    def _assign(self, stays: list[Stay], working: list[str]) -> dict[int, str]:
        """Balance patients across working nurses. High-acuity nurses carry a larger share."""
        w_high = self.sim["staffing"]["high_acuity_assignment_weight"]
        load = {u: 0 for u in working}
        weight = {u: (w_high if u in self.high_acuity else 1.0) for u in working}
        out = {}
        for stay in stays:
            prev = self.last_nurse.get(stay.stay_id)
            if prev in load and load[prev] / weight[prev] <= min(load[u] / weight[u] for u in working) + 1:
                nurse = prev  # keep continuity when it doesn't unbalance the assignment
            else:
                nurse = min(working, key=lambda u: (load[u] / weight[u], working.index(u)))
            load[nurse] += 1
            out[stay.stay_id] = nurse
        return out

    def dispense(
        self, nurse: str, o: Order, ts: datetime, working: list[str], cabinets: list[str], allow_cancel: bool = True
    ) -> None:
        rng, base = self.rng, self.base
        drug = self.sim["drugs"][o.drug_key]
        units = max(1, math.ceil(o.dose_mg / drug["mg_per_unit"] - 1e-9))
        cab = rng.choice(cabinets)
        order_id = o.order_id
        if rng.random() < base["pull_no_order"]:
            order_id = ""  # benign emergency override with no order on file
        override = not order_id or ts < o.verify_ts or rng.random() < base["override_random"]
        pull = self._add(
            ts,
            "OVERRIDE" if override else "PULL",
            cab,
            o.drug_key,
            nurse,
            patient_id=o.stay.patient_id,
            admission_id=o.stay.admission_id,
            order_id=order_id,
            quantity=units,
            count_on_access=self.schedules[o.drug_key] in self.count_schedules,
        )
        p_cancel = self.pat["high_cancel"]["cancel_prob"] if self.active(nurse, "high_cancel", ts) else base["cancel"]
        if allow_cancel and rng.random() < p_cancel:
            self._add(
                ts + self._minutes([1, 5]),
                "CANCEL",
                cab,
                o.drug_key,
                nurse,
                patient_id=o.stay.patient_id,
                admission_id=o.stay.admission_id,
                order_id=order_id,
                quantity=units,
                related=pull.k,
            )
            self.dispense(nurse, o, ts + self._minutes([6, 15]), working, [cab], allow_cancel=False)
            return
        if rng.random() < base["return"]:
            self._add(
                ts + self._minutes([10, 120]),
                "RETURN",
                cab,
                o.drug_key,
                nurse,
                patient_id=o.stay.patient_id,
                admission_id=o.stay.admission_id,
                order_id=order_id,
                quantity=units,
                related=pull.k,
            )
            return
        waste = units - o.dose_mg / drug["mg_per_unit"]
        if (
            self.active(nurse, "high_waste_ratio", ts)
            and rng.random() < self.pat["high_waste_ratio"]["extra_waste_prob"]
        ):
            claimed = units * rng.uniform(*self.pat["high_waste_ratio"]["waste_fraction"])
            if drug["unit_of_issue"] in ("TAB", "CAP"):
                claimed = max(0.5, round(claimed * 2) / 2)
            waste = max(waste, min(claimed, units))
        if waste > 1e-9:
            self._waste(nurse, pull, round(waste, 3), working)

    def _waste(self, nurse: str, pull: Tx, qty: float, working: list[str]) -> None:
        rng, base, ts = self.rng, self.base, pull.ts
        if self.active(nurse, "delayed_waste", ts) and rng.random() < self.pat["delayed_waste"]["prob"]:
            delay = self._minutes(self.pat["delayed_waste"]["delay_min"])
        elif rng.random() < base["late_waste"]:
            delay = self._minutes(base["late_waste_delay_min"])
        else:
            delay = self._minutes(base["waste_delay_min"])
        peers = [u for u in working if u != nurse]
        inj = self.injected.get(nurse)
        if (
            not peers
            or rng.random() < base["unwitnessed_waste"]
            or self.active(nurse, "unwitnessed_waste", ts)
            and rng.random() < self.pat["unwitnessed_waste"]["prob"]
        ):
            witness = ""
        elif (
            self.active(nurse, "witness_collusion", ts)
            and inj.colluder in peers
            and rng.random() < self.pat["witness_collusion"]["prob"]
        ):
            witness = inj.colluder
        else:
            witness = rng.choice(peers)
        self._add(
            ts + delay,
            "WASTE",
            pull.cabinet_id,
            pull.drug_key,
            nurse,
            witness_id=witness,
            patient_id=pull.patient_id,
            admission_id=pull.admission_id,
            order_id=pull.order_id,
            quantity=qty,
            related=pull.k,
        )

    def _injected_extras(
        self,
        s: int,
        s_start: datetime,
        s_end: datetime,
        unit_id: str,
        cabinets: list[str],
        drug_keys: list[str],
        working: list[str],
        assigned: dict[int, str],
    ) -> None:
        """Pulls injected users add on top of normal care: no order on file, or for a patient who already left."""
        rng = self.rng
        for nurse in working:
            if nurse not in self.injected:
                continue
            if (
                self.active(nurse, "pull_no_order", s_start)
                and rng.random() < self.pat["pull_no_order"]["per_shift_prob"]
            ):
                mine = [sid for sid, u in assigned.items() if u == nurse]
                stay = self.stays_by_id[rng.choice(mine)] if mine else None
                if stay:
                    cii = [d for d in drug_keys if self.schedules[d] == "CII"] or drug_keys
                    dk = rng.choice(cii)
                    ts = s_start + timedelta(minutes=rng.uniform(30, 690))
                    self._add(
                        ts,
                        "OVERRIDE",
                        rng.choice(cabinets),
                        dk,
                        nurse,
                        patient_id=stay.patient_id,
                        admission_id=stay.admission_id,
                        quantity=1,
                        count_on_access=self.schedules[dk] in self.count_schedules,
                    )
            cfg = self.pat["pull_after_discharge_or_transfer"]
            if self.active(nurse, "pull_after_discharge_or_transfer", s_start) and rng.random() < cfg["per_shift_prob"]:
                window = timedelta(hours=cfg["window_hours"])
                gone = [
                    st
                    for st in self.stays_by_unit[unit_id]
                    if s_start - window <= st.end < s_end - timedelta(minutes=20) and self.orders_by_stay[st.stay_id]
                ]
                if gone:
                    stay = rng.choice(gone)
                    o = max(self.orders_by_stay[stay.stay_id], key=lambda x: (x.start, x.order_id))
                    ts = max(stay.end, s_start) + timedelta(minutes=rng.uniform(10, 120))
                    if ts < min(s_end, self.tl.end):
                        drug = self.sim["drugs"][o.drug_key]
                        units = max(1, math.ceil(o.dose_mg / drug["mg_per_unit"] - 1e-9))
                        self._add(
                            ts,
                            "PULL",
                            rng.choice(cabinets),
                            o.drug_key,
                            nurse,
                            patient_id=stay.patient_id,
                            admission_id=stay.admission_id,
                            order_id=o.order_id,
                            quantity=units,
                            count_on_access=self.schedules[o.drug_key] in self.count_schedules,
                        )

    # pass 2: inventory, restocks, counts

    def _inventory_pass(self) -> list[Tx]:
        tl, inv, rng = self.tl, self.sim["inventory"], self.rng
        on_hand = dict(self.h.par)
        actions: list[tuple[datetime, int, str, Any]] = [(tx.ts, tx.k, "TX", tx) for tx in self.txs]
        for d in range(tl.days):
            for fac in self.sim["facilities"]:
                actions.append(
                    (tl.start + timedelta(days=d, hours=inv["restock_hour"]), self._next_k(), "RESTOCK", fac)
                )
        for s in range(tl.n_shifts):
            for cab in self.h.cabinets:
                actions.append((tl.shift_start(s), self._next_k(), "SHIFT_COUNT", (s, cab["cabinet_id"])))
        actions.sort(key=lambda a: (a[0], a[1]))

        out: list[Tx] = []

        def emit(tx: Tx) -> None:
            out.append(tx)

        def new(ts: datetime, etype: str, cab: str, dk: str, user: str, **kw: Any) -> Tx:
            tx = Tx(self._next_k(), ts, etype, cab, dk, user, **kw)
            emit(tx)
            return tx

        def count(ts: datetime, cab: str, dk: str, user: str) -> None:
            expected = on_hand[(cab, dk)]
            counted = expected
            if rng.random() < inv["discrepancy_rate"]:
                counted = max(0, expected + rng.choice([-1, -1, 1]))
            c = new(ts, "COUNT", cab, dk, user, quantity=counted, expected_quantity=expected)
            if counted != expected and rng.random() < inv["resolve_rate"]:
                fac = self.cab_fac[cab]
                ts_r = ts + timedelta(hours=rng.uniform(*inv["resolve_hours"]))
                new(
                    ts_r,
                    "DISCREPANCY_RESOLVED",
                    cab,
                    dk,
                    rng.choice(self.rphs[fac]),
                    related=c.k,
                    resolution_code=weighted(rng, inv["resolution_codes"]),
                )

        def restock(ts: datetime, cab: str, dk: str) -> None:
            tech = rng.choice(self.techs[self.cab_fac[cab]])
            qty = self.h.par[(cab, dk)] - on_hand[(cab, dk)]
            new(ts, "RESTOCK", cab, dk, tech, quantity=qty)
            on_hand[(cab, dk)] = self.h.par[(cab, dk)]
            count(ts + timedelta(seconds=60), cab, dk, tech)

        cab_drugs: dict[str, list[str]] = defaultdict(list)
        for cab, dk in self.h.par:
            cab_drugs[cab].append(dk)

        for ts, _, kind, obj in actions:
            if kind == "RESTOCK":
                for cab in [c["cabinet_id"] for c in self.h.cabinets if c["facility_id"] == obj]:
                    for dk in cab_drugs[cab]:
                        if on_hand[(cab, dk)] <= self.h.par[(cab, dk)] * inv["restock_below_fraction"]:
                            restock(ts, cab, dk)
            elif kind == "SHIFT_COUNT":
                s, cab = obj
                nurses = self.working.get((s, self.cab_unit[cab])) or []
                if nurses:
                    for dk in cab_drugs[cab]:
                        if self.schedules[dk] in self.sim["inventory"]["shift_count_schedules"]:
                            count(ts + timedelta(minutes=rng.uniform(0, 30)), cab, dk, nurses[0])
            else:
                tx: Tx = obj
                key = (tx.cabinet_id, tx.drug_key)
                if tx.event_type in ("PULL", "OVERRIDE"):
                    if on_hand[key] < tx.quantity:
                        restock(tx.ts - timedelta(seconds=120), tx.cabinet_id, tx.drug_key)
                    if tx.count_on_access:
                        count(tx.ts - timedelta(seconds=30), tx.cabinet_id, tx.drug_key, tx.user_id)
                    on_hand[key] -= tx.quantity
                elif tx.event_type in ("CANCEL", "RETURN"):
                    on_hand[key] += tx.quantity
                emit(tx)
        return [tx for tx in out if tx.ts < tl.end]


def pick_injected(
    sim: dict[str, Any], staff: list[Staff], tl: Timeline, rng: random.Random
) -> tuple[dict[str, Injected], list[str]]:
    """Injected users with their patterns, start dates, and colluders, plus benign high-acuity nurses."""
    cfg = sim["injection"]
    rns = [s for s in staff if s.role == "RN"]
    n = round(cfg["rate"] * len(rns))
    chosen = rng.sample(rns, n)
    names = list(cfg["patterns"])
    rng.shuffle(names)
    # As many users as needed get the max so every pattern is dealt at least once; the rest get one.
    n_max = min(n, max(0, len(names) - n) // (cfg["max_patterns_per_user"] - 1))
    sizes = [cfg["max_patterns_per_user"]] * n_max + [1] * (n - n_max)
    rng.shuffle(sizes)
    deck = list(names)
    injected: dict[str, Injected] = {}
    for s, k in zip(chosen, sizes):
        patterns = []
        while len(patterns) < k:
            if not deck:
                deck = list(names)
                rng.shuffle(deck)
            p = deck.pop()
            if p not in patterns:
                patterns.append(p)
        start = tl.start + timedelta(days=rng.randint(*cfg["start_day_range"]))
        colluder = ""
        if "witness_collusion" in patterns:
            peers = [p.user_id for p in rns if (p.unit_id, p.shift) == (s.unit_id, s.shift) and p not in chosen]
            colluder = rng.choice(peers)
        injected[s.user_id] = Injected(s.user_id, patterns, start, colluder)
    others = [
        s.user_id for s in rns if s.user_id not in injected and s.user_id not in {i.colluder for i in injected.values()}
    ]
    high_acuity = sorted(rng.sample(others, sim["staffing"]["high_acuity_nurses"]))
    return injected, high_acuity
