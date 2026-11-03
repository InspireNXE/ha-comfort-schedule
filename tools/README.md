# Test pages

Checks for `custom_components/comfort_schedule` that run on a Windows PC with nothing installed: a
small PowerShell web server, plus pages that run in a normal browser. The Python checks use
[Pyodide](https://pyodide.org) (Python in the browser), downloaded from cdn.jsdelivr.net the first
time a page loads.

## Run

From the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File tools\serve.ps1
```

Then open <http://localhost:8765/>. Use `-Port 8766` if 8765 is taken. Ctrl+C stops the server.

The server looks for files in `tools\` first, then in `custom_components\comfort_schedule\`, so the
pages always test the files in this repo. It only listens on localhost and refuses `..` paths.

## Pages

| Page | What it checks | Pass |
|---|---|---|
| `check.html` | Parses the card script and the JSON files, compiles every Python file, and unit-tests `schedule.py` (set-point guard, active and next period, DST-safe next change, JSON validation). | Every line starts with `OK` or `ok`. |
| `emu.html` | Runs `emu_test.py`: the controller with Home Assistant stubbed out. It covers Heat/Cool switching on thermostats that only have Heat and Cool, holds, resume, per-block mode and fan, runtime (daily, weekly, monthly, comparisons), last run, battery and humidity detection, label colors, and the health warnings (no progress, wrong way that has to last, short idle blips, and clearing on progress, set point reached or Off). | No `FAIL` lines. |
| `cards.html` | All four cards against a mocked `hass`, including two thermostat cards (full with the week strip open, and compact). Switches at the top change the theme (dark/light) and the state (following, hold, paused, health problem). Service calls the cards make are listed at the bottom. | No red error text at the top; the cards look right in both themes. |

Each page sets its title to `done` when it finishes.

With Python 3.11 or later, `python tools/run_emu.py` runs the same `emu_test.py` without a browser and exits 1 if any check fails. CI runs it on every push.

## Notes for `cards.html`

- `ha-card` is mocked the way Home Assistant builds it: a Lit element whose `<slot>`
  renders a moment after the card is created. Anything the cards measure or focus has to
  wait for `card.updateComplete`, which is what the real one needs too.
- `ha-icon` draws every icon as a diamond, at the real size and alignment
  (`inline-flex`, `vertical-align: middle`). Shapes are wrong; spacing is right.
- `<home-assistant>` is defined 400 ms after load, to mirror the scoped custom-element
  registry that HA installs before cards may register.
- Two stand-in `hui-error-card`s ("Custom element doesn't exist") are on the page before the
  cards load: one for a Comfort card, one for another card. `window.rebuilt` should list only
  `comfort-health-card` once the cards are defined (the late-load recovery).
- The page has a mobile viewport tag, so a phone-sized browser window shows the cards at
  phone width.

## Adding a scenario test

Add checks to the end of `run()` in `emu_test.py` with `check("what it proves", condition, extra)`.
`advance(minutes)` moves the fake clock, `settle(hass)` lets queued service calls finish,
and `Hass([...modes])` gives a fake thermostat whose state you change with `set_source(...)`.

## Screenshots

`screenshots.html?shot=<name>` shows one card from the README, with the same mocked `hass` as
`cards.html` but real [Material Design Icons](https://pictogrammers.com/library/mdi/) (loaded from
cdn.jsdelivr.net). With `serve.ps1` running:

```powershell
powershell -ExecutionPolicy Bypass -File tools\screenshots.ps1            # every shot
powershell -ExecutionPolicy Bypass -File tools\screenshots.ps1 -Only health,runtime
```

It opens each shot in headless Microsoft Edge at 2x and crops it to the card, writing
`docs\images\<name>.png`. To add a shot, add a line to `shots` in `screenshots.html` and its name
to the list in `screenshots.ps1`.
