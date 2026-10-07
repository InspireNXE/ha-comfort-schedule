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
| `emu.html` | Runs `emu_test.py`: the controller with Home Assistant stubbed out. It covers Heat/Cool switching on thermostats that only have Heat and Cool, holds, resume, per-block mode and fan, runtime (daily, weekly, monthly, comparisons), last run, battery and humidity detection, and label colors. | No `FAIL` lines. |
| `cards.html` | All four cards against a mocked `hass`, including two thermostat cards (full with the week strip open, and compact). Switches at the top change the theme (dark/light) and the state (following, hold, paused, health problem). Service calls the cards make are listed at the bottom. | No red error text at the top; the cards look right in both themes. |

Each page sets its title to `done` when it finishes.
