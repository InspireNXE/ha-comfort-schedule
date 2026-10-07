"""The brain: decides what the thermostat should be set to and keeps it there."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
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
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

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
    HOLD_INDEFINITE,
    HOLD_NEXT_PERIOD,
    MAX_REPEAT_WRITES,
    RESYNC_INTERVAL,
    STORAGE_VERSION,
    WRITE_COOLDOWN,
    WRITE_GRACE,
)
from .health import HealthMonitor
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

_LOGGER = logging.getLogger(__name__)

UNAVAILABLE = (STATE_UNAVAILABLE, STATE_UNKNOWN)
HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


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
        # True while we provide Heat/Cool ourselves by switching the thermostat
        # between Heat and Cool (for thermostats without a native Heat/Cool mode).
        self.emulating = False
        self.health = HealthMonitor(self)
        self.runtime = RuntimeTracker()
        self.battery_entity: str | None = None
        # Colors chosen for schedule labels ({"Away": "#8C98A5"}); unset labels use the defaults.
        self.label_colors: dict[str, str] = {}
        self.humidity_entity: str | None = None

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        # Runtime totals change every minute while the HVAC runs, so they live in their
        # own file with a slow save; a pending save is still flushed when HA stops.
        self._runtime_store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.runtime"
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

    @property
    def deadband(self) -> float:
        return float(self._opt(CONF_DEADBAND, DEFAULT_DEADBAND))

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
        return float(self._opt(CONF_MIN_PROGRESS, DEFAULT_MIN_PROGRESS))

    @property
    def wrong_way(self) -> float:
        return float(self._opt(CONF_WRONG_WAY, DEFAULT_WRONG_WAY))

    @property
    def notify_entity(self) -> str | None:
        return self._opt(CONF_NOTIFY, None) or None

    @property
    def changeover_margin(self) -> float:
        return float(self._opt(CONF_CHANGEOVER_MARGIN, DEFAULT_CHANGEOVER_MARGIN))

    @property
    def changeover_minutes(self) -> int:
        return int(self._opt(CONF_CHANGEOVER_MINUTES, DEFAULT_CHANGEOVER_MINUTES))

    def source_state(self) -> State | None:
        return self.hass.states.get(self.source)

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
        return HVACMode.HEAT_COOL in self._source_modes() and not self._opt(CONF_FORCE_EMULATION, False)

    def can_emulate(self) -> bool:
        modes = self._source_modes()
        return HVACMode.HEAT in modes and HVACMode.COOL in modes

    def proxy_mode(self) -> str | None:
        """The mode to show: Heat/Cool while we're switching between Heat and Cool ourselves."""
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
        colors = data.get("label_colors") or {}
        self.label_colors = {
            str(k): v for k, v in colors.items() if isinstance(v, str) and HEX_COLOR.fullmatch(v)
        } if isinstance(colors, dict) else {}
        if hold := data.get("hold"):
            until = dt_util.parse_datetime(hold["until"]) if hold.get("until") else None
            if until is None or until > dt_util.utcnow():
                started = dt_util.parse_datetime(hold["started"]) if hold.get("started") else None
                self.hold = Hold(float(hold["heat"]), float(hold["cool"]), until, started)

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
            async_track_time_interval(self.hass, self._changeover_tick, timedelta(minutes=1))
        )
        # "Today" runtime sensors roll over at midnight even when nothing is running.
        self._unsubs.append(
            async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=1)
        )
        state = self.source_state()
        self.runtime.update(state)
        if state is not None and state.state not in UNAVAILABLE:
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
        self._runtime_store.async_delay_save(self.runtime.dump, 0)
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._timer_unsub:
            self._timer_unsub()
            self._timer_unsub = None
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
            "label_colors": self.label_colors,
        }

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
            "mode": period.mode,
            "fan": period.fan,
        }

    def desired(self) -> tuple[float, float, str] | None:
        """(heat, cool, source) the thermostat should have, or None to leave it alone."""
        if self.hold:
            return self.hold.heat, self.hold.cool, "hold"
        if not self.schedule_enabled:
            return None
        period = self.active()[2]
        return period.heat, period.cool, "schedule"

    def setpoints(self) -> tuple[float, float]:
        """Heat and cool to show, even in modes that only use one of them."""
        if (desired := self.desired()) is not None:
            return desired[0], desired[1]
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
        cur_heat, cur_cool = self.setpoints()
        heat = cur_heat if heat is None else heat
        cool = cur_cool if cool is None else cool
        low, high = self.limits()
        heat, cool = guard(heat, cool, changed, self.deadband, low, high)
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
        before = self.active()[2]
        self.week = week
        # Keep only colors for labels still in use.
        in_use = {p.label for day in week for p in day}
        self.label_colors = {k: v for k, v in colors.items() if k in in_use}
        self._save()
        self._reschedule()
        self._period_key = self.active()[:2]  # edits (e.g. a split) may renumber today's blocks
        after = self.active()[2]
        if (after.mode, after.fan) != (before.mode, before.fan) and self.schedule_enabled and self.hold is None:
            # The block running now got a different mode or fan: apply it now rather
            # than waiting for the next block.
            self._enter_period(after)
        else:
            self._apply()
        self.async_notify_listeners()

    @callback
    def note_mode(self, mode: str) -> None:
        if mode not in UNAVAILABLE and mode != HVACMode.OFF:
            self.last_active_mode = mode
            self._save()

    async def async_set_mode(self, mode: str) -> None:
        """Mode chosen on our thermostat entity."""
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

    async def _async_set_source_mode(self, mode: str) -> None:
        state = self.source_state()
        if state is not None and state.state == mode:
            return
        self._mode_target = mode
        self._mode_write = dt_util.utcnow()
        await self.hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: self.source, ATTR_HVAC_MODE: mode},
            blocking=True,
        )

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
        if self.runtime.accrue():
            self._save_runtime()
            self.async_notify_listeners()

    @callback
    def _midnight(self, _now: datetime) -> None:
        self.runtime.accrue()  # closes yesterday's share of a run that spans midnight
        self._save_runtime()
        self.async_notify_listeners()

    @callback
    def _save_runtime(self) -> None:
        self._runtime_store.async_delay_save(self.runtime.dump, 300)

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
        day, index, period = self.active()
        block_started = (day, index) != self._period_key
        self._period_key = (day, index)
        self._reschedule()
        if (block_started or hold_ended) and self.schedule_enabled and self.hold is None:
            self._enter_period(period)
        else:
            self._apply()
        self.async_notify_listeners()

    @callback
    def _enter_period(self, period: Period) -> None:
        """A block took effect: apply its mode and fan (unless "use current"), then set points."""
        if not period.mode and not period.fan:
            self._apply()
            return
        self.hass.async_create_task(self._async_enter_period(period), f"{DOMAIN} enter period")

    async def _async_enter_period(self, period: Period) -> None:
        state = self.source_state()
        if state is None or state.state in UNAVAILABLE:
            self._apply()
            return
        mode_changed = False
        if period.mode and self.proxy_mode() != period.mode:
            try:
                await self.async_set_mode(period.mode)  # also writes the set points for the new mode
                mode_changed = True
            except HomeAssistantError as err:
                _LOGGER.warning("Schedule: couldn't set %s to %s: %s", self.source, period.mode, err)
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

    async def _async_write(self) -> None:
        desired = self.desired()
        state = self.source_state()
        if desired is None or state is None or state.state in UNAVAILABLE:
            return
        heat, cool, _ = desired
        attrs = state.attributes
        data: dict[str, float] = {}
        if state.state == HVACMode.HEAT_COOL:
            if not (
                _same(attrs.get(ATTR_TARGET_TEMP_LOW), heat)
                and _same(attrs.get(ATTR_TARGET_TEMP_HIGH), cool)
            ):
                data = {ATTR_TARGET_TEMP_LOW: heat, ATTR_TARGET_TEMP_HIGH: cool}
        elif state.state == HVACMode.HEAT:
            if not _same(attrs.get(ATTR_TEMPERATURE), heat):
                data = {ATTR_TEMPERATURE: heat}
        elif state.state == HVACMode.COOL:
            if not _same(attrs.get(ATTR_TEMPERATURE), cool):
                data = {ATTR_TEMPERATURE: cool}
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

        # Record what we expect *before* sending: Z-Wave may report the new value while
        # the call is still in flight, and that echo must not look like a wall change.
        self._expected = dict(data)
        self._before_write = {key: attrs.get(key) for key in data}
        self._last_write = dt_util.utcnow()
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
        self.runtime.update(new)
        if was_running:
            self._save_runtime()

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
        in_grace =self._last_write is not None and dt_util.utcnow() - self._last_write < WRITE_GRACE
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
