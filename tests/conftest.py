"""Fixtures for the Home Assistant tests (pytest-homeassistant-custom-component)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from homeassistant.core import HomeAssistant

pytest_plugins = "pytest_homeassistant_custom_component"

SOURCE = "climate.hallway"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_frontend(hass: HomeAssistant):
    """Skip the web side: the cards (frontend, http) and the repairs UI. Issues still work."""
    hass.config.components.update({"frontend", "http", "repairs"})
    with patch("custom_components.comfort_schedule.async_setup", return_value=True):
        yield


@pytest.fixture
def thermostat(hass: HomeAssistant) -> str:
    """A thermostat with Heat and Cool only (so Heat/Cool is switched by us)."""
    hass.states.async_set(
        SOURCE,
        "heat",
        {
            "friendly_name": "Hallway Thermostat",
            "hvac_modes": ["off", "heat", "cool"],
            "hvac_action": "idle",
            "current_temperature": 69,
            "temperature": 66,
            "min_temp": 45,
            "max_temp": 95,
        },
    )
    return SOURCE
