"""Constants for Comfort Schedule."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "comfort_schedule"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.SWITCH,
]

STORAGE_VERSION = 1

# Config entry data
CONF_CLIMATE = "climate_entity"
CONF_INITIAL_SCHEDULE = "initial_schedule"

# Options
CONF_DEADBAND = "deadband"
CONF_NOTIFY = "notify_entity"
CONF_HOLD_DEFAULT = "hold_default"
CONF_HEAT_MINUTES = "heat_minutes"
CONF_COOL_MINUTES = "cool_minutes"
CONF_MIN_PROGRESS = "min_progress"
CONF_WRONG_WAY = "wrong_way"
CONF_CHANGEOVER_MARGIN = "changeover_margin"
CONF_CHANGEOVER_MINUTES = "changeover_minutes"
CONF_FORCE_EMULATION = "force_heat_cool_emulation"
CONF_OUTDOOR = "outdoor_entity"
CONF_PRESENCE = "presence_entities"
CONF_AWAY_HEAT = "away_heat"
CONF_AWAY_COOL = "away_cool"
CONF_AWAY_DELAY = "away_delay"
CONF_FILTER_HOURS = "filter_hours"
CONF_HEAT_COST = "heat_cost"
CONF_COOL_COST = "cool_cost"
CONF_FOLLOWERS = "follower_entities"
CONF_EARLY_START = "early_start_max"
CONF_TREND_DROP = "trend_drop"
CONF_SUGGESTIONS = "suggestions"

DEFAULT_DEADBAND = 3.0
DEFAULT_HOLD = "next_period"
DEFAULT_HEAT_MINUTES = 45
DEFAULT_COOL_MINUTES = 60
DEFAULT_MIN_PROGRESS = 1.0
DEFAULT_WRONG_WAY = 0.8
# Heat/Cool for thermostats that only have Heat and Cool: switch modes once the room is
# this far past the other set point, and no more often than every N minutes.
DEFAULT_CHANGEOVER_MARGIN = 1.0
DEFAULT_CHANGEOVER_MINUTES = 30
# Presence: wait this long after the last person leaves before switching to the away set points.
DEFAULT_AWAY_DELAY = 10
# Filter reminder after this many hours of heating plus cooling (0 turns it off).
DEFAULT_FILTER_HOURS = 300
# Early start: start at most this many minutes before a block (0 turns it off).
DEFAULT_EARLY_START = 0
# Warn when runs get this much slower (percent) than they used to be (0 turns it off).
DEFAULT_TREND_DROP = 25

HOLD_NEXT_PERIOD = "next_period"
HOLD_INDEFINITE = "indefinite"
HOLD_CHOICES = [HOLD_NEXT_PERIOD, "120", "240", HOLD_INDEFINITE]

# Schedule granularity, minutes
STEP_MINUTES = 5
MINUTES_PER_DAY = 1440

# Many thermostats run on batteries: coalesce rapid changes into one write.
WRITE_COOLDOWN = 2.0
# After we write, ignore unexpected set point reports for this long so the
# device catching up is not mistaken for someone changing it at the wall.
WRITE_GRACE = timedelta(seconds=120)
# Re-check that the thermostat matches the plan this often (writes only on mismatch).
RESYNC_INTERVAL = timedelta(minutes=10)
# Resend an identical command at most this many times if the thermostat won't take it.
MAX_REPEAT_WRITES = 2

# Health monitor
# Wrong way is checked from this many minutes into a run, and must then last longer than this.
WRONG_WAY_MIN_MINUTES = 10
# An idle gap this short between two heating (or cooling) updates counts as the same run.
RUN_GAP = timedelta(minutes=2)
PROBLEM_NO_PROGRESS = "no_progress"
PROBLEM_WRONG_WAY = "wrong_way"
EVENT_PROBLEM = f"{DOMAIN}_problem"

SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_SET_HOLD = "set_hold"
SERVICE_RESUME = "resume_schedule"
SERVICE_PAUSE = "pause_schedule"
SERVICE_SET_VACATION = "set_vacation"
SERVICE_CANCEL_VACATION = "cancel_vacation"
SERVICE_FILTER_CHANGED = "filter_changed"
SERVICE_APPLY_SUGGESTION = "apply_suggestion"
SERVICE_DISMISS_SUGGESTION = "dismiss_suggestion"

# Degree-days are counted against this outdoor base temperature (°F, °C).
DEGREE_DAY_BASE = {"°F": 65.0, "°C": 18.0}

ATTR_SCHEDULE = "schedule"
ATTR_DURATION = "duration"
ATTR_HEAT = "heat"
ATTR_COOL = "cool"
ATTR_START = "start"
ATTR_END = "end"
ATTR_ID = "id"

FRONTEND_URL = "/comfort_schedule_static"
FRONTEND_FILE = "comfort-schedule-cards.js"
