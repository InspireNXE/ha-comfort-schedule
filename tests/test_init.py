"""The integration set up in a real Home Assistant: entities, services, diagnostics."""

from __future__ import annotations

from datetime import timedelta

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.comfort_schedule.const import DOMAIN
from custom_components.comfort_schedule.diagnostics import async_get_config_entry_diagnostics

from .conftest import SOURCE

WEEK = [[{"start": 0, "heat": 68, "cool": 74, "label": "Home"}] for _ in range(7)]


@pytest.fixture
async def entry(hass: HomeAssistant, thermostat: str) -> MockConfigEntry:
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "climate", "set_hvac_mode")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hallway Comfort",
        unique_id=SOURCE,
        data={"climate_entity": SOURCE, "initial_schedule": WEEK},
        options={"deadband": 3, "presence_entities": ["person.sam"], "away_heat": 62, "away_cool": 80},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _entity(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        {"climate": "climate", "filter_changed": "button", "filter_due": "binary_sensor",
         "hvac_problem": "binary_sensor", "schedule": "switch"}.get(key, "sensor"),
        DOMAIN,
        f"{entry.entry_id}_{key}",
    )
    assert entity_id, key
    return entity_id


async def test_setup_creates_entities(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    for key in ("climate", "period", "run_time", "heat_runtime_today", "cool_runtime_today", "filter_runtime",
                "filter_changed", "filter_due", "hvac_problem", "schedule"):
        assert hass.states.get(_entity(hass, entry, key)) is not None, key
    climate = hass.states.get(_entity(hass, entry, "climate"))
    assert climate.attributes["status"] == "schedule"
    assert climate.attributes["presence"] is True
    assert "heat_cool" in climate.attributes["hvac_modes"]  # switched by us: the thermostat has Heat and Cool only
    for service in ("set_vacation", "cancel_vacation", "filter_changed", "apply_suggestion", "dismiss_suggestion"):
        assert hass.services.has_service(DOMAIN, service), service


async def test_vacation_service(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    climate = _entity(hass, entry, "climate")
    end = dt_util.now() + timedelta(days=2)
    await hass.services.async_call(
        DOMAIN, "set_vacation", {"entity_id": climate, "end": end.isoformat(), "heat": 60, "cool": 82}, blocking=True
    )
    await hass.async_block_till_done()
    state = hass.states.get(climate)
    assert state.attributes["status"] == "vacation"
    assert state.attributes["heat_setpoint"] == 60
    assert hass.states.get(_entity(hass, entry, "period")).state == "Vacation"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "set_vacation",
            {"entity_id": climate, "end": (dt_util.now() - timedelta(hours=1)).isoformat(), "heat": 60, "cool": 82},
            blocking=True,
        )
    await hass.services.async_call(DOMAIN, "cancel_vacation", {"entity_id": climate}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(climate).attributes["status"] == "schedule"


async def test_set_points_must_be_numbers(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    climate = _entity(hass, entry, "climate")
    calls = (
        (DOMAIN, "set_hold", {"heat": "nan"}),
        ("climate", "set_temperature", {"temperature": "nan"}),
        (DOMAIN, "set_vacation", {"end": (dt_util.now() + timedelta(days=1)).isoformat(), "heat": "inf", "cool": 82}),
    )
    for domain, service, data in calls:
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(domain, service, {"entity_id": climate, **data}, blocking=True)
    assert entry.runtime_data.hold is None
    assert entry.runtime_data.vacation is None


async def test_presence(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    climate = _entity(hass, entry, "climate")
    hass.states.async_set("person.sam", "home")
    await hass.async_block_till_done()
    assert hass.states.get(climate).attributes["status"] == "schedule"
    hass.states.async_set("person.sam", "not_home")
    await hass.async_block_till_done()
    controller = entry.runtime_data
    assert not controller.away  # waits for the away delay
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=11))
    await hass.async_block_till_done()
    assert hass.states.get(climate).attributes["status"] == "away"
    assert hass.states.get(_entity(hass, entry, "period")).state == "Away"


async def test_filter_button(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    controller = entry.runtime_data
    controller.filter_offset = -301 * 3600  # pretend 301 hours have run
    controller._check_filter()
    await hass.async_block_till_done()
    assert hass.states.get(_entity(hass, entry, "filter_due")).state == "on"
    await hass.services.async_call(
        "button", "press", {"entity_id": _entity(hass, entry, "filter_changed")}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(_entity(hass, entry, "filter_due")).state == "off"
    assert float(hass.states.get(_entity(hass, entry, "filter_runtime")).state) == 0


async def test_unknown_suggestion(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "apply_suggestion", {"entity_id": _entity(hass, entry, "climate"), "id": "nope"}, blocking=True
        )


async def test_diagnostics(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["options"]["presence_entities"] == "**REDACTED**"
    assert diag["source"]["entity_id"] == SOURCE
    assert diag["source"]["native_heat_cool"] is False
    assert diag["schedule"][0][0]["label"] == "Home"
    assert diag["status"] == "schedule"
    assert isinstance(diag["recent_writes"], list)


async def test_unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    for issue in (f"filter_due_{entry.entry_id}", f"hvac_slowdown_{entry.entry_id}_heat"):
        ir.async_create_issue(hass, DOMAIN, issue, is_fixable=False, severity=ir.IssueSeverity.WARNING, translation_key="filter_due")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"filter_due_{entry.entry_id}") is None
    assert registry.async_get_issue(DOMAIN, f"hvac_slowdown_{entry.entry_id}_heat") is None
