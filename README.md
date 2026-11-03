<p align="center">
  <img src="https://raw.githubusercontent.com/InspireNXE/ha-comfort-schedule/main/custom_components/comfort_schedule/brand/icon@2x.png" width="96" alt="">
</p>

<h1 align="center">Comfort Schedule</h1>

<p align="center">
  A Nest-style weekly heat/cool schedule for any Home Assistant thermostat,<br>
  with dashboard cards to run it, edit it and keep an eye on your HVAC.
</p>

<p align="center">
  <a href="https://github.com/InspireNXE/ha-comfort-schedule/releases/latest"><img src="https://img.shields.io/github/v/release/InspireNXE/ha-comfort-schedule" alt="Latest release"></a>
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.9%2B-03A9F4.svg" alt="Home Assistant 2026.9+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT license"></a>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/InspireNXE/ha-comfort-schedule/main/docs/images/thermostat.png" width="330" alt="Thermostat card with the week at a glance and today's runtime">
  &nbsp;
  <img src="https://raw.githubusercontent.com/InspireNXE/ha-comfort-schedule/main/docs/images/thermostat-light.png" width="330" alt="Thermostat card, dial style, light theme">
</p>

## Why

Thermostat schedules in Home Assistant usually mean one automation per time change, and they fall apart the moment you switch between Heat, Cool and Heat/Cool. Comfort Schedule replaces all of that with one schedule that stores both set points for every part of the day, a wrapper thermostat that keeps them sane, and cards that make it feel like a smart thermostat app.

- **One schedule, both set points.** Each period has a heat and a cool set point, so changing the mode never needs a schedule edit.
- **Heat always stays below cool** by a gap you choose, everywhere: the cards, the stock thermostat card, voice, actions and the wall unit.
- **Heat/Cool on thermostats that only have Heat and Cool**, by switching between them for you, with limits that stop it flipping back and forth.
- **Holds** for manual changes: until the next period, for a set time, or until you resume. **Vacations** with start and end dates.
- **Away set points** while nobody is home, from people, trackers or a zone.
- **Learns your home**: starts heating or cooling early so the room is ready when a block begins, and suggests schedule changes when you keep making the same manual change.
- **Health warnings** when heating or cooling runs a long time without moving the temperature, moves it the wrong way, or gets slower over the weeks. Plus a filter reminder.
- **Runtime tracking**: heating and cooling hours per day, week, month and year, with long-term statistics, a cost estimate, and a weather-adjusted comparison when you have an outdoor sensor.
- **More than one thermostat** can follow the same schedule.
- **Battery-friendly.** It writes to the thermostat only when something changes, and combines rapid taps into one command.
- **Four cards** with visual editors that load on their own. No resources to add.

It works with any `climate` entity that offers Heat and Cool. It's been in daily use on a battery-powered Z-Wave thermostat (Honeywell T6 Pro) with a gas furnace and central AC.

## A tour of the cards

### Thermostat

Set points, mode and fan, what the schedule is doing now and what comes next. Drag the heat and cool handles on the range bar or the dial, or use − / +. Push heat into cool and cool moves up to keep the gap.

<table>
  <tr>
    <td align="center"><img src="https://raw.githubusercontent.com/InspireNXE/ha-comfort-schedule/main/docs/images/thermostat-dial.png" width="260" alt="Dial style"><br><sub><b>Dial style</b><br><code>style: dial</code></sub></td>
    <td align="center"><img src="https://raw.githubusercontent.com/InspireNXE/ha-comfort-schedule/main/docs/images/thermostat-compact.png" width="260" alt="Compact layout"><br><sub><b>Compact, week on top, 7-day runtime</b><br><code>layout: compact</code></sub></td>
  </tr>
  <tr>
    <td align="center"><img src="https://raw.githubusercontent.com/InspireNXE/ha-comfort-schedule/main/docs/images/thermostat-hold.png" width="260" alt="Hold"><br><sub><b>A hold, with when it ends and what it resumes to</b></sub></td>
    <td align="center"><img src="https://raw.githubusercontent.com/InspireNXE/ha-comfort-schedule/main/docs/images/thermostat-vacation.png" width="260" alt="Vacation"><br><sub><b>A vacation, then back to the schedule</b></sub></td>
  </tr>
</table>

- **Week at a glance** behind the calendar button at the top left: every day's blocks, colored by label, with a line at the current time. Tap a block for its details. It also has the **Pause schedule** / **Resume schedule** button.
- **Holds** show how long they last and what the schedule goes back to. Tap the end time to pick another length, **Pause the schedule** to keep the held set points, or **Resume** to go back now.
- **Vacation.** Tap the time in the schedule status (or a hold's end time) and pick **Vacation…** to set the dates and the set points while you're gone. While it runs, the status shows when it ends, and **End** goes back to the schedule early.
- **Away** and **Starting early** show in the same place when presence or early start (see [Options](#options)) is in charge.
- **Humidity and battery** under the indoor temperature. Battery turns red below 20%. Tap either one for that sensor's details.
- **Runtime row** (optional): today's heating and cooling time compared with the week before, or a bar per day for the last 7 days.

### Weekly schedule

See the whole week, then press **Edit** to change it. Drag a block's edges to move it, split and delete blocks, set the heat and cool for each one, and optionally the mode and fan when it starts. Copy a block or a whole day to weekdays, the weekend or every day. Nothing changes until you save.

<p align="center"><img src="https://raw.githubusercontent.com/InspireNXE/ha-comfort-schedule/main/docs/images/schedule.png" width="680" alt="Weekly schedule card"></p>

<p align="center"><img src="https://raw.githubusercontent.com/InspireNXE/ha-comfort-schedule/main/docs/images/schedule-edit.png" width="680" alt="Weekly schedule card in Edit mode, with a block open"></p>

**Suggestions.** When you keep making the same manual change, the card offers to put it in the schedule: "Start Wake at 5:45 AM on weekdays" if you keep turning the heat up before Wake begins, or "Heat to 71° for Home on the weekend" if you keep setting the same temperature. It takes the same change on 3 different days in the last three weeks. **Apply** changes the schedule (weekdays or the weekend only, as the suggestion says); **Dismiss** hides it for good.

### HVAC health

Indoor temperature against the heat and cool set points, with heating and cooling runs shaded and the schedule underneath. When a run isn't getting anywhere, the card says so, and a warning goes to the notify target you chose and to **Settings › Repairs**. It clears itself once a run makes progress or reaches the set point, or when the system is turned off.

<table>
  <tr>
    <td align="center"><img src="https://raw.githubusercontent.com/InspireNXE/ha-comfort-schedule/main/docs/images/health.png" width="330" alt="HVAC health card"><br><sub><b>All good</b></sub></td>
    <td align="center"><img src="https://raw.githubusercontent.com/InspireNXE/ha-comfort-schedule/main/docs/images/health-problem.png" width="330" alt="HVAC health card showing a warning"><br><sub><b>Heating isn't moving the temperature</b></sub></td>
  </tr>
</table>

Tap any point on the chart to see the temperature, set points, what the system was doing and which block was running at that moment.

With an outdoor temperature sensor (see [Options](#options)), the chart also has a dotted outdoor line, on its own scale on the right. Below the chart:
- **Getting slower.** Once enough runs are recorded, the card warns when heating or cooling has moved the room noticeably slower over the last two weeks than in the weeks before, compared at similar outdoor temperatures so a cold snap doesn't count. That warning also goes to your notify target and Repairs.
- **Filter.** Hours of heating plus cooling since the filter was last changed, against the reminder (300 h by default). Press **Changed it** after a filter change.

### HVAC runtime

Heating and cooling time today, yesterday, the last 7 days, this month and this year, plus a like-for-like comparison with the period before. Switch between days, weeks and months; tap a bar for that day, week or month.

Under the comparison: an estimated cost, if you set a cost per hour in the options, and with an outdoor sensor a **weather-adjusted** change. That's the change in hours per heating (or cooling) degree-day, so a colder week that needed more heat doesn't look like a problem.

<table>
  <tr>
    <td align="center"><img src="https://raw.githubusercontent.com/InspireNXE/ha-comfort-schedule/main/docs/images/runtime.png" width="330" alt="Runtime card, day view"><br><sub><b>Day view</b></sub></td>
    <td align="center"><img src="https://raw.githubusercontent.com/InspireNXE/ha-comfort-schedule/main/docs/images/runtime-week.png" width="330" alt="Runtime card, week view, stacked"><br><sub><b>Week view, stacked</b><br><code>chart_style: stacked</code></sub></td>
  </tr>
</table>

## Install

Needs Home Assistant 2026.9 or later.

### HACS

1. In HACS, open the menu (⋮) › **Custom repositories**, add `https://github.com/InspireNXE/ha-comfort-schedule` with type **Integration**.
2. Search for **Comfort Schedule** in HACS and download it.
3. Restart Home Assistant.

### Manually

Copy `custom_components/comfort_schedule` from this repository to `custom_components/comfort_schedule` in your Home Assistant config folder, then restart Home Assistant.

## Set up

1. If your thermostat has its own built-in schedule, turn it off (often called "non-programmable" or "hold" mode in the thermostat's setup), so the two schedules don't fight.
2. Go to **Settings › Devices & services › Add integration › Comfort Schedule**.
   - **Thermostat:** the thermostat to schedule.
   - **Name:** optional; leave blank to name it after the thermostat (e.g. "Living Room Comfort").
   - **Send HVAC warnings to:** optional notify entity. Warnings also appear under **Settings › Repairs**.
   - **Minimum gap between heat and cool:** at least your thermostat's own auto-changeover deadband (3°F / 1.5°C is a good start).
3. The schedule starts as one period a day at the thermostat's current set points. Open the schedule card and press **Edit** to build your week.

Each thermostat gets its own Comfort Schedule; add the integration again for another one. Or, to run several thermostats on one schedule, add the others under **More thermostats** in the options.

### Options

**Settings › Devices & services › Comfort Schedule › Configure.** The first three are always shown; the rest are in sections that open when you need them.

| Section | Option | Default | What it does |
|---|---|---|---|
| | Send HVAC warnings to | none | Notify entity for health, slowdown and filter warnings. They also go to Repairs. |
| | Minimum gap between heat and cool | 3°F / 1.5°C | The set point rule. |
| | Manual changes hold | until the next period | How long a manual change lasts. |
| HVAC health | Warn if heating / cooling this long without progress | 45 / 60 min | See [How it behaves](#how-it-behaves). |
| | Progress that counts | 1° | |
| | Warn if the temperature moves the wrong way by | 0.8° | |
| | Warn when runs get this much slower | 25% | Last two weeks against the 60 days before, in similar weather. 0 turns it off. |
| Heat/Cool | Switch once the room is this far past the other set point | 1° | Only for thermostats without their own Heat/Cool mode. |
| | Minimum time between switching | 30 min | |
| | Switch Heat and Cool myself | off | Use Comfort Schedule's switching even when the thermostat has a Heat/Cool mode. |
| Presence | People or trackers | none | `person`, `device_tracker`, `zone`, `group`, `binary_sensor` or `input_boolean` entities. Anyone home (home, a zone above 0, or on) counts as home; unavailable counts as home. |
| | Away heat / Away cool | none | Set points while nobody is home. Both are needed. |
| | Wait this long after the last person leaves | 10 min | |
| Weather, cost and filter | Outdoor temperature | none | A temperature sensor or a weather entity. Adds the outdoor line, degree-days, weather-adjusted runtime, and makes early start and the slowdown check weather-aware. |
| | Filter reminder after | 300 h | Hours of heating plus cooling. 0 turns it off. |
| | Cost per hour of heating / cooling | none | For the cost estimate. Roughly: furnace gas use per hour × your gas rate; the AC's kW × your electricity rate. |
| Learning | Early start | 0 (off) | Start a block up to this many minutes early so the room is at its set point when it begins. Learns how fast the room heats and cools from the last 30 days of runs (at least 3 runs), in similar outdoor weather when there's an outdoor sensor. Only when the next block asks for more heat or more cooling. |
| | Suggest schedule changes | on | Suggestions on the schedule card. |
| More thermostats | Also set these thermostats | none | Other thermostats that get the same set points and mode. |

## What it adds

After setup there's a new device (named e.g. "Living Room Comfort") with:

| Entity | What it's for |
|---|---|
| `climate.living_room_comfort` | A wrapper thermostat that controls the real one. Use this one on dashboards and with voice. Changing a set point here starts a hold. |
| `switch.living_room_comfort_schedule` | Turns the whole schedule on or off. Turning it on also ends any hold. |
| `sensor.living_room_comfort_period` | The current period (e.g. "Home"), or "Hold", "Vacation", "Away" or "Paused", or e.g. "Wake (early start)". Attributes include the next change. |
| `sensor.living_room_comfort_run_time` | Minutes the current heating or cooling run has lasted. |
| `sensor.living_room_comfort_heating_runtime_today` / `…_cooling_runtime_today` | Hours of heating / cooling today (resets at midnight). Home Assistant keeps long-term statistics, so a Statistics graph card can show daily, weekly or monthly totals. Attributes give yesterday, last 7 days, this month, last 30 days and this year. |
| `binary_sensor.living_room_comfort_hvac_problem` | On when a run isn't moving the temperature toward the set point, or is moving it the wrong way. |
| `sensor.living_room_comfort_filter_runtime` | Hours of heating plus cooling since the filter was last changed. |
| `binary_sensor.living_room_comfort_filter_change_due` | On once the filter reminder's hours are reached. |
| `button.living_room_comfort_filter_changed` | Press after changing the filter; the count starts over. |

The thermostat's attributes also carry what the cards use: `status` (`schedule`, `hold`, `vacation`, `away`, `early` or `paused`), `vacation`, `suggestions`, `filter`, `costs`, `outdoor_temperature` and the learned heating and cooling rates.

### Actions

All of them target the Comfort Schedule thermostat (`climate.living_room_comfort`).

| Action | What it does |
|---|---|
| `comfort_schedule.set_hold` | Holds `heat` and/or `cool` (either can be left out). `duration` is `next_period`, a number of minutes (up to 10080, one week), or `indefinite`; leave it out to keep the end time of a hold that's already running, or to use the default for a new one. |
| `comfort_schedule.resume_schedule` | Ends any hold and turns the schedule back on. |
| `comfort_schedule.pause_schedule` | Stops following the schedule. The current set points stay until you resume. |
| `comfort_schedule.set_schedule` | Replaces the whole week. `schedule` is a list of 7 days, Monday first. Each day is a list of blocks with `start` (minutes after midnight, in 5-minute steps; the first block starts at 0), `heat`, `cool`, and optionally `label`, `mode` and `fan`. `label_colors` is optional. |
| `comfort_schedule.set_vacation` | Holds `heat` and `cool` from `start` (leave out for now) until `end`, then goes back to the schedule. Times without a time zone are local time. Replaces any earlier vacation. |
| `comfort_schedule.cancel_vacation` | Removes the vacation, whether it has started or not. |
| `comfort_schedule.filter_changed` | Same as the Filter changed button. |
| `comfort_schedule.apply_suggestion` / `dismiss_suggestion` | Applies or hides a schedule suggestion, by the `id` in the thermostat's `suggestions` attribute. |

For example, a weekend away:

```yaml
action: comfort_schedule.set_vacation
target:
  entity_id: climate.living_room_comfort
data:
  start: "2026-12-18 17:00:00"
  end: "2026-12-21 15:00:00"
  heat: 60
  cool: 82
```

The schedule card's editor is usually easier than `set_schedule`; the action is there for scripts and backups.

## How it behaves

- **Set-point rule.** Heat always stays at least the gap below cool. If you raise heat into cool, cool moves up to keep the gap, and the same happens the other way around.
- **Mode.** The mode comes from the thermostat itself. Every period stores both a heat and a cool set point, so switching between Heat, Cool and Heat/Cool never needs a schedule edit.
- **Heat/Cool on thermostats with it.** A thermostat with its own Heat/Cool mode gets both set points as they are, and keeps Heat/Cool itself.
- **Heat/Cool on thermostats without it.** If the thermostat only has Heat and Cool, the wrapper thermostat still offers Heat/Cool. It keeps the real thermostat in Heat or Cool. Changing a set point (on the card, by a hold, or by the schedule) switches straight away if the room is already past the new set point. Temperature drift on its own switches only once the room is 1° past the other set point, and at most once every 30 min, so the system doesn't flip back and forth. Changing the mode at the wall unit hands control back to you. Both limits, plus an option to use this even on thermostats with their own Heat/Cool mode, are in the options.
- **Mode and fan per block.** Each block can also set the mode (Off, Heat, Cool, Heat/Cool) and fan when it starts. Both default to "Use current", which leaves whatever is set alone. They're applied when the block starts (and when you resume the schedule, a hold ends, or you change the running block). So if you change the mode by hand mid-block, it stays until the next block. Blocks don't change the mode or fan during a hold, a vacation or Away, so a block that turns the system off never does it in an empty house.
- **Holds.** A manual change holds until the next period by default. You can change the default in the options, or pick a different length from the card. Changes made at the wall unit or on the original thermostat entity also count as holds.
- **What wins.** A hold, then a vacation, then Away, then an early start, then the schedule. So a manual change during a vacation is a hold on top of it, and resuming goes back to the vacation. A vacation applies even with the schedule paused; when it starts, a hold from before it ends.
- **Presence.** When everyone has been gone for the delay, the away set points apply; the moment anyone is back, the schedule does. Away and the countdown carry over a restart or an options change. If everyone left while Home Assistant was off, the delay starts when it's back.
- **Early start.** In the minutes before a block that needs more heat (or more cooling), it checks how long the room would take to get there at the rate it has been heating or cooling lately. When it's time, the coming block's set points apply early; the block then starts as usual. Nothing happens until it has seen at least 3 runs in the last 30 days.
- **More thermostats.** The other thermostats get the same set points (in whatever mode each one is in, within its own limits) and follow the main thermostat's mode when they have it. Changes made on them directly are put back at the next check, every 10 minutes; make changes on the Comfort Schedule thermostat. Health and runtime only follow the main thermostat.
- **Battery-friendly.** It only writes to the thermostat when something differs, and rapid taps on the card are combined into one command.
- **Restarts.** After a restart the current period is applied again. Holds survive restarts.
- **Health.** Warnings go to the notify entity you choose and to **Settings › Repairs**. You can change every number below in the options (**Settings › Devices & services › Comfort Schedule › Configure**).
  - *No progress:* heating runs 45 min, or cooling 60 min, with less than 1.0° of progress toward the set point and without reaching it.
  - *Wrong way:* the temperature stays 0.8° or more the wrong way for over 10 min, checked from 10 min into a run. A single sensor tick down before the heat arrives doesn't count.
  - A short idle report (2 min or less) between updates counts as the same run.
  - A warning clears when a run makes 1.0° of progress or reaches the set point, when the room reaches the set point while idle, or when the system is turned off.
  - On a very hot day the AC can hold the room flat for an hour and get a warning. If that happens often, raise the cooling time.
  - *Getting slower:* every run of 10 min or more that moved the temperature is kept for 90 days, with the outdoor temperature at the time. When runs in the last two weeks move the room 25% slower than the 60 days before at similar outdoor temperatures (at least 8 runs each), you get a warning. It clears when that's no longer true, checked after each run and every night.
- **Filter.** Hours of heating plus cooling since the filter was last changed. At the reminder's hours you get one notification and a Repairs entry, until **Filter changed** is pressed.

## Add the cards

The cards load automatically, so there's no resource to add. On a dashboard, choose **Add card** and search for "Comfort", or paste YAML:

```yaml
type: vertical-stack
cards:
  - type: custom:comfort-thermostat-card
    entity: climate.living_room_comfort
  - type: custom:comfort-schedule-card
    entity: climate.living_room_comfort
  - type: custom:comfort-health-card
    entity: climate.living_room_comfort
    hours: 3
  - type: custom:comfort-runtime-card
    entity: climate.living_room_comfort
    days: 14
```

### Card options

Every option below can be set in the card's visual editor, as well as in YAML. The editor groups them into collapsible sections; every card starts with **Appearance** (title, hide title, hide subtitle), followed by that card's own sections (for the thermostat: Temperature and set points, Mode and fan, Schedule, Runtime).

| Card | Option | Default | What it does |
|---|---|---|---|
| All | `entity` | (required) | The Comfort Schedule thermostat, e.g. `climate.living_room_comfort`. |
| | `hide_title` | `false` | Hides the card's title. With `hide_subtitle` too, the header keeps only its buttons, or goes away if it has none. |
| | `hide_subtitle` | `false` | Hides the line under the title. |
| Thermostat | `name` | thermostat's name | Card title. |
| | `style` | `classic` | How the top of the card looks. `classic`: the indoor temperature, large and centered, with the set points under it and the range bar below. `dial`: the indoor temperature inside a ring; drag the heat and cool handles around the ring (or press the ring to move the nearer one), and the set points sit under the ring's two ends (below the dial on narrow cards). |
| | `layout` | `full` | `compact` makes the temperature smaller, drops the range bar (classic) or shrinks the dial, and turns mode and fan into two menu buttons. |
| | `hide_targets` | `false` | Hides the set point controls under the temperature. The range bar or dial still sets them, and the status line still says what it's heating or cooling to. |
| | `hide_target_buttons` | `false` | Keeps the set point values but hides their − / + buttons. Change them on the range bar or dial; in the compact classic layout (no range bar), tap a value for − / +. |
| | `mode_layout` | `buttons` | `compact` is one row like the fan row: the current mode plus an icon button per mode. (The compact card layout uses its own mode menu instead.) |
| | `fan_layout` | `buttons` | `buttons` is a full row of labeled buttons, like the mode row. `compact` is one row: the current fan mode plus an icon button per mode. |
| | `week_expanded` | `false` | Shows the week at a glance when the card loads. The calendar button in the header shows and hides it either way. |
| | `week_position` | `bottom` | Where the week at a glance goes: `bottom` (below the schedule status) or `top` (under the header). |
| | `show_runtime` | `false` | Adds a runtime row under the schedule status. Tap it (or a bar) for more. |
| | `runtime_style` | `today` | `today`: heating and cooling time so far today, plus the change vs the 7 days before for whichever ran more (or its 7-day total, until there's a prior week to compare with). `week`: a small stacked bar per day for the last 7 days, plus the 7-day totals. |
| | `runtime_show` | `both` | `heat` or `cool` shows only heating or only cooling in the runtime row. |
| | `hide_mode` | `false` | Hides the Heat / Heat/Cool / Cool buttons. |
| | `hide_fan` | `false` | Hides the fan control. |
| | `hide_humidity` | `false` | Hides humidity under the indoor temperature. |
| | `hide_battery` | `false` | Hides the battery level under the indoor temperature. |
| | `battery_entity` | detected | Battery sensor to show. The default is the thermostat's own battery sensor. |
| | `humidity_entity` | detected | Humidity sensor opened when you tap the humidity value. The default is the thermostat's own. |
| Schedule | `title` | "Weekly schedule" | Card title. |
| | `read_only` | `false` | Removes the Edit button (and suggestions), e.g. for a wall tablet. Block details still open on tap. |
| | `hide_suggestions` | `false` | Hides schedule suggestions on this card. |
| Health | `title` | "HVAC health" | Card title. |
| | `hours` | `3` | Hours of history the chart opens with (1 to 24). The card also has a 3h / 12h / 24h switch for the person viewing it. |
| | `hide_outdoor` | `false` | Leaves the outdoor temperature off the chart. |
| Runtime | `title` | "HVAC runtime" | Card title. |
| | `show` | `both` | `heat` or `cool` shows only heating or only cooling everywhere on the card, e.g. for a heat-only system. |
| | `totals` | `full` | The heating and cooling tiles at the top. `compact` shows only today's time; `hidden` removes them. |
| | `view` | `day` | What the chart and comparison open with: `day`, `week` or `month`. The card also has a Day / Week / Month switch for the person viewing it. |
| | `chart_style` | `grouped` | `stacked` puts cooling on top of heating, so each bar's height is the combined time. |
| | `days` | `14` | Days in the Day view (7 to 31). |
| | `weeks` | `12` | Weeks in the Week view (4 to 12). |
| | `months` | `12` | Months in the Month view (3 to 12). |
| | `hide_view_switch` | `false` | Hides the Day / Week / Month switch, so the card stays on `view`. It's also hidden when both the chart and the comparison are hidden. |
| | `hide_compare` | `false` | Hides the comparison row (e.g. last 7 days vs the 7 before). |
| | `hide_chart` | `false` | Hides the bar chart and its legend. |
| | `hide_legend` | `false` | Hides the legend under the chart. |
| | `hide_cost` | `false` | Hides the cost estimate. |

Tap for details works almost everywhere:
- A block on the schedule card (outside Edit mode) or in the week strip shows its details.
- A bar on the runtime chart shows that day, week or month.
- A point on the health chart shows the indoor temperature, set points, what the system was doing (and how far into the run), and the schedule block at that moment. Tap elsewhere on the chart to move it; with a keyboard, Enter opens it and the arrow keys step through time.

The runtime card also compares like with like: in the Day view, the last 7 days against the 7 before; in Week and Month, this week or month so far against the same stretch of the one before. Until runtime has been tracked that far back (right after installing), it says "no earlier data yet" and shows just the totals.

**Block colors.** Each block label has a color, shown on the schedule card, the week strip and the health chart's schedule band. Labels that include sleep/night, wake/morning, away/work or home/evening get a matching default; others get one from a palette. To pick your own, open a block in Edit mode and choose a swatch (or **Auto** to go back to the default). Colors are saved with the schedule. `comfort_schedule.set_schedule` also takes an optional `label_colors` map, e.g. `{"Home": "#2E9E6B"}`.

**Light and dark themes.** The cards use your theme's colors. On light themes, heat/cool/fan text is darkened a little so it stays readable on white.

## Troubleshooting

To see every write and every change detected at the wall, add this to `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.comfort_schedule: debug
```

- **Card changes don't show up after an update?** Refresh the browser. In the companion app, use **Settings › Companion app › Debugging › Reset frontend cache**.
- **The set points keep changing back, or holds start on their own?** The thermostat's own schedule is probably still on. Turn it off at the wall unit (see [Set up](#set-up)).
- **Heat/Cool switching too often, or not soon enough?** Adjust the changeover margin and minimum time in the options.
- **Health warnings you don't think are real?** Raise the heating or cooling time, or the progress needed, in the options. A slow system or a thermostat that reports temperature only in whole degrees may need more time.
- **Reporting a problem?** **Settings › Devices & services › Comfort Schedule › ⋮ › Download diagnostics** saves the options, the schedule, what's in charge and the last 25 writes to the thermostat. Who is home is left out.

## Testing changes

`tools/` has test pages that run on a Windows PC with nothing installed: a small PowerShell web server plus pages that run the Python in the browser with Pyodide. See [`tools/README.md`](https://github.com/InspireNXE/ha-comfort-schedule/blob/main/tools/README.md). `python tools/run_emu.py` runs the same scenarios with plain Python.

`tests/` has tests that run against a real Home Assistant (setup, the config and options flows, the actions, diagnostics). They need Linux or macOS:

```bash
pip install pytest-homeassistant-custom-component
pytest
```

Both run on every push.

The screenshots in this README come from `tools/screenshots.html` (the cards against a mocked Home Assistant, with real icons). With the test server running, `powershell -ExecutionPolicy Bypass -File tools\screenshots.ps1` regenerates every image in `docs/images/` using headless Microsoft Edge.

## License

MIT. See [LICENSE](https://github.com/InspireNXE/ha-comfort-schedule/blob/main/LICENSE).
