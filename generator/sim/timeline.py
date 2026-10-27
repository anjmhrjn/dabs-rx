"""Simulation window and shift arithmetic. Shift index 2*d is day d's day shift, 2*d + 1 its night shift."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

TS_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class Timeline:
    start: datetime
    days: int
    day_start_hour: int
    night_start_hour: int
    shift_hours: int

    @classmethod
    def from_config(cls, sim: dict[str, Any]) -> "Timeline":
        return cls(
            start=datetime.fromisoformat(sim["start_date"]),
            days=sim["days"],
            day_start_hour=sim["shifts"]["day"]["start_hour"],
            night_start_hour=sim["shifts"]["night"]["start_hour"],
            shift_hours=sim["shift_hours"],
        )

    @property
    def end(self) -> datetime:
        return self.start + timedelta(days=self.days)

    @property
    def n_shifts(self) -> int:
        return 2 * self.days

    def shift_start(self, s: int) -> datetime:
        hour = self.day_start_hour if s % 2 == 0 else self.night_start_hour
        return self.start + timedelta(days=s // 2, hours=hour)

    def shift_end(self, s: int) -> datetime:
        return self.shift_start(s) + timedelta(hours=self.shift_hours)

    def shift_name(self, s: int) -> str:
        return "day" if s % 2 == 0 else "night"

    def shift_index(self, ts: datetime) -> int:
        """Shift containing ts. Times before the first day shift belong to shift -1 (the night before)."""
        hours = (ts - self.start).total_seconds() / 3600
        day, hour = divmod(hours, 24)
        if hour < self.day_start_hour:
            return int(day) * 2 - 1
        if hour < self.night_start_hour:
            return int(day) * 2
        return int(day) * 2 + 1

    def day_of(self, ts: datetime) -> date:
        return ts.date()


def fmt(ts: datetime | None) -> str:
    return ts.strftime(TS_FORMAT) if ts else ""
