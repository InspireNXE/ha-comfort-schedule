"""Set up Comfort Schedule from the UI."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.climate import (
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import ATTR_FRIENDLY_NAME, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_CHANGEOVER_MARGIN,
    CONF_CHANGEOVER_MINUTES,
    CONF_CLIMATE,
    CONF_FORCE_EMULATION,
    CONF_COOL_MINUTES,
    CONF_DEADBAND,
    CONF_HEAT_MINUTES,
    CONF_HOLD_DEFAULT,
    CONF_INITIAL_SCHEDULE,
    CONF_MIN_PROGRESS,
    CONF_NOTIFY,
    CONF_WRONG_WAY,
    DEFAULT_CHANGEOVER_MARGIN,
    DEFAULT_CHANGEOVER_MINUTES,
    DEFAULT_COOL_MINUTES,
    DEFAULT_DEADBAND,
    DEFAULT_HEAT_MINUTES,
    DEFAULT_HOLD,
    DEFAULT_MIN_PROGRESS,
    DEFAULT_WRONG_WAY,
    DOMAIN,
)
from .schedule import default_week, guard, unit_defaults, week_to_json

HOLD_OPTIONS = [
    SelectOptionDict(value="next_period", label="Until the next period"),
    SelectOptionDict(value="120", label="2 hours"),
    SelectOptionDict(value="240", label="4 hours"),
    SelectOptionDict(value="indefinite", label="Until I resume the schedule"),
]


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _default_name(friendly: str) -> str:
    base = friendly.strip()
    if base.lower().endswith(" thermostat"):
        base = base[: -len(" thermostat")]
    return f"{base} Comfort" if base else "Comfort Schedule"


def _degrees(unit: str, minimum: float, maximum: float, step: float) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX, unit_of_measurement=unit
        )
    )


def _minutes(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=5, mode=NumberSelectorMode.BOX, unit_of_measurement="min"
        )
    )


class ComfortScheduleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pick the thermostat; the schedule starts as one period a day at its current set points."""

    VERSION = 1

    def __init__(self) -> None:
        self._input: dict[str, Any] = {}
        self._limits: tuple[float, float] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ComfortScheduleOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        unit = self.hass.config.units.temperature_unit
        if user_input is not None:
            source = user_input[CONF_CLIMATE]
            entry = er.async_get(self.hass).async_get(source)
            state = self.hass.states.get(source)
            if entry is not None and entry.platform == DOMAIN:
                errors[CONF_CLIMATE] = "own_entity"
            elif state is None:
                errors[CONF_CLIMATE] = "not_found"
            else:
                await self.async_set_unique_id(source)
                self._abort_if_unique_id_configured()
                self._input = user_input
                low, high, *_ = unit_defaults(unit)
                self._limits = (
                    _float(state.attributes.get(ATTR_MIN_TEMP), low),
                    _float(state.attributes.get(ATTR_MAX_TEMP), high),
                )
                if not user_input.get(CONF_NAME):
                    self._input[CONF_NAME] = _default_name(
                        str(state.attributes.get(ATTR_FRIENDLY_NAME) or source)
                    )
                return self._create()

        schema = vol.Schema(
            {
                vol.Required(CONF_CLIMATE): EntitySelector(EntitySelectorConfig(domain="climate")),
                vol.Optional(CONF_NAME): TextSelector(),
                vol.Optional(CONF_NOTIFY): EntitySelector(EntitySelectorConfig(domain="notify")),
                vol.Required(CONF_DEADBAND, default=unit_defaults(unit)[4]): _degrees(unit, 0.5, 10, 0.5),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    def _create(self) -> ConfigFlowResult:
        source = self._input[CONF_CLIMATE]
        deadband = float(self._input[CONF_DEADBAND])
        assert self._limits is not None
        state = self.hass.states.get(source)
        attrs = state.attributes if state else {}
        _, _, default_heat, default_cool, _ = unit_defaults(self.hass.config.units.temperature_unit)
        heat, cool = guard(
            _float(attrs.get(ATTR_TARGET_TEMP_LOW), default_heat),
            _float(attrs.get(ATTR_TARGET_TEMP_HIGH), default_cool),
            None,
            deadband,
            *self._limits,
        )
        week = default_week(heat, cool)
        options: dict[str, Any] = {CONF_DEADBAND: deadband}
        if self._input.get(CONF_NOTIFY):
            options[CONF_NOTIFY] = self._input[CONF_NOTIFY]
        return self.async_create_entry(
            title=self._input[CONF_NAME],
            data={CONF_CLIMATE: source, CONF_INITIAL_SCHEDULE: week_to_json(week)},
            options=options,
        )


class ComfortScheduleOptionsFlow(OptionsFlow):
    """Deadband, notifications, hold length and health thresholds."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        opts = self.config_entry.options
        unit = self.hass.config.units.temperature_unit
        schema = vol.Schema(
            {
                vol.Optional(CONF_NOTIFY): EntitySelector(EntitySelectorConfig(domain="notify")),
                vol.Required(
                    CONF_DEADBAND, default=opts.get(CONF_DEADBAND, DEFAULT_DEADBAND)
                ): _degrees(unit, 1, 10, 0.5),
                vol.Required(
                    CONF_HOLD_DEFAULT, default=opts.get(CONF_HOLD_DEFAULT, DEFAULT_HOLD)
                ): SelectSelector(
                    SelectSelectorConfig(options=HOLD_OPTIONS, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(
                    CONF_HEAT_MINUTES, default=opts.get(CONF_HEAT_MINUTES, DEFAULT_HEAT_MINUTES)
                ): _minutes(15, 240),
                vol.Required(
                    CONF_COOL_MINUTES, default=opts.get(CONF_COOL_MINUTES, DEFAULT_COOL_MINUTES)
                ): _minutes(15, 240),
                vol.Required(
                    CONF_MIN_PROGRESS, default=opts.get(CONF_MIN_PROGRESS, DEFAULT_MIN_PROGRESS)
                ): _degrees(unit, 0.2, 5, 0.1),
                vol.Required(
                    CONF_WRONG_WAY, default=opts.get(CONF_WRONG_WAY, DEFAULT_WRONG_WAY)
                ): _degrees(unit, 0.2, 5, 0.1),
                vol.Required(
                    CONF_CHANGEOVER_MARGIN,
                    default=opts.get(CONF_CHANGEOVER_MARGIN, DEFAULT_CHANGEOVER_MARGIN),
                ): _degrees(unit, 0, 5, 0.5),
                vol.Required(
                    CONF_CHANGEOVER_MINUTES,
                    default=opts.get(CONF_CHANGEOVER_MINUTES, DEFAULT_CHANGEOVER_MINUTES),
                ): _minutes(5, 120),
                vol.Required(
                    CONF_FORCE_EMULATION, default=opts.get(CONF_FORCE_EMULATION, False)
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, {CONF_NOTIFY: opts.get(CONF_NOTIFY)} if opts.get(CONF_NOTIFY) else {}
            ),
        )
