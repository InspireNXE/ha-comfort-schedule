"""Shared entity plumbing."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .controller import ComfortController


class ComfortEntity(Entity):
    """Base for all Comfort Schedule entities; redraws whenever the controller changes."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, key: str) -> None:
        self.controller: ComfortController = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Comfort Schedule",
            model="Thermostat schedule",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.controller.add_listener(self.async_write_ha_state))
