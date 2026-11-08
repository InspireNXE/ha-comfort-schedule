"""Config and options flows."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comfort_schedule.const import DOMAIN

from .conftest import SOURCE

SETUP = "custom_components.comfort_schedule.async_setup_entry"


async def test_user_flow_creates_entry(hass: HomeAssistant, thermostat: str) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"climate_entity": SOURCE, "cooling_enabled": True}
        )
        assert result["type"] is FlowResultType.FORM and result["step_id"] == "cooling"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"deadband": 3})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hallway Comfort"
    assert result["data"]["climate_entity"] == SOURCE
    week = result["data"]["initial_schedule"]
    assert len(week) == 7 and week[0][0]["cool"] - week[0][0]["heat"] >= 3
    assert result["options"] == {"cooling_enabled": True, "deadband": 3.0}


async def test_user_flow_creates_heat_only_entry(hass: HomeAssistant, thermostat: str) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"climate_entity": SOURCE, "cooling_enabled": False},
        )
        assert result["type"] is FlowResultType.FORM and result["step_id"] == "heat_only"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"off_fallback_temperature": 45}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {"cooling_enabled": False, "off_fallback_temperature": 45.0}
    assert result["data"]["initial_schedule"][0][0]["heat"] == 66


async def test_user_flow_errors(hass: HomeAssistant, thermostat: str) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"climate_entity": "climate.missing", "cooling_enabled": False}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"climate_entity": "not_found"}


async def test_same_thermostat_twice_aborts(hass: HomeAssistant, thermostat: str) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=SOURCE, data={"climate_entity": SOURCE}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"climate_entity": SOURCE, "cooling_enabled": False}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def _sections(**overrides: dict) -> dict:
    form = {
        "cooling_enabled": True,
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
    hass.config.units = US_CUSTOMARY_SYSTEM
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
        assert result["type"] is FlowResultType.FORM and result["step_id"] == "offsets"
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"climate.upstairs": -1.5})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    options = entry.options
    assert options["follower_offsets"] == {"climate.upstairs": -1.5}
    assert options["similar_weather"] == 10 and options["degree_day_base"] == 65
    assert "presence" not in options and "health" not in options
    assert options["presence_entities"] == ["person.sam"]
    assert options["away_heat"] == 62 and options["heat_cost"] == 0.9
    assert options["follower_entities"] == ["climate.upstairs"]
    assert options["heat_minutes"] == 45 and options["filter_hours"] == 300


async def test_options_need_both_away_setpoints(hass: HomeAssistant, thermostat: str) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
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


async def test_heat_only_options_hide_and_preserve_cooling(hass: HomeAssistant, thermostat: str) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SOURCE,
        data={"climate_entity": SOURCE},
        options={"cooling_enabled": False, "deadband": 3, "cool_minutes": 60, "away_cool": 80},
    )
    entry.add_to_hass(hass)
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        fields = {str(k): v for k, v in result["data_schema"].schema.items()}
        assert "deadband" not in fields and "heat_cool" not in fields
        assert "cool_minutes" not in {str(k) for k in fields["health"].schema.schema}
        assert "away_cool" not in {str(k) for k in fields["presence"].schema.schema}
        form = {
            "cooling_enabled": False,
            "hold_default": "next_period",
            "health": {"heat_minutes": 45, "min_progress": 1, "wrong_way": 0.8, "trend_drop": 25},
            "presence": {"away_delay": 10},
            "energy": {"filter_hours": 300, "similar_weather": 10, "degree_day_base": 65},
            "learning": {"early_start_max": 0, "suggestions": True},
            "thermostats": {},
        }
        result = await hass.config_entries.options.async_configure(result["flow_id"], form)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["deadband"] == 3 and entry.options["cool_minutes"] == 60
    assert entry.options["away_cool"] == 80


async def test_away_setpoints_in_celsius(hass: HomeAssistant, thermostat: str) -> None:
    hass.config.units = METRIC_SYSTEM
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SOURCE, data={"climate_entity": SOURCE}, options={"deadband": 1.5})
    entry.add_to_hass(hass)
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            _sections(presence={"presence_entities": ["person.sam"], "away_heat": 16, "away_cool": 28}),
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["away_heat"] == 16 and entry.options["away_cool"] == 28


async def test_options_without_followers_drop_offsets(hass: HomeAssistant, thermostat: str) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SOURCE,
        data={"climate_entity": SOURCE},
        options={"deadband": 3, "follower_entities": ["climate.upstairs"], "follower_offsets": {"climate.upstairs": -1}},
    )
    entry.add_to_hass(hass)
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        form = _sections(energy={"similar_weather": 8, "degree_day_base": 60})
        result = await hass.config_entries.options.async_configure(result["flow_id"], form)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "follower_offsets" not in entry.options and not entry.options.get("follower_entities")
    assert entry.options["similar_weather"] == 8 and entry.options["degree_day_base"] == 60


async def test_followers_cannot_be_comfort_thermostats(hass: HomeAssistant, thermostat: str) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SOURCE, data={"climate_entity": SOURCE}, options={"deadband": 3})
    entry.add_to_hass(hass)
    comfort = er.async_get(hass).async_get_or_create("climate", DOMAIN, "other_climate", suggested_object_id="upstairs_comfort")
    hass.states.async_set(comfort.entity_id, "heat_cool", {"hvac_modes": ["off", "heat_cool"]})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    fields = {str(k): v for k, v in result["data_schema"].schema.items()}
    selector = fields["thermostats"].schema.schema
    followers = next(v for k, v in selector.items() if str(k) == "follower_entities")
    assert comfort.entity_id in followers.config["exclude_entities"]
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], _sections(thermostats={"follower_entities": [comfort.entity_id]})
        )
