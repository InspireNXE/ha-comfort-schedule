"""On when a heating/cooling run isn't moving the temperature."""

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
    async_add_entities([HvacProblemSensor(entry)])


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
