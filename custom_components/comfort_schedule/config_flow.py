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
from homeassistant.const import ATTR_FRIENDLY_NAME, CONF_NAME, UnitOfTemperature
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
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
    CONF_AWAY_COOL,
    CONF_AWAY_DELAY,
    CONF_AWAY_HEAT,
    CONF_CHANGEOVER_MARGIN,
    CONF_CHANGEOVER_MINUTES,
    CONF_CLIMATE,
    CONF_COOL_COST,
    CONF_COOL_MINUTES,
    CONF_DEADBAND,
    CONF_COLD_SNAP_BELOW,
    CONF_COLD_SNAP_SETBACK,
    CONF_EARLY_START,
    CONF_FILTER_HOURS,
    CONF_DEGREE_DAY_BASE,
    CONF_FOLLOWER_OFFSETS,
    CONF_FOLLOWERS,
    CONF_SIMILAR_WEATHER,
    CONF_FORCE_EMULATION,
    CONF_HEAT_COST,
    CONF_HEAT_MINUTES,
    CONF_HOLD_DEFAULT,
    CONF_INITIAL_SCHEDULE,
    CONF_MIN_PROGRESS,
    CONF_NOTIFY,
    CONF_OUTDOOR,
    CONF_PRESENCE,
    CONF_SUGGESTIONS,
    CONF_TREND_DROP,
    CONF_WRONG_WAY,
    DEFAULT_AWAY_DELAY,
    DEFAULT_CHANGEOVER_MARGIN,
    DEFAULT_CHANGEOVER_MINUTES,
    DEFAULT_COOL_MINUTES,
    DEFAULT_COLD_SNAP_SETBACK,
    DEFAULT_EARLY_START,
    DEFAULT_FILTER_HOURS,
    DEFAULT_HEAT_MINUTES,
    DEFAULT_HOLD,
    DEFAULT_MIN_PROGRESS,
    DEFAULT_TREND_DROP,
    DEFAULT_WRONG_WAY,
    DOMAIN,
    DEGREE_DAY_BASE,
    SIMILAR_WEATHER,
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


# Options are grouped into sections on the form but saved flat, so older entries keep working.
SECTIONS = ("health", "heat_cool", "presence", "energy", "learning", "thermostats")


def _number(minimum: float, maximum: float, step: float, unit: str | None = None) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX, unit_of_measurement=unit)
    )


def flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    """The form's sections as one flat dict of options."""
    out: dict[str, Any] = {}
    for key, value in user_input.items():
        if key in SECTIONS and isinstance(value, dict):
            out.update(value)
        else:
            out[key] = value
    return out


class ComfortScheduleOptionsFlow(OptionsFlow):
    """Deadband, notifications, holds, health, Heat/Cool, presence, energy, learning, more thermostats."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = flatten(user_input)
            if data.get(CONF_PRESENCE) and (data.get(CONF_AWAY_HEAT) is None or data.get(CONF_AWAY_COOL) is None):
                errors["base"] = "away_setpoints"
            elif data.get(CONF_FOLLOWERS):
                self._pending = data
                return await self.async_step_offsets()
            else:
                data.pop(CONF_FOLLOWER_OFFSETS, None)
                return self.async_create_entry(data=data)
        opts = flatten(user_input) if user_input is not None else dict(self.config_entry.options)
        unit = self.hass.config.units.temperature_unit
        low, high, *_ = unit_defaults(unit)
        celsius = unit == UnitOfTemperature.CELSIUS
        source = self.config_entry.data.get(CONF_CLIMATE)

        def req(key: str, default: Any) -> vol.Required:
            return vol.Required(key, default=opts.get(key, default))

        def opt(key: str) -> vol.Optional:
            # Suggested, not default: a cleared field stays cleared.
            return vol.Optional(key, description={"suggested_value": opts.get(key)})

        def group(fields: dict[Any, Any]) -> section:
            return section(vol.Schema(fields), {"collapsed": True})

        schema = vol.Schema(
            {
                opt(CONF_NOTIFY): EntitySelector(EntitySelectorConfig(domain="notify")),
                req(CONF_DEADBAND, unit_defaults(unit)[4]): _degrees(unit, 0.5, 10, 0.5),
                req(CONF_HOLD_DEFAULT, DEFAULT_HOLD): SelectSelector(
                    SelectSelectorConfig(options=HOLD_OPTIONS, mode=SelectSelectorMode.DROPDOWN)
                ),
                vol.Required("health"): group(
                    {
                        req(CONF_HEAT_MINUTES, DEFAULT_HEAT_MINUTES): _minutes(15, 240),
                        req(CONF_COOL_MINUTES, DEFAULT_COOL_MINUTES): _minutes(15, 240),
                        req(CONF_MIN_PROGRESS, DEFAULT_MIN_PROGRESS.get(unit, 1.0)): _degrees(unit, 0.2, 5, 0.1),
                        req(CONF_WRONG_WAY, DEFAULT_WRONG_WAY.get(unit, 0.8)): _degrees(unit, 0.2, 5, 0.1),
                        req(CONF_TREND_DROP, DEFAULT_TREND_DROP): _number(0, 90, 5, "%"),
                    }
                ),
                vol.Required("heat_cool"): group(
                    {
                        req(CONF_CHANGEOVER_MARGIN, DEFAULT_CHANGEOVER_MARGIN.get(unit, 1.0)): _degrees(unit, 0, 5, 0.5),
                        req(CONF_CHANGEOVER_MINUTES, DEFAULT_CHANGEOVER_MINUTES): _minutes(5, 120),
                        req(CONF_FORCE_EMULATION, False): BooleanSelector(),
                    }
                ),
                vol.Required("presence"): group(
                    {
                        opt(CONF_PRESENCE): EntitySelector(
                            EntitySelectorConfig(
                                domain=["person", "device_tracker", "zone", "group", "binary_sensor", "input_boolean"],
                                multiple=True,
                            )
                        ),
                        opt(CONF_AWAY_HEAT): _degrees(unit, low, high, 0.5),
                        opt(CONF_AWAY_COOL): _degrees(unit, low, high, 0.5),
                        req(CONF_AWAY_DELAY, DEFAULT_AWAY_DELAY): _number(0, 240, 5, "min"),
                    }
                ),
                vol.Required("energy"): group(
                    {
                        opt(CONF_OUTDOOR): EntitySelector(EntitySelectorConfig(domain=["sensor", "weather"])),
                        req(CONF_SIMILAR_WEATHER, SIMILAR_WEATHER[unit]): _degrees(unit, 1, 15, 0.5) if celsius else _degrees(unit, 2, 30, 1),
                        req(CONF_DEGREE_DAY_BASE, DEGREE_DAY_BASE[unit]): _degrees(unit, 5, 25, 0.5) if celsius else _degrees(unit, 40, 80, 1),
                        req(CONF_FILTER_HOURS, DEFAULT_FILTER_HOURS): _number(0, 2000, 10, "h"),
                        opt(CONF_HEAT_COST): _number(0, 100, 0.01, self.hass.config.currency),
                        opt(CONF_COOL_COST): _number(0, 100, 0.01, self.hass.config.currency),
                    }
                ),
                vol.Required("learning"): group(
                    {
                        req(CONF_EARLY_START, DEFAULT_EARLY_START): _number(0, 180, 15, "min"),
                        opt(CONF_COLD_SNAP_BELOW): _degrees(unit, -40, 50, 1),
                        req(CONF_COLD_SNAP_SETBACK, DEFAULT_COLD_SNAP_SETBACK[unit]): _degrees(unit, 0, 15, 0.5),
                        req(CONF_SUGGESTIONS, True): BooleanSelector(),
                    }
                ),
                vol.Required("thermostats"): group(
                    {
                        opt(CONF_FOLLOWERS): EntitySelector(
                            EntitySelectorConfig(
                                domain="climate",
                                multiple=True,
                                # Not Comfort thermostats: their changes would come back as holds.
                                exclude_entities=([source] if source else []) + self._comfort_entities(),
                            )
                        ),
                    }
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    def _comfort_entities(self) -> list[str]:
        """Every Comfort Schedule thermostat (this one included)."""
        registry = er.async_get(self.hass)
        return [e.entity_id for e in registry.entities.values() if e.platform == DOMAIN and e.domain == "climate"]

    async def async_step_offsets(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """An offset per other thermostat, e.g. -1° for an upstairs that runs warm."""
        data = self._pending
        followers = list(data.get(CONF_FOLLOWERS) or [])
        if user_input is not None:
            data[CONF_FOLLOWER_OFFSETS] = {e: float(user_input.get(e, 0)) for e in followers if user_input.get(e)}
            return self.async_create_entry(data=data)
        saved = self.config_entry.options.get(CONF_FOLLOWER_OFFSETS) or {}
        unit = self.hass.config.units.temperature_unit
        span = 5 if unit == UnitOfTemperature.CELSIUS else 10
        names = {e: (st.name if (st := self.hass.states.get(e)) else e) for e in followers}
        schema = vol.Schema(
            {vol.Required(e, default=float(saved.get(e, 0))): _degrees(unit, -span, span, 0.5) for e in followers}
        )
        return self.async_show_form(
            step_id="offsets",
            data_schema=schema,
            description_placeholders={"thermostats": ", ".join(f"{names[e]} ({e})" for e in followers)},
        )
