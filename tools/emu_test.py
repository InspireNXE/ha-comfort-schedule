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
    ATTR_TARGET_TEMP_HIGH="target_temp_high", ATTR_TARGET_TEMP_LOW="target_temp_low", ATTR_TARGET_TEMP_STEP="target_temp_step", DOMAIN="climate",
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
    Platform=type("P", (), {"BINARY_SENSOR": 1, "BUTTON": 5, "CLIMATE": 2, "SENSOR": 3, "SWITCH": 4}))
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
ISSUES = {}
mod("homeassistant.helpers.issue_registry", async_create_issue=lambda hass, dom, iid, **k: ISSUES.__setitem__(iid, k),
    async_delete_issue=lambda hass, dom, iid: ISSUES.pop(iid, None),
    IssueSeverity=type("S", (), {"WARNING": "warning"}))
dt = mod("homeassistant.util.dt", utcnow=lambda: CLOCK["now"], as_local=lambda d: d.astimezone(TZ),
    as_utc=lambda d: d.astimezone(timezone.utc), parse_datetime=datetime.fromisoformat,
    start_of_local_day=lambda day: datetime.combine(day, datetime.min.time(), tzinfo=TZ),
    get_default_time_zone=lambda: TZ)
sys.modules["homeassistant.util"].dt = dt
sys.modules["homeassistant.helpers"].issue_registry = sys.modules["homeassistant.helpers.issue_registry"]
sys.modules["homeassistant.helpers"].entity_registry = sys.modules["homeassistant.helpers.entity_registry"]

pkg = mod("cs"); pkg.__path__ = []
for name in ("const", "schedule", "health", "runtime", "learning", "suggestions", "controller"):
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
                                            path=lambda n: "/" + n, currency="USD")
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
        if data.get("entity_id", SRC) != SRC:
            # Another thermostat (a follower): record it and update its state, nothing listens.
            eid = data["entity_id"]
            self.calls.append((service, {k: v for k, v in data.items()}))
            old = self.states_map[eid]
            if service == "set_hvac_mode":
                self.states_map[eid] = State(data["hvac_mode"], old.attributes)
            elif service == "set_temperature":
                self.states_map[eid] = State(old.state, {**old.attributes, **{k: v for k, v in data.items() if k != "entity_id"}})
            return
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
    check("compare week: so far vs last week to the same weekday", cmp5["week"] == {"heat": 3, "cool": 0.5, "prev_heat": 2, "prev_cool": 0, "hdd": 0, "cdd": 0, "prev_hdd": 0, "prev_cdd": 0, "complete": True}, cmp5["week"])
    check("compare month: so far vs last month to the same day", cmp5["month"]["heat"] == 5 and cmp5["month"]["prev_heat"] == 1, cmp5["month"])
    check("compare day: last 7 days vs the 7 before", cmp5["day"] == {"heat": 3, "cool": 1.5, "prev_heat": 2, "prev_cool": 0, "hdd": 0, "cdd": 0, "prev_hdd": 0, "prev_cdd": 0, "complete": True}, cmp5["day"])
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
    await run_features()


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
    # Range-capable thermostats report target_temp_low/high as None in Heat.
    hass, c, sent = await health_make(69.5)
    hass.set_source(target_temp_low=None, target_temp_high=None); await settle(hass)
    hass.set_source(hvac_action="heating")
    health_step(hass, c, 20, current_temperature=70.0); health_step(hass, c, 30, current_temperature=70.3)
    check("health: low/high None in Heat still reads the set point", c.health.problem is None and c.health.heat_target == 70,
          (c.health.heat_target, c.health.problem))
    CLOCK["now"] = saved_now


# --- Features: vacation, presence, filter, outdoor, followers, early start, slowdown, suggestions.
def at(y, mo, d, h, mi=0, s=0):
    CLOCK["now"] = datetime(y, mo, d, h, mi, s, tzinfo=TZ).astimezone(timezone.utc)

async def feature_make(options=None, schedule=None, modes=("off", "heat", "cool"), states=None, entry_id="f1"):
    hass = Hass(list(modes))
    hass.states_map.update(states or {})
    day = schedule or [{"start": 0, "heat": 68, "cool": 74, "label": "Home"}]
    entry = types.SimpleNamespace(entry_id=entry_id, title="Feat", options={"deadband": 3, **(options or {})},
                                  data={"climate_entity": SRC, "initial_schedule": [day for _ in range(7)]})
    c = controller_mod.ComfortController(hass, entry); hass.controller = c
    sent = []
    c.async_send_notification = lambda t, m: sent.append(t)
    await c.async_load(); c.async_start(); await settle(hass)
    return hass, c, sent

def temp_of(hass, eid=SRC):
    return hass.states_map[eid].attributes.get("temperature")

async def run_features():
    saved_now = CLOCK["now"]

    # Vacation.
    at(2026, 9, 23, 12)
    hass, c, sent = await feature_make()
    await c.async_set_hold(heat=70, changed="heat"); await settle(hass)
    end = CLOCK["now"] + timedelta(days=3)
    await c.async_set_vacation(end, 60, 82); await settle(hass)
    check("vacation now: hold cleared, 60 written", c.hold is None and c.status() == "vacation" and temp_of(hass) == 60, (c.status(), temp_of(hass)))
    await c.async_set_hold(heat=64, changed="heat"); await settle(hass)
    check("a manual change during a vacation is a hold on top", c.status() == "hold" and temp_of(hass) == 64)
    await c.async_resume(); await settle(hass)
    check("resume goes back to the vacation, not the schedule", c.status() == "vacation" and temp_of(hass) == 60, c.status())
    await c.async_pause(); await settle(hass)
    check("vacation applies while the schedule is paused", c.desired() == (60, 82, "vacation"), c.desired())
    await c.async_resume(); await settle(hass)
    saved = c._data_to_save()["vacation"]
    check("vacation saved", saved and saved["heat"] == 60 and saved["end"] == end.isoformat(), saved)
    hass2, c2, _ = await feature_make(entry_id="f1b")
    async def loader(): return {"vacation": saved}
    c2._store.async_load = loader; await c2.async_load()
    check("vacation survives a restart", c2.vacation_active() and c2.desired()[2] == "vacation")
    CLOCK["now"] = end + timedelta(seconds=1); c._on_timer(None); await settle(hass)
    check("vacation over: back to the schedule", c.vacation is None and c.status() == "schedule" and temp_of(hass) == 68, (c.status(), temp_of(hass)))
    # Starting later, with a naive (local) time.
    at(2026, 9, 23, 12)
    await c.async_set_hold(heat=71, changed="heat", duration="indefinite"); await settle(hass)
    await c.async_set_vacation(datetime(2026, 9, 26, 15, 0), 58, 85, datetime(2026, 9, 24, 8, 0)); await settle(hass)
    check("future vacation: not yet, hold stays", c.status() == "hold" and c.vacation["start"] == datetime(2026, 9, 24, 8, 0, tzinfo=TZ).astimezone(timezone.utc), c.vacation)
    at(2026, 9, 24, 8, 0, 1); c._on_timer(None); await settle(hass)
    check("vacation starts: earlier hold ends, 58 written", c.hold is None and c.status() == "vacation" and temp_of(hass) == 58, (c.status(), temp_of(hass)))
    await c.async_cancel_vacation(); await settle(hass)
    check("cancel vacation: back to the schedule", c.status() == "schedule" and temp_of(hass) == 68)
    try:
        await c.async_set_vacation(CLOCK["now"] - timedelta(hours=1), 60, 80); check("vacation ending in the past rejected", False)
    except ValueError:
        check("vacation ending in the past rejected", True)
    await c.async_set_vacation(CLOCK["now"] + timedelta(days=1), 70, 71); await settle(hass)
    check("vacation set points kept a deadband apart", c.vacation["cool"] - c.vacation["heat"] >= 3, c.vacation)

    # Presence.
    at(2026, 9, 23, 12)
    pres = {"presence_entities": ["person.a", "person.b"], "away_heat": 62, "away_cool": 80, "away_delay": 10}
    people = {"person.a": State("home", {}), "person.b": State("not_home", {})}
    hass, c, sent = await feature_make(pres, states=people)
    check("someone home: schedule", not c.away and temp_of(hass) == 68)
    hass.states_map["person.a"] = State("not_home", {}); c._presence_changed(None); await settle(hass)
    check("everyone left: waits for the delay", not c.away and c._away_unsub is not None)
    c._away_timer(None); await settle(hass)
    check("after the delay: away set points (62)", c.away and c.status() == "away" and temp_of(hass) == 62, (c.status(), temp_of(hass)))
    hass.states_map["person.b"] = State("home", {}); c._presence_changed(None); await settle(hass)
    check("someone back: schedule again", not c.away and temp_of(hass) == 68)
    hass.states_map["person.b"] = State("not_home", {}); c._presence_changed(None)
    hass.states_map["person.a"] = State("home", {}); c._presence_changed(None); c._away_timer(None); await settle(hass)
    check("back before the delay ran out: never away", not c.away)
    hass.states_map["person.a"] = State("unavailable", {}); hass.states_map["person.b"] = State("unknown", {})
    c._presence_changed(None); await settle(hass)
    check("trackers unavailable count as home", not c.away and c._away_unsub is None)
    gone = {"person.a": State("not_home", {}), "zone.home": State("0", {})}
    hass, c, _ = await feature_make({**pres, "presence_entities": ["person.a", "zone.home"]}, states=gone, entry_id="f2")
    check("empty at a first start: the delay starts now", not c.away and c._away_unsub is not None and c._left_at == CLOCK["now"])
    c._away_timer(None); await settle(hass)
    check("...then away", c.away and temp_of(hass) == 62)
    hass.states_map["zone.home"] = State("2", {}); c._presence_changed(None); await settle(hass)
    check("zone with people in it counts as home", not c.away)
    hass, c, _ = await feature_make({"presence_entities": ["person.a"]}, states=gone, entry_id="f3")
    check("no away set points: presence does nothing", not c.away and c.away_setpoints is None)
    hass, c, _ = await feature_make(pres, states={"person.a": State("not_home", {}), "person.b": State("not_home", {})}, entry_id="f4")
    await c.async_set_hold(heat=66, changed="heat"); await settle(hass)
    check("a hold wins over away", c.status() == "hold" and temp_of(hass) == 66)

    # Filter reminder, outdoor degree-days, cost.
    at(2026, 9, 24, 8)
    hass, c, sent = await feature_make({"filter_hours": 2, "outdoor_entity": "sensor.out", "heat_cost": 0.9},
                                       states={"sensor.out": State("45", {})})
    check("filter count starts at setup", c.filter_since == "2026-09-24" and c.filter_used_hours() == 0)
    hass.set_source(hvac_action="heating")
    for _ in range(119):
        advance(1); c._changeover_tick(None)
    filter_sent = lambda: [t for t in sent if "filter" in t]
    check("filter not due at 1h59", not c.filter_due() and not filter_sent(), sent)
    for _ in range(2):
        advance(1); c._changeover_tick(None)
    check("filter due at 2h: issue and one notification", c.filter_due() and "filter_due_f1" in ISSUES and len(filter_sent()) == 1, (c.filter_used_hours(), sent))
    advance(1); c._changeover_tick(None)
    check("filter: notified once", len(filter_sent()) == 1)
    await c.async_filter_changed()
    check("filter changed: count restarts, issue gone", c.filter_used_hours() == 0 and not c.filter_due() and "filter_due_f1" not in ISSUES, c.filter_used_hours())
    hdd = c.runtime.days["2026-09-24"].get("hdd", 0)
    check("outdoor 45°F for 2h2m counts toward heating degree-days", abs(hdd - 20 * 122 / 1440) < 0.01, hdd)
    check("compare reports degree-days", c.runtime.compare()["day"]["hdd"] == round(hdd, 1), c.runtime.compare()["day"])
    check("costs: heat rate set, cool 0, currency", c.costs == {"heat": 0.9, "cool": 0.0, "currency": "USD"}, c.costs)
    hass.states_map["sensor.out"] = State("unavailable", {})
    check("unavailable outdoor sensor -> None", c.outdoor_temp() is None)
    hass, c, _ = await feature_make({"outdoor_entity": "weather.home"}, states={"weather.home": State("sunny", {"temperature": 50})}, entry_id="f5")
    check("weather entity: uses its temperature", c.outdoor_temp() == 50 and c.costs is None)

    # More thermostats on one schedule.
    at(2026, 9, 23, 12)
    f2 = State("heat", {"hvac_modes": ["off", "heat", "cool"], "temperature": 60, "min_temp": 45, "max_temp": 90})
    hass, c, _ = await feature_make({"follower_entities": ["climate.f2", SRC]}, states={"climate.f2": f2})
    check("followers exclude the main thermostat", c.followers == ["climate.f2"])
    check("follower gets the heat set point", temp_of(hass, "climate.f2") == 68, hass.states_map["climate.f2"].attributes)
    await c.async_set_mode("cool"); await settle(hass)
    check("follower follows the mode, gets cool 74", hass.states_map["climate.f2"].state == "cool" and temp_of(hass, "climate.f2") == 74)
    hass.calls.clear()
    for _ in range(5):
        hass.states_map["climate.f2"] = State("cool", {**hass.states_map["climate.f2"].attributes, "temperature": 79})
        await c._async_write_followers(68, 74)
    n = sum(1 for sv, d in hass.calls if sv == "set_temperature" and d.get("entity_id") == "climate.f2")
    check("a follower that won't take it is sent the same thing at most 3 times", n == 3, n)
    check("writes are logged for diagnostics", any(w["entity_id"] == "climate.f2" for w in c.write_log))

    # Early start, learned from runs.
    sched = [{"start": 0, "heat": 62, "cool": 80, "label": "Sleep"}, {"start": 390, "heat": 68, "cool": 76, "label": "Wake"}]
    at(2026, 9, 24, 5, 0)
    hass, c, _ = await feature_make({"early_start_max": 90}, schedule=sched)
    hass.set_source(current_temperature=65); await settle(hass)
    hass.set_source(hvac_action="heating"); health_step(hass, c, 20, current_temperature=66.5, hvac_action="idle"); health_step(hass, c, 3)
    check("a finished run leaves a sample (1.5° in 20 min = 4.5°/h)", len(c.learning.samples) == 1 and c.learning.samples[0]["rate"] == 4.5, c.learning.samples)
    c.learning.samples = [{"t": (CLOCK["now"] - timedelta(days=i)).isoformat(), "kind": "heat", "rate": 4.0, "out": None} for i in range(5, 0, -1)]
    hass.set_source(current_temperature=65)
    at(2026, 9, 24, 5, 30); c._changeover_tick(None); await settle(hass)
    check("60 min out, 3° at 4°/h needs 45 min: not yet", c.status() == "schedule" and temp_of(hass) == 62, (c.status(), temp_of(hass)))
    at(2026, 9, 24, 5, 46); c._changeover_tick(None); await settle(hass)
    check("44 min out: Wake starts early (68)", c.status() == "early" and temp_of(hass) == 68 and c.early_info()["label"] == "Wake", (c.status(), temp_of(hass)))
    hass.set_source(current_temperature=67); at(2026, 9, 24, 6, 0); c._changeover_tick(None)
    check("stays started as the room warms", c.status() == "early")
    at(2026, 9, 24, 6, 30, 1); c._on_timer(None); await settle(hass)
    check("block begins: plain schedule again", c.status() == "schedule" and c.early_info() is None and temp_of(hass) == 68)
    hass, c, _ = await feature_make({"early_start_max": 0}, schedule=sched, entry_id="f6")
    at(2026, 9, 25, 5, 0)
    c.learning.samples = [{"t": (CLOCK["now"] - timedelta(days=i)).isoformat(), "kind": "heat", "rate": 4.0, "out": None} for i in range(5, 0, -1)]
    hass.set_source(current_temperature=60); at(2026, 9, 25, 6, 20); c._changeover_tick(None)
    check("early start off: nothing early", c.status() == "schedule")
    check("not enough samples: no lead time", c.learning.lead_minutes("cool", 3, None, 90) is None)

    # Slowdown warning, compared in similar weather.
    at(2026, 9, 24, 12)
    hass, c, sent = await feature_make(entry_id="f7")
    now = CLOCK["now"]
    def samples(days, rate, out):
        return [{"t": (now - timedelta(days=d, hours=1)).isoformat(), "kind": "heat", "rate": rate, "out": out} for d in days]
    c.learning.samples = samples(range(20, 30), 4.0, 40) + samples(range(0, 10), 2.5, 40)
    c._check_trend()
    check("heating 38% slower in the same weather: warning", "hvac_slowdown_f7_heat" in ISSUES and c.trend["heat"]["drop"] == 38 and len(sent) == 1, (c.trend, sent))
    c._check_trend()
    check("...notified once", len(sent) == 1)
    c.learning.samples = samples(range(20, 30), 4.0, 40) + samples(range(0, 10), 3.8, 40)
    c._check_trend()
    check("runs back to normal: warning cleared", "hvac_slowdown_f7_heat" not in ISSUES and not c.trend)
    c.learning.samples = samples(range(20, 30), 4.0, 45) + samples(range(0, 10), 2.5, 15)
    c._check_trend()
    check("slower in a cold snap isn't a slowdown", not c.trend, c.trend)
    c.learning.samples = samples(range(20, 30), 4.0, 40) + samples(range(0, 4), 2.5, 40)
    c._check_trend()
    check("too few recent runs: no verdict", not c.trend)
    c.learning.samples = samples(range(20, 30), 4.0, 40) + samples(range(0, 10), 2.5, 40)
    c._check_trend()
    at(2026, 10, 9, 0, 0, 1); c._midnight(None)
    check("no runs for two weeks: the warning clears at midnight", "hvac_slowdown_f7_heat" not in ISSUES and not c.trend, c.trend)

    # Suggestions from repeated manual changes (Wed, Thu, Fri).
    hass, c, _ = await feature_make(entry_id="f8")
    for day in (23, 24, 25):
        at(2026, 9, day, 19); await c.async_set_hold(heat=70, changed="heat"); await c.async_resume()
    sug = c.current_suggestions()
    check("same change on 3 weekdays: set point suggestion", len(sug) == 1 and sug[0]["kind"] == "setpoint" and sug[0]["value"] == 70 and sug[0]["days"] == "weekdays", sug)
    await c.async_apply_suggestion(sug[0]["id"]); await settle(hass)
    check("applied: weekdays Home heat 70, weekend still 68", c.week[0][0].heat == 70 and c.week[4][0].heat == 70 and c.week[5][0].heat == 68)
    check("applied suggestion is gone", c.current_suggestions() == [], c.current_suggestions())
    try:
        await c.async_apply_suggestion("nope"); check("unknown suggestion rejected", False)
    except ValueError:
        check("unknown suggestion rejected", True)
    hass, c, _ = await feature_make(schedule=sched, entry_id="f9")
    for day in (23, 24, 25):
        at(2026, 9, day, 6, 0); await c.async_set_hold(heat=68, changed="heat"); await c.async_resume()
    sug = c.current_suggestions()
    check("changing to Wake's set point 30 min early, 3 times: start suggestion only", [s["kind"] for s in sug] == ["start"] and sug[0]["start"] == 360, sug)
    hass2, c2, _ = await feature_make(schedule=sched, entry_id="f10")
    c2.suggestions.load(c.suggestions.dump())
    await c2.async_dismiss_suggestion(sug[0]["id"])
    check("dismissed suggestion stays hidden", c2.current_suggestions() == [])
    await c.async_apply_suggestion(sug[0]["id"]); await settle(hass)
    check("applied: Wake starts at 6:00 on weekdays only", c.week[0][1].start == 360 and c.week[6][1].start == 390, [d[1].start for d in c.week])
    hass, c, _ = await feature_make({"suggestions": False}, entry_id="f11")
    for day in (23, 24, 25):
        at(2026, 9, day, 19); await c.async_set_hold(heat=70, changed="heat"); await c.async_resume()
    check("suggestions off: nothing logged or offered", c.current_suggestions() == [] and c.suggestions.log == [])

    # One tracker unavailable while the other is away: still counts as someone home.
    at(2026, 9, 23, 12)
    hass, c, _ = await feature_make(pres, states={"person.a": State("home", {}), "person.b": State("home", {})}, entry_id="f12")
    hass.states_map["person.b"] = State("unavailable", {}); c._presence_changed(None)
    hass.states_map["person.a"] = State("not_home", {}); c._presence_changed(None); await settle(hass)
    check("one tracker unavailable, the other away: not away", not c.away and c._away_unsub is None)
    hass.states_map.pop("person.b"); c._presence_changed(None)
    check("a removed tracker has no say: away after the delay", c._away_unsub is not None)

    # A block that turns the system off doesn't do it during a vacation.
    at(2026, 9, 23, 12)
    sched = [{"start": 0, "heat": 68, "cool": 74, "label": "Home"}, {"start": 780, "heat": 68, "cool": 74, "label": "Out", "mode": "off"}]
    hass, c, _ = await feature_make(schedule=sched, entry_id="f13")
    await c.async_set_vacation(CLOCK["now"] + timedelta(days=3), 50, 85); await settle(hass)
    at(2026, 9, 23, 13); c._on_timer(None); await settle(hass)
    check("vacation: a block's mode isn't applied", hass.states_map[SRC].state == "heat" and c.status() == "vacation" and temp_of(hass) == 50,
          (hass.states_map[SRC].state, c.status(), temp_of(hass)))

    # A vacation that started while Home Assistant was down ends an earlier hold.
    at(2026, 9, 23, 12)
    hass, c, _ = await feature_make(entry_id="f14")
    await c.async_set_hold(heat=71, changed="heat", duration="indefinite")
    await c.async_set_vacation(CLOCK["now"] + timedelta(days=3), 58, 85, CLOCK["now"] + timedelta(days=1))
    saved = c._data_to_save()
    at(2026, 9, 24, 15)
    hass2, c2, _ = await feature_make(entry_id="f15")
    async def loader2(): return saved
    c2._store.async_load = loader2; await c2.async_load()
    check("restart after the vacation started: the earlier hold is gone", c2.hold is None and c2.status() == "vacation", c2.status())

    # A mode change at the wall reaches the followers too.
    at(2026, 9, 23, 12)
    f2 = State("heat", {"hvac_modes": ["off", "heat", "cool"], "temperature": 60, "min_temp": 45, "max_temp": 90})
    hass, c, _ = await feature_make({"follower_entities": ["climate.f2"]}, states={"climate.f2": f2}, entry_id="f16")
    hass.set_source("off"); await settle(hass)
    check("wall mode change: follower follows", hass.states_map["climate.f2"].state == "off", hass.states_map["climate.f2"].state)

    # An outdoor sensor in the other unit is converted.
    hass, c, _ = await feature_make({"outdoor_entity": "sensor.out"}, states={"sensor.out": State("10", {"unit_of_measurement": "°C"})}, entry_id="f17")
    check("outdoor sensor in °C on a °F system: 50°F", c.outdoor_temp() == 50, c.outdoor_temp())
    check("°F system: similar weather is within 10°", c.learning.near == 10.0)
    hass, c, _ = await feature_make({"outdoor_entity": "sensor.out", "similar_weather": 6, "degree_day_base": 60},
                                    states={"sensor.out": State("50", {"unit_of_measurement": "°F"})}, entry_id="f22")
    check("similar weather from the options", c.learning.near == 6 and c.similar_weather == 6)
    check("degree-day base from the options", c.degree_day_base == 60)

    # Offsets for the other thermostats.
    at(2026, 9, 23, 12)
    f2 = State("heat", {"hvac_modes": ["off", "heat", "cool"], "temperature": 60, "min_temp": 45, "max_temp": 90})
    f3 = State("heat", {"hvac_modes": ["off", "heat", "cool"], "temperature": 60})
    hass, c, _ = await feature_make({"follower_entities": ["climate.f2", "climate.f3"], "follower_offsets": {"climate.f2": -2, "climate.f3": 1.5}},
                                    states={"climate.f2": f2, "climate.f3": f3}, entry_id="f23")
    check("follower offset -2: heats to 66", temp_of(hass, "climate.f2") == 66, temp_of(hass, "climate.f2"))
    check("follower without limits, offset +1.5: heats to 69.5", temp_of(hass, "climate.f3") == 69.5, temp_of(hass, "climate.f3"))
    check("main thermostat unaffected", temp_of(hass) == 68)

    # Presence across a reload: still away, or the same countdown.
    at(2026, 9, 23, 12)
    gone = {"person.a": State("not_home", {}), "person.b": State("not_home", {})}
    async def reload_with(data, entry_id):
        h, cc, _ = await feature_make(pres, states={"person.a": State("home", {}), "person.b": State("home", {})}, entry_id=entry_id)
        h.states_map.update(gone)
        async def loader3(): return data
        cc._store.async_load = loader3; await cc.async_load(); cc._update_presence(starting=True); await settle(h)
        return h, cc
    hass, c = await reload_with({"away": True}, "f18")
    check("reload while away: still away", c.away and temp_of(hass) == 62)
    left = (CLOCK["now"] - timedelta(minutes=4)).isoformat()
    hass, c = await reload_with({"left_at": left}, "f19")
    check("reload 4 min into a 10 min delay: 6 min to go", not c.away and c._left_at == CLOCK["now"] - timedelta(minutes=4))
    hass, c = await reload_with({"left_at": (CLOCK["now"] - timedelta(minutes=12)).isoformat()}, "f20")
    check("reload after the delay ran out: away", c.away)
    hass, c, _ = await feature_make(pres, states={"person.a": State("home", {}), "person.b": State("home", {})}, entry_id="f21")
    hass.states_map.update(gone); c._presence_changed(None)
    check("countdown saved", c._data_to_save()["left_at"] == CLOCK["now"].isoformat())
    hass.states_map["person.a"] = State("home", {}); c._presence_changed(None)
    check("back before the delay: countdown cleared from storage", c._data_to_save()["left_at"] is None and not c._data_to_save()["away"])

    # Coming home in a block that began while Away: its mode applies.
    at(2026, 9, 23, 6, 0)
    away_opts = {"presence_entities": ["person.a"], "away_heat": 62, "away_cool": 80, "away_delay": 0}
    sched = [{"start": 0, "heat": 62, "cool": 80, "label": "Night", "mode": "off"},
             {"start": 420, "heat": 68, "cool": 74, "label": "Morning", "mode": "heat"}]
    hass, c, _ = await feature_make(away_opts, schedule=sched, states={"person.a": State("home", {})}, entry_id="g1")
    hass.set_source("off"); await settle(hass)
    hass.states_map["person.a"] = State("not_home", {}); c._presence_changed(None); await settle(hass)
    at(2026, 9, 23, 7, 0, 1); c._on_timer(None); await settle(hass)
    check("Morning (Heat) began while Away: system stays off", hass.states_map[SRC].state == "off")
    hass.states_map["person.a"] = State("home", {}); c._presence_changed(None); await settle(hass)
    check("home again: Morning's Heat applies", hass.states_map[SRC].state == "heat" and temp_of(hass) == 68,
          (hass.states_map[SRC].state, temp_of(hass)))

    # Editing the block that's starting early.
    sched = [{"start": 0, "heat": 62, "cool": 80, "label": "Sleep"}, {"start": 390, "heat": 68, "cool": 76, "label": "Wake"}]
    at(2026, 9, 24, 5, 0)
    hass, c, _ = await feature_make({"early_start_max": 90}, schedule=sched, entry_id="g2")
    c.learning.samples = [{"t": (CLOCK["now"] - timedelta(days=i)).isoformat(), "kind": "heat", "rate": 4.0, "out": None} for i in range(5, 0, -1)]
    hass.set_source(current_temperature=65)
    at(2026, 9, 24, 5, 46); c._changeover_tick(None); await settle(hass)
    check("early start of Wake at 68", c.status() == "early" and temp_of(hass) == 68, (c.status(), temp_of(hass)))
    sched[1] = {**sched[1], "heat": 70}
    await c.async_set_schedule([sched] * 7); await settle(hass)
    check("Wake edited to 70 during its early start: 70 written", c.status() == "early" and temp_of(hass) == 70,
          (c.status(), temp_of(hass)))

    # Set points that aren't numbers.
    at(2026, 9, 23, 12)
    hass, c, _ = await feature_make(entry_id="g3")
    for name, call in (("hold", c.async_set_hold(heat=float("nan"), changed="heat")),
                       ("vacation", c.async_set_vacation(CLOCK["now"] + timedelta(days=1), float("inf"), 80)),
                       ("schedule", c.async_set_schedule([[{"start": 0, "heat": "nan", "cool": 74, "label": "H"}]] * 7))):
        try:
            await call
            check(f"NaN {name} rejected", False)
        except ValueError:
            check(f"NaN {name} rejected", c.hold is None and c.vacation is None and temp_of(hass) == 68)
    async def bad_store():
        return {"hold": {"heat": None, "cool": 74, "until": None},
                "vacation": {"start": CLOCK["now"].isoformat(), "end": (CLOCK["now"] + timedelta(days=1)).isoformat(), "heat": None, "cool": 80}}
    hass, c, _ = await feature_make(entry_id="g4")
    c._store.async_load = bad_store
    try:
        await c.async_load()
        check("saved hold/vacation with a null set point is dropped, not a crash", c.hold is None and c.vacation is None)
    except Exception as err:  # noqa: BLE001
        check("saved hold/vacation with a null set point is dropped, not a crash", False, repr(err))

    # A saved hold after the deadband was widened.
    hass, c, _ = await feature_make({"deadband": 5}, entry_id="g5")
    async def narrow_hold():
        return {"hold": {"heat": 70, "cool": 72, "until": None}}
    c._store.async_load = narrow_hold; await c.async_load()
    check("saved hold meets the wider deadband", c.hold is not None and c.hold.cool - c.hold.heat >= 5, c.hold)

    # Heat/Cool emulation: an edit elsewhere in the week isn't a set point change.
    at(2026, 9, 23, 12)
    hass, c, _ = await feature_make(entry_id="g6")
    await c.async_set_mode("heat_cool"); await settle(hass)
    advance(2); hass.set_source(current_temperature=74.3); await settle(hass)
    check("74.3 against cool 74: still on Heat (inside the margin)", hass.states_map[SRC].state == "heat")
    week = [[{"start": 0, "heat": 68, "cool": 74, "label": "Home"}]] * 5 + [[{"start": 0, "heat": 66, "cool": 74, "label": "Home"}]] * 2
    await c.async_set_schedule(week); await settle(hass)
    check("editing the weekend doesn't switch to Cool", hass.states_map[SRC].state == "heat", hass.states_map[SRC].state)

    # A whole-degree thermostat rounds 68.5 to 69: that's our write, not a person at the wall.
    at(2026, 9, 23, 12)
    hass, c, _ = await feature_make(schedule=[{"start": 0, "heat": 68, "cool": 74, "label": "Home"},
                                              {"start": 780, "heat": 68.5, "cool": 74, "label": "Aft"}], entry_id="g7")
    orig = hass.services.async_call
    async def rounding(domain, service, data, blocking=False):
        if service == "set_temperature" and "temperature" in data:
            data = {**data, "temperature": float(round(data["temperature"] + 0.001))}
        await orig(domain, service, data, blocking)
    hass.services.async_call = rounding
    at(2026, 9, 23, 13, 0, 1); c._on_timer(None); await settle(hass)
    check("thermostat rounding 68.5 to 69 isn't a hold", c.hold is None and temp_of(hass) == 69, (c.hold, temp_of(hass)))
    hass.services.async_call = orig

    # A vacation that ends with the schedule paused puts the thermostat back where it was.
    at(2026, 9, 23, 12)
    hass, c, _ = await feature_make(entry_id="g8")
    await c.async_set_vacation(CLOCK["now"] + timedelta(days=1), 58, 85); await settle(hass)
    await c.async_pause(); await settle(hass)
    check("paused during a vacation: the vacation still runs", c.status() == "vacation" and temp_of(hass) == 58)
    CLOCK["now"] = CLOCK["now"] + timedelta(days=1, seconds=1); c._on_timer(None); await settle(hass)
    check("vacation over while paused: back to the 68 from before, still paused",
          c.status() == "paused" and temp_of(hass) == 68, (c.status(), temp_of(hass)))
    hass.set_source(temperature=71); await settle(hass)
    check("then left alone: a change at the wall stays", c.hold is None and temp_of(hass) == 71)

    at(2026, 9, 23, 12)
    hass, c, _ = await feature_make(entry_id="g9")
    await c.async_pause(); await settle(hass)
    hass.set_source(temperature=64); await settle(hass)
    await c.async_set_vacation(CLOCK["now"] + timedelta(days=1), 58, 85, CLOCK["now"] + timedelta(hours=1))
    at(2026, 9, 23, 13, 0, 1); c._on_timer(None); await settle(hass)
    check("paused, then a vacation starts later: vacation set points", temp_of(hass) == 58)
    check("what to go back to is saved", c._data_to_save()["vacation"]["before"] == (64.0, 74.0), c._data_to_save()["vacation"])
    await c.async_cancel_vacation(); await settle(hass)
    check("vacation cancelled while paused: back to 64", c.status() == "paused" and temp_of(hass) == 64, temp_of(hass))

    # Defaults in °C are about half the °F ones.
    hass, c, _ = await feature_make(entry_id="g10")
    hass.config.units.temperature_unit = "°C"
    check("°C defaults: progress 0.5, wrong way 0.4, Heat/Cool margin 0.5",
          (c.min_progress, c.wrong_way, c.changeover_margin) == (0.5, 0.4, 0.5),
          (c.min_progress, c.wrong_way, c.changeover_margin))
    hass.config.units.temperature_unit = "°F"

    # Another Comfort thermostat is never a follower.
    er_mod = sys.modules["homeassistant.helpers.entity_registry"]
    saved_get = er_mod.async_get
    class CReg:
        def async_get(self, eid):
            return types.SimpleNamespace(platform="comfort_schedule") if eid == "climate.upstairs_comfort" else None
    er_mod.async_get = lambda hass: CReg()
    hass, c, _ = await feature_make({"follower_entities": ["climate.upstairs_comfort", "climate.f2"]}, entry_id="g11")
    check("Comfort thermostats are left out of the followers", c.followers == ["climate.f2"], c.followers)
    er_mod.async_get = saved_get

    # Heating-only mode and virtual Off for thermostats that only advertise Heat.
    at(2026, 9, 23, 12)
    heat_only = {"cooling_enabled": False, "off_fallback_temperature": 45}
    hass, c, _ = await feature_make(heat_only, modes=("heat",), entry_id="ho1")
    check("heat-only disables Heat/Cool emulation", not c.can_emulate() and not c.uses_native_heat_cool())
    hass.set_source(hvac_action="cooling"); await settle(hass)
    check("heat-only ignores a raw cooling action for runtime and health",
          not c.runtime.running and c.health.action is None, (c.runtime.running, c.health.action))
    hass.set_source(hvac_action="idle"); await settle(hass)
    await c.async_set_mode("off"); await settle(hass)
    check("virtual Off: reports Off while the device stays in Heat at the fallback",
          c.virtual_off and c.proxy_mode() == "off" and hass.states_map[SRC].state == "heat" and temp_of(hass) == 45,
          (c.virtual_off, c.proxy_mode(), hass.states_map[SRC].state, temp_of(hass)))
    c._resync(None); await settle(hass)
    check("virtual Off: resync keeps the fallback", c.virtual_off and temp_of(hass) == 45, temp_of(hass))

    # Heat-only schedules can omit cool, but the paired value remains internally.
    single = [[{"start": 0, "heat": 64, "label": "Home"}]] * 7
    await c.async_set_schedule(single); await settle(hass)
    check("heat-only schedule accepts one target and retains internal cool",
          c.week[0][0].heat == 64 and c.week[0][0].cool == 74 and temp_of(hass) == 45,
          (c.week[0][0].heat, c.week[0][0].cool, temp_of(hass)))
    try:
        await c.async_set_schedule([[{"start": 0, "heat": 64, "mode": "cool"}]] * 7)
        check("heat-only schedule rejects Cool mode", False)
    except ValueError:
        check("heat-only schedule rejects Cool mode", True)

    # A real wall adjustment exits virtual Off and becomes a normal hold.
    hass.set_source(temperature=50); await settle(hass)
    check("wall target change exits virtual Off and creates a heating hold",
          not c.virtual_off and c.hold is not None and c.hold.heat == 50 and temp_of(hass) == 50,
          (c.virtual_off, c.hold, temp_of(hass)))

    # The runtime flag survives a reload and reapplies the low target.
    await c.async_set_mode("off"); await settle(hass)
    saved_off = c._data_to_save()
    hass2, c2, _ = await feature_make(heat_only, modes=("heat",), entry_id="ho2")
    async def load_virtual_off(): return saved_off
    c2._store.async_load = load_virtual_off
    await c2.async_load(); c2._resync(None); await settle(hass2)
    check("virtual Off survives reload and reapplies the fallback",
          c2.virtual_off and c2.proxy_mode() == "off" and temp_of(hass2) == 45,
          (c2.virtual_off, c2.proxy_mode(), temp_of(hass2)))
    c2.entry.options.pop("off_fallback_temperature")
    c2._resync(None); await settle(hass2)
    check("removing the fallback exits virtual Off and resumes the plan",
          not c2.virtual_off and temp_of(hass2) == 50,
          (c2.virtual_off, temp_of(hass2)))

    # Native Off wins; followers use their own best available Off mechanism.
    hass, c, _ = await feature_make(heat_only, modes=("off", "heat"), entry_id="ho3")
    await c.async_set_mode("off"); await settle(hass)
    check("native Off takes precedence over the configured fallback",
          not c.virtual_off and hass.states_map[SRC].state == "off", (c.virtual_off, hass.states_map[SRC].state))
    native = State("heat", {"hvac_modes": ["off", "heat"], "temperature": 68, "min_temp": 40, "max_temp": 90})
    fallback = State("heat", {"hvac_modes": ["heat"], "temperature": 68, "min_temp": 50, "max_temp": 80})
    unsupported = State("cool", {"hvac_modes": ["cool"], "temperature": 70, "min_temp": 40, "max_temp": 90})
    opts = {**heat_only, "follower_entities": ["climate.native", "climate.fallback", "climate.unsupported"],
            "follower_offsets": {"climate.fallback": -3}}
    hass, c, _ = await feature_make(opts, modes=("heat",), states={
        "climate.native": native, "climate.fallback": fallback, "climate.unsupported": unsupported,
    }, entry_id="ho4")
    unsupported_before = (hass.states_map["climate.unsupported"].state, temp_of(hass, "climate.unsupported"))
    await c.async_set_mode("off"); await settle(hass)
    check("followers use native Off or a clamped offset fallback",
          hass.states_map["climate.native"].state == "off" and temp_of(hass, "climate.fallback") == 50,
          (hass.states_map["climate.native"].state, temp_of(hass, "climate.fallback")))
    check("a follower without Off or Heat is left unchanged",
          (hass.states_map["climate.unsupported"].state, temp_of(hass, "climate.unsupported")) == unsupported_before)

    hass, c, _ = await feature_make({"cooling_enabled": False}, modes=("heat",), entry_id="ho5")
    try:
        await c.async_set_mode("off")
        check("heat-only device without native or virtual Off rejects Off", False)
    except HomeAssistantError:
        check("heat-only device without native or virtual Off rejects Off", True)

    # Scheduled Off uses the fallback; the following Heat block restores its target.
    scheduled_modes = [
        {"start": 0, "heat": 68, "cool": 74, "label": "Home", "mode": "heat"},
        {"start": 780, "heat": 62, "cool": 80, "label": "Off", "mode": "off"},
        {"start": 840, "heat": 68, "cool": 74, "label": "Home", "mode": "heat"},
    ]
    at(2026, 9, 23, 12)
    hass, c, _ = await feature_make(heat_only, schedule=scheduled_modes, modes=("heat",), entry_id="ho6")
    at(2026, 9, 23, 13, 0, 1); c._on_timer(None); await settle(hass)
    check("scheduled Off block uses virtual Off", c.virtual_off and c.proxy_mode() == "off" and temp_of(hass) == 45)
    at(2026, 9, 23, 14, 0, 1); c._on_timer(None); await settle(hass)
    check("scheduled Heat block exits virtual Off and restores its target",
          not c.virtual_off and c.proxy_mode() == "heat" and temp_of(hass) == 68,
          (c.virtual_off, c.proxy_mode(), temp_of(hass)))
    CLOCK["now"] = saved_now
