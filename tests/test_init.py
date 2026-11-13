"""The integration set up in a real Home Assistant: entities, services, diagnostics."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.comfort_schedule.const import DOMAIN
from custom_components.comfort_schedule.diagnostics import async_get_config_entry_diagnostics
from custom_components.comfort_schedule.schedule import Period, next_change

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
         "hvac_problem": "binary_sensor", "schedule": "switch", "away": "switch"}.get(key, "sensor"),
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


async def test_heat_only_virtual_off(hass: HomeAssistant) -> None:
    hass.states.async_set(
        SOURCE,
        "heat",
        {
            "friendly_name": "Shelly Thermostat",
            "hvac_modes": ["heat"],
            "hvac_action": "idle",
            "current_temperature": 69,
            "temperature": 66,
            "min_temp": 45,
            "max_temp": 95,
        },
    )
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "climate", "set_hvac_mode")
    heat_week = [[{"start": 0, "heat": 66, "cool": 95, "label": "Home"}] for _ in range(7)]
    heat_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Shelly Comfort",
        unique_id=SOURCE,
        data={"climate_entity": SOURCE, "initial_schedule": heat_week},
        options={"cooling_enabled": False, "off_fallback_temperature": 45},
    )
    heat_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(heat_entry.entry_id)
    await hass.async_block_till_done()
    climate = _entity(hass, heat_entry, "climate")
    state = hass.states.get(climate)
    assert state.attributes["hvac_modes"] == ["off", "heat"]
    assert state.attributes["cooling_enabled"] is False
    assert "cool_setpoint" not in state.attributes and "cool" not in state.attributes["period"]
    assert er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{heat_entry.entry_id}_cool_runtime_today"
    ) is None

    await hass.services.async_call("climate", "turn_off", {"entity_id": climate}, blocking=True)
    await hass.async_block_till_done()
    state = hass.states.get(climate)
    assert state.state == "off" and state.attributes["virtual_off"] is True
    assert any(write.get("temperature") == 45 for write in heat_entry.runtime_data.write_log)

    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": climate, "temperature": 66}, blocking=True
    )
    await hass.async_block_till_done()
    state = hass.states.get(climate)
    assert state.state == "heat" and state.attributes["virtual_off"] is False
    assert state.attributes["hold"]["heat"] == 66
    await hass.services.async_call("climate", "turn_off", {"entity_id": climate}, blocking=True)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "set_hold", {"entity_id": climate, "cool": 70}, blocking=True
        )

    schedule = [[{"start": 0, "heat": 64, "label": "Home"}] for _ in range(7)]
    await hass.services.async_call(
        DOMAIN, "set_schedule", {"entity_id": climate, "schedule": schedule}, blocking=True
    )
    await hass.async_block_till_done()
    assert "cool" not in hass.states.get(climate).attributes["schedule"][0][0]

    await hass.services.async_call("climate", "turn_on", {"entity_id": climate}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(climate).state == "heat"


async def test_turning_cooling_off_removes_the_cooling_sensor(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    registry = er.async_get(hass)
    unique_id = f"{entry.entry_id}_cool_runtime_today"
    assert registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "cooling_enabled": False})
    await hass.async_block_till_done()
    assert registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None
    hass.config_entries.async_update_entry(entry, options={**entry.options, "cooling_enabled": True})
    await hass.async_block_till_done()
    assert registry.async_get_entity_id("sensor", DOMAIN, unique_id)


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
    # The Away mode switch: off goes back to the schedule even with nobody home.
    away_switch = _entity(hass, entry, "away")
    assert hass.states.get(away_switch).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": away_switch}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(away_switch).state == "off"
    assert hass.states.get(climate).attributes["status"] == "schedule"
    assert hass.states.get(climate).attributes["away_enabled"] is False
    await hass.services.async_call("switch", "turn_on", {"entity_id": away_switch}, blocking=True)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=11))
    await hass.async_block_till_done()
    assert hass.states.get(climate).attributes["status"] == "away"


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
    entry.runtime_data.away = True  # whether the house is empty is who is home, too
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["status"] == "**REDACTED**" and diag["desired"] == "**REDACTED**"
    assert "away" not in diag


async def test_unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    for issue in (f"filter_due_{entry.entry_id}", f"hvac_slowdown_{entry.entry_id}_heat"):
        ir.async_create_issue(hass, DOMAIN, issue, is_fixable=False, severity=ir.IssueSeverity.WARNING, translation_key="filter_due")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"filter_due_{entry.entry_id}") is None
    assert registry.async_get_issue(DOMAIN, f"hvac_slowdown_{entry.entry_id}_heat") is None


async def test_next_change_when_the_clocks_go_back(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("America/Chicago")
    tz = dt_util.get_default_time_zone()
    day = [Period(0, 62, 80, "Night"), Period(60, 66, 78, "Early"), Period(90, 68, 76, "Late"), Period(360, 68, 74, "Day")]
    week = [day] * 7
    # Sunday 1 November 2026: 2:00 CDT becomes 1:00 CST, so 1:00 to 2:00 happens twice.
    first_late = datetime(2026, 11, 1, 1, 30, tzinfo=tz)  # 1:30 CDT
    second_early = datetime(2026, 11, 1, 1, 0, fold=1, tzinfo=tz)  # 1:00 CST
    when, period = next_change(week, first_late + timedelta(minutes=1))
    assert (period.label, dt_util.as_utc(when)) == ("Early", dt_util.as_utc(second_early))
    when, period = next_change(week, dt_util.as_utc(second_early) + timedelta(minutes=1))  # (adding to a local time drops fold)
    assert period.label == "Late"
    assert dt_util.as_utc(when) == dt_util.as_utc(datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=tz))
    when, period = next_change(week, datetime(2026, 11, 1, 1, 31, fold=1, tzinfo=tz))
    assert period.label == "Day" and dt_util.as_local(when).hour == 6
    # Spring forward (8 March 2026): 2:00 to 3:00 doesn't happen; nothing fires early.
    spring = [[Period(0, 62, 80, "Night"), Period(150, 68, 76, "Morning")]] * 7
    when, period = next_change(spring, datetime(2026, 3, 8, 1, 0, tzinfo=tz))
    assert period.label == "Morning" and dt_util.as_utc(when) > dt_util.as_utc(datetime(2026, 3, 8, 1, 59, tzinfo=tz))
