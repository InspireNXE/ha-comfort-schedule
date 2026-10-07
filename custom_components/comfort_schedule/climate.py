"""The wrapper thermostat: the real one, plus the schedule and the heat/cool guard."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.climate import (
    ATTR_CURRENT_HUMIDITY,
    ATTR_CURRENT_TEMPERATURE,
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TARGET_TEMP_STEP,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    PRECISION_TENTHS,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ComfortConfigEntry
from .const import (
    ATTR_COOL,
    ATTR_DURATION,
    ATTR_HEAT,
    ATTR_SCHEDULE,
    HOLD_CHOICES,
    SERVICE_PAUSE,
    SERVICE_RESUME,
    SERVICE_SET_HOLD,
    SERVICE_SET_SCHEDULE,
)
from .entity import ComfortEntity
from .schedule import week_to_json

SUPPORTED_MODES = (HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.HEAT_COOL)


def _same(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.05
    except (TypeError, ValueError):
        return False


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ComfortConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([ComfortClimate(entry)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_SCHEDULE,
        {
            vol.Required(ATTR_SCHEDULE): vol.All(list, vol.Length(min=7, max=7)),
            vol.Optional("label_colors"): dict,
        },
        "async_service_set_schedule",
    )
    platform.async_register_entity_service(
        SERVICE_SET_HOLD,
        {
            vol.Optional(ATTR_HEAT): vol.Coerce(float),
            vol.Optional(ATTR_COOL): vol.Coerce(float),
            vol.Optional(ATTR_DURATION): vol.Any(
                vol.In(HOLD_CHOICES), vol.All(vol.Coerce(int), vol.Range(min=1, max=10080))
            ),
        },
        "async_service_set_hold",
    )
    platform.async_register_entity_service(SERVICE_RESUME, {}, "async_service_resume")
    platform.async_register_entity_service(SERVICE_PAUSE, {}, "async_service_pause")


class ComfortClimate(ComfortEntity, ClimateEntity):
    """Mirrors the real thermostat; set point changes go through the guard and become holds."""

    _attr_name = None
    # Half-degree set points are allowed; the default (whole degrees in °F) would round them away.
    _attr_precision = PRECISION_TENTHS
    _unrecorded_attributes = frozenset(
        {"schedule", "period", "next_period", "hold", "health", "health_rules", "source_entity", "runtime", "label_colors"}
    )

    def __init__(self, entry: ComfortConfigEntry) -> None:
        super().__init__(entry, "climate")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.controller.climate_entity_id = self.entity_id

    # ------------------------------------------------------------ mirrored from the real thermostat

    @property
    def _attrs(self) -> dict[str, Any]:
        state = self.controller.source_state()
        return dict(state.attributes) if state else {}

    @property
    def available(self) -> bool:
        state = self.controller.source_state()
        return state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)

    @property
    def temperature_unit(self) -> str:
        return self.hass.config.units.temperature_unit

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        if self._attrs.get(ATTR_FAN_MODES):
            features |= ClimateEntityFeature.FAN_MODE
        return features

    @property
    def hvac_modes(self) -> list[HVACMode]:
        modes = set(self._attrs.get(ATTR_HVAC_MODES) or [])
        if self.controller.can_emulate():
            # Thermostats with only Heat and Cool get Heat/Cool from us.
            modes.add(HVACMode.HEAT_COOL)
        return [HVACMode(m) for m in SUPPORTED_MODES if m in modes] or [HVACMode.OFF]

    @property
    def hvac_mode(self) -> HVACMode | None:
        mode = self.controller.proxy_mode()
        if mode is None:
            return None
        try:
            return HVACMode(mode)
        except ValueError:
            return None

    @property
    def hvac_action(self) -> HVACAction | None:
        try:
            return HVACAction(self._attrs.get(ATTR_HVAC_ACTION))
        except ValueError:
            return None

    @property
    def current_temperature(self) -> float | None:
        return self._attrs.get(ATTR_CURRENT_TEMPERATURE)

    @property
    def current_humidity(self) -> float | None:
        return self._attrs.get(ATTR_CURRENT_HUMIDITY)

    @property
    def fan_modes(self) -> list[str] | None:
        return self._attrs.get(ATTR_FAN_MODES)

    @property
    def fan_mode(self) -> str | None:
        return self._attrs.get(ATTR_FAN_MODE)

    @property
    def min_temp(self) -> float:
        return self.controller.limits()[0]

    @property
    def max_temp(self) -> float:
        return self.controller.limits()[1]

    @property
    def target_temperature_step(self) -> float | None:
        return self._attrs.get(ATTR_TARGET_TEMP_STEP)

    # ------------------------------------------------------------ what we want it set to

    @property
    def target_temperature(self) -> float | None:
        heat, cool = self.controller.setpoints()
        if self.hvac_mode == HVACMode.HEAT:
            return heat
        if self.hvac_mode == HVACMode.COOL:
            return cool
        return None

    @property
    def target_temperature_low(self) -> float | None:
        return self.controller.setpoints()[0] if self.hvac_mode == HVACMode.HEAT_COOL else None

    @property
    def target_temperature_high(self) -> float | None:
        return self.controller.setpoints()[1] if self.hvac_mode == HVACMode.HEAT_COOL else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        c = self.controller
        heat, cool = c.setpoints()
        nxt = c.next_change() if c.schedule_enabled else None
        return {
            "source_entity": c.source,
            "schedule_enabled": c.schedule_enabled,
            "heat_setpoint": heat,
            "cool_setpoint": cool,
            "deadband": c.deadband,
            # Heat/Cool provided by switching the thermostat itself between Heat and Cool.
            "heat_cool_switching": c.emulating,
            "device_mode": state.state if (state := c.source_state()) else None,
            "hold": c.hold.as_dict() if c.hold else None,
            "hold_default": c.hold_default,
            "period": c.period_info(),
            "next_change": nxt[0].isoformat() if nxt else None,
            "next_period": nxt[1].as_dict() if nxt else None,
            "schedule": week_to_json(c.week),
            "health": {**c.health.as_dict(), **c.health.problem_detail},
            "battery_entity": c.battery_entity,
            "humidity_entity": c.humidity_entity,
            "battery_level": c.battery_level(),
            "last_run": c.runtime.last_run,
            "label_colors": c.label_colors,
            "runtime": {
                "heat": c.runtime.summary("heat"),
                "cool": c.runtime.summary("cool"),
                "daily": c.runtime.daily(31),
                "weekly": c.runtime.weekly(12),
                "monthly": c.runtime.monthly(12),
                "compare": c.runtime.compare(),
            },
            "health_rules": {
                "heat_minutes": c.heat_minutes,
                "cool_minutes": c.cool_minutes,
                "min_progress": c.min_progress,
                "wrong_way": c.wrong_way,
                "notify_entity": c.notify_entity,
            },
        }

    # ------------------------------------------------------------ commands

    async def async_set_temperature(self, **kwargs: Any) -> None:
        mode = kwargs.get(ATTR_HVAC_MODE)
        if mode is not None:
            await self.async_set_hvac_mode(HVACMode(mode))
        mode = mode or self.hvac_mode
        heat, cool = self.controller.setpoints()
        current = (heat, cool)
        changed: str | None = None
        if ATTR_TARGET_TEMP_LOW in kwargs or ATTR_TARGET_TEMP_HIGH in kwargs:
            low = kwargs.get(ATTR_TARGET_TEMP_LOW, heat)
            high = kwargs.get(ATTR_TARGET_TEMP_HIGH, cool)
            low_moved, high_moved = not _same(low, heat), not _same(high, cool)
            if low_moved and not high_moved:
                changed = "heat"
            elif high_moved and not low_moved:
                changed = "cool"
            heat, cool = low, high
        elif ATTR_TEMPERATURE in kwargs:
            if mode == HVACMode.COOL:
                cool, changed = kwargs[ATTR_TEMPERATURE], "cool"
            else:
                heat, changed = kwargs[ATTR_TEMPERATURE], "heat"
        else:
            return
        if _same(heat, current[0]) and _same(cool, current[1]):
            # "Setting" the temperature it's already at (a card, voice, an automation) isn't
            # a manual change, so it doesn't start a hold. comfort_schedule.set_hold still does.
            return
        await self.controller.async_set_hold(heat, cool, changed)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self.controller.async_set_mode(hvac_mode)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_FAN_MODE,
            {ATTR_ENTITY_ID: self.controller.source, ATTR_FAN_MODE: fan_mode},
            blocking=True,
        )

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        modes = self.hvac_modes
        last = self.controller.last_active_mode
        if last in modes and last != HVACMode.OFF:
            target = HVACMode(last)
        elif HVACMode.HEAT_COOL in modes:
            target = HVACMode.HEAT_COOL
        else:
            target = next((m for m in modes if m != HVACMode.OFF), HVACMode.OFF)
        await self.async_set_hvac_mode(target)

    # ------------------------------------------------------------ integration services

    async def async_service_set_schedule(
        self, schedule: list[Any], label_colors: dict[str, str] | None = None
    ) -> None:
        try:
            await self.controller.async_set_schedule(schedule, label_colors)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    async def async_service_set_hold(
        self,
        heat: float | None = None,
        cool: float | None = None,
        duration: str | int | None = None,
    ) -> None:
        changed = "heat" if heat is not None and cool is None else (
            "cool" if cool is not None and heat is None else None
        )
        await self.controller.async_set_hold(heat, cool, changed, duration)

    async def async_service_resume(self) -> None:
        await self.controller.async_resume()

    async def async_service_pause(self) -> None:
        await self.controller.async_pause()
