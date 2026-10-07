"""Comfort Schedule: weekly heat/cool schedule, set point guard and HVAC health for one thermostat."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, FRONTEND_FILE, FRONTEND_URL, PLATFORMS, STORAGE_VERSION
from .controller import ComfortController

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type ComfortConfigEntry = ConfigEntry[ComfortController]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the Lovelace cards so they load without adding a dashboard resource."""
    frontend_dir = Path(__file__).parent / "frontend"
    js_file = frontend_dir / FRONTEND_FILE
    version = int(await hass.async_add_executor_job(lambda: js_file.stat().st_mtime))
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL, str(frontend_dir), cache_headers=False)]
    )
    add_extra_js_url(hass, f"{FRONTEND_URL}/{FRONTEND_FILE}?v={version}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ComfortConfigEntry) -> bool:
    controller = ComfortController(hass, entry)
    await controller.async_load()
    entry.runtime_data = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    controller.async_start()
    entry.async_on_unload(controller.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ComfortConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ComfortConfigEntry) -> None:
    """Clean up the saved schedule and any open repair issue."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.runtime").async_remove()
    ir.async_delete_issue(hass, DOMAIN, f"hvac_problem_{entry.entry_id}")


async def _async_options_updated(hass: HomeAssistant, entry: ComfortConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
