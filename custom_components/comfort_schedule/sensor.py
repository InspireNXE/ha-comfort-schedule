"""Current period and current run time."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import ComfortConfigEntry
from .entity import ComfortEntity
from .schedule import format_minutes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComfortConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [
            PeriodSensor(entry),
            RunTimeSensor(entry),
            RuntimeTodaySensor(entry, "heat"),
            RuntimeTodaySensor(entry, "cool"),
            FilterRuntimeSensor(entry),
        ]
    )


class PeriodSensor(ComfortEntity, SensorEntity):
    """Which schedule period is in effect, and what comes next."""

    _attr_name = "Period"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, entry: ComfortConfigEntry) -> None:
        super().__init__(entry, "period")

    @property
    def native_value(self) -> str:
        c = self.controller
        status = c.status()
        if status in ("paused", "hold", "vacation", "away"):
            return status.capitalize()
        if status == "early" and (early := c.early_info()):
            return f"{early['label']} (early start)"
        return c.period_info()["label"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.controller
        info = c.period_info()
        nxt = c.next_change() if c.schedule_enabled else None
        return {
            "scheduled_label": info["label"],
            "starts": format_minutes(info["start"]),
            "ends": format_minutes(info["end"]),
            "heat": info["heat"],
            "cool": info["cool"],
            "mode": info["mode"] or "use current",
            "fan": info["fan"] or "use current",
            "hold_until": (
                dt_util.as_local(c.hold.until).isoformat() if c.hold and c.hold.until else None
            ),
            "next_change": dt_util.as_local(nxt[0]).isoformat() if nxt else None,
            "next_label": nxt[1].label if nxt else None,
            "next_heat": nxt[1].heat if nxt else None,
            "next_cool": nxt[1].cool if nxt else None,
            "next_mode": (nxt[1].mode or "use current") if nxt else None,
            "next_fan": (nxt[1].fan or "use current") if nxt else None,
        }


class RunTimeSensor(ComfortEntity, SensorEntity):
    """Minutes the furnace/AC has been running in the current cycle."""

    _attr_name = "Run time"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-outline"

    def __init__(self, entry: ComfortConfigEntry) -> None:
        super().__init__(entry, "run_time")

    @property
    def native_value(self) -> int:
        return self.controller.health.run_minutes()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"action": self.controller.health.action}


class RuntimeTodaySensor(ComfortEntity, SensorEntity):
    """Hours of heating or cooling today; resets at midnight.

    TOTAL_INCREASING lets Home Assistant keep long-term statistics, so the statistics
    graph card can show daily/weekly/monthly runtime. Attributes carry the common
    periods for dashboards that don't use statistics.
    """

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def __init__(self, entry: ComfortConfigEntry, kind: str) -> None:
        super().__init__(entry, f"{kind}_runtime_today")
        self._kind = kind
        self._attr_name = "Heating runtime today" if kind == "heat" else "Cooling runtime today"
        self._attr_icon = "mdi:fire" if kind == "heat" else "mdi:snowflake"

    @property
    def native_value(self) -> float:
        return self.controller.runtime.summary(self._kind)["today"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        summary = self.controller.runtime.summary(self._kind)
        return {f"{name}_hours": value for name, value in summary.items() if name != "today"}


class FilterRuntimeSensor(ComfortEntity, SensorEntity):
    """Hours of heating plus cooling since the filter was last changed."""

    _attr_name = "Filter runtime"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:air-filter"

    def __init__(self, entry: ComfortConfigEntry) -> None:
        super().__init__(entry, "filter_runtime")

    @property
    def native_value(self) -> float:
        return self.controller.filter_used_hours()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.controller
        return {"limit_hours": c.filter_hours, "since": c.filter_since, "due": c.filter_due()}
