"""Heating and cooling runtime, kept as daily totals (local dates)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.climate import ATTR_HVAC_ACTION, HVACAction
from homeassistant.core import State
from homeassistant.util import dt as dt_util

KINDS = {HVACAction.HEATING: "heat", HVACAction.COOLING: "cool"}
KEEP_DAYS = 400  # a little over a year, so "this year" and year-over-year stay complete


class RuntimeTracker:
    """Accrues seconds of heating/cooling into per-day buckets."""

    def __init__(self) -> None:
        self.days: dict[str, dict[str, float]] = {}
        # The most recent finished run: {"kind": "heat" | "cool", "end": ISO time}.
        self.last_run: dict[str, str] | None = None
        # First local date runtime was tracked (ISO). Before it, zero means "no data", not
        # "didn't run", so comparisons that reach back past it aren't meaningful.
        self.since: str | None = None
        self._kind: str | None = None
        self._since: datetime | None = None

    # ------------------------------------------------------------ persistence

    def load(self, data: dict[str, Any] | None) -> None:
        days = (data or {}).get("days") or {}
        self.days = {
            d: {k: float(v.get(k, 0)) for k in ("heat", "cool", "hdd", "cdd") if k in v or k in ("heat", "cool")}
            for d, v in days.items()
            if isinstance(v, dict)
        }
        last = (data or {}).get("last_run")
        self.last_run = last if isinstance(last, dict) and last.get("end") else None
        since = (data or {}).get("since")
        # Saved before "since" existed: the first day with any runtime is close enough.
        self.since = since if isinstance(since, str) else (min(self.days) if self.days else None)

    def dump(self) -> dict[str, Any]:
        return {"days": self.days, "last_run": self.last_run, "since": self.since}

    # ------------------------------------------------------------ tracking

    @property
    def running(self) -> bool:
        return self._kind is not None

    def update(self, state: State | None, cooling_enabled: bool = True) -> None:
        """Called on every thermostat state change (and at startup)."""
        now = dt_util.utcnow()
        if self.since is None:
            self.since = dt_util.as_local(now).date().isoformat()
        self.accrue(now)
        action = state.attributes.get(ATTR_HVAC_ACTION) if state is not None else None
        kind = KINDS.get(action) if action is not None else None
        if kind == "cool" and not cooling_enabled:
            kind = None
        if self._kind and kind != self._kind:
            self.last_run = {"kind": self._kind, "end": now.isoformat()}
        self._kind = kind
        self._since = now if self._kind else None

    def accrue(self, now: datetime | None = None) -> bool:
        """Move running time since the last call into the day buckets. True if anything was added."""
        if not self._kind or self._since is None:
            return False
        now = now or dt_util.utcnow()
        start = self._since
        if now <= start:
            return False
        # Split at local midnights so each day gets its own share.
        while start < now:
            local = dt_util.as_local(start)
            next_midnight = dt_util.as_utc(dt_util.start_of_local_day(local.date() + timedelta(days=1)))
            end = min(now, next_midnight)
            bucket = self.days.setdefault(local.date().isoformat(), {"heat": 0.0, "cool": 0.0})
            bucket[self._kind] += (end - start).total_seconds()
            start = end
        self._since = now
        self._prune(dt_util.as_local(now).date())
        return True

    def add_degree_minutes(self, outdoor: float, base: float, minutes: float = 1.0) -> None:
        """Count `minutes` of outdoor temperature toward today's heating and cooling degree-days."""
        bucket = self.days.setdefault(
            dt_util.as_local(dt_util.utcnow()).date().isoformat(), {"heat": 0.0, "cool": 0.0}
        )
        bucket["hdd"] = bucket.get("hdd", 0.0) + max(0.0, base - outdoor) * minutes / 1440
        bucket["cdd"] = bucket.get("cdd", 0.0) + max(0.0, outdoor - base) * minutes / 1440

    def seconds_since(self, first: str, offset: float = 0.0) -> float:
        """Heating plus cooling seconds from local date `first` (ISO) on, less `offset`."""
        total = sum(v.get("heat", 0.0) + v.get("cool", 0.0) for d, v in self.days.items() if d >= first)
        return max(0.0, total - offset)

    def day_seconds(self, day: str) -> float:
        v = self.days.get(day, {})
        return v.get("heat", 0.0) + v.get("cool", 0.0)

    def _prune(self, today: date) -> None:
        cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
        for key in [d for d in self.days if d < cutoff]:
            del self.days[key]

    # ------------------------------------------------------------ reporting (hours)

    def _hours(self, day: date, kind: str) -> float:
        """Hours for heat/cool; degree-days as they are for hdd/cdd."""
        value = self.days.get(day.isoformat(), {}).get(kind, 0.0)
        return value if kind in ("hdd", "cdd") else value / 3600

    def _range(self, first: date, last: date, kind: str) -> float:
        total, day = 0.0, first
        while day <= last:
            total += self._hours(day, kind)
            day += timedelta(days=1)
        return total

    def summary(self, kind: str) -> dict[str, float]:
        today = dt_util.as_local(dt_util.utcnow()).date()
        periods = {
            "today": (today, today),
            "yesterday": (today - timedelta(days=1), today - timedelta(days=1)),
            "last_7_days": (today - timedelta(days=6), today),
            "this_month": (today.replace(day=1), today),
            "last_30_days": (today - timedelta(days=29), today),
            "this_year": (today.replace(month=1, day=1), today),
        }
        return {name: round(self._range(a, b, kind), 2) for name, (a, b) in periods.items()}

    def daily(self, count: int = 31) -> list[dict[str, Any]]:
        """Oldest first, including today, zero-filled."""
        today = dt_util.as_local(dt_util.utcnow()).date()
        out = []
        for back in range(count - 1, -1, -1):
            day = today - timedelta(days=back)
            out.append(
                {
                    "date": day.isoformat(),
                    "heat": round(self._hours(day, "heat"), 2),
                    "cool": round(self._hours(day, "cool"), 2),
                }
            )
        return out

    def _bucket(self, first: date, last: date) -> dict[str, Any]:
        return {
            "start": first.isoformat(),
            "heat": round(self._range(first, last, "heat"), 2),
            "cool": round(self._range(first, last, "cool"), 2),
        }

    def weekly(self, count: int = 12) -> list[dict[str, Any]]:
        """Monday-start weeks, oldest first; the current week runs up to today."""
        today = dt_util.as_local(dt_util.utcnow()).date()
        this_week = today - timedelta(days=today.weekday())
        out = []
        for back in range(count - 1, -1, -1):
            start = this_week - timedelta(weeks=back)
            out.append(self._bucket(start, min(start + timedelta(days=6), today)))
        return out

    def monthly(self, count: int = 12) -> list[dict[str, Any]]:
        """Calendar months, oldest first; the current month runs up to today."""
        today = dt_util.as_local(dt_util.utcnow()).date()
        starts = [today.replace(day=1)]
        while len(starts) < count:
            starts.append((starts[-1] - timedelta(days=1)).replace(day=1))
        out = []
        for start in reversed(starts):
            next_month = (start + timedelta(days=32)).replace(day=1)
            out.append(self._bucket(start, min(next_month - timedelta(days=1), today)))
        return out

    def compare(self) -> dict[str, dict[str, float]]:
        """Like-for-like comparisons for the card: the current stretch vs the one before it,
        up to the same point (this week so far vs last week to the same weekday, etc.)."""
        today = dt_util.as_local(dt_util.utcnow()).date()
        week_start = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)
        prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
        prev_month_last = month_start - timedelta(days=1)
        prev_month_same_day = prev_month_start.replace(day=min(today.day, prev_month_last.day))
        spans = {
            "day": ((today - timedelta(days=6), today), (today - timedelta(days=13), today - timedelta(days=7))),
            "week": ((week_start, today), (week_start - timedelta(days=7), today - timedelta(days=7))),
            "month": ((month_start, today), (prev_month_start, prev_month_same_day)),
        }
        out = {}
        for name, ((a, b), (c, d)) in spans.items():
            out[name] = {
                "heat": round(self._range(a, b, "heat"), 2),
                "cool": round(self._range(a, b, "cool"), 2),
                "prev_heat": round(self._range(c, d, "heat"), 2),
                "prev_cool": round(self._range(c, d, "cool"), 2),
                # Degree-days for the same stretches (0 without an outdoor sensor), so the card
                # can compare runtime per degree-day, i.e. adjusted for the weather.
                "hdd": round(self._range(a, b, "hdd"), 1),
                "cdd": round(self._range(a, b, "cdd"), 1),
                "prev_hdd": round(self._range(c, d, "hdd"), 1),
                "prev_cdd": round(self._range(c, d, "cdd"), 1),
                # Tracked for the whole earlier stretch, so a zero there is a real zero.
                "complete": self.since is not None and self.since <= c.isoformat(),
            }
        return out
