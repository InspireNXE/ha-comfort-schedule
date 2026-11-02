# Scenario test for Heat/Cool emulation, run under Pyodide with Home Assistant stubbed.
import asyncio, sys, types
from datetime import datetime, timedelta, timezone
from enum import StrEnum

def mod(name, **kw):
    m = types.ModuleType(name); m.__dict__.update(kw); sys.modules[name] = m; return m

TZ = timezone(timedelta(hours=-5))
CLOCK = {"now": datetime(2026, 9, 23, 12, 0, tzinfo=TZ).astimezone(timezone.utc)}

class HVACMode(StrEnum):
    OFF = "off"; HEAT = "heat"; COOL = "cool"; HEAT_COOL = "heat_cool"
class HVACAction(StrEnum):
    HEATING = "heating"; COOLING = "cooling"; IDLE = "idle"

mod("homeassistant"); mod("homeassistant.components"); mod("homeassistant.helpers"); mod("homeassistant.util")
mod("homeassistant.components.climate",
    ATTR_CURRENT_TEMPERATURE="current_temperature", ATTR_HVAC_MODE="hvac_mode", ATTR_HVAC_MODES="hvac_modes",
    ATTR_HVAC_ACTION="hvac_action", ATTR_MAX_TEMP="max_temp", ATTR_MIN_TEMP="min_temp",
    ATTR_FAN_MODE="fan_mode", ATTR_FAN_MODES="fan_modes", SERVICE_SET_FAN_MODE="set_fan_mode",
    ATTR_TARGET_TEMP_HIGH="target_temp_high", ATTR_TARGET_TEMP_LOW="target_temp_low", DOMAIN="climate",
    SERVICE_SET_HVAC_MODE="set_hvac_mode", SERVICE_SET_TEMPERATURE="set_temperature",
    HVACMode=HVACMode, HVACAction=HVACAction)
mod("homeassistant.config_entries", ConfigEntry=object)
mod("homeassistant.components.sensor", SensorDeviceClass=type("SDC", (), {"BATTERY": "battery", "HUMIDITY": "humidity"}))
class _Reg:
    def async_get(self, entity_id): return None
mod("homeassistant.helpers.entity_registry", async_get=lambda hass: _Reg(), async_entries_for_device=lambda reg, dev: [])
class U: CELSIUS = "°C"; FAHRENHEIT = "°F"
mod("homeassistant.const", ATTR_ENTITY_ID="entity_id", ATTR_TEMPERATURE="temperature",
    STATE_UNAVAILABLE="unavailable", STATE_UNKNOWN="unknown", UnitOfTemperature=U,
    Platform=type("P", (), {"BINARY_SENSOR": 1, "CLIMATE": 2, "SENSOR": 3, "SWITCH": 4}))
class State:
    def __init__(self, state, attributes): self.state = state; self.attributes = dict(attributes)
class Event:
    def __init__(self, data): self.data = data
    def __class_getitem__(cls, item): return cls
mod("homeassistant.core", CALLBACK_TYPE=object, Event=Event, EventStateChangedData=dict, HomeAssistant=object,
    State=State, callback=lambda f: f)
class HomeAssistantError(Exception): pass
mod("homeassistant.exceptions", HomeAssistantError=HomeAssistantError)
class Debouncer:
    def __init__(self, hass, logger, *, cooldown, immediate, function): self.function = function
    async def async_call(self): await self.function()
    def async_shutdown(self): pass
mod("homeassistant.helpers.debounce", Debouncer=Debouncer)
mod("homeassistant.helpers.event", async_track_point_in_time=lambda *a: (lambda: None),
    async_track_state_change_event=lambda *a: (lambda: None), async_track_time_interval=lambda *a: (lambda: None),
    async_track_time_change=lambda *a, **k: (lambda: None))
class Store:
    def __init__(self, *a): pass
    async def async_load(self): return None
    def async_delay_save(self, *a): pass
    async def async_remove(self): pass
mod("homeassistant.helpers.storage", Store=Store)
mod("homeassistant.helpers.issue_registry", async_create_issue=lambda *a, **k: None, async_delete_issue=lambda *a, **k: None,
    IssueSeverity=type("S", (), {"WARNING": "warning"}))
dt = mod("homeassistant.util.dt", utcnow=lambda: CLOCK["now"], as_local=lambda d: d.astimezone(TZ),
    as_utc=lambda d: d.astimezone(timezone.utc), parse_datetime=datetime.fromisoformat,
    start_of_local_day=lambda day: datetime.combine(day, datetime.min.time(), tzinfo=TZ))
sys.modules["homeassistant.util"].dt = dt
sys.modules["homeassistant.helpers"].issue_registry = sys.modules["homeassistant.helpers.issue_registry"]
sys.modules["homeassistant.helpers"].entity_registry = sys.modules["homeassistant.helpers.entity_registry"]

pkg = mod("cs"); pkg.__path__ = []
for name in ("const", "schedule", "health", "runtime", "controller"):
    m = mod("cs." + name, __package__="cs"); exec(SOURCES[name], m.__dict__)
controller_mod = sys.modules["cs.controller"]

SRC = "climate.plain"
results = []
def check(name, cond, extra=""):
    results.append(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))

class Hass:
    def __init__(self, modes):
        self.states_map = {SRC: State("heat", {"hvac_modes": modes, "current_temperature": 70, "temperature": 60,
                                             "min_temp": 40, "max_temp": 95, "hvac_action": "idle"})}
        self.calls = []
        self.tasks = []
        self.config = types.SimpleNamespace(units=types.SimpleNamespace(temperature_unit="°F"),
                                            path=lambda n: "/" + n)
        self.states = types.SimpleNamespace(get=lambda e: self.states_map.get(e))
        self.services = types.SimpleNamespace(async_call=self._call)
        self.config_entries = types.SimpleNamespace(async_update_entry=lambda *a, **k: None)
        self.bus = types.SimpleNamespace(async_fire=lambda *a, **k: None)
        self.controller = None
    def async_create_task(self, coro, name=None):
        t = asyncio.ensure_future(coro); self.tasks.append(t); return t
    def set_source(self, state=None, **attrs):
        old = self.states_map[SRC]
        new = State(state or old.state, {**old.attributes, **attrs})
        self.states_map[SRC] = new
        self.controller._source_changed(Event({"old_state": old, "new_state": new}))
    async def _call(self, domain, service, data, blocking=False):
        self.calls.append((service, {k: v for k, v in data.items() if k != "entity_id"}))
        if service == "set_hvac_mode":
            self.set_source(data["hvac_mode"])
        elif service == "set_fan_mode":
            self.set_source(fan_mode=data["fan_mode"])
        elif service == "set_temperature":
            self.set_source(**{k: v for k, v in data.items() if k != "entity_id"})

async def settle(hass):
    for _ in range(5):
        pending = [t for t in hass.tasks if not t.done()]
        if not pending: break
        await asyncio.gather(*pending)

def advance(minutes):
    CLOCK["now"] = CLOCK["now"] + timedelta(minutes=minutes)

async def run():
    hass = Hass(["off", "heat", "cool"])
    entry = types.SimpleNamespace(entry_id="e1", title="Plain", options={"deadband": 3},
                                  data={"climate_entity": SRC, "initial_schedule": [[{"start": 0, "heat": 68, "cool": 74, "label": "Home"}] for _ in range(7)]})
    c = controller_mod.ComfortController(hass, entry)
    hass.controller = c
    await c.async_load()
    c.async_start()
    await settle(hass)
    check("no native heat_cool -> emulation available", c.can_emulate() and not c.uses_native_heat_cool())
    check("startup write sets heat 68", hass.states_map[SRC].attributes["temperature"] == 68, hass.calls)

    hass.calls.clear()
    await c.async_set_mode("heat_cool"); await settle(hass)
    check("heat_cool picked -> emulating, device stays heat", c.emulating and hass.states_map[SRC].state == "heat")
    check("proxy shows heat_cool", c.proxy_mode() == "heat_cool")

    advance(5); hass.set_source(current_temperature=74.5); await settle(hass)
    check("74.5 (< cool+1) -> no switch", hass.states_map[SRC].state == "heat")
    hass.set_source(current_temperature=75.2); await settle(hass)
    check("75.2 but within 30 min of start -> no switch", hass.states_map[SRC].state == "heat")

    advance(30); hass.calls.clear(); c._changeover_tick(None); await settle(hass)
    s = hass.states_map[SRC]
    check("after 30 min -> switched to cool", s.state == "cool", hass.calls)
    check("cool set point written (74)", s.attributes["temperature"] == 74, hass.calls)
    check("still emulating after our own switch", c.emulating)

    # Device later reports the cool set point it had stored (stale), within grace of the mode change.
    advance(1); hass.set_source(temperature=78); await settle(hass)
    check("stale stored set point after switch is not a hold", c.hold is None)

    advance(10); hass.set_source(current_temperature=66.5); await settle(hass)
    check("66.5 within 30 min of last switch -> no switch", hass.states_map[SRC].state == "cool")
    advance(25); c._changeover_tick(None); await settle(hass)
    check("after dwell, 66.5 <= heat-1 -> switched to heat", hass.states_map[SRC].state == "heat")
    check("heat set point written (68)", hass.states_map[SRC].attributes["temperature"] == 68)

    # Someone turns it off at the wall.
    advance(5); hass.set_source("off"); await settle(hass)
    check("mode changed at the wall -> emulation stops", not c.emulating and c.proxy_mode() == "off")

    # turn back on via our entity with last_active_mode = heat_cool
    check("last active mode remembered as heat_cool", c.last_active_mode == "heat_cool", c.last_active_mode)

    # Manual set point change at the wall in heat mode (well after any mode change) -> hold.
    await c.async_set_mode("heat"); await settle(hass)
    advance(5); hass.set_source(temperature=70); await settle(hass)
    check("wall set point change -> hold 70/74", c.hold is not None and (c.hold.heat, c.hold.cool) == (70, 74),
          c.hold and (c.hold.heat, c.hold.cool))

    # Native thermostat: heat_cool passes straight through.
    hass2 = Hass(["off", "heat", "cool", "heat_cool"])
    c2 = controller_mod.ComfortController(hass2, entry); hass2.controller = c2
    await c2.async_load(); c2.async_start(); await settle(hass2)
    await c2.async_set_mode("heat_cool"); await settle(hass2)
    st = hass2.states_map[SRC]
    check("native thermostat -> real heat_cool, not emulated", st.state == "heat_cool" and not c2.emulating)
    check("native heat_cool gets low/high 68/74", (st.attributes.get("target_temp_low"), st.attributes.get("target_temp_high")) == (68, 74))

    # Forced emulation on a native thermostat.
    entry3 = types.SimpleNamespace(**{**entry.__dict__, "options": {"deadband": 3, "force_heat_cool_emulation": True}})
    hass3 = Hass(["off", "heat", "cool", "heat_cool"])
    c3 = controller_mod.ComfortController(hass3, entry3); hass3.controller = c3
    await c3.async_load(); c3.async_start(); await settle(hass3)
    await c3.async_set_mode("heat_cool"); await settle(hass3)
    check("forced emulation on native thermostat", c3.emulating and hass3.states_map[SRC].state in ("heat", "cool"))


    # --- Reported case: Heat/Cool on the cooling side, room 70, raise heat to 71 right away.
    hass4 = Hass(["off", "heat", "cool"])
    hass4.states_map[SRC] = State("cool", {**hass4.states_map[SRC].attributes, "current_temperature": 72})
    c4 = controller_mod.ComfortController(hass4, entry); hass4.controller = c4
    await c4.async_load(); c4.async_start(); await settle(hass4)
    await c4.async_set_mode("heat_cool"); await settle(hass4)
    check("starts on cooling side (72 between 68 and 74, was cool)", c4.emulating and hass4.states_map[SRC].state == "cool")
    advance(2); hass4.set_source(current_temperature=70); await settle(hass4)
    check("drift to 70 alone doesn't switch (not past heat 68)", hass4.states_map[SRC].state == "cool")
    hass4.calls.clear()
    await c4.async_set_hold(heat=71, changed="heat"); await settle(hass4)
    s4 = hass4.states_map[SRC]
    check("raising heat to 71 above room 70 switches to heat immediately", s4.state == "heat", hass4.calls)
    check("heat 71 written after the switch", s4.attributes["temperature"] == 71, hass4.calls)
    check("no wasted cool write before switching", not any(sv == "set_temperature" and d.get("temperature") == 74 for sv, d in hass4.calls), hass4.calls)
    # Drift right after still respects margin + minimum time.
    advance(5); hass4.set_source(current_temperature=75.5); await settle(hass4)
    check("drift to 75.5 within 30 min of the switch -> stays heat", hass4.states_map[SRC].state == "heat")
    # A schedule change is a set point change too: new week with cool 72 while room is 75.5.
    advance(1)
    await c4.async_resume(); await settle(hass4)
    await c4.async_set_schedule([[{"start": 0, "heat": 66, "cool": 72, "label": "Home"}] for _ in range(7)]); await settle(hass4)
    check("schedule change below room temp switches to cool immediately", hass4.states_map[SRC].state == "cool")
    check("cool 72 written", hass4.states_map[SRC].attributes["temperature"] == 72)

    # --- Per-block mode and fan ("use current" = None).
    CLOCK["now"] = datetime(2026, 9, 24, 7, 0, tzinfo=TZ).astimezone(timezone.utc)
    blocks = [{"start": 0, "heat": 65, "cool": 70, "label": "Sleep"},
              {"start": 480, "heat": 68, "cool": 74, "label": "Day", "mode": "cool", "fan": "Circulation"},
              {"start": 1020, "heat": 69, "cool": 73, "label": "Eve", "fan": "Low"}]
    entry5 = types.SimpleNamespace(entry_id="e5", title="Modes", options={"deadband": 3},
                                   data={"climate_entity": SRC, "initial_schedule": [blocks for _ in range(7)]})
    h5 = Hass(["off", "heat", "cool"])
    h5.states_map[SRC] = State("heat", {**h5.states_map[SRC].attributes,
                                        "fan_modes": ["Auto low", "Low", "Circulation"], "fan_mode": "Auto low"})
    c5 = controller_mod.ComfortController(h5, entry5); h5.controller = c5
    await c5.async_load(); c5.async_start(); await settle(h5)
    src = lambda: h5.states_map[SRC]
    check("restart mid-block doesn't touch mode/fan", src().state == "heat" and src().attributes["fan_mode"] == "Auto low")
    CLOCK["now"] = datetime(2026, 9, 24, 8, 0, 1, tzinfo=TZ).astimezone(timezone.utc)
    c5._on_timer(None); await settle(h5)
    check("8:00 block sets mode Cool", src().state == "cool", src().state)
    check("8:00 block sets fan Circulation", src().attributes["fan_mode"] == "Circulation")
    check("cool set point 74 written for the new mode", src().attributes["temperature"] == 74, src().attributes.get("temperature"))
    CLOCK["now"] = datetime(2026, 9, 24, 9, 0, tzinfo=TZ).astimezone(timezone.utc)
    h5.set_source("heat"); await settle(h5)
    CLOCK["now"] = datetime(2026, 9, 24, 12, 0, tzinfo=TZ).astimezone(timezone.utc)
    c5._on_timer(None); await settle(h5)
    check("manual mode change mid-block sticks (timer without a new block)", src().state == "heat")
    CLOCK["now"] = datetime(2026, 9, 24, 17, 0, 1, tzinfo=TZ).astimezone(timezone.utc)
    c5._on_timer(None); await settle(h5)
    check("17:00 'use current' mode leaves Heat alone", src().state == "heat")
    check("17:00 block sets fan Low", src().attributes["fan_mode"] == "Low")
    CLOCK["now"] = datetime(2026, 9, 24, 17, 30, tzinfo=TZ).astimezone(timezone.utc)
    await c5.async_set_hold(heat=71, changed="heat", duration="indefinite"); await settle(h5)
    CLOCK["now"] = datetime(2026, 9, 25, 8, 0, 1, tzinfo=TZ).astimezone(timezone.utc)
    c5._on_timer(None); await settle(h5)
    check("during a hold, the next block doesn't change mode", src().state == "heat" and c5.hold is not None)
    await c5.async_resume(); await settle(h5)
    check("resume applies the current block's mode and fan", src().state == "cool" and src().attributes["fan_mode"] == "Circulation")
    edited = [[dict(b) for b in blocks] for _ in range(7)]
    for day in edited: day[1]["mode"] = "heat"
    await c5.async_set_schedule(edited); await settle(h5)
    check("saving a new mode for the running block applies it now", src().state == "heat")
    bad = [[dict(b) for b in blocks] for _ in range(7)]; bad[0][1]["fan"] = "Turbo"
    try:
        await c5.async_set_schedule(bad); check("unknown fan mode rejected", False)
    except ValueError as err:
        check("unknown fan mode rejected (" + str(err) + ")", True)
    sched = sys.modules["cs.schedule"]
    wk = sched.week_from_json(edited, 3)
    check("mode/fan survive a save round trip", wk[0][1].mode == "heat" and wk[0][1].fan == "Circulation" and wk[0][0].mode is None)
    same_but_mode = [[{"start": 0, "heat": 68, "cool": 74, "label": "X"}, {"start": 600, "heat": 68, "cool": 74, "label": "X", "mode": "cool"}] for _ in range(7)]
    wk2 = sched.week_from_json(same_but_mode, 3)
    nxt = sched.next_change(wk2, datetime(2026, 9, 24, 9, 0, tzinfo=TZ))
    check("a block that only changes the mode still counts as a change", nxt is not None and nxt[1].mode == "cool")

    # --- Runtime statistics.
    rt_mod = sys.modules["cs.runtime"]
    CLOCK["now"] = datetime(2026, 9, 24, 23, 30, tzinfo=TZ).astimezone(timezone.utc)
    tr = rt_mod.RuntimeTracker()
    tr.update(State("heat", {"hvac_action": "heating"}))
    CLOCK["now"] = datetime(2026, 9, 25, 0, 30, tzinfo=TZ).astimezone(timezone.utc)
    tr.accrue()
    check("run across midnight split 30/30 min", tr.days.get("2026-09-24", {}).get("heat") == 1800 and tr.days.get("2026-09-25", {}).get("heat") == 1800, tr.days)
    tr.update(State("heat", {"hvac_action": "idle"}))
    CLOCK["now"] = datetime(2026, 9, 25, 14, 0, tzinfo=TZ).astimezone(timezone.utc)
    tr.update(State("cool", {"hvac_action": "cooling"}))
    CLOCK["now"] = datetime(2026, 9, 25, 15, 15, tzinfo=TZ).astimezone(timezone.utc)
    tr.update(State("cool", {"hvac_action": "idle"}))
    sh, sc = tr.summary("heat"), tr.summary("cool")
    check("today heat 0.5h / cool 1.25h", sh["today"] == 0.5 and sc["today"] == 1.25, (sh, sc))
    check("yesterday heat 0.5h", sh["yesterday"] == 0.5, sh)
    check("last 7 days heat 1h", sh["last_7_days"] == 1.0, sh)
    check("idle time isn't counted", tr.days["2026-09-25"]["heat"] == 1800)
    daily = tr.daily(3)
    check("daily list oldest-first, zero-filled, today last", [d["date"] for d in daily] == ["2026-09-23", "2026-09-24", "2026-09-25"] and daily[0]["heat"] == 0 and daily[2]["cool"] == 1.25, daily)
    tr2 = rt_mod.RuntimeTracker(); tr2.load(tr.dump())
    check("save/load round trip", tr2.summary("cool")["today"] == 1.25)
    old = rt_mod.RuntimeTracker(); old.load({"days": {"2024-01-01": {"heat": 99, "cool": 0}}})
    old.update(State("heat", {"hvac_action": "heating"})); CLOCK["now"] = CLOCK["now"] + timedelta(minutes=1); old.accrue()
    check("days older than ~13 months are pruned", "2024-01-01" not in old.days)
    # Through the controller: thermostat state changes feed the tracker.
    h6 = Hass(["off", "heat", "cool"])
    c6 = controller_mod.ComfortController(h6, types.SimpleNamespace(**{**entry.__dict__, "entry_id": "e6"})); h6.controller = c6
    await c6.async_load(); c6.async_start(); await settle(h6)
    h6.set_source(hvac_action="heating"); advance(20)
    c6._changeover_tick(None)
    check("controller accrues 20 min of heating", abs(c6.runtime.summary("heat")["today"] - 0.33) < 0.01, c6.runtime.summary("heat"))
    h6.set_source(hvac_action="idle"); advance(20); c6._changeover_tick(None)
    check("stops counting when idle", abs(c6.runtime.summary("heat")["today"] - 0.33) < 0.01)

    # --- Battery / humidity sensors found on the thermostat's device.
    er_mod = sys.modules["homeassistant.helpers.entity_registry"]
    E = lambda eid, dom, odc=None, dis=None: types.SimpleNamespace(entity_id=eid, domain=dom, device_class=None, original_device_class=odc, disabled_by=dis, device_id="dev1")
    class Reg2:
        def async_get(self, eid): return E(eid, "climate") if eid == SRC else None
    er_mod.async_get = lambda hass: Reg2()
    er_mod.async_entries_for_device = lambda reg, dev: [E(SRC, "climate"), E("sensor.t_temp", "sensor", "temperature"),
        E("sensor.t_batt_old", "sensor", "battery", "user"), E("sensor.t_batt", "sensor", "battery"), E("sensor.t_hum", "sensor", "humidity")]
    h7 = Hass(["off", "heat", "cool"])
    h7.states_map["sensor.t_batt"] = State("15", {})
    c7 = controller_mod.ComfortController(h7, types.SimpleNamespace(**{**entry.__dict__, "entry_id": "e7"})); h7.controller = c7
    await c7.async_load(); c7.async_start(); await settle(h7)
    check("battery sensor found on the same device (disabled one skipped)", c7.battery_entity == "sensor.t_batt", c7.battery_entity)
    check("humidity sensor found on the same device", c7.humidity_entity == "sensor.t_hum", c7.humidity_entity)
    check("battery level read as a number", c7.battery_level() == 15.0, c7.battery_level())
    h7.states_map["sensor.t_batt"] = State("unavailable", {})
    check("unavailable battery -> None", c7.battery_level() is None)

    # --- Last finished run (for the thermostat card subtitle).
    tr3 = rt_mod.RuntimeTracker()
    tr3.update(State("heat", {"hvac_action": "heating"})); advance(15)
    check("no last run while the first run is going", tr3.last_run is None)
    tr3.update(State("heat", {"hvac_action": "idle"}))
    check("last run recorded when heating stops", tr3.last_run and tr3.last_run["kind"] == "heat" and tr3.last_run["end"] == CLOCK["now"].isoformat(), tr3.last_run)
    tr4 = rt_mod.RuntimeTracker(); tr4.load(tr3.dump())
    check("last run survives save/load", tr4.last_run == tr3.last_run)

    # --- Runtime by week and month, and like-for-like comparisons (Wed 2026-09-23).
    saved_now = CLOCK["now"]
    CLOCK["now"] = datetime(2026, 9, 23, 12, 0, tzinfo=TZ).astimezone(timezone.utc)
    tr5 = rt_mod.RuntimeTracker()
    tr5.load({"days": {
        "2026-09-21": {"heat": 3600, "cool": 0}, "2026-09-22": {"heat": 3600, "cool": 0}, "2026-09-23": {"heat": 3600, "cool": 1800},
        "2026-09-15": {"heat": 7200, "cool": 0}, "2026-09-18": {"heat": 0, "cool": 3600},
        "2026-08-10": {"heat": 3600, "cool": 0}, "2026-08-30": {"heat": 3600, "cool": 0},
    }})
    wk5, mo5, cmp5 = tr5.weekly(12), tr5.monthly(12), tr5.compare()
    check("12 weeks, Monday start, current last", len(wk5) == 12 and wk5[-1]["start"] == "2026-09-21" and wk5[-2]["start"] == "2026-09-14", wk5[-2:])
    check("week totals", wk5[-1]["heat"] == 3 and wk5[-1]["cool"] == 0.5 and wk5[-2]["heat"] == 2 and wk5[-2]["cool"] == 1, wk5[-2:])
    check("12 months, current last", len(mo5) == 12 and mo5[-1]["start"] == "2026-09-01" and mo5[-2]["start"] == "2026-08-01" and mo5[0]["start"] == "2025-10-01", [m["start"] for m in mo5])
    check("month totals", mo5[-1]["heat"] == 5 and mo5[-2]["heat"] == 2, mo5[-2:])
    check("compare week: so far vs last week to the same weekday", cmp5["week"] == {"heat": 3, "cool": 0.5, "prev_heat": 2, "prev_cool": 0, "complete": True}, cmp5["week"])
    check("compare month: so far vs last month to the same day", cmp5["month"]["heat"] == 5 and cmp5["month"]["prev_heat"] == 1, cmp5["month"])
    check("compare day: last 7 days vs the 7 before", cmp5["day"] == {"heat": 3, "cool": 1.5, "prev_heat": 2, "prev_cool": 0, "complete": True}, cmp5["day"])
    CLOCK["now"] = datetime(2026, 3, 31, 12, 0, tzinfo=TZ).astimezone(timezone.utc)
    tr6 = rt_mod.RuntimeTracker(); tr6.load({"days": {"2026-02-28": {"heat": 3600, "cool": 0}, "2026-03-01": {"heat": 1800, "cool": 0}}})
    check("Mar 31 compares to all of Feb (shorter month)", tr6.compare()["month"]["prev_heat"] == 1, tr6.compare()["month"])
    # Tracking started recently: earlier stretches are "no data", not "didn't run".
    CLOCK["now"] = datetime(2026, 9, 23, 12, 0, tzinfo=TZ).astimezone(timezone.utc)
    tr7 = rt_mod.RuntimeTracker()
    tr7.update(State("cool", {"hvac_action": "cooling"}))
    check("tracking start date set on first update", tr7.since == "2026-09-23", tr7.since)
    check("no comparison history yet", not any(v["complete"] for v in tr7.compare().values()), tr7.compare())
    tr8 = rt_mod.RuntimeTracker(); tr8.load(tr7.dump())
    check("tracking start date survives save/load", tr8.since == "2026-09-23", tr8.since)
    tr9 = rt_mod.RuntimeTracker(); tr9.load({"days": {"2026-09-14": {"heat": 0, "cool": 60}}, "since": "2026-09-14"})
    c9 = tr9.compare()
    check("complete only once tracked for the whole earlier stretch", c9["week"]["complete"] and not c9["day"]["complete"] and not c9["month"]["complete"], c9)
    CLOCK["now"] = saved_now

    # --- Label colors saved with the schedule.
    sched_mod = sys.modules["cs.schedule"]
    await c7.async_set_schedule(sched_mod.week_to_json(c7.week), {"Home": "#2e9e6b", "Unused": "#000000"})
    check("label colors saved (upper-cased, unused labels dropped)", c7.label_colors == {"Home": "#2E9E6B"}, c7.label_colors)
    await c7.async_set_schedule(sched_mod.week_to_json(c7.week))
    check("colors kept when not sent", c7.label_colors == {"Home": "#2E9E6B"}, c7.label_colors)
    try:
        await c7.async_set_schedule(sched_mod.week_to_json(c7.week), {"Home": "red"}); check("rejects a non-hex color", False)
    except ValueError:
        check("rejects a non-hex color", True)
    check("colors saved to storage", c7._data_to_save().get("label_colors") == {"Home": "#2E9E6B"})

    await run_health()


# --- Health: "not heating / not cooling" warnings, driven through the real controller.
async def health_make(temp=68, sp=70):
    hass = Hass(["off", "heat", "cool"])
    entry = types.SimpleNamespace(entry_id="h1", title="Test", options={"deadband": 3},
        data={"climate_entity": SRC, "initial_schedule": [[{"start": 0, "heat": sp, "cool": 76, "label": "Home"}] for _ in range(7)]})
    c = controller_mod.ComfortController(hass, entry); hass.controller = c
    sent = []
    c.async_send_notification = lambda t, m: sent.append(t)
    await c.async_load(); c.async_start(); await settle(hass)
    hass.set_source(current_temperature=temp, hvac_action="idle"); await settle(hass)
    return hass, c, sent

def health_step(hass, c, minutes, **attrs):
    """Let `minutes` pass with a health tick each minute, then change the thermostat."""
    for _ in range(minutes):
        advance(1); c.health._tick(None)
    if attrs: hass.set_source(**attrs)

async def health_cool(temp):
    hass, c, sent = await health_make(temp)
    hass.set_source(state="cool", hvac_action="cooling"); await settle(hass); sent.clear()
    return hass, c, sent

async def run_health():
    saved_now = CLOCK["now"]
    hass, c, sent = await health_make(68)
    hass.set_source(hvac_action="heating")
    health_step(hass, c, 10, current_temperature=69); health_step(hass, c, 10, current_temperature=70); health_step(hass, c, 2, hvac_action="idle")
    check("health: normal heat run, no alerts", sent == [], sent)

    hass, c, sent = await health_make(68)
    hass.set_source(hvac_action="heating"); health_step(hass, c, 50)
    check("health: heating 50 min flat raises no_progress", c.health.problem == "no_progress" and len(sent) == 1, sent)

    # Whole-degree sensor: one tick the wrong way before the heat arrives is a normal cycle.
    hass, c, sent = await health_make(69)
    hass.set_source(hvac_action="heating")
    health_step(hass, c, 11, current_temperature=68); health_step(hass, c, 9, current_temperature=69)
    health_step(hass, c, 10, current_temperature=70); health_step(hass, c, 1, hvac_action="idle")
    check("health: 1° dip at 11 min that recovers, no alerts", sent == [], sent)
    hass, c, sent = await health_make(69)
    hass.set_source(hvac_action="heating")
    health_step(hass, c, 6, current_temperature=68); health_step(hass, c, 14, current_temperature=69); health_step(hass, c, 10, current_temperature=70)
    check("health: 1° dip at 6 min, back by 20 min, no alerts", sent == [], sent)

    hass, c, sent = await health_cool(75)
    health_step(hass, c, 15, current_temperature=76); health_step(hass, c, 10)
    check("health: AC 1° warmer, still after 10 more min: no wrong_way yet", c.health.problem is None, c.health.problem)
    health_step(hass, c, 1)
    check("health: ...and wrong_way once it lasts longer than 10 min", c.health.problem == "wrong_way" and len(sent) == 1, sent)

    # Documented: AC holding a hot room flat this long is flagged.
    hass, c, sent = await health_cool(77)
    health_step(hass, c, 70)
    check("health: AC flat 70 min raises no_progress", c.health.problem == "no_progress", sent)

    # Clearing.
    hass, c, sent = await health_make(68)
    hass.set_source(hvac_action="heating"); health_step(hass, c, 50); health_step(hass, c, 1, hvac_action="idle")
    health_step(hass, c, 360)
    check("health: idle below the set point keeps the warning", c.health.problem == "no_progress", c.health.problem)
    hass.set_source(state="off"); await settle(hass)
    check("health: turning the system off clears it", c.health.problem is None and len(sent) == 2, sent)

    hass, c, sent = await health_make(68)
    hass.set_source(hvac_action="heating"); health_step(hass, c, 50); health_step(hass, c, 5, hvac_action="idle")
    health_step(hass, c, 30, current_temperature=70)
    check("health: idle with the room at the set point clears it", c.health.problem is None, c.health.problem)

    hass, c, sent = await health_make(69.5)
    hass.set_source(hvac_action="heating"); health_step(hass, c, 50); health_step(hass, c, 5, hvac_action="idle")
    hass.set_source(hvac_action="heating"); health_step(hass, c, 8, current_temperature=70.0); health_step(hass, c, 5, hvac_action="idle")
    check("health: next run reaches the set point with +0.5, cleared", c.health.problem is None and sent[-1].endswith("back to normal"), sent)

    hass, c, sent = await health_make(68)
    hass.set_source(hvac_action="heating"); health_step(hass, c, 50)
    health_step(hass, c, 0, current_temperature=69.5)
    check("health: progress during the run clears it", c.health.problem is None, c.health.problem)

    # A thermostat that blips to idle between updates is still one run.
    hass, c, sent = await health_make(68)
    for _ in range(3):
        hass.set_source(hvac_action="heating"); health_step(hass, c, 30); hass.set_source(hvac_action="idle"); health_step(hass, c, 1)
    check("health: 90 min of heating split by 1-min idle blips raises", c.health.problem == "no_progress", c.health.problem)
    hass, c, sent = await health_make(68)
    hass.set_source(hvac_action="heating"); health_step(hass, c, 30, hvac_action="idle")
    health_step(hass, c, 3, hvac_action="heating"); health_step(hass, c, 30)
    check("health: an idle gap over 2 min starts a new run", c.health.problem is None and c.health.run_minutes() == 30, c.health.as_dict())
    CLOCK["now"] = saved_now
