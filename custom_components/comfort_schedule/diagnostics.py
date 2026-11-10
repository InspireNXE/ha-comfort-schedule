"""Diagnostics download: settings, schedule and recent state, without who is home."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.core import HomeAssistant

from . import ComfortConfigEntry
from .const import CONF_NOTIFY, CONF_PRESENCE
from .schedule import week_to_json

TO_REDACT = {CONF_PRESENCE, CONF_NOTIFY}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ComfortConfigEntry) -> dict[str, Any]:
    c = entry.runtime_data
    state = c.source_state()
    # Whether the house is empty is who is home, too.
    status = REDACTED if c.status() == "away" else c.status()
    desired = c.desired()
    if desired is not None and desired[2] == "away":
        desired = REDACTED
    return {
        "options": async_redact_data(dict(entry.options), TO_REDACT),
        "source": {
            "entity_id": c.source,
            "state": state.state if state else None,
            "attributes": dict(state.attributes) if state else None,
            "native_heat_cool": c.uses_native_heat_cool(),
            "can_switch_heat_cool": c.can_emulate(),
        },
        "followers": {
            e: (s.state if (s := hass.states.get(e)) else None) for e in c.followers
        },
        "status": status,
        "desired": desired,
        "emulating": c.emulating,
        "schedule_enabled": c.schedule_enabled,
        "schedule": week_to_json(c.week),
        "hold": c.hold.as_dict() if c.hold else None,
        "vacation": c.vacation_dict(),
        "early_start": c.early_info(),
        "cold_snap": c.cold_snap_info(),
        "outdoor_temperature": c.outdoor_temp(),
        "health": {**c.health.as_dict(), **c.health.problem_detail},
        "slowdown": c.trend,
        "learning": c.learning.summary(c.outdoor_temp()),
        "filter": {"hours": c.filter_used_hours(), "since": c.filter_since, "notified": c.filter_notified},
        "runtime": {"heat": c.runtime.summary("heat"), "cool": c.runtime.summary("cool"), "compare": c.runtime.compare()},
        "last_run": c.runtime.last_run,
        "recent_writes": list(c.write_log),
        "suggestions": c.current_suggestions(),
        "manual_changes_logged": len(c.suggestions.log),
    }
