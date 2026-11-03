"""Problems: a run that isn't moving the temperature, and a filter that's due."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ComfortConfigEntry
from .entity import ComfortEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComfortConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([HvacProblemSensor(entry), FilterDueSensor(entry)])


class HvacProblemSensor(ComfortEntity, BinarySensorEntity):
    """HVAC problem detected by the health monitor."""

    _attr_name = "HVAC problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: ComfortConfigEntry) -> None:
        super().__init__(entry, "hvac_problem")

    @property
    def is_on(self) -> bool:
        return self.controller.health.problem is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        health = self.controller.health
        return {
            "reason": health.problem,
            "run_minutes": health.run_minutes(),
            "temp_change": (round(p, 1) if (p := health.progress()) is not None else None),
            "action": health.action,
            "message": health.problem_detail.get("message"),
        }


class FilterDueSensor(ComfortEntity, BinarySensorEntity):
    """On once the system has run the filter reminder's hours since the last change."""

    _attr_name = "Filter change due"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:air-filter"

    def __init__(self, entry: ComfortConfigEntry) -> None:
        super().__init__(entry, "filter_due")

    @property
    def is_on(self) -> bool:
        return self.controller.filter_due()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.controller
        return {"hours": c.filter_used_hours(), "limit": c.filter_hours, "since": c.filter_since}
