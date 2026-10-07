"""Weekly schedule model and the heat/cool set point guard."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import UnitOfTemperature
from homeassistant.util import dt as dt_util

from .const import MINUTES_PER_DAY, STEP_MINUTES

MAX_LABEL = 40
MAX_PERIODS_PER_DAY = 24


# Modes a period may switch to when it starts. None ("use current") leaves the mode alone.
PERIOD_MODES = ("off", "heat", "cool", "heat_cool")
MAX_FAN = 40


@dataclass(frozen=True)
class Period:
    """One block of a day: from `start` (minutes after midnight) until the next block.

    `mode` and `fan` are applied when the block starts; None means "use current",
    i.e. keep whatever the thermostat is set to at that moment.
    """

    start: int
    heat: float
    cool: float
    label: str
    mode: str | None = None
    fan: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize for storage, attributes and services."""
        return {
            "start": self.start,
            "heat": self.heat,
            "cool": self.cool,
            "label": self.label,
            "mode": self.mode,
            "fan": self.fan,
        }

    def same_settings(self, other: Period) -> bool:
        """True if starting `other` after this one would change nothing."""
        return (self.heat, self.cool, self.label, self.mode, self.fan) == (
            other.heat, other.cool, other.label, other.mode, other.fan,
        )


# Index 0 is Monday, matching datetime.weekday().
Week = list[list[Period]]


def guard(
    heat: float,
    cool: float,
    changed: str | None,
    deadband: float,
    low: float,
    high: float,
) -> tuple[float, float]:
    """Keep heat at least `deadband` below cool.

    The side the user did not touch is pushed out of the way (Nest behaviour). When
    neither side is known to have changed, cool is pushed up.
    """
    heat, cool = float(heat), float(cool)
    if cool - heat < deadband:
        if changed == "cool":
            heat = cool - deadband
        else:
            cool = heat + deadband
    if cool > high:
        cool = high
        heat = min(heat, cool - deadband)
    if heat < low:
        heat = low
        cool = max(cool, heat + deadband)
    return round(heat, 1), round(cool, 1)


def week_from_json(data: Any, deadband: float) -> Week:
    """Parse and strictly validate a week. Raises ValueError with a readable message."""
    if not isinstance(data, list) or len(data) != 7:
        raise ValueError("The schedule must have exactly 7 days, Monday first")
    week: Week = []
    for day_index, day in enumerate(data):
        name = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")[day_index]
        if not isinstance(day, list) or not day:
            raise ValueError(f"{name} needs at least one period")
        if len(day) > MAX_PERIODS_PER_DAY:
            raise ValueError(f"{name} has more than {MAX_PERIODS_PER_DAY} periods")
        periods: list[Period] = []
        for raw in day:
            if not isinstance(raw, dict):
                raise ValueError(f"{name}: each period must be an object")
            try:
                start = int(raw["start"])
                heat = round(float(raw["heat"]), 1)
                cool = round(float(raw["cool"]), 1)
            except (KeyError, TypeError, ValueError) as err:
                raise ValueError(f"{name}: each period needs start, heat and cool") from err
            label = str(raw.get("label") or "Period").strip()[:MAX_LABEL] or "Period"
            mode = raw.get("mode") or None
            if mode is not None and mode not in PERIOD_MODES:
                raise ValueError(f"{name} {format_minutes(start)}: unknown mode '{mode}'")
            fan = str(raw["fan"]).strip()[:MAX_FAN] if raw.get("fan") else None
            if not 0 <= start < MINUTES_PER_DAY or start % STEP_MINUTES:
                raise ValueError(f"{name}: start times must be on a {STEP_MINUTES}-minute step")
            if periods and start <= periods[-1].start:
                raise ValueError(f"{name}: periods must be in time order without overlaps")
            if cool - heat < deadband - 0.001:
                raise ValueError(
                    f"{name} {format_minutes(start)}: cool ({cool}) must be at least "
                    f"{deadband} above heat ({heat})"
                )
            periods.append(Period(start, heat, cool, label, mode, fan))
        if periods[0].start != 0:
            raise ValueError(f"{name}: the first period must start at midnight")
        week.append(periods)
    return week


def week_to_json(week: Week) -> list[list[dict[str, Any]]]:
    """Serialize a week."""
    return [[p.as_dict() for p in day] for day in week]


def normalize_week(week: Week, deadband: float, low: float, high: float) -> tuple[Week, bool]:
    """Apply the guard to every period, e.g. after the deadband was widened."""
    changed = False
    out: Week = []
    for day in week:
        new_day = []
        for p in day:
            heat, cool = guard(p.heat, p.cool, None, deadband, low, high)
            if (heat, cool) != (p.heat, p.cool):
                changed = True
            new_day.append(replace(p, heat=heat, cool=cool))
        out.append(new_day)
    return out, changed


def default_week(heat: float, cool: float) -> Week:
    """One all-day period, used when there is nothing to import."""
    return [[Period(0, heat, cool, "Home")] for _ in range(7)]


def format_minutes(minutes: int) -> str:
    """Format minutes after midnight as 12-hour time, e.g. 5:30 AM."""
    hour, minute = divmod(minutes % MINUTES_PER_DAY, 60)
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {suffix}"


def _day_start(date_value: datetime) -> datetime:
    return dt_util.start_of_local_day(date_value.date())


def active_period(week: Week, now: datetime) -> tuple[int, int, Period]:
    """Return (weekday, index, period) in effect at `now`."""
    local = dt_util.as_local(now)
    day_index = local.weekday()
    day = week[day_index]
    minute = local.hour * 60 + local.minute
    index = 0
    for i, period in enumerate(day):
        if period.start <= minute:
            index = i
    return day_index, index, day[index]


def period_end(week: Week, day_index: int, index: int) -> int:
    """Minutes after midnight at which this period ends (1440 = midnight)."""
    day = week[day_index]
    return day[index + 1].start if index + 1 < len(day) else MINUTES_PER_DAY


def next_change(week: Week, now: datetime) -> tuple[datetime, Period] | None:
    """The next boundary strictly after `now` where something actually changes.

    A midnight period that just continues the previous day's last one (same set points,
    label, mode and fan) isn't a change, so it's skipped.
    """
    local = dt_util.as_local(now)
    now_utc = dt_util.as_utc(now)
    current = active_period(week, now)[2]
    for offset in range(8):
        day_start = _day_start(local + timedelta(days=offset))
        day = week[(local.weekday() + offset) % 7]
        for period in day:
            # Build in wall-clock time (keeps local time across DST), but compare in UTC:
            # same-tzinfo comparisons ignore fold and would misorder the repeated hour.
            when = dt_util.as_utc(day_start + timedelta(minutes=period.start))
            if when <= now_utc:
                continue
            if not current.same_settings(period):
                return when, period
            current = period
    return None


def unit_defaults(unit: str) -> tuple[float, float, float, float, float]:
    """(min, max, default heat, default cool, default deadband) for the system's unit."""
    if unit == UnitOfTemperature.CELSIUS:
        return 5.0, 35.0, 20.0, 24.0, 1.5
    return 40.0, 95.0, 68.0, 76.0, 3.0
