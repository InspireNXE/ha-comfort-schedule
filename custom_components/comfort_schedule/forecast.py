"""The outdoor forecast, from a weather entity's hourly forecast (weather.get_forecasts).

Two uses, both pure functions here so they're easy to test:

- Early start looks at the temperature forecast for the hours until the block, not just now:
  an evening that's about to turn cold needs a longer lead.
- The cold snap guard: when the forecast low in the next 24 hours is at or below a set
  temperature, setbacks are kept shallow (heat no more than N degrees under the day's
  warmest heat set point). A house that drops far on a bitter night can take hours to
  recover, or not recover at all while the furnace runs flat out.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

REFRESH = timedelta(minutes=30)
LOOKAHEAD = timedelta(hours=24)
# Forecast points older than this are stale (the weather entity stopped updating).
STALE = timedelta(hours=3)


def _convert(value: float, unit: str | None, want: str) -> float:
    if unit != want and {unit, want} == {"°C", "°F"}:
        return round(value * 9 / 5 + 32, 1) if want == "°F" else round((value - 32) * 5 / 9, 1)
    return value


def parse(items: Any, unit: str | None, want: str) -> list[tuple[datetime, float]]:
    """[(time, temperature)] from a get_forecasts list, oldest first, in our unit."""
    out: list[tuple[datetime, float]] = []
    for f in items if isinstance(items, list) else []:
        if not isinstance(f, dict):
            continue
        when = dt_util.parse_datetime(str(f.get("datetime") or ""))
        temp = f.get("temperature")
        if when is None or not isinstance(temp, (int, float)):
            continue
        out.append((dt_util.as_utc(when), _convert(float(temp), unit, want)))
    return sorted(out)


def mean_between(points: list[tuple[datetime, float]], start: datetime, end: datetime) -> float | None:
    """The mean forecast temperature from start to end (the hour containing start counts)."""
    if not points or points[-1][0] < start - STALE:
        return None
    vals = [t for when, t in points if start - timedelta(hours=1) < when <= end]
    return round(sum(vals) / len(vals), 1) if vals else None


def low_within(points: list[tuple[datetime, float]], now: datetime, span: timedelta = LOOKAHEAD) -> tuple[float, datetime] | None:
    """The lowest forecast temperature from now to now + span, and when."""
    if not points or points[-1][0] < now - STALE:
        return None
    window = [(t, when) for when, t in points if now - timedelta(hours=1) < when <= now + span]
    if not window:
        return None
    temp, when = min(window)
    return temp, when


def shallow_heat(heat: float, warmest: float, setback: float) -> float:
    """A block's heat set point with the setback limited to `setback` under `warmest`."""
    return max(heat, warmest - setback)
