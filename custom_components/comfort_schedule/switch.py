"""Turn the whole schedule on or off (e.g. on vacation)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ComfortConfigEntry
from .entity import ComfortEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComfortConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([ScheduleSwitch(entry)])


class ScheduleSwitch(ComfortEntity, SwitchEntity):
    """On = follow the schedule. Turning it on also ends any hold."""

    _attr_name = "Schedule"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, entry: ComfortConfigEntry) -> None:
        super().__init__(entry, "schedule")

    @property
    def is_on(self) -> bool:
        return self.controller.schedule_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_resume()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_pause()
