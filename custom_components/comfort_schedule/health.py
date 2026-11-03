"""Watch heating/cooling runs and flag ones that aren't moving the temperature."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import CALLBACK_TYPE, State, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    EVENT_PROBLEM,
    PROBLEM_NO_PROGRESS,
    PROBLEM_WRONG_WAY,
    RUN_GAP,
    WRONG_WAY_MIN_MINUTES,
)

if TYPE_CHECKING:
    from .controller import ComfortController

_LOGGER = logging.getLogger(__name__)

RUNNING = (HVACAction.HEATING, HVACAction.COOLING)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class HealthMonitor:
    """Track the current run and raise/clear a problem."""

    def __init__(self, controller: ComfortController) -> None:
        self._c = controller
        self.action: str | None = None
        self.run_start: datetime | None = None
        self.start_temp: float | None = None
        self.last_temp: float | None = None
        self.heat_target: float | None = None
        self.cool_target: float | None = None
        # When the thermostat last stopped reporting the run (it ends after RUN_GAP).
        self.paused_at: datetime | None = None
        # When the temperature first went the wrong way in this run, if it still is.
        self.wrong_since: datetime | None = None
        self.problem: str | None = None
        self.problem_since: datetime | None = None
        self.problem_action: str | None = None
        self.problem_detail: dict[str, Any] = {}
        self._unsub_tick: CALLBACK_TYPE | None = None

    @property
    def issue_id(self) -> str:
        return f"hvac_problem_{self._c.entry.entry_id}"

    @callback
    def async_start(self, state: State | None) -> None:
        if state is not None:
            self.update(state)
        self._unsub_tick = async_track_time_interval(
            self._c.hass, self._tick, timedelta(minutes=1)
        )

    @callback
    def async_stop(self) -> None:
        if self._unsub_tick:
            self._unsub_tick()
            self._unsub_tick = None

    def run_minutes(self) -> int:
        if self.run_start is None:
            return 0
        return int((dt_util.utcnow() - self.run_start).total_seconds() // 60)

    def progress(self) -> float | None:
        """Temperature movement toward the set point since the run started (negative = wrong way)."""
        if self.start_temp is None or self.last_temp is None or self.action is None:
            return None
        delta = self.last_temp - self.start_temp
        return delta if self.action == HVACAction.HEATING else -delta

    def as_dict(self) -> dict[str, Any]:
        progress = self.progress()
        return {
            "problem": self.problem,
            "problem_since": self.problem_since.isoformat() if self.problem_since else None,
            "action": self.action,
            "run_minutes": self.run_minutes(),
            "progress": round(progress, 1) if progress is not None else None,
        }

    @callback
    def update(self, state: State) -> None:
        """Called on every thermostat state change."""
        action = state.attributes.get(ATTR_HVAC_ACTION)
        temp = _as_float(state.attributes.get(ATTR_CURRENT_TEMPERATURE))
        if temp is not None:
            self.last_temp = temp
        attrs = state.attributes
        # Range-capable thermostats report low/high as None outside heat_cool.
        low, high = _as_float(attrs.get(ATTR_TARGET_TEMP_LOW)), _as_float(attrs.get(ATTR_TARGET_TEMP_HIGH))
        single = _as_float(attrs.get(ATTR_TEMPERATURE))
        self.heat_target = low if low is not None else single
        self.cool_target = high if high is not None else single

        if state.state == HVACMode.OFF:
            if self.action is not None:
                self._run_ended()
            self._clear("The system was turned off, so the warning was cleared.")
            return

        if action in RUNNING:
            if action != self.action:
                if self.action is not None:
                    self._run_ended()
                self.action = action
                self.run_start = dt_util.utcnow()
                self.start_temp = temp
                self.wrong_since = None
            elif self.start_temp is None:
                self.start_temp = temp
            self.paused_at = None
        elif self.action is not None and self.paused_at is None:
            # A short idle blip between updates keeps the run going.
            self.paused_at = dt_util.utcnow()

        self._evaluate()
        if self.action is None and self.problem and self._at_target(self.problem_action):
            self._clear()

    @callback
    def _tick(self, _now: datetime) -> None:
        if self.action is not None:
            self._evaluate()
            self._c.async_notify_listeners()

    def _at_target(self, action: str | None = None) -> bool:
        """The room reached the set point for this run (so the run did its job)."""
        action = action or self.action
        temp = self.last_temp
        if temp is None:
            return False
        if action == HVACAction.HEATING:
            return self.heat_target is not None and temp >= self.heat_target
        if action == HVACAction.COOLING:
            return self.cool_target is not None and temp <= self.cool_target
        return False

    def _worked(self) -> bool:
        progress = self.progress()
        return self._at_target() or (
            progress is not None and progress >= self._c.min_progress
        )

    @callback
    def _run_ended(self) -> None:
        if self.problem and self._worked():
            self._clear()
        if self.action is not None and self.run_start is not None:
            # Up to when it stopped, not the end of the idle gap that confirmed it.
            end = self.paused_at or dt_util.utcnow()
            self._c.run_finished(self.action, (end - self.run_start).total_seconds() / 60, self.progress())
        self.action = None
        self.run_start = None
        self.start_temp = None
        self.paused_at = None
        self.wrong_since = None

    @callback
    def _evaluate(self) -> None:
        if self.action is None:
            return
        if self.paused_at is not None:
            if dt_util.utcnow() - self.paused_at > RUN_GAP:
                self._run_ended()
            return
        progress = self.progress()
        if progress is None:
            return
        minutes = self.run_minutes()

        if self.problem:
            if self._worked():
                self._clear()
            return

        now = dt_util.utcnow()
        if minutes >= WRONG_WAY_MIN_MINUTES and progress <= -self._c.wrong_way:
            # One sensor tick the wrong way is normal; it has to last.
            if self.wrong_since is None:
                self.wrong_since = now
            elif now - self.wrong_since > timedelta(minutes=WRONG_WAY_MIN_MINUTES):
                self._raise(PROBLEM_WRONG_WAY, minutes, progress)
                return
        else:
            self.wrong_since = None

        limit = (
            self._c.heat_minutes
            if self.action == HVACAction.HEATING
            else self._c.cool_minutes
        )
        if minutes >= limit and progress < self._c.min_progress and not self._at_target():
            self._raise(PROBLEM_NO_PROGRESS, minutes, progress)

    def _describe(self, reason: str, minutes: int, progress: float) -> tuple[str, str, str]:
        """Title, the full message, and the advice part alone (the card already shows the numbers)."""
        unit = self._c.hass.config.units.temperature_unit
        verb = "Heating" if self.action == HVACAction.HEATING else "Cooling"
        name = self._c.entry.title
        if reason == PROBLEM_WRONG_WAY:
            title = f"{name}: temperature going the wrong way"
            advice = (
                "The blower may be pushing unheated air, for example if the igniter "
                "or flame sensor failed and the burner never lit."
                if self.action == HVACAction.HEATING
                else "The AC may not be cooling, for example low refrigerant or a "
                "stopped outdoor unit."
            )
            message = (
                f"{verb} for {minutes} min, but the temperature moved "
                f"{abs(progress):.1f}{unit} the wrong way. {advice}"
            )
        else:
            title = f"{name}: {verb.lower()} without progress"
            advice = (
                "Check the filter, the vents, and whether the "
                f"{'furnace' if self.action == HVACAction.HEATING else 'AC'} is actually running."
            )
            message = f"{verb} for {minutes} min with only {progress:+.1f}{unit} of change. {advice}"
        return title, message, advice

    @callback
    def _raise(self, reason: str, minutes: int, progress: float) -> None:
        self.problem = reason
        self.problem_action = self.action
        self.problem_since = dt_util.utcnow()
        title, message, advice = self._describe(reason, minutes, progress)
        self.problem_detail = {"title": title, "message": message, "advice": advice}
        _LOGGER.warning("%s: %s", title, message)

        hass = self._c.hass
        ir.async_create_issue(
            hass,
            DOMAIN,
            self.issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="hvac_problem",
            translation_placeholders={"title": title, "message": message},
        )
        hass.bus.async_fire(
            EVENT_PROBLEM,
            {
                "entity_id": self._c.climate_entity_id,
                "reason": reason,
                "run_minutes": minutes,
                "progress": round(progress, 1),
            },
        )
        self._c.async_send_notification(title, message)
        self._c.async_notify_listeners()

    @callback
    def _clear(self, message: str = "The temperature is moving toward the set point again.") -> None:
        if not self.problem:
            return
        title = f"{self._c.entry.title}: HVAC back to normal"
        self.problem = None
        self.problem_since = None
        self.problem_detail = {}
        ir.async_delete_issue(self._c.hass, DOMAIN, self.issue_id)
        self._c.async_send_notification(title, message)
        self._c.async_notify_listeners()
