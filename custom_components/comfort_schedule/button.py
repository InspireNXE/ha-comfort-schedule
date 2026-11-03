"""Press after changing the HVAC filter."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ComfortConfigEntry
from .entity import ComfortEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComfortConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([FilterChangedButton(entry)])


class FilterChangedButton(ComfortEntity, ButtonEntity):
    """Starts the filter's runtime count over."""

    _attr_name = "Filter changed"
    _attr_icon = "mdi:air-filter"

    def __init__(self, entry: ComfortConfigEntry) -> None:
        super().__init__(entry, "filter_changed")

    async def async_press(self) -> None:
        await self.controller.async_filter_changed()
