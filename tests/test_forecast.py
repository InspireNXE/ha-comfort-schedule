"""The hourly forecast: early start's outdoor temperature and the cold snap guard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.comfort_schedule import forecast as fc
from custom_components.comfort_schedule.const import DOMAIN

from .conftest import SOURCE

UTC = timezone.utc
T0 = datetime(2026, 1, 14, 18, 0, tzinfo=UTC)


def _points(*temps: float) -> list[tuple[datetime, float]]:
    return [(T0 + timedelta(hours=i), t) for i, t in enumerate(temps)]


def test_parse_converts_and_sorts() -> None:
    items = [
        {"datetime": "2026-01-14T19:00:00+00:00", "temperature": 0},
        {"datetime": "2026-01-14T18:00:00+00:00", "temperature": -10},
        {"datetime": "bad", "temperature": 3},
        {"datetime": "2026-01-14T20:00:00+00:00"},
    ]
    assert fc.parse(items, "°C", "°F") == [(T0, 14.0), (T0 + timedelta(hours=1), 32.0)]
    assert fc.parse(None, "°F", "°F") == []


def test_mean_and_low() -> None:
    pts = _points(40, 30, 20, 10, 30)
    assert fc.mean_between(pts, T0, T0 + timedelta(hours=2)) == 30.0
    assert fc.low_within(pts, T0) == (10, T0 + timedelta(hours=3))
    assert fc.low_within(pts, T0, timedelta(hours=1)) == (30, T0 + timedelta(hours=1))
    # A forecast that stopped updating hours ago isn't used.
    assert fc.low_within(pts, T0 + timedelta(hours=10)) is None
    assert fc.mean_between([], T0, T0) is None


def test_shallow_heat() -> None:
    assert fc.shallow_heat(60, 70, 3) == 67
    assert fc.shallow_heat(69, 70, 3) == 69


# A setback overnight: 62 until 6:00, then 70.
WEEK = [[{"start": 0, "heat": 62, "cool": 78, "label": "Sleep"}, {"start": 360, "heat": 70, "cool": 76, "label": "Home"}] for _ in range(7)]


@pytest.fixture
async def snap_entry(hass: HomeAssistant, thermostat: str, freezer: FrozenDateTimeFactory) -> tuple[MockConfigEntry, list]:
    await hass.config.async_set_time_zone("UTC")
    hass.config.units = US_CUSTOMARY_SYSTEM
    freezer.move_to("2026-01-14 02:00:00+00:00")  # Sleep
    async_mock_service(hass, "climate", "set_temperature")
    async_mock_service(hass, "climate", "set_hvac_mode")
    hass.states.async_set("weather.home", "cloudy", {"temperature": 20, "temperature_unit": "°F"})
    forecast: list = []

    async def get_forecasts(call: ServiceCall) -> dict:
        return {"weather.home": {"forecast": forecast}}

    hass.services.async_register("weather", "get_forecasts", get_forecasts, supports_response=SupportsResponse.ONLY)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hallway Comfort",
        unique_id=SOURCE,
        data={"climate_entity": SOURCE, "initial_schedule": WEEK},
        options={"deadband": 3, "outdoor_entity": "weather.home", "cold_snap_below": 5, "cold_snap_setback": 3},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, forecast


def _hours(*temps: float) -> list[dict]:
    start = datetime(2026, 1, 14, 2, 0, tzinfo=UTC)
    return [{"datetime": (start + timedelta(hours=i)).isoformat(), "temperature": t} for i, t in enumerate(temps)]


async def test_cold_snap_keeps_the_setback_shallow(hass: HomeAssistant, snap_entry) -> None:
    entry, forecast = snap_entry
    c = entry.runtime_data
    assert c.desired()[:2] == (62, 78)
    assert c.cold_snap_info()["active"] is False
    forecast[:] = _hours(10, 6, 2, 8)
    await c.async_refresh_forecast()
    info = c.cold_snap_info()
    assert info["active"] is True and info["low"] == 2
    assert c.desired()[:2] == (67, 78)  # 70 - 3
    climate = next(s for s in hass.states.async_all("climate") if s.entity_id != SOURCE)
    assert climate.attributes["cold_snap"]["active"] is True
    # Warmer again: back to the schedule's own set points.
    forecast[:] = _hours(20, 22, 25)
    await c.async_refresh_forecast()
    assert c.desired()[:2] == (62, 78)


async def test_early_start_uses_the_forecast(hass: HomeAssistant, snap_entry) -> None:
    entry, forecast = snap_entry
    c = entry.runtime_data
    forecast[:] = _hours(30, 20, 10, 0)
    await c.async_refresh_forecast()
    assert c.outdoor_until(datetime(2026, 1, 14, 4, 0, tzinfo=UTC)) == 20.0
    forecast[:] = []
    await c.async_refresh_forecast()
    assert c.outdoor_until(datetime(2026, 1, 14, 4, 0, tzinfo=UTC)) == 20  # the weather entity now
