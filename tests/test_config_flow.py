"""Config and options flows."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comfort_schedule.const import DOMAIN

from .conftest import SOURCE

SETUP = "custom_components.comfort_schedule.async_setup_entry"


async def test_user_flow_creates_entry(hass: HomeAssistant, thermostat: str) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"climate_entity": SOURCE, "deadband": 3}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hallway Comfort"
    assert result["data"]["climate_entity"] == SOURCE
    week = result["data"]["initial_schedule"]
    assert len(week) == 7 and week[0][0]["cool"] - week[0][0]["heat"] >= 3
    assert result["options"] == {"deadband": 3.0}


async def test_user_flow_errors(hass: HomeAssistant, thermostat: str) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"climate_entity": "climate.missing", "deadband": 3}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"climate_entity": "not_found"}


async def test_same_thermostat_twice_aborts(hass: HomeAssistant, thermostat: str) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=SOURCE, data={"climate_entity": SOURCE}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"climate_entity": SOURCE, "deadband": 3}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def _sections(**overrides: dict) -> dict:
    form = {
        "deadband": 3,
        "hold_default": "next_period",
        "health": {"heat_minutes": 45, "cool_minutes": 60, "min_progress": 1, "wrong_way": 0.8, "trend_drop": 25},
        "heat_cool": {"changeover_margin": 1, "changeover_minutes": 30, "force_heat_cool_emulation": False},
        "presence": {"away_delay": 10},
        "energy": {"filter_hours": 300},
        "learning": {"early_start_max": 0, "suggestions": True},
        "thermostats": {},
    }
    for key, value in overrides.items():
        form[key] = {**form[key], **value}
    return form


async def test_options_saved_flat(hass: HomeAssistant, thermostat: str) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SOURCE, data={"climate_entity": SOURCE}, options={"deadband": 3})
    entry.add_to_hass(hass)
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            _sections(
                presence={"presence_entities": ["person.sam"], "away_heat": 62, "away_cool": 80},
                energy={"outdoor_entity": "sensor.outside", "heat_cost": 0.9},
                thermostats={"follower_entities": ["climate.upstairs"]},
            ),
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    options = entry.options
    assert "presence" not in options and "health" not in options
    assert options["presence_entities"] == ["person.sam"]
    assert options["away_heat"] == 62 and options["heat_cost"] == 0.9
    assert options["follower_entities"] == ["climate.upstairs"]
    assert options["heat_minutes"] == 45 and options["filter_hours"] == 300


async def test_options_need_both_away_setpoints(hass: HomeAssistant, thermostat: str) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SOURCE, data={"climate_entity": SOURCE}, options={"deadband": 3})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _sections(presence={"presence_entities": ["person.sam"], "away_heat": 62})
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "away_setpoints"}


async def test_options_form_shows_saved_values(hass: HomeAssistant, thermostat: str) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SOURCE,
        data={"climate_entity": SOURCE},
        options={"deadband": 3, "presence_entities": ["person.sam"], "away_heat": 62, "away_cool": 80},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    fields = {str(k): v for k, v in result["data_schema"].schema.items()}
    for name in ("health", "heat_cool", "presence", "energy", "learning", "thermostats"):
        assert name in fields
    presence = {str(k): k for k in fields["presence"].schema.schema}
    assert presence["away_heat"].description == {"suggested_value": 62}
    assert presence["presence_entities"].description == {"suggested_value": ["person.sam"]}
