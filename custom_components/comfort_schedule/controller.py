"""The brain: decides what the thermostat should be set to and keeps it there."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math
import re
from typing import Any

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_FAN_MODE,
    ATTR_FAN_MODES,
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ATTR_TARGET_TEMP_STEP,
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_SET_FAN_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AWAY_COOL,
    CONF_AWAY_DELAY,
    CONF_AWAY_HEAT,
    CONF_CHANGEOVER_MARGIN,
    CONF_CHANGEOVER_MINUTES,
    CONF_CLIMATE,
    CONF_COLD_SNAP_BELOW,
    CONF_COLD_SNAP_SETBACK,
    CONF_COOL_COST,
    CONF_COOLING_ENABLED,
    CONF_EARLY_START,
    CONF_FILTER_HOURS,
    CONF_DEGREE_DAY_BASE,
    CONF_FOLLOWER_OFFSETS,
    CONF_FOLLOWERS,
    CONF_SIMILAR_WEATHER,
    CONF_FORCE_EMULATION,
    CONF_COOL_MINUTES,
    CONF_DEADBAND,
    CONF_HEAT_COST,
    CONF_HEAT_MINUTES,
    CONF_HOLD_DEFAULT,
    CONF_INITIAL_SCHEDULE,
    CONF_MIN_PROGRESS,
    CONF_NOTIFY,
    CONF_OFF_FALLBACK,
    CONF_OUTDOOR,
    CONF_PRESENCE,
    CONF_SUGGESTIONS,
    CONF_TREND_DROP,
    CONF_WRONG_WAY,
    DEFAULT_AWAY_DELAY,
    DEFAULT_CHANGEOVER_MARGIN,
    DEFAULT_CHANGEOVER_MINUTES,
    DEFAULT_COLD_SNAP_SETBACK,
    DEFAULT_COOL_MINUTES,
    DEFAULT_EARLY_START,
    DEFAULT_FILTER_HOURS,
    DEFAULT_HEAT_MINUTES,
    DEFAULT_HOLD,
    DEFAULT_MIN_PROGRESS,
    DEFAULT_TREND_DROP,
    DEFAULT_WRONG_WAY,
    DEGREE_DAY_BASE,
    SIMILAR_WEATHER,
    DOMAIN,
    HOLD_INDEFINITE,
    HOLD_NEXT_PERIOD,
    MAX_REPEAT_WRITES,
    RESYNC_INTERVAL,
    STORAGE_VERSION,
    WRITE_COOLDOWN,
    WRITE_GRACE,
)
from . import forecast as fc
from .health import HealthMonitor
from .learning import Learning
from .runtime import RuntimeTracker
from .schedule import (
    Period,
    Week,
    active_period,
    default_week,
    guard,
    next_change,
    normalize_week,
    period_end,
    unit_defaults,
    week_from_json,
    week_to_json,
)
from .suggestions import Suggestions

_LOGGER = logging.getLogger(__name__)

UNAVAILABLE = (STATE_UNAVAILABLE, STATE_UNKNOWN)
HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


def _check_numbers(heat: Any, cool: Any) -> tuple[float, float]:
    """Heat and cool as floats; raises ValueError for NaN or infinity (TypeError for None)."""
    heat, cool = float(heat), float(cool)
    if not (math.isfinite(heat) and math.isfinite(cool)):
        raise ValueError("Set points must be numbers")
    return heat, cool


def _rounded(reported: Any, sent: float, state: State) -> bool:
    """True if `reported` is `sent` rounded to the thermostat's step (less than one step off)."""
    step = _as_float(state.attributes.get(ATTR_TARGET_TEMP_STEP)) or 1.0
    try:
        return abs(float(reported) - sent) < step - 0.01
    except (TypeError, ValueError):
        return False


def _same(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.05
    except (TypeError, ValueError):
        return False


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _aware(value: datetime) -> datetime:
    """UTC; a time without a zone is taken as local time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(value)


@dataclass
class Hold:
    """A manual override of the schedule."""

    heat: float
    cool: float
    until: datetime | None  # None = until resumed
    started: datetime | None = None  # when this hold (or its current length) began

    def as_dict(self) -> dict[str, Any]:
        return {
            "heat": self.heat,
            "cool": self.cool,
            "until": self.until.isoformat() if self.until else None,
            "started": self.started.isoformat() if self.started else None,
        }


class ComfortController:
    """Owns the schedule, holds and writes to the real thermostat."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.source: str = entry.data[CONF_CLIMATE]
        self.climate_entity_id: str | None = None  # set by our climate entity
        self.week: Week = []
        self.schedule_enabled = True
        self.hold: Hold | None = None
        self.last_active_mode: str | None = None
        # A thermostat without HVAC Off can emulate it by staying in Heat at a low target.
        self.virtual_off = False
        # True while we provide Heat/Cool ourselves by switching the thermostat
        # between Heat and Cool (for thermostats without a native Heat/Cool mode).
        self.emulating = False
        self.health = HealthMonitor(self)
        self.runtime = RuntimeTracker()
        self.battery_entity: str | None = None
        # Colors chosen for schedule labels ({"Away": "#8C98A5"}); unset labels use the defaults.
        self.label_colors: dict[str, str] = {}
        self.humidity_entity: str | None = None
        # Vacation: {"start", "end" (datetimes), "heat", "cool"}; it applies between start and end.
        self.vacation: dict[str, Any] | None = None
        # Presence: True once everyone has been away for the away delay.
        self.away = False
        # The Away mode switch: off keeps the schedule whoever is home (guests, on the way back).
        self.away_enabled = True
        self._left_at: datetime | None = None
        self._was_away = False  # from storage: where presence was when we last stopped
        self._saved_left_at: datetime | None = None
        # Filter: runtime counts from this local date (ISO), less what had run that day already.
        self.filter_since: str | None = None
        self.filter_offset = 0.0
        self.filter_notified = False
        self.learning = Learning(hass.config.units.temperature_unit, self.similar_weather)
        self.suggestions = Suggestions()
        # Slowdown warnings currently raised, by kind ("heat"/"cool").
        self.trend: dict[str, dict[str, Any]] = {}
        # Early start: the block being started early, and the change time it's for.
        self._early: Period | None = None
        self._early_for: datetime | None = None
        # The weather entity's hourly forecast [(time, temperature)], and whether the cold snap guard is on.
        self._forecast: list[tuple[datetime, float]] = []
        self._cold_snap = False
        # Recent writes, for diagnostics.
        self.write_log: deque[dict[str, Any]] = deque(maxlen=25)
        # Followers: what we last sent each one, and how many times in a row.
        self._follower_sent: dict[str, tuple[dict[str, Any], int]] = {}
        self._away_unsub: CALLBACK_TYPE | None = None
        # One delayed save at a time: asking again would keep pushing the write back.
        self._runtime_pending = False
        self._learn_pending = False

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        # Runtime totals change every minute while the HVAC runs, so they live in their
        # own file with a slow save; a pending save is still flushed when HA stops.
        self._runtime_store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.runtime"
        )
        # Run samples and the manual change log grow slowly; they get their own slow-saved file.
        self._learn_store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.learning"
        )
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._timer_unsub: CALLBACK_TYPE | None = None
        self._expected: dict[str, float] = {}
        self._before_write: dict[str, Any] = {}
        self._last_write: datetime | None = None
        self._repeats = 0
        self._manual: tuple[float, float] | None = None
        self._mode_target: str | None = None
        self._mode_write: datetime | None = None
        # One correction per continuous Cool/Heat-Cool episode in heat-only mode.
        # Some devices ignore mode writes; do not retry on each attribute update.
        self._heat_only_correction_attempted = False
        self._last_changeover: datetime | None = None
        self._mode_seen: datetime | None = None
        self._period_key: tuple[int, int] | None = None  # (weekday, index) of the running block
        self._writer = Debouncer(
            hass,
            _LOGGER,
            cooldown=WRITE_COOLDOWN,
            immediate=False,
            function=self._async_write,
        )

    # ------------------------------------------------------------------ settings

    def _opt(self, key: str, default: Any) -> Any:
        return self.entry.options.get(key, default)

    def _unit(self) -> str:
        return self.hass.config.units.temperature_unit

    @property
    def similar_weather(self) -> float:
        unit = self.hass.config.units.temperature_unit
        return float(self._opt(CONF_SIMILAR_WEATHER, None) or SIMILAR_WEATHER.get(unit, 10.0))

    @property
    def degree_day_base(self) -> float:
        unit = self.hass.config.units.temperature_unit
        value = self._opt(CONF_DEGREE_DAY_BASE, None)
        return float(value) if value is not None else DEGREE_DAY_BASE.get(unit, 65.0)

    def follower_offset(self, entity_id: str) -> float:
        """Degrees added to both set points for another thermostat (e.g. -1 for a warmer upstairs)."""
        offsets = self._opt(CONF_FOLLOWER_OFFSETS, None) or {}
        return float(_as_float(offsets.get(entity_id)) or 0.0) if isinstance(offsets, dict) else 0.0

    @property
    def deadband(self) -> float:
        if not self.cooling_enabled:
            return 0.0
        return float(self._opt(CONF_DEADBAND, unit_defaults(self._unit())[4]))

    @property
    def cooling_enabled(self) -> bool:
        """Cooling was enabled for this config entry (missing means legacy/dual mode)."""
        return bool(self._opt(CONF_COOLING_ENABLED, True))

    @property
    def off_fallback_temperature(self) -> float | None:
        value = self._opt_float(CONF_OFF_FALLBACK)
        if value is None or not math.isfinite(value):
            return None
        low, high = self.limits()
        return max(low, min(high, value))

    def native_off(self, entity_id: str | None = None) -> bool:
        state = self.hass.states.get(entity_id or self.source)
        return bool(state and HVACMode.OFF in (state.attributes.get(ATTR_HVAC_MODES) or []))

    @property
    def off_available(self) -> bool:
        return self.native_off() or self.off_fallback_temperature is not None

    @property
    def hold_default(self) -> str:
        return str(self._opt(CONF_HOLD_DEFAULT, DEFAULT_HOLD))

    @property
    def heat_minutes(self) -> int:
        return int(self._opt(CONF_HEAT_MINUTES, DEFAULT_HEAT_MINUTES))

    @property
    def cool_minutes(self) -> int:
        return int(self._opt(CONF_COOL_MINUTES, DEFAULT_COOL_MINUTES))

    @property
    def min_progress(self) -> float:
        return float(self._opt(CONF_MIN_PROGRESS, DEFAULT_MIN_PROGRESS.get(self._unit(), 1.0)))

    @property
    def wrong_way(self) -> float:
        return float(self._opt(CONF_WRONG_WAY, DEFAULT_WRONG_WAY.get(self._unit(), 0.8)))

    @property
    def notify_entity(self) -> str | None:
        return self._opt(CONF_NOTIFY, None) or None

    @property
    def changeover_margin(self) -> float:
        return float(self._opt(CONF_CHANGEOVER_MARGIN, DEFAULT_CHANGEOVER_MARGIN.get(self._unit(), 1.0)))

    @property
    def changeover_minutes(self) -> int:
        return int(self._opt(CONF_CHANGEOVER_MINUTES, DEFAULT_CHANGEOVER_MINUTES))

    @property
    def outdoor_entity(self) -> str | None:
        return self._opt(CONF_OUTDOOR, None) or None

    @property
    def presence_entities(self) -> list[str]:
        return list(self._opt(CONF_PRESENCE, None) or [])

    def _opt_float(self, key: str) -> float | None:
        return _as_float(self._opt(key, None))

    @property
    def away_setpoints(self) -> tuple[float, float] | None:
        """Away set points, or None when presence isn't set up."""
        heat, cool = self._opt_float(CONF_AWAY_HEAT), self._opt_float(CONF_AWAY_COOL)
        if not self.presence_entities or heat is None or (self.cooling_enabled and cool is None):
            return None
        low, high = self.limits()
        return guard(heat, cool if cool is not None else high, None, self.deadband, low, high)

    @property
    def away_delay(self) -> int:
        return int(self._opt(CONF_AWAY_DELAY, DEFAULT_AWAY_DELAY))

    @property
    def filter_hours(self) -> float:
        return float(self._opt(CONF_FILTER_HOURS, DEFAULT_FILTER_HOURS))

    @property
    def costs(self) -> dict[str, Any] | None:
        heat, cool = self._opt_float(CONF_HEAT_COST), self._opt_float(CONF_COOL_COST)
        if not self.cooling_enabled:
            return {"heat": heat, "currency": self.hass.config.currency} if heat else None
        if not heat and not cool:
            return None
        return {"heat": heat or 0.0, "cool": cool or 0.0, "currency": self.hass.config.currency}

    @property
    def followers(self) -> list[str]:
        registry = er.async_get(self.hass)
        # Never a Comfort thermostat (ours or another): its changes would come back as holds.
        return [
            e for e in self._opt(CONF_FOLLOWERS, None) or []
            if e != self.source and not ((reg := registry.async_get(e)) and reg.platform == DOMAIN)
        ]

    @property
    def cold_snap_below(self) -> float | None:
        return self._opt_float(CONF_COLD_SNAP_BELOW)

    @property
    def cold_snap_setback(self) -> float:
        unit = self.hass.config.units.temperature_unit
        return float(self._opt(CONF_COLD_SNAP_SETBACK, DEFAULT_COLD_SNAP_SETBACK.get(unit, 3.0)))

    @property
    def early_start_max(self) -> int:
        return int(self._opt(CONF_EARLY_START, DEFAULT_EARLY_START))

    @property
    def trend_drop(self) -> float:
        return float(self._opt(CONF_TREND_DROP, DEFAULT_TREND_DROP))

    @property
    def suggestions_enabled(self) -> bool:
        return bool(self._opt(CONF_SUGGESTIONS, True))

    def source_state(self) -> State | None:
        return self.hass.states.get(self.source)

    def outdoor_temp(self) -> float | None:
        """Outdoor temperature from a sensor, or a weather entity's temperature."""
        if not self.outdoor_entity or (state := self.hass.states.get(self.outdoor_entity)) is None:
            return None
        if self.outdoor_entity.startswith("weather."):
            value, unit = _as_float(state.attributes.get("temperature")), state.attributes.get("temperature_unit")
        else:
            value, unit = _as_float(state.state), state.attributes.get("unit_of_measurement")
        want = self.hass.config.units.temperature_unit
        # A sensor shown in the other unit (or a weather entity set to it) still has to compare with ours.
        if value is not None and unit != want and {unit, want} == {"°C", "°F"}:
            value = round(value * 9 / 5 + 32, 1) if want == "°F" else round((value - 32) * 5 / 9, 1)
        return value

    def _find_device_sensor(self, device_class: SensorDeviceClass) -> str | None:
        """A sensor of this class on the same device as the thermostat, if there is one."""
        registry = er.async_get(self.hass)
        source = registry.async_get(self.source)
        if source is None or source.device_id is None:
            return None
        for entry in er.async_entries_for_device(registry, source.device_id):
            if (
                entry.domain == "sensor"
                and (entry.device_class or entry.original_device_class) == device_class
                and entry.disabled_by is None
            ):
                return entry.entity_id
        return None

    @callback
    def _battery_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.async_notify_listeners()

    def battery_level(self) -> float | None:
        state = self.hass.states.get(self.battery_entity) if self.battery_entity else None
        return _as_float(state.state) if state is not None else None

    def _source_modes(self) -> list[str]:
        state = self.source_state()
        return list(state.attributes.get(ATTR_HVAC_MODES) or []) if state else []

    def uses_native_heat_cool(self) -> bool:
        """The thermostat's own Heat/Cool mode is used (unless the options force ours)."""
        return self.cooling_enabled and HVACMode.HEAT_COOL in self._source_modes() and not self._opt(CONF_FORCE_EMULATION, False)

    def can_emulate(self) -> bool:
        if not self.cooling_enabled:
            return False
        modes = self._source_modes()
        return HVACMode.HEAT in modes and HVACMode.COOL in modes

    def proxy_mode(self) -> str | None:
        """The mode to show: Heat/Cool while we're switching between Heat and Cool ourselves."""
        if self.virtual_off:
            return HVACMode.OFF
        state = self.source_state()
        if state is None:
            return None
        if self.emulating and state.state in (HVACMode.HEAT, HVACMode.COOL):
            return HVACMode.HEAT_COOL
        return state.state

    def limits(self) -> tuple[float, float]:
        state = self.source_state()
        low = _as_float(state.attributes.get(ATTR_MIN_TEMP)) if state else None
        high = _as_float(state.attributes.get(ATTR_MAX_TEMP)) if state else None
        fallback_low, fallback_high, *_ = unit_defaults(self.hass.config.units.temperature_unit)
        return (low if low is not None else fallback_low, high if high is not None else fallback_high)

    # ------------------------------------------------------------------ lifecycle

    async def async_load(self) -> None:
        """Load the saved schedule/hold, or seed it from the config entry."""
        self.runtime.load(await self._runtime_store.async_load())
        learned = await self._learn_store.async_load() or {}
        self.learning.load(learned.get("samples"))
        self.suggestions.load(learned.get("suggestions"))
        data = await self._store.async_load() or {}
        raw_week = data.get("schedule") or self.entry.data.get(CONF_INITIAL_SCHEDULE)
        low, high = self.limits()
        week: Week | None = None
        if raw_week:
            try:
                # Parse with a zero deadband, then normalize to the current one so a
                # widened deadband in the options doesn't throw the schedule away.
                week = week_from_json(raw_week, 0)
            except ValueError as err:
                _LOGGER.error("Saved schedule for %s is invalid (%s); starting fresh", self.source, err)
        if week is None:
            _, _, heat, cool, _ = unit_defaults(self.hass.config.units.temperature_unit)
            heat, cool = guard(heat, cool, None, self.deadband, low, high)
            week = default_week(heat, cool)
        self.week, changed = normalize_week(week, self.deadband, low, high)

        self.schedule_enabled = bool(data.get("schedule_enabled", True))
        self.last_active_mode = data.get("last_active_mode")
        self.emulating = bool(data.get("emulate_heat_cool", False))
        self.virtual_off = bool(data.get("virtual_off", False))
        if self.virtual_off and (self.native_off() or self.off_fallback_temperature is None):
            # Native Off does not need emulation; a removed fallback cannot sustain it.
            self.virtual_off = False
        colors = data.get("label_colors") or {}
        self.label_colors = {
            str(k): v for k, v in colors.items() if isinstance(v, str) and HEX_COLOR.fullmatch(v)
        } if isinstance(colors, dict) else {}
        if (hold := data.get("hold")) and isinstance(hold, dict):
            try:
                until = dt_util.parse_datetime(hold["until"]) if hold.get("until") else None
                if until is None or until > dt_util.utcnow():
                    started = dt_util.parse_datetime(hold["started"]) if hold.get("started") else None
                    heat, cool = _check_numbers(hold["heat"], hold["cool"])
                    # Through the guard: the deadband or limits may have changed since.
                    heat, cool = guard(heat, cool, None, self.deadband, low, high)
                    self.hold = Hold(heat, cool, until, started)
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.error("Saved hold for %s is invalid (%s); dropping it", self.source, err)
                changed = True
        if (vac := data.get("vacation")) and isinstance(vac, dict):
            start, end = dt_util.parse_datetime(vac.get("start") or ""), dt_util.parse_datetime(vac.get("end") or "")
            try:
                heat, cool = _check_numbers(vac.get("heat"), vac.get("cool"))
                heat, cool = guard(heat, cool, None, self.deadband, low, high)
            except (TypeError, ValueError) as err:
                _LOGGER.error("Saved vacation for %s is invalid (%s); dropping it", self.source, err)
                start = end = None
                changed = True
            if start and end and end > dt_util.utcnow():
                self.vacation = {"start": start, "end": end, "heat": heat, "cool": cool}
                before = vac.get("before")
                if isinstance(before, list) and len(before) == 2:
                    try:
                        self.vacation["before"] = _check_numbers(*before)
                    except (TypeError, ValueError):
                        pass
                if start <= dt_util.utcnow() and "before" not in self.vacation:
                    self.vacation["before"] = self._plan_without_vacation()
                # Started while Home Assistant was down: a hold from before the trip ends, as it would have.
                if self.hold and start <= dt_util.utcnow() and (self.hold.started is None or self.hold.started < start):
                    self.hold = None
                    changed = True
        self.filter_since = data.get("filter_since")
        self.filter_offset = float(data.get("filter_offset") or 0)
        self.filter_notified = bool(data.get("filter_notified", False))
        self.trend = {k: v for k, v in (data.get("trend") or {}).items() if isinstance(v, dict)}
        self._was_away = bool(data.get("away", False))
        self.away_enabled = bool(data.get("away_enabled", True))
        self._saved_left_at = dt_util.parse_datetime(data["left_at"]) if data.get("left_at") else None

        if changed or not data.get("schedule"):
            self._save()
        if CONF_INITIAL_SCHEDULE in self.entry.data:
            # The seed now lives in storage; keep the config entry small.
            new_data = {k: v for k, v in self.entry.data.items() if k != CONF_INITIAL_SCHEDULE}
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    @callback
    def async_start(self) -> None:
        self._unsubs.append(
            async_track_state_change_event(self.hass, [self.source], self._source_changed)
        )
        # The thermostat's battery sensor (same device) is shown on the card; redraw when it changes.
        self.battery_entity = self._find_device_sensor(SensorDeviceClass.BATTERY)
        self.humidity_entity = self._find_device_sensor(SensorDeviceClass.HUMIDITY)
        if self.battery_entity:
            self._unsubs.append(
                async_track_state_change_event(self.hass, [self.battery_entity], self._battery_changed)
            )
        self._unsubs.append(
            async_track_time_interval(self.hass, self._resync, RESYNC_INTERVAL)
        )
        self._unsubs.append(
            async_track_time_interval(self.hass, self.async_refresh_forecast, fc.REFRESH)
        )
        self.hass.async_create_task(self.async_refresh_forecast(), f"{DOMAIN} forecast")
        if self.outdoor_entity and self.outdoor_entity.startswith("weather."):
            # At boot the weather entity often loads after us: fetch as soon as it's there.
            self._unsubs.append(
                async_track_state_change_event(self.hass, [self.outdoor_entity], self._weather_changed)
            )
        self._unsubs.append(
            async_track_time_interval(self.hass, self._changeover_tick, timedelta(minutes=1))
        )
        # "Today" runtime sensors roll over at midnight even when nothing is running.
        self._unsubs.append(
            async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=1)
        )
        if self.presence_entities:
            self._unsubs.append(
                async_track_state_change_event(self.hass, self.presence_entities, self._presence_changed)
            )
        self._update_presence(starting=True)
        self._check_trend()
        self._check_filter()
        state = self.source_state()
        self.runtime.update(state, self.cooling_enabled)
        if state is not None and state.state not in UNAVAILABLE:
            if not self.cooling_enabled and self.emulating:
                self.emulating = False
                self._save()
            if self.emulating and state.state not in (HVACMode.HEAT, HVACMode.COOL):
                # Mode was changed (e.g. at the wall) while we weren't running.
                self.emulating = False
                self._save()
            elif self.emulating and self.uses_native_heat_cool():
                # The "switch it myself" option was turned off: hand back to the thermostat.
                self.emulating = False
                self._save()
                self.hass.async_create_task(
                    self._async_set_source_mode(HVACMode.HEAT_COOL), f"{DOMAIN} native heat_cool"
                )
            if state.state != HVACMode.OFF and not self.emulating:
                self.last_active_mode = state.state
        self.health.async_start(state)
        # Remember which block is running; its mode/fan were applied when it started (or
        # someone has changed them since), so a restart mustn't re-apply them.
        self._period_key = self.active()[:2]
        self._reschedule()
        self._request_write()

    @callback
    def async_stop(self) -> None:
        # Keep the minutes of a run that's in progress across a reload.
        self.runtime.accrue()
        self._runtime_store.async_delay_save(self._runtime_data, 0)
        self._learn_store.async_delay_save(self._learn_data, 0)
        self._store.async_delay_save(self._data_to_save, 0)
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._timer_unsub:
            self._timer_unsub()
            self._timer_unsub = None
        if self._away_unsub:
            self._away_unsub()
            self._away_unsub = None
        self.health.async_stop()
        self._writer.async_shutdown()

    def _save(self) -> None:
        self._store.async_delay_save(self._data_to_save, 1)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "schedule": week_to_json(self.week),
            "schedule_enabled": self.schedule_enabled,
            "hold": self.hold.as_dict() if self.hold else None,
            "last_active_mode": self.last_active_mode,
            "emulate_heat_cool": self.emulating,
            "virtual_off": self.virtual_off,
            "label_colors": self.label_colors,
            "vacation": {**vac, "before": self.vacation.get("before")} if (vac := self.vacation_dict()) else None,
            "filter_since": self.filter_since,
            "filter_offset": self.filter_offset,
            "filter_notified": self.filter_notified,
            "trend": self.trend,
            "away": self.away,
            "away_enabled": self.away_enabled,
            "left_at": self._left_at.isoformat() if self._left_at else None,
        }

    def vacation_dict(self) -> dict[str, Any] | None:
        if self.vacation is None:
            return None
        return {
            "start": self.vacation["start"].isoformat(),
            "end": self.vacation["end"].isoformat(),
            "heat": self.vacation["heat"],
            "cool": self.vacation["cool"],
        }

    @callback
    def _save_learning(self) -> None:
        if not self._learn_pending:
            self._learn_pending = True
            self._learn_store.async_delay_save(self._learn_data, 60)

    def _learn_data(self) -> dict[str, Any]:
        self._learn_pending = False
        return {"samples": self.learning.dump(), "suggestions": self.suggestions.dump()}

    def _runtime_data(self) -> dict[str, Any]:
        self._runtime_pending = False
        return self.runtime.dump()

    # ------------------------------------------------------------------ listeners

    @callback
    def add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update)

        @callback
        def remove() -> None:
            self._listeners.remove(update)

        return remove

    @callback
    def async_notify_listeners(self) -> None:
        for update in list(self._listeners):
            update()

    @callback
    def async_send_notification(self, title: str, message: str) -> None:
        if not (target := self.notify_entity):
            return
        self.hass.async_create_task(
            self.hass.services.async_call(
                "notify",
                "send_message",
                {ATTR_ENTITY_ID: target, "title": title, "message": message},
                blocking=False,
            ),
            f"{DOMAIN} notify",
        )

    # ------------------------------------------------------------------ what should be set

    def active(self) -> tuple[int, int, Period]:
        return active_period(self.week, dt_util.utcnow())

    def next_change(self) -> tuple[datetime, Period] | None:
        return next_change(self.week, dt_util.utcnow())

    def period_info(self) -> dict[str, Any]:
        day, index, period = self.active()
        return {
            "label": period.label,
            "start": period.start,
            "end": period_end(self.week, day, index),
            "heat": period.heat,
            "cool": period.cool,
            "mode": self.effective_period_mode(period.mode),
            "fan": period.fan,
        }

    def effective_period_mode(self, mode: str | None) -> str | None:
        """Translate retained dual-mode schedule values without mutating storage."""
        if self.cooling_enabled:
            return mode
        if mode == HVACMode.HEAT_COOL:
            return HVACMode.HEAT
        if mode == HVACMode.COOL:
            return HVACMode.OFF
        return mode

    def vacation_active(self) -> bool:
        return self.vacation is not None and self.vacation["start"] <= dt_util.utcnow() < self.vacation["end"]

    def desired(self) -> tuple[float, float, str] | None:
        """(heat, cool, source) the thermostat should have, or None to leave it alone.

        A hold wins, then a vacation (even with the schedule paused: it was set for those
        dates), then Away, then an early start, then the schedule.
        """
        if self.hold:
            return self.hold.heat, self.hold.cool, "hold"
        if self.vacation_active():
            assert self.vacation is not None
            return self.vacation["heat"], self.vacation["cool"], "vacation"
        if not self.schedule_enabled:
            return None
        if self.away and (away := self.away_setpoints) is not None:
            return away[0], away[1], "away"
        if self._early is not None:
            return (*self._shallow(self._early.heat, self._early.cool), "early")
        period = self.active()[2]
        return (*self._shallow(period.heat, period.cool), "schedule")

    def _shallow(self, heat: float, cool: float) -> tuple[float, float]:
        """In a cold snap, heat no more than cold_snap_setback under today's and tomorrow's warmest."""
        if not self._cold_snap or not self.week:
            return heat, cool
        today = dt_util.now().weekday()
        warmest = max(p.heat for d in (today, (today + 1) % 7) for p in self.week[d])
        new = fc.shallow_heat(heat, warmest, self.cold_snap_setback)
        if new == heat:
            return heat, cool
        low, high = self.limits()
        return guard(new, cool, "heat", self.deadband, low, high)

    def cold_snap_info(self) -> dict[str, Any] | None:
        """The guard's state for the card: None when it isn't set up."""
        below = self.cold_snap_below
        if below is None:
            return None
        low = fc.low_within(self._forecast, dt_util.utcnow())
        return {
            "active": self._cold_snap,
            "below": below,
            "setback": self.cold_snap_setback,
            "low": low[0] if low else None,
            "low_at": low[1].isoformat() if low else None,
        }

    def outdoor_until(self, end: datetime) -> float | None:
        """The forecast's mean temperature from now until `end`, else the outdoor temperature now."""
        mean = fc.mean_between(self._forecast, dt_util.utcnow(), end)
        return mean if mean is not None else self.outdoor_temp()

    @callback
    def _weather_changed(self, event: Event[EventStateChangedData]) -> None:
        """Fetch the forecast when the weather entity comes up and we have none yet."""
        new = event.data.get("new_state")
        if new is None or new.state in UNAVAILABLE or self._forecast:
            return
        self.hass.async_create_task(self.async_refresh_forecast(), f"{DOMAIN} forecast")

    async def async_refresh_forecast(self, _now: datetime | None = None) -> None:
        """Fetch the hourly forecast (weather entities only) and re-check the cold snap guard."""
        entity = self.outdoor_entity
        if entity and entity.startswith("weather."):
            try:
                resp = await self.hass.services.async_call(
                    "weather", "get_forecasts", {"entity_id": entity, "type": "hourly"}, blocking=True, return_response=True
                )
                state = self.hass.states.get(entity)
                unit = state.attributes.get("temperature_unit") if state else None
                items = ((resp or {}).get(entity) or {}).get("forecast")
                self._forecast = fc.parse(items, unit, self.hass.config.units.temperature_unit)
            except (HomeAssistantError, ValueError) as err:
                _LOGGER.debug("%s: no hourly forecast from %s: %s", self.entry.title, entity, err)
        else:
            self._forecast = []
        below = self.cold_snap_below
        low = fc.low_within(self._forecast, dt_util.utcnow())
        snap = below is not None and low is not None and low[0] <= below
        if snap != self._cold_snap:
            self._cold_snap = snap
            _LOGGER.info("%s: cold snap guard %s (forecast low %s)", self.entry.title, "on" if snap else "off", low[0] if low else None)
            self._apply()
        self.async_notify_listeners()

    def status(self) -> str:
        """What's in charge right now: hold, vacation, paused, away, early or schedule."""
        if (desired := self.desired()) is None:
            return "paused"
        return desired[2]

    def setpoints(self) -> tuple[float, float]:
        """Heat and cool to show, even in modes that only use one of them."""
        if (desired := self.desired()) is not None:
            return desired[0], desired[1]
        return self._thermostat_setpoints()

    def _thermostat_setpoints(self) -> tuple[float, float]:
        """What the thermostat has (the schedule's set points for the side it doesn't show)."""
        if self._manual is not None:
            heat, cool = self._manual
        else:
            period = self.active()[2]
            heat, cool = period.heat, period.cool
        state = self.source_state()
        if state is not None:
            attrs = state.attributes
            low = _as_float(attrs.get(ATTR_TARGET_TEMP_LOW))
            high = _as_float(attrs.get(ATTR_TARGET_TEMP_HIGH))
            target = _as_float(attrs.get(ATTR_TEMPERATURE))
            if state.state == HVACMode.HEAT_COOL and low is not None and high is not None:
                heat, cool = low, high
            elif state.state == HVACMode.HEAT and target is not None:
                heat = target
            elif state.state == HVACMode.COOL and target is not None:
                cool = target
        low_limit, high_limit = self.limits()
        self._manual = guard(heat, cool, None, self.deadband, low_limit, high_limit)
        return self._manual

    def _plan_without_vacation(self) -> tuple[float, float]:
        """The set points that were in charge before a vacation took over."""
        if self.hold:
            return self.hold.heat, self.hold.cool
        if not self.schedule_enabled:
            return self._thermostat_setpoints()
        period = self.active()[2]
        return period.heat, period.cool

    @callback
    def _restore_after_vacation(self, before: tuple[float, float] | None) -> None:
        """A vacation ended with the schedule paused: put the thermostat back where it was, once.

        Paused leaves the thermostat alone, which would otherwise keep it at the vacation's
        set points indefinitely.
        """
        if self.schedule_enabled or self.hold is not None or not before:
            return
        low, high = self.limits()
        heat, cool = guard(before[0], before[1], None, self.deadband, low, high)
        self._manual = (heat, cool)
        _LOGGER.info("%s: vacation over with the schedule paused, back to %s/%s", self.entry.title, heat, cool)
        self.hass.async_create_task(self._async_write_once(heat, cool), f"{DOMAIN} after vacation")

    # ------------------------------------------------------------------ user actions

    def _hold_until(self, duration: str | int | None) -> datetime | None:
        duration = self.hold_default if duration is None else duration
        if duration == HOLD_INDEFINITE or not self.schedule_enabled:
            return None
        if duration == HOLD_NEXT_PERIOD:
            nxt = self.next_change()
            return nxt[0] if nxt else None
        return dt_util.utcnow() + timedelta(minutes=int(duration))

    async def async_set_hold(
        self,
        heat: float | None = None,
        cool: float | None = None,
        changed: str | None = None,
        duration: str | int | None = None,
    ) -> None:
        """Manual set point change. Keeps an existing hold's end time unless a duration is given."""
        if not self.cooling_enabled and changed == "cool":
            raise ValueError("Cooling is disabled for this Comfort Schedule")
        cur_heat, cur_cool = self.setpoints()
        heat = cur_heat if heat is None else heat
        cool = cur_cool if cool is None else cool
        _check_numbers(heat, cool)
        low, high = self.limits()
        heat, cool = guard(heat, cool, changed, self.deadband, low, high)
        self.virtual_off = False
        if self.suggestions_enabled and self.status() in ("schedule", "early"):
            # Moving away from the schedule: remember it, in case it keeps happening.
            nxt = self.next_change()
            upcoming = ((nxt[0] - dt_util.utcnow()).total_seconds() / 60, nxt[1]) if nxt else None
            self.suggestions.record(heat, cool, changed, self.active(), upcoming)
            self._save_learning()
        if self.hold is not None and duration is None:
            # Adjusting an existing hold keeps its end time and its start (for the progress bar).
            until, started = self.hold.until, self.hold.started
        else:
            until, started = self._hold_until(duration), dt_util.utcnow()
        self.hold = Hold(heat, cool, until, started)
        self._save()
        self._reschedule()
        self._apply()
        self.async_notify_listeners()

    async def async_resume(self) -> None:
        self.schedule_enabled = True
        self.hold = None
        self._save()
        self._reschedule()
        # Back on the schedule: the current block's mode and fan apply again.
        self._enter_period(self.active()[2])
        self.async_notify_listeners()

    async def async_pause(self) -> None:
        self.schedule_enabled = False
        if self.hold is not None:
            self.hold.until = None
        self._save()
        self._reschedule()
        self.async_notify_listeners()

    async def async_set_schedule(self, raw: Any, label_colors: Any = None) -> None:
        """Replace the week (and optionally the per-label colors). Raises ValueError if invalid."""
        if not self.cooling_enabled:
            raw = self._prepare_heat_only_schedule(raw)
        week = week_from_json(raw, self.deadband)
        colors = self.label_colors
        if label_colors is not None:
            if not isinstance(label_colors, dict):
                raise ValueError("label_colors must map labels to #RRGGBB colors")
            for label, color in label_colors.items():
                if not isinstance(color, str) or not HEX_COLOR.fullmatch(color):
                    raise ValueError(f"Color for '{label}' must look like #3A8FB7")
            colors = {str(k)[:40]: v.upper() for k, v in label_colors.items()}
        low, high = self.limits()
        for day in week:
            for p in day:
                if p.heat < low or p.cool > high:
                    raise ValueError(f"Set points must be between {low} and {high}")
                if p.fan and (fans := self.fan_modes()) and p.fan not in fans:
                    raise ValueError(f"Fan mode '{p.fan}' isn't one of {', '.join(fans)}")
        before, wanted = self.active()[2], self.desired()
        self.week = week
        # Keep only colors for labels still in use.
        in_use = {p.label for day in week for p in day}
        self.label_colors = {k: v for k, v in colors.items() if k in in_use}
        self._save()
        self._reschedule()
        self._period_key = self.active()[:2]  # edits (e.g. a split) may renumber today's blocks
        self._check_early()  # an early start follows edits to the block it's starting
        after = self.active()[2]
        if (after.mode, after.fan) != (before.mode, before.fan) and self.schedule_enabled and self.hold is None:
            # The block running now got a different mode or fan: apply it now rather
            # than waiting for the next block.
            self._enter_period(after)
        elif self.desired() != wanted:
            self._apply()
        else:
            # Edits elsewhere in the week aren't a set point change: Heat/Cool switching
            # keeps its margin and minimum time.
            self._request_write()
        self.async_notify_listeners()

    def _prepare_heat_only_schedule(self, raw: Any) -> Any:
        """Add retained internal cool values and reject cooling modes for a heat-only service call."""
        if not isinstance(raw, list) or len(raw) != 7:
            return raw
        high = self.limits()[1]
        prepared: list[Any] = []
        for day_index, day in enumerate(raw):
            if not isinstance(day, list):
                prepared.append(day)
                continue
            old_day = self.week[day_index] if day_index < len(self.week) else []
            new_day: list[Any] = []
            for item in day:
                if not isinstance(item, dict):
                    new_day.append(item)
                    continue
                block = dict(item)
                if block.get("mode") in (HVACMode.COOL, HVACMode.HEAT_COOL):
                    raise ValueError("Cooling modes are disabled for this Comfort Schedule")
                if block.get("mode") == HVACMode.OFF and not self.off_available:
                    raise ValueError("This thermostat has no Off mode; configure an Off fallback temperature first")
                if "cool" not in block:
                    try:
                        start = int(block.get("start", 0))
                    except (TypeError, ValueError):
                        start = 0
                    retained = next((p.cool for p in old_day if p.start == start), None)
                    if retained is None and old_day:
                        retained = next((p.cool for p in reversed(old_day) if p.start <= start), old_day[0].cool)
                    cool = retained if retained is not None else high
                    try:
                        # The hidden cool value never blocks a warmer heat set point
                        # (turning cooling back on widens the gap again when the schedule loads).
                        cool = max(cool, float(block.get("heat")))
                    except (TypeError, ValueError):
                        pass  # week_from_json reports the bad heat value
                    block["cool"] = cool
                new_day.append(block)
            prepared.append(new_day)
        return prepared

    async def async_set_vacation(
        self, end: datetime, heat: float, cool: float, start: datetime | None = None
    ) -> None:
        """Hold heat/cool from `start` (default now) to `end`, then go back to the schedule."""
        now = dt_util.utcnow()
        start = _aware(start) if start else now
        end = _aware(end)
        if end <= max(start, now):
            raise ValueError("The vacation has to end after it starts, and in the future")
        _check_numbers(heat, cool)
        low, high = self.limits()
        heat, cool = guard(float(heat), float(cool), None, self.deadband, low, high)
        # What to go back to if the schedule is paused when it ends (an edit keeps the first one).
        before = self.vacation.get("before") if self.vacation_active() else None
        self.vacation = {"start": start, "end": end, "heat": heat, "cool": cool}
        if start <= now:
            self.vacation["before"] = before or self._plan_without_vacation()
        if start <= now:
            self.hold = None  # the vacation is the new plan
        self._save()
        self._reschedule()
        self._apply()
        self.async_notify_listeners()

    async def async_cancel_vacation(self) -> None:
        if self.vacation is None:
            return
        was_active = self.vacation_active()
        before = self.vacation.get("before")
        self.vacation = None
        self._save()
        self._reschedule()
        if was_active and self.schedule_enabled and self.hold is None:
            self._enter_period(self.active()[2])
        else:
            if was_active:
                self._restore_after_vacation(before)
            self._apply()
        self.async_notify_listeners()

    def current_suggestions(self) -> list[dict[str, Any]]:
        suggestions = self.suggestions.current(self.week) if self.suggestions_enabled else []
        if not self.cooling_enabled:
            suggestions = [s for s in suggestions if s.get("side") != "cool"]
        return suggestions

    async def async_apply_suggestion(self, suggestion_id: str) -> None:
        """Apply one of the open suggestions to the schedule. Raises ValueError if it's gone."""
        match = next((s for s in self.current_suggestions() if s["id"] == suggestion_id), None)
        if match is None:
            raise ValueError("That suggestion isn't open anymore")
        low, high = self.limits()
        week = Suggestions.apply(match, self.week, self.deadband, low, high)
        await self.async_set_schedule(week_to_json(week))
        self.suggestions.dismiss(suggestion_id)
        self._save_learning()
        self.async_notify_listeners()

    async def async_dismiss_suggestion(self, suggestion_id: str) -> None:
        self.suggestions.dismiss(suggestion_id)
        self._save_learning()
        self.async_notify_listeners()

    # ------------------------------------------------------------------ filter

    def _filter_seconds(self) -> float:
        if self.filter_since is None:
            return 0.0
        return self.runtime.seconds_since(self.filter_since, self.filter_offset)

    def filter_used_hours(self) -> float:
        return round(self._filter_seconds() / 3600, 1)

    def filter_due(self) -> bool:
        return self.filter_hours > 0 and self._filter_seconds() >= self.filter_hours * 3600

    @property
    def _filter_issue(self) -> str:
        return f"filter_due_{self.entry.entry_id}"

    @callback
    def _check_filter(self) -> None:
        if self.filter_since is None:
            # Count from now: what already ran today isn't on the new filter.
            today = dt_util.as_local(dt_util.utcnow()).date().isoformat()
            self.filter_since, self.filter_offset = today, self.runtime.day_seconds(today)
            self._save()
        if not self.filter_due():
            if self.filter_notified:
                self.filter_notified = False
                ir.async_delete_issue(self.hass, DOMAIN, self._filter_issue)
                self._save()
            return
        hours = self.filter_used_hours()
        # Repair issues don't survive a restart, so this is raised again on every start.
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._filter_issue,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="filter_due",
            translation_placeholders={"name": self.entry.title, "hours": f"{hours:g}"},
        )
        if not self.filter_notified:
            self.filter_notified = True
            self._save()
            self.async_send_notification(
                f"{self.entry.title}: time to check the filter",
                f"The system has run {hours:g} hours since the filter was last changed. "
                "Press Filter changed once it's done.",
            )
            self.async_notify_listeners()

    async def async_filter_changed(self) -> None:
        self.runtime.accrue()
        today = dt_util.as_local(dt_util.utcnow()).date().isoformat()
        self.filter_since, self.filter_offset = today, self.runtime.day_seconds(today)
        self.filter_notified = False
        ir.async_delete_issue(self.hass, DOMAIN, self._filter_issue)
        self._save()
        self.async_notify_listeners()

    # ------------------------------------------------------------------ presence

    def _someone_home(self) -> bool:
        """Anyone home? Unknown counts as home, so a flaky tracker never turns the heat down."""
        known = False
        for entity_id in self.presence_entities:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue  # removed or renamed: no say either way
            if state.state in UNAVAILABLE:
                return True
            known = True
            domain = entity_id.split(".", 1)[0]
            if domain == "zone":
                if (_as_float(state.state) or 0) > 0:
                    return True
            elif state.state in ("home", "on"):
                return True
        return not known

    @callback
    def _presence_changed(self, _event: Event[EventStateChangedData]) -> None:
        self._update_presence()

    @callback
    def _update_presence(self, starting: bool = False) -> None:
        if not self.away_enabled or self.away_setpoints is None or self._someone_home():
            self._cancel_away_timer()
            if self.away:
                _LOGGER.info("%s: someone is home, back to the schedule", self.entry.title)
                self.away = False
                self._save()
                if self.schedule_enabled and self.hold is None:
                    # A block that began while Away skipped its mode and fan: apply them now.
                    self._enter_period(self.active()[2])
                else:
                    self._apply()
                self.async_notify_listeners()
            return
        if self.away or self._away_unsub is not None:
            return
        # After a restart or reload, carry on where presence was: still Away, or the same
        # countdown. If everyone left while Home Assistant was off, the delay starts now.
        if starting and self._was_away:
            self._set_away()
            return
        left = self._saved_left_at if starting and self._saved_left_at else dt_util.utcnow()
        ready = left + timedelta(minutes=self.away_delay)
        if self.away_delay <= 0 or ready <= dt_util.utcnow():
            self._set_away()
            return
        self._left_at = left
        self._save()
        self._away_unsub = async_track_point_in_time(self.hass, self._away_timer, ready)

    async def async_set_away_enabled(self, enabled: bool) -> None:
        """The Away mode switch. Off ends Away now; on lets presence decide again."""
        if enabled == self.away_enabled:
            return
        self.away_enabled = enabled
        self._save()
        self._update_presence()
        self.async_notify_listeners()

    @callback
    def _away_timer(self, _now: datetime) -> None:
        self._away_unsub = None
        if self.away_enabled and not self._someone_home():
            self._set_away()

    @callback
    def _set_away(self) -> None:
        _LOGGER.info("%s: everyone is away, using the away set points", self.entry.title)
        self.away = True
        self._left_at = None
        self._save()
        self._apply()
        self.async_notify_listeners()

    @callback
    def _cancel_away_timer(self) -> None:
        if self._away_unsub:
            self._away_unsub()
            self._away_unsub = None
        if self._left_at is not None:
            self._left_at = None
            self._save()  # back before the delay ran out: don't count down from then after a reload

    # ------------------------------------------------------------------ learning

    @callback
    def run_finished(self, action: str, minutes: float, gained: float | None) -> None:
        """The health monitor saw a heating or cooling run end."""
        if gained is None:
            return
        kind = "heat" if action == "heating" else "cool"
        if kind == "cool" and not self.cooling_enabled:
            return
        if self.learning.add(kind, minutes, gained, self.outdoor_temp()):
            self._save_learning()
            self._check_trend()

    @callback
    def _check_trend(self) -> None:
        """Raise (or clear) the warning that runs are getting slower than they used to be."""
        changed = False
        kinds = ("heat", "cool") if self.cooling_enabled else ("heat",)
        if not self.cooling_enabled:
            self.trend.pop("cool", None)
            ir.async_delete_issue(self.hass, DOMAIN, f"hvac_slowdown_{self.entry.entry_id}_cool")
        for kind in kinds:
            issue = f"hvac_slowdown_{self.entry.entry_id}_{kind}"
            found = self.learning.trend(kind, self.trend_drop)
            if found is None:
                if self.trend.pop(kind, None) is not None:
                    ir.async_delete_issue(self.hass, DOMAIN, issue)
                    changed = True
                continue
            name = "heating" if kind == "heat" else "cooling"
            unit = self.hass.config.units.temperature_unit
            placeholders = {
                "name": self.entry.title,
                "kind": name,
                "drop": str(found["drop"]),
                "recent": f"{found['recent_rate']:g}{unit}",
                "baseline": f"{found['baseline_rate']:g}{unit}",
            }
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue,
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="hvac_slowdown",
                translation_placeholders=placeholders,
            )
            if kind not in self.trend:
                self.async_send_notification(
                    f"{self.entry.title}: {name} is getting slower",
                    f"Over the last two weeks {name} moved the room {placeholders['recent']} an hour, "
                    f"against {placeholders['baseline']} before in similar weather ({found['drop']}% slower). "
                    "A dirty filter or a system that needs service are the usual causes.",
                )
                changed = True
            self.trend[kind] = found
        if changed:
            self._save()
            self.async_notify_listeners()

    @callback
    def _check_early(self) -> None:
        """Start the next block early so the room is at its set point when it begins."""
        target, change_at = self._early_target()
        if target is not None and self._early is not None and change_at == self._early_for:
            if target != self._early:
                self._early = target  # the block was edited while starting early
                self._apply()
                self.async_notify_listeners()
            return
        if target is None:
            if self._early is not None:
                self._early = self._early_for = None
                self._apply()
                self.async_notify_listeners()
            return
        self._early, self._early_for = target, change_at
        _LOGGER.info("%s: starting %s early, for %s", self.entry.title, target.label, change_at)
        self._apply()
        self.async_notify_listeners()

    def _early_target(self) -> tuple[Period | None, datetime | None]:
        """The block to start now, and when it's due, or (None, None)."""
        cap = self.early_start_max
        if cap <= 0 or not self.schedule_enabled or self.hold or self.vacation_active() or self.away:
            return None, None
        if (nxt := self.next_change()) is None:
            return None, None
        change_at, block = nxt
        if not self.cooling_enabled and self.effective_period_mode(block.mode) == HVACMode.OFF:
            return None, None
        if self._early is not None and change_at == self._early_for:
            return block, change_at  # once started, stay started until the block begins
        minutes = (change_at - dt_util.utcnow()).total_seconds() / 60
        state = self.source_state()
        temp = _as_float(state.attributes.get(ATTR_CURRENT_TEMPERATURE)) if state else None
        if minutes > cap or temp is None or state is None:
            return None, None
        mode = HVACMode.HEAT_COOL if self.emulating else state.state
        now_block = self.active()[2]
        if block.heat > now_block.heat and temp < block.heat and mode in (HVACMode.HEAT, HVACMode.HEAT_COOL):
            kind, degrees = "heat", block.heat - temp
        elif self.cooling_enabled and block.cool < now_block.cool and temp > block.cool and mode in (HVACMode.COOL, HVACMode.HEAT_COOL):
            kind, degrees = "cool", temp - block.cool
        else:
            return None, None
        # The weather the run will happen in: the forecast until the block, when there is one.
        lead = self.learning.lead_minutes(kind, degrees, self.outdoor_until(change_at), cap)
        if lead is None or minutes > lead:
            return None, None
        return block, change_at

    def early_info(self) -> dict[str, Any] | None:
        if self._early is None or self._early_for is None:
            return None
        return {"label": self._early.label, "starts": self._early_for.isoformat()}

    @callback
    def note_mode(self, mode: str) -> None:
        if mode not in UNAVAILABLE and mode != HVACMode.OFF:
            self.last_active_mode = mode
            self._save()

    async def async_set_mode(self, mode: str) -> None:
        """Mode chosen on our thermostat entity."""
        if not self.cooling_enabled and mode in (HVACMode.COOL, HVACMode.HEAT_COOL):
            raise HomeAssistantError("Cooling is disabled for this Comfort Schedule")
        if mode == HVACMode.OFF:
            self.emulating = False
            if self.native_off():
                self.virtual_off = False
                await self._async_set_source_mode(HVACMode.OFF)
                await self._async_set_followers_off(self.off_fallback_temperature)
            elif self.off_fallback_temperature is not None:
                self.virtual_off = True
                await self._async_apply_virtual_off()
            else:
                raise HomeAssistantError(
                    f"{self.source} has no Off mode; configure an Off fallback temperature first"
                )
            self._save()
            self.async_notify_listeners()
            return
        self.virtual_off = False
        if mode == HVACMode.HEAT_COOL and not self.uses_native_heat_cool():
            if not self.can_emulate():
                raise HomeAssistantError(f"{self.source} has no Heat/Cool mode and can't switch between Heat and Cool")
            self.emulating = True
            self._last_changeover = dt_util.utcnow()
            self.note_mode(HVACMode.HEAT_COOL)
            await self._async_set_source_mode(self._pick_mode())
        else:
            self.emulating = False
            self.note_mode(mode)
            await self._async_set_source_mode(mode)
        self._save()
        self._request_write()
        self.async_notify_listeners()

    async def _async_apply_virtual_off(self, *, sync_followers: bool = True) -> None:
        """Keep devices without native Off in Heat at the configured frost target."""
        fallback = self.off_fallback_temperature
        if fallback is None:
            self.virtual_off = False
            self._save()
            self._request_write()
            return
        state = self.source_state()
        if state is not None and state.state not in UNAVAILABLE:
            modes = state.attributes.get(ATTR_HVAC_MODES) or []
            if state.state != HVACMode.HEAT:
                if HVACMode.HEAT not in modes:
                    raise HomeAssistantError(f"{self.source} cannot use Heat for the Off fallback")
                self._mode_target = HVACMode.HEAT
                self._mode_write = dt_util.utcnow()
                self._log_write(self.source, {ATTR_HVAC_MODE: HVACMode.HEAT})
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN,
                    SERVICE_SET_HVAC_MODE,
                    {ATTR_ENTITY_ID: self.source, ATTR_HVAC_MODE: HVACMode.HEAT},
                    blocking=True,
                )
            if not _same(state.attributes.get(ATTR_TEMPERATURE), fallback):
                await self._async_send(state, {ATTR_TEMPERATURE: fallback})
        if sync_followers:
            await self._async_set_followers_off(fallback)

    async def _async_set_followers_off(self, fallback: float | None) -> None:
        """Use each follower's native Off, or the shared fallback when it has none."""
        for entity_id in self.followers:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in UNAVAILABLE:
                continue
            modes = state.attributes.get(ATTR_HVAC_MODES) or []
            if HVACMode.OFF in modes:
                if state.state != HVACMode.OFF:
                    self._log_write(entity_id, {ATTR_HVAC_MODE: HVACMode.OFF})
                    try:
                        await self.hass.services.async_call(
                            CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE,
                            {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: HVACMode.OFF}, blocking=True,
                        )
                    except HomeAssistantError as err:
                        _LOGGER.warning("Could not turn off %s: %s", entity_id, err)
                continue
            if HVACMode.HEAT not in modes:
                _LOGGER.warning("Could not turn off %s: it has neither Off nor Heat", entity_id)
                continue
            if fallback is None:
                _LOGGER.warning("Could not turn off %s: configure an Off fallback temperature", entity_id)
                continue
            low = _as_float(state.attributes.get(ATTR_MIN_TEMP))
            high = _as_float(state.attributes.get(ATTR_MAX_TEMP))
            target = fallback + self.follower_offset(entity_id)
            if low is not None:
                target = max(low, target)
            if high is not None:
                target = min(high, target)
            try:
                if state.state != HVACMode.HEAT:
                    self._log_write(entity_id, {ATTR_HVAC_MODE: HVACMode.HEAT})
                    await self.hass.services.async_call(
                        CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE,
                        {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: HVACMode.HEAT}, blocking=True,
                    )
                if not _same(state.attributes.get(ATTR_TEMPERATURE), target):
                    data = {ATTR_TEMPERATURE: round(target, 1)}
                    self._log_write(entity_id, data)
                    await self.hass.services.async_call(
                        CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE,
                        {ATTR_ENTITY_ID: entity_id, **data}, blocking=True,
                    )
            except HomeAssistantError as err:
                _LOGGER.warning("Could not apply the Off fallback to %s: %s", entity_id, err)

    async def _async_set_source_mode(self, mode: str) -> None:
        await self._async_set_follower_modes(mode)
        state = self.source_state()
        if state is not None and state.state == mode:
            return
        self._mode_target = mode
        self._mode_write = dt_util.utcnow()
        self._log_write(self.source, {ATTR_HVAC_MODE: mode})
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: self.source, ATTR_HVAC_MODE: mode},
            blocking=True,
        )

    async def _async_set_follower_modes(self, mode: str) -> None:
        """Followers get the main thermostat's actual mode, when they have it."""
        for entity_id in self.followers:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in UNAVAILABLE or state.state == mode:
                continue
            if mode not in (state.attributes.get(ATTR_HVAC_MODES) or []):
                continue
            self._log_write(entity_id, {ATTR_HVAC_MODE: mode})
            try:
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN, SERVICE_SET_HVAC_MODE, {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: mode}, blocking=True
                )
            except HomeAssistantError as err:
                _LOGGER.warning("Could not set %s to %s: %s", entity_id, mode, err)

    def _pick_mode(self) -> str:
        """Heat or Cool to start Heat/Cool emulation in."""
        state = self.source_state()
        current = state.state if state else None
        temp = _as_float(state.attributes.get(ATTR_CURRENT_TEMPERATURE)) if state else None
        heat, cool = self.setpoints()
        if temp is None:
            return current if current in (HVACMode.HEAT, HVACMode.COOL) else HVACMode.HEAT
        if temp <= heat:
            return HVACMode.HEAT
        if temp >= cool:
            return HVACMode.COOL
        if current in (HVACMode.HEAT, HVACMode.COOL):
            return current
        return HVACMode.HEAT if temp - heat <= cool - temp else HVACMode.COOL

    @callback
    def _changeover_tick(self, _now: datetime) -> None:
        self._evaluate_changeover()
        changed = self.runtime.accrue()
        if (outdoor := self.outdoor_temp()) is not None:
            self.runtime.add_degree_minutes(outdoor, self.degree_day_base)
            self._save_runtime()
        if changed:
            self._save_runtime()
            self._check_filter()
            self.async_notify_listeners()
        self._check_early()

    @callback
    def _midnight(self, _now: datetime) -> None:
        self.runtime.accrue()  # closes yesterday's share of a run that spans midnight
        self._save_runtime()
        self._check_trend()  # the windows move on even with no new runs (say, heating off for summer)
        self.async_notify_listeners()

    @callback
    def _save_runtime(self) -> None:
        if not self._runtime_pending:
            self._runtime_pending = True
            self._runtime_store.async_delay_save(self._runtime_data, 300)

    @callback
    def _evaluate_changeover(self, setpoint_change: bool = False) -> bool:
        """While emulating Heat/Cool, switch the thermostat between Heat and Cool when needed.

        Temperature drift only switches once the room is `changeover_margin` past the other
        set point, and at most every `changeover_minutes`, so the system doesn't flip-flop.
        A set point change (manual, hold, schedule, resume) is deliberate: it switches as
        soon as the room is on the wrong side of the new set point. Returns True if a
        switch was started.
        """
        if not self.emulating:
            return False
        state = self.source_state()
        if state is None or state.state not in (HVACMode.HEAT, HVACMode.COOL):
            return False
        temp = _as_float(state.attributes.get(ATTR_CURRENT_TEMPERATURE))
        if temp is None:
            return False
        now = dt_util.utcnow()
        current = state.state
        if self._mode_write and now - self._mode_write < timedelta(minutes=1):
            # A mode change is still reaching the thermostat.
            if not setpoint_change:
                return False
            if self._mode_target in (HVACMode.HEAT, HVACMode.COOL):
                current = self._mode_target
        heat, cool = self.setpoints()
        if setpoint_change:
            to_cool, to_heat = temp > cool, temp < heat
        else:
            if self._last_changeover and now - self._last_changeover < timedelta(minutes=self.changeover_minutes):
                return False
            to_cool = temp >= cool + self.changeover_margin
            to_heat = temp <= heat - self.changeover_margin
        if current == HVACMode.HEAT and to_cool:
            target = HVACMode.COOL
        elif current == HVACMode.COOL and to_heat:
            target = HVACMode.HEAT
        else:
            return False
        _LOGGER.info(
            "Heat/Cool: %s is %s (heat %s, cool %s), switching %s to %s",
            self.source, temp, heat, cool, current, target,
        )
        self._last_changeover = now
        self.hass.async_create_task(self._async_set_source_mode(target), f"{DOMAIN} changeover")
        return True

    @callback
    def _apply(self) -> None:
        """Set points changed: switch Heat/Cool side right away if needed, then write.

        When a switch starts, the resulting mode change triggers the write, so the
        battery-powered thermostat isn't sent the soon-to-be-unused set point first.
        """
        if not self._evaluate_changeover(setpoint_change=True):
            self._request_write()

    # ------------------------------------------------------------------ timers

    @callback
    def _reschedule(self) -> None:
        if self._timer_unsub:
            self._timer_unsub()
            self._timer_unsub = None
        candidates: list[datetime] = []
        if self.schedule_enabled and (nxt := self.next_change()):
            candidates.append(nxt[0])
        if self.hold and self.hold.until:
            candidates.append(self.hold.until)
        if self.vacation is not None:
            now = dt_util.utcnow()
            candidates.extend(t for t in (self.vacation["start"], self.vacation["end"]) if t > now)
        if candidates:
            self._timer_unsub = async_track_point_in_time(
                self.hass, self._on_timer, min(candidates)
            )

    @callback
    def _on_timer(self, _now: datetime) -> None:
        self._timer_unsub = None
        hold_ended = False
        if (
            self.hold
            and self.hold.until
            and self.hold.until <= dt_util.utcnow() + timedelta(seconds=1)
        ):
            self.hold = None
            self._save()
            hold_ended = True
        now = dt_util.utcnow() + timedelta(seconds=1)
        if self.vacation is not None and self.vacation["end"] <= now:
            before = self.vacation.get("before")
            self.vacation = None  # over: back to the schedule, like the end of a hold
            self._save()
            hold_ended = True
            self._restore_after_vacation(before)
        elif self.vacation is not None and self.vacation["start"] <= now:
            if "before" not in self.vacation:
                self.vacation["before"] = self._plan_without_vacation()
                self._save()
            if self.hold is not None and (self.hold.started is None or self.hold.started < self.vacation["start"]):
                self.hold = None  # a hold from before the trip doesn't outlast its start
                self._save()
        day, index, period = self.active()
        block_started = (day, index) != self._period_key
        self._period_key = (day, index)
        if block_started:
            self._early = self._early_for = None
        self._reschedule()
        if (block_started or hold_ended) and self.schedule_enabled and self.hold is None:
            self._enter_period(period)
        else:
            self._apply()
        self.async_notify_listeners()

    @callback
    def _enter_period(self, period: Period) -> None:
        """A block took effect: apply its mode and fan (unless "use current"), then set points.

        Not during a vacation or Away: a block that turns the system off mustn't do it in an empty house.
        """
        if (not period.mode and not period.fan) or self.vacation_active() or self.away:
            self._apply()
            return
        self.hass.async_create_task(self._async_enter_period(period), f"{DOMAIN} enter period")

    async def _async_enter_period(self, period: Period) -> None:
        state = self.source_state()
        if state is None or state.state in UNAVAILABLE:
            self._apply()
            return
        mode_changed = False
        period_mode = self.effective_period_mode(period.mode)
        if period_mode and self.proxy_mode() != period_mode:
            try:
                await self.async_set_mode(period_mode)  # also writes the set points for the new mode
                mode_changed = True
            except HomeAssistantError as err:
                _LOGGER.warning("Schedule: couldn't set %s to %s: %s", self.source, period_mode, err)
        if period.fan:
            await self.async_set_fan(period.fan)
        if not mode_changed:
            self._apply()

    def fan_modes(self) -> list[str]:
        state = self.source_state()
        return list(state.attributes.get(ATTR_FAN_MODES) or []) if state else []

    async def async_set_fan(self, fan: str) -> None:
        state = self.source_state()
        if state is None or state.attributes.get(ATTR_FAN_MODE) == fan:
            return
        if fan not in self.fan_modes():
            _LOGGER.warning("Schedule: %s has no fan mode '%s'", self.source, fan)
            return
        try:
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_FAN_MODE,
                {ATTR_ENTITY_ID: self.source, ATTR_FAN_MODE: fan},
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Couldn't set %s fan to %s: %s", self.source, fan, err)

    @callback
    def _resync(self, _now: datetime) -> None:
        self._request_write()

    # ------------------------------------------------------------------ writing to the thermostat

    @callback
    def _request_write(self) -> None:
        self.hass.async_create_task(self._writer.async_call(), f"{DOMAIN} write")

    @staticmethod
    def _needed(state: State, heat: float, cool: float) -> dict[str, float]:
        """The set_temperature data that would bring `state` to heat/cool ({} if it's there)."""
        attrs = state.attributes
        if state.state == HVACMode.HEAT_COOL:
            if not (
                _same(attrs.get(ATTR_TARGET_TEMP_LOW), heat)
                and _same(attrs.get(ATTR_TARGET_TEMP_HIGH), cool)
            ):
                return {ATTR_TARGET_TEMP_LOW: heat, ATTR_TARGET_TEMP_HIGH: cool}
        elif state.state == HVACMode.HEAT:
            if not _same(attrs.get(ATTR_TEMPERATURE), heat):
                return {ATTR_TEMPERATURE: heat}
        elif state.state == HVACMode.COOL:
            if not _same(attrs.get(ATTR_TEMPERATURE), cool):
                return {ATTR_TEMPERATURE: cool}
        return {}

    def _log_write(self, entity_id: str, data: dict[str, Any]) -> None:
        self.write_log.append({"t": dt_util.utcnow().isoformat(), "entity_id": entity_id, **data})

    async def _async_write_followers(self, heat: float, cool: float, main_off: bool = False) -> None:
        """Copy the set points to the other thermostats on this schedule, in their own modes."""
        for entity_id in self.followers:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in UNAVAILABLE:
                continue
            if (
                main_off
                and self.off_fallback_temperature is not None
                and HVACMode.OFF not in (state.attributes.get(ATTR_HVAC_MODES) or [])
            ):
                continue  # held at the Off fallback in Heat: that's its Off
            low, high = (
                _as_float(state.attributes.get(ATTR_MIN_TEMP)),
                _as_float(state.attributes.get(ATTR_MAX_TEMP)),
            )
            offset = self.follower_offset(entity_id)
            f_heat, f_cool = guard(
                heat + offset, cool + offset, None, self.deadband,
                low if low is not None else heat + offset, high if high is not None else cool + offset,
            )
            data = self._needed(state, f_heat, f_cool)
            if not data:
                self._follower_sent.pop(entity_id, None)
                continue
            sent, repeats = self._follower_sent.get(entity_id, ({}, 0))
            repeats = repeats + 1 if sent == data else 1
            self._follower_sent[entity_id] = (data, repeats)
            if repeats > MAX_REPEAT_WRITES + 1:
                continue  # it won't take it; don't keep waking it
            self._log_write(entity_id, data)
            try:
                await self.hass.services.async_call(
                    CLIMATE_DOMAIN, SERVICE_SET_TEMPERATURE, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
                )
            except HomeAssistantError as err:
                _LOGGER.warning("Could not update %s: %s", entity_id, err)

    async def _async_write(self) -> None:
        main_off = self.virtual_off or self.proxy_mode() == HVACMode.OFF
        if self.virtual_off:
            # Periodic resync keeps the primary fallback in place, but follower
            # mode changes remain tied to an actual mode transition.
            await self._async_apply_virtual_off(sync_followers=False)
        desired = self.desired()
        if desired is not None and self.followers:
            # Followers still get the schedule while the main thermostat is Off: one turned
            # back on by hand follows it (one that's off has nothing to set).
            await self._async_write_followers(desired[0], desired[1], main_off=main_off)
        if main_off:
            return
        state = self.source_state()
        if desired is None or state is None or state.state in UNAVAILABLE:
            return
        heat, cool, _ = desired
        attrs = state.attributes
        data = self._needed(state, heat, cool)
        if not data:
            self._repeats = 0
            return

        if data == self._expected and self._last_write is not None:
            # We already sent exactly this and the thermostat still reports something else
            # (it may round, clamp, or enforce a wider deadband). Don't keep waking a
            # battery-powered device with the same command.
            self._repeats += 1
            if self._repeats > MAX_REPEAT_WRITES:
                if self._repeats == MAX_REPEAT_WRITES + 1:
                    _LOGGER.warning(
                        "%s still reports %s after being set to %s; not retrying until the plan changes",
                        self.source,
                        {key: attrs.get(key) for key in data},
                        data,
                    )
                return
        else:
            self._repeats = 0
        await self._async_send(state, data)

    async def _async_write_once(self, heat: float, cool: float) -> None:
        """Set the thermostat to heat/cool now, outside the plan (nothing keeps it there)."""
        if self.virtual_off:
            await self._async_apply_virtual_off(sync_followers=False)
            return
        state = self.source_state()
        if state is None or state.state in UNAVAILABLE:
            return
        if data := self._needed(state, heat, cool):
            await self._async_send(state, data)

    async def _async_send(self, state: State, data: dict[str, float]) -> None:
        attrs = state.attributes
        # Record what we expect *before* sending: Z-Wave may report the new value while
        # the call is still in flight, and that echo must not look like a wall change.
        self._expected = dict(data)
        self._before_write = {key: attrs.get(key) for key in data}
        self._last_write = dt_util.utcnow()
        self._log_write(self.source, data)
        _LOGGER.debug("Writing %s to %s", data, self.source)
        try:
            await self.hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_TEMPERATURE,
                {ATTR_ENTITY_ID: self.source, **data},
                blocking=True,
            )
        except HomeAssistantError as err:
            # No grace window or repeat counting for a write that never went out.
            self._last_write = None
            _LOGGER.warning("Could not update %s: %s (will retry)", self.source, err)

    # ------------------------------------------------------------------ reacting to the thermostat

    @callback
    def _source_changed(self, event: Event[EventStateChangedData]) -> None:
        old = event.data["old_state"]
        new = event.data["new_state"]
        if new is None:
            return
        self.health.update(new)
        was_running = self.runtime.running
        self.runtime.update(new, self.cooling_enabled)
        if was_running:
            self._save_runtime()

        invalid_heat_only_mode = (
            not self.cooling_enabled and new.state in (HVACMode.COOL, HVACMode.HEAT_COOL)
        )
        if invalid_heat_only_mode:
            if not self._heat_only_correction_attempted:
                self._heat_only_correction_attempted = True
                modes = new.attributes.get(ATTR_HVAC_MODES) or []
                # Heat first: someone who picked Heat/Cool still wants the house warm.
                safe_mode = (
                    HVACMode.HEAT if HVACMode.HEAT in modes
                    else HVACMode.OFF if HVACMode.OFF in modes
                    else None
                )
                if safe_mode is None:
                    _LOGGER.warning(
                        "%s entered %s while cooling is disabled, but it advertises neither "
                        "Heat nor Off; leaving it unchanged",
                        self.source,
                        new.state,
                    )
                else:
                    _LOGGER.warning(
                        "%s entered %s while cooling is disabled; switching once to supported mode %s",
                        self.source,
                        new.state,
                        safe_mode,
                    )
                    self.hass.async_create_task(
                        self.async_set_mode(safe_mode), f"{DOMAIN} heat-only safety"
                    )
            self.async_notify_listeners()
            return
        self._heat_only_correction_attempted = False

        if old is None or old.state in UNAVAILABLE:
            if new.state not in UNAVAILABLE:
                self._request_write()
        elif new.state != old.state:
            # Mode changed (card, voice or wall): each mode needs different set points.
            ours = (
                self._mode_target == new.state
                and self._mode_write is not None
                and dt_util.utcnow() - self._mode_write < WRITE_GRACE
            )
            if self.emulating and not ours and new.state not in UNAVAILABLE:
                # Someone picked a mode at the wall or on the raw entity: they're in charge now.
                _LOGGER.info("%s set to %s outside Comfort Schedule; stopping Heat/Cool switching", self.source, new.state)
                self.emulating = False
                self._save()
            if not self.emulating:
                self.note_mode(new.state)
            if not ours and self.followers and new.state not in UNAVAILABLE:
                # Our own mode writes already went to the followers; this one came from the wall.
                self.hass.async_create_task(self._async_set_follower_modes(new.state), f"{DOMAIN} follower modes")
            self._mode_seen = dt_util.utcnow()
            self._request_write()
        elif (external := self._external_change(old, new)) is not None:
            heat, cool, changed = external
            cur_heat, cur_cool = self.setpoints()
            if not (_same(heat, cur_heat) and _same(cool, cur_cool)):
                _LOGGER.debug("Set point changed outside Comfort Schedule; holding %s/%s", heat, cool)
                self.hass.async_create_task(self.async_set_hold(heat, cool, changed))
        self._evaluate_changeover()
        self.async_notify_listeners()

    def _external_change(self, old: State, new: State) -> tuple[float, float, str | None] | None:
        """Detect a set point change made at the wall unit or on the raw entity."""
        if new.state in UNAVAILABLE or self.desired() is None:
            return None
        if self._mode_seen and dt_util.utcnow() - self._mode_seen < WRITE_GRACE:
            # Right after a mode change the thermostat reports the set point it had stored
            # for that mode; we're about to overwrite it, so it isn't a person at the wall.
            return None
        in_grace = self._last_write is not None and dt_util.utcnow() - self._last_write < WRITE_GRACE
        heat, cool = self.setpoints()
        if new.state == HVACMode.HEAT_COOL:
            keys = (ATTR_TARGET_TEMP_LOW, ATTR_TARGET_TEMP_HIGH)
        elif new.state in (HVACMode.HEAT, HVACMode.COOL):
            keys = (ATTR_TEMPERATURE,)
        else:
            return None

        moved = False
        for key in keys:
            before = old.attributes.get(key)
            after = new.attributes.get(key)
            if after is None or _same(before, after):
                continue
            if key in self._expected and _same(after, self._expected[key]):
                continue
            if in_grace and key in self._expected and _rounded(after, self._expected[key], new):
                # Our write, rounded to the thermostat's step (68.5 shows as 69).
                continue
            if in_grace and key in self._before_write and _same(after, self._before_write[key]):
                # A late report of the value from before our write, not a person at the wall.
                continue
            moved = True
        if not moved:
            return None

        attrs = new.attributes
        if new.state == HVACMode.HEAT_COOL:
            low = _as_float(attrs.get(ATTR_TARGET_TEMP_LOW))
            high = _as_float(attrs.get(ATTR_TARGET_TEMP_HIGH))
            if low is None or high is None:
                return None
            changed = "heat" if not _same(low, heat) and _same(high, cool) else (
                "cool" if _same(low, heat) else None
            )
            return low, high, changed
        target = _as_float(attrs.get(ATTR_TEMPERATURE))
        if target is None:
            return None
        if new.state == HVACMode.HEAT:
            return target, cool, "heat"
        return heat, target, "cool"
