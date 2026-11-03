"""Schedule suggestions from repeated manual changes.

Every manual change (card, voice, the wall unit) is logged with what the schedule wanted at
the time. When the same change keeps happening, a suggestion is offered; nothing changes
until someone applies it:

- Set point: the same block keeps getting set to the same value ("Wake heat to 70° on
  weekdays").
- Earlier start: shortly before a block, the set points keep being changed to that block's
  values ("Start Wake at 6:10 on weekdays").
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import timedelta
import hashlib
from statistics import median
from typing import Any

from homeassistant.util import dt as dt_util

from .const import STEP_MINUTES
from .schedule import Period, Week, format_minutes, guard

LOOKBACK_DAYS = 21
MIN_DAYS = 3  # distinct days the same change has to happen on
EARLY_WINDOW = 90  # minutes before a block that count as "starting it early"
MAX_LOG = 300
DAY_GROUPS = {"weekdays": range(0, 5), "weekend": range(5, 7)}


def _group(weekday: int) -> str:
    return "weekdays" if weekday < 5 else "weekend"


def _id(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:10]


class Suggestions:
    """The manual change log, the open suggestions and the dismissed ones."""

    def __init__(self) -> None:
        self.log: list[dict[str, Any]] = []
        self.dismissed: list[str] = []

    def load(self, data: Any) -> None:
        data = data if isinstance(data, dict) else {}
        self.log = [e for e in data.get("log") or [] if isinstance(e, dict) and "t" in e][-MAX_LOG:]
        self.dismissed = [str(d) for d in data.get("dismissed") or []][-200:]

    def dump(self) -> dict[str, Any]:
        return {"log": self.log, "dismissed": self.dismissed}

    def record(
        self,
        heat: float,
        cool: float,
        changed: str | None,
        current: tuple[int, int, Period],
        upcoming: tuple[int, Period] | None,
    ) -> None:
        """A manual change. `current` is (weekday, index, block); `upcoming` is (minutes until, block)."""
        now = dt_util.utcnow()
        local = dt_util.as_local(now)
        day, _, block = current
        entry: dict[str, Any] = {
            "t": now.isoformat(),
            "date": local.date().isoformat(),
            "day": day,
            "minute": local.hour * 60 + local.minute,
            "heat": heat,
            "cool": cool,
            "changed": changed,
            "label": block.label,
            "block_heat": block.heat,
            "block_cool": block.cool,
        }
        if upcoming is not None and upcoming[0] <= EARLY_WINDOW:
            nxt = upcoming[1]
            entry.update(next_label=nxt.label, next_start=nxt.start, next_heat=nxt.heat, next_cool=nxt.cool)
        self.log.append(entry)
        cutoff = (now - timedelta(days=LOOKBACK_DAYS * 2)).isoformat()
        self.log = [e for e in self.log if e["t"] >= cutoff][-MAX_LOG:]

    def dismiss(self, suggestion_id: str) -> None:
        if suggestion_id not in self.dismissed:
            self.dismissed.append(suggestion_id)

    def current(self, week: Week) -> list[dict[str, Any]]:
        """Open suggestions, strongest first."""
        cutoff = (dt_util.utcnow() - timedelta(days=LOOKBACK_DAYS)).isoformat()
        recent = [e for e in self.log if e["t"] >= cutoff]
        out = self._setpoints(recent, week) + self._early(recent, week)
        out = [s for s in out if s["id"] not in self.dismissed]
        return sorted(out, key=lambda s: -s["days_seen"])

    @staticmethod
    def _blocks(week: Week, group: str, label: str) -> list[tuple[int, int, Period]]:
        return [(d, i, p) for d in DAY_GROUPS[group] for i, p in enumerate(week[d]) if p.label == label]

    def _setpoints(self, log: list[dict[str, Any]], week: Week) -> list[dict[str, Any]]:
        seen: dict[tuple[str, str, str, float], set[str]] = defaultdict(set)
        for e in log:
            if self._matches_next(e):
                continue  # that's starting the next block early, not a new set point
            for side in ("heat", "cool"):
                if e.get("changed") not in (side, None):
                    continue
                value = e[side]
                if abs(value - e[f"block_{side}"]) < 0.25:
                    continue
                seen[(_group(e["day"]), e["label"], side, round(value * 2) / 2)].add(e["date"])
        out = []
        for (group, label, side, value), dates in seen.items():
            blocks = self._blocks(week, group, label)
            if len(dates) < MIN_DAYS or not blocks or all(abs(getattr(p, side) - value) < 0.25 for _, _, p in blocks):
                continue
            out.append(
                {
                    "id": _id("sp", group, label, side, value),
                    "kind": "setpoint",
                    "days": group,
                    "label": label,
                    "side": side,
                    "value": value,
                    "days_seen": len(dates),
                    "text": f"Set {label} {side} to {value:g}° on {group}",
                    "why": f"You changed it to {value:g}° on {len(dates)} days in the last three weeks.",
                }
            )
        return out

    @staticmethod
    def _matches_next(e: dict[str, Any]) -> bool:
        """The change set the coming block's set point (on the side that was changed)."""
        if "next_label" not in e:
            return False
        sides = [e["changed"]] if e.get("changed") in ("heat", "cool") else ["heat", "cool"]
        return all(abs(e[s] - e[f"next_{s}"]) <= 1 for s in sides)

    def _early(self, log: list[dict[str, Any]], week: Week) -> list[dict[str, Any]]:
        seen: dict[tuple[str, str, int], list[tuple[str, int]]] = defaultdict(list)
        for e in log:
            if not self._matches_next(e):
                continue
            seen[(_group(e["day"]), e["next_label"], e["next_start"])].append((e["date"], e["minute"]))
        out = []
        for (group, label, start), hits in seen.items():
            dates = {d for d, _ in hits}
            if len(dates) < MIN_DAYS or not self._blocks(week, group, label):
                continue
            minute = int(round(median(m for _, m in hits) / STEP_MINUTES)) * STEP_MINUTES
            if minute >= start or start - minute < STEP_MINUTES:
                continue
            out.append(
                {
                    "id": _id("early", group, label, start, minute),
                    "kind": "start",
                    "days": group,
                    "label": label,
                    "start": minute,
                    "was": start,
                    "days_seen": len(dates),
                    "text": f"Start {label} at {format_minutes(minute)} on {group}",
                    "why": (
                        f"On {len(dates)} days in the last three weeks you changed to {label}'s "
                        f"set points before it began at {format_minutes(start)}."
                    ),
                }
            )
        return out

    @staticmethod
    def apply(suggestion: dict[str, Any], week: Week, deadband: float, low: float, high: float) -> Week:
        """The week with the suggestion applied (blocks it can't move are left alone)."""
        new = [list(day) for day in week]
        for d in DAY_GROUPS[suggestion["days"]]:
            day = new[d]
            for i, p in enumerate(day):
                if p.label != suggestion["label"]:
                    continue
                if suggestion["kind"] == "setpoint":
                    side = suggestion["side"]
                    heat, cool = (float(suggestion["value"]), p.cool) if side == "heat" else (p.heat, float(suggestion["value"]))
                    heat, cool = guard(heat, cool, side, deadband, low, high)
                    day[i] = replace(p, heat=heat, cool=cool)
                elif i > 0 and day[i - 1].start < suggestion["start"] < p.start:
                    day[i] = replace(p, start=int(suggestion["start"]))
        return new
