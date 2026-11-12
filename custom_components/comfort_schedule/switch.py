"""Turn the whole schedule on or off (e.g. on vacation), and Away mode on or off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ComfortConfigEntry
from .const import DOMAIN
from .entity import ComfortEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComfortConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entities: list[SwitchEntity] = [ScheduleSwitch(entry)]
    if entry.runtime_data.presence_entities:
        entities.append(AwaySwitch(entry))
    elif entity_id := er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{entry.entry_id}_away"):
        # Presence was turned off in the options: the switch has nothing left to do.
        er.async_get(hass).async_remove(entity_id)
    async_add_entities(entities)


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


class AwaySwitch(ComfortEntity, SwitchEntity):
    """On = Away set points apply once everyone has left. Off = the schedule, whoever is home."""

    _attr_name = "Away mode"
    _attr_icon = "mdi:home-export-outline"

    def __init__(self, entry: ComfortConfigEntry) -> None:
        super().__init__(entry, "away")

    @property
    def is_on(self) -> bool:
        return self.controller.away_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.controller.async_set_away_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.controller.async_set_away_enabled(False)
