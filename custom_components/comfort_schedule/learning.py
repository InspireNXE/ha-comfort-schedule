"""How fast heating and cooling move the room, learned from finished runs.

Each run that lasted long enough and moved the temperature toward the set point leaves a
sample: degrees per hour, and the outdoor temperature at the time (if there's an outdoor
sensor). The samples drive two things:

- Early start: how long before a block the system needs to start so the room is at the
  block's set point when it begins.
- The slowdown warning: runs in the last two weeks against the weeks before, compared at
  similar outdoor temperatures, so a cold snap isn't mistaken for a failing furnace.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Any

from homeassistant.util import dt as dt_util

MIN_RUN_MINUTES = 10  # shorter runs are mostly the system getting going
MAX_SAMPLES = 1500  # roughly 90 days of a busy system
KEEP_DAYS = 90
RATE_DAYS = 30  # early start uses this recent window
NEAR_OUTDOOR = 10.0  # °F: "similar weather" for both uses (scaled for °C)
RECENT_DAYS = 14  # the trend's recent window...
BASELINE_DAYS = 60  # ...against the rest of this window
MIN_TREND_SAMPLES = 8


class Learning:
    """Run samples, with rate estimates and the slowdown check."""

    def __init__(self, unit: str = "°F", near: float | None = None) -> None:
        self.samples: list[dict[str, Any]] = []
        self.near = near if near else NEAR_OUTDOOR * 5 / 9 if unit == "°C" else NEAR_OUTDOOR

    def load(self, data: Any) -> None:
        samples = data if isinstance(data, list) else []
        self.samples = [
            s for s in samples
            if isinstance(s, dict) and s.get("kind") in ("heat", "cool") and isinstance(s.get("rate"), (int, float))
        ][-MAX_SAMPLES:]

    def dump(self) -> list[dict[str, Any]]:
        return self.samples

    def add(self, kind: str, minutes: float, gained: float, outdoor: float | None) -> bool:
        """A run finished. Returns True if it was kept as a sample."""
        if minutes < MIN_RUN_MINUTES or gained <= 0:
            return False
        now = dt_util.utcnow()
        self.samples.append(
            {
                "t": now.isoformat(),
                "kind": kind,
                "rate": round(gained / minutes * 60, 3),
                "out": None if outdoor is None else round(outdoor, 1),
            }
        )
        cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
        self.samples = [s for s in self.samples if s["t"] >= cutoff][-MAX_SAMPLES:]
        return True

    def _window(self, kind: str, newest: datetime, days: int, oldest: datetime | None = None) -> list[dict[str, Any]]:
        start = (oldest or newest - timedelta(days=days)).isoformat()
        end = newest.isoformat()
        return [s for s in self.samples if s["kind"] == kind and start <= s["t"] <= end]

    def _near(self, samples: list[dict[str, Any]], outdoor: float | None) -> list[dict[str, Any]]:
        if outdoor is None:
            return samples
        return [s for s in samples if s.get("out") is not None and abs(s["out"] - outdoor) <= self.near]

    def rate(self, kind: str, outdoor: float | None = None) -> float | None:
        """Typical degrees per hour for this kind of run, in similar weather when we know it."""
        recent = self._window(kind, dt_util.utcnow(), RATE_DAYS)
        near = self._near(recent, outdoor)
        pool = near if len(near) >= 3 else recent
        if len(pool) < 3:
            return None
        return round(median(s["rate"] for s in pool[-20:]), 3)

    def lead_minutes(self, kind: str, degrees: float, outdoor: float | None, cap: int) -> int | None:
        """Minutes needed to move the room `degrees`, capped. None until enough is learned."""
        rate = self.rate(kind, outdoor)
        if rate is None or rate <= 0 or degrees <= 0:
            return None
        return min(cap, int(round(degrees / rate * 60)))

    def trend(self, kind: str, drop_pct: float) -> dict[str, Any] | None:
        """Runs in the last two weeks are `drop_pct`% slower than before, in similar weather."""
        if drop_pct <= 0:
            return None
        now = dt_util.utcnow()
        recent = self._window(kind, now, RECENT_DAYS)
        before = self._window(kind, now - timedelta(days=RECENT_DAYS), 0, now - timedelta(days=BASELINE_DAYS))
        outs = [s["out"] for s in recent if s.get("out") is not None]
        if outs and len(outs) == len(recent):
            # Compare like with like: only earlier runs in the outdoor range seen lately.
            low, high = min(outs) - self.near / 2, max(outs) + self.near / 2
            before = [s for s in before if s.get("out") is not None and low <= s["out"] <= high]
        if len(recent) < MIN_TREND_SAMPLES or len(before) < MIN_TREND_SAMPLES:
            return None
        now_rate = median(s["rate"] for s in recent)
        base_rate = median(s["rate"] for s in before)
        if base_rate <= 0:
            return None
        drop = (1 - now_rate / base_rate) * 100
        if drop < drop_pct:
            return None
        return {
            "kind": kind,
            "recent_rate": round(now_rate, 2),
            "baseline_rate": round(base_rate, 2),
            "drop": round(drop),
            "recent_runs": len(recent),
            "baseline_runs": len(before),
        }

    def summary(self, outdoor: float | None) -> dict[str, Any]:
        return {
            "heat_rate": self.rate("heat", outdoor),
            "cool_rate": self.rate("cool", outdoor),
            "samples": len(self.samples),
        }
