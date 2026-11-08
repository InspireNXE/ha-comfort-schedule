/*
 * Comfort Schedule cards: thermostat, weekly schedule, HVAC health and runtime.
 * Plain web components (no build step). Served by the integration and loaded on
 * every dashboard automatically, so no resource needs to be added by hand.
 */
(() => {
  const DOMAIN = "comfort_schedule";
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const STEP_MIN = 5;

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const num = (v) => (v === null || v === undefined || v === "" || isNaN(Number(v)) ? null : Number(v));
  const round1 = (v) => Math.round(v * 10) / 10;
  const fmtTemp = (v) => (v === null || v === undefined ? "–" : `${round1(v)}`);

  function hour12(hass) {
    const tf = hass?.locale?.time_format;
    if (tf === "12") return true;
    if (tf === "24") return false;
    // "system" follows the browser's own clock style, as Home Assistant does; "language" the language's.
    if (tf === "system") return new Date(2023, 0, 1, 22).toLocaleString(undefined).includes("10");
    return undefined;
  }
  // Moments show in the browser's time zone, or the server's if the user's profile asks for it.
  const zone = (hass) => (hass?.locale?.time_zone === "server" && hass?.config?.time_zone ? { timeZone: hass.config.time_zone } : {});
  const clockFormats = new Map();
  // Day (Monday 0) and minute of the day at home. Schedule times are Home Assistant's wall
  // clock, wherever the dashboard is opened (say, on a phone on vacation in another zone).
  function homeClock(hass, t = Date.now()) {
    const d = new Date(t);
    const tz = hass?.config?.time_zone;
    if (tz) {
      try {
        if (!clockFormats.has(tz)) {
          clockFormats.set(tz, new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "short", hour: "numeric", minute: "numeric", hourCycle: "h23" }));
        }
        const parts = Object.fromEntries(clockFormats.get(tz).formatToParts(d).map((x) => [x.type, x.value]));
        const day = DAYS.indexOf(parts.weekday);
        if (day >= 0) return { day, minute: (Number(parts.hour) % 24) * 60 + Number(parts.minute) };
      } catch (e) {
        clockFormats.delete(tz); // not a zone this browser knows: use its own
      }
    }
    return { day: (d.getDay() + 6) % 7, minute: d.getHours() * 60 + d.getMinutes() };
  }
  function fmtMinutes(hass, m) {
    const d = new Date(2000, 0, 1, Math.floor(m / 60) % 24, m % 60);
    return d.toLocaleTimeString(hass?.locale?.language || undefined, { hour: "numeric", minute: "2-digit", hour12: hour12(hass) });
  }
  function fmtWhen(hass, iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const lang = hass?.locale?.language || undefined;
    const time = d.toLocaleTimeString(lang, { hour: "numeric", minute: "2-digit", hour12: hour12(hass), ...zone(hass) });
    const date = (x) => x.toLocaleDateString("en-CA", zone(hass));
    if (date(d) === date(new Date())) return time;
    const day = d.toLocaleDateString(lang, { weekday: "short", ...zone(hass) });
    return `${day} ${time}`;
  }

  // Like fmtWhen, with the date once it's more than six days away ("Nov 14, 3:00 PM").
  function fmtWhenFar(hass, iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Math.abs(d - Date.now()) < 6 * 864e5) return fmtWhen(hass, iso);
    const lang = hass?.locale?.language || undefined;
    return `${d.toLocaleDateString(lang, { month: "short", day: "numeric", ...zone(hass) })}, ${d.toLocaleTimeString(lang, { hour: "numeric", minute: "2-digit", hour12: hour12(hass), ...zone(hass) })}`;
  }

  // A Date as a datetime-local input value (local time, to the minute).
  const localInput = (d) => {
    const t = new Date(d.getTime() - d.getTimezoneOffset() * 60e3);
    return t.toISOString().slice(0, 16);
  };

  // Keep heat at least `db` below cool, pushing whichever side the user didn't touch.
  function guard(heat, cool, changed, db, lo, hi) {
    let note = "";
    if (cool - heat < db) {
      if (changed === "cool") {
        heat = cool - db;
        note = `Heat moved to ${fmtTemp(heat)}° to keep a ${db}° gap`;
      } else {
        cool = heat + db;
        note = `Cool moved to ${fmtTemp(cool)}° to keep a ${db}° gap`;
      }
    }
    if (cool > hi) {
      cool = hi;
      heat = Math.min(heat, cool - db);
      note = `Cool can't go above ${hi}°`;
    }
    if (heat < lo) {
      heat = lo;
      cool = Math.max(cool, heat + db);
      note = `Heat can't go below ${lo}°`;
    }
    return { heat: round1(heat), cool: round1(cool), note };
  }

  const sameSettings = (a, b) =>
    a.heat === b.heat && a.cool === b.cool && a.label === b.label && (a.mode || null) === (b.mode || null) && (a.fan || null) === (b.fan || null);

  // Put `block` (running from block.start until `end`) into `day`: whatever was there in
  // that time range is replaced, the block before it is cut short, the one it overlapped
  // resumes at `end`, and neighbours left with identical settings are merged.
  function insertBlock(day, block, end) {
    const start = block.start;
    const out = day.filter((q) => q.start < start).map((q) => ({ ...q }));
    out.push({ ...block, start });
    const after = day.filter((q) => q.start >= end).map((q) => ({ ...q }));
    if (end < 1440 && !(after.length && after[0].start === end)) {
      let resumes = day[0];
      day.forEach((q) => { if (q.start < end) resumes = q; });
      out.push({ ...resumes, start: end });
    }
    out.push(...after);
    return out.filter((q, k) => k === 0 || !sameSettings(q, out[k - 1]));
  }

  const MODE_LABELS = { off: "Off", heat: "Heat", cool: "Cool", heat_cool: "Heat/Cool" };
  const MODE_ICONS = { off: "mdi:power", heat: "mdi:fire", cool: "mdi:snowflake", heat_cool: "mdi:sun-snowflake-variant" };

  // Colors picked per label in the schedule editor (saved with the schedule). Each card sets
  // this from its thermostat before drawing; labels without one use the keyword defaults.
  let LABEL_COLORS = {};
  const PALETTE = ["#6A60D6", "#D08A2A", "#8C98A5", "#2E9E6B", "#3A8FB7", "#B2567A", "#C0663A", "#7A9A2E", "#D4537E", "#1D9E75"];

  // Details popup for one schedule block (schedule card and the thermostat's week strip).
  function blockInfoHtml(hass, week, d, i, fans) {
    const day = week[d], p = day[i];
    const end = i + 1 < day.length ? day[i + 1].start : 1440;
    return `<div class="ph"><span class="sw" style="background:${labelColor(p.label)}"></span>${esc(p.label)}</div>
      <div class="ps">${DAYS[d]} · ${esc(fmtMinutes(hass, p.start))} – ${esc(fmtMinutes(hass, end))}</div>
      <dl>
        <dt>Heat to</dt><dd style="color:var(--cs-heat-ink)">${fmtTemp(p.heat)}°</dd>
        ${p.cool === undefined ? "" : `<dt>Cool to</dt><dd style="color:var(--cs-cool-ink)">${fmtTemp(p.cool)}°</dd>`}
        <dt>Mode</dt><dd>${p.mode ? MODE_LABELS[p.mode] : "Keep current"}</dd>
        <dt>Fan</dt><dd>${p.fan ? esc(fanInfo(p.fan, fans || []).label) : "Keep current"}</dd>
      </dl>`;
  }

  function labelColor(label) {
    if (LABEL_COLORS[label]) return LABEL_COLORS[label];
    const s = String(label || "").toLowerCase();
    if (s.includes("sleep") || s.includes("night")) return "#6A60D6";
    if (s.includes("wake") || s.includes("morning")) return "#D08A2A";
    if (s.includes("away") || s.includes("work")) return "#8C98A5";
    if (s.includes("home") || s.includes("evening")) return "#2E9E6B";
    const palette = ["#B2567A", "#3A8FB7", "#7A9A2E", "#C0663A", "#5B7C99"];
    let h = 0;
    for (const ch of s) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return palette[h % palette.length];
  }

  const BASE_CSS = `
    :host {
      display: block;
      --cs-heat: var(--state-climate-heat-color, #ff8100);
      --cs-cool: var(--state-climate-cool-color, #2b9af9);
      --cs-fan: var(--state-climate-fan_only-color, #00bcd4);
      --cs-bg: var(--ha-card-background, var(--card-background-color, #fff));
      --cs-soft: var(--secondary-background-color, #f1f1f1);
      --cs-line: var(--divider-color, #e0e0e0);
      --cs-text: var(--primary-text-color, #212121);
      --cs-muted: var(--secondary-text-color, #727272);
      --cs-accent: var(--primary-color, #03a9f4);
      --cs-ok: var(--success-color, #43a047);
      --cs-bad: var(--error-color, #db4437);
      --cs-warn: var(--warning-color, #ffa600);
      /* Text in the heat/cool/fan/accent colors. Those colors are tuned for dark cards; on a
         light card they're too pale to read (orange on white is ~2.6:1), so light themes pull
         them toward the text color, and white-on-color labels get a darker fill. */
      --cs-ink: 100%;
      --cs-heat-ink: color-mix(in srgb, var(--cs-heat) var(--cs-ink), var(--cs-text));
      --cs-cool-ink: color-mix(in srgb, var(--cs-cool) var(--cs-ink), var(--cs-text));
      --cs-fan-ink: color-mix(in srgb, var(--cs-fan) var(--cs-ink), var(--cs-text));
      --cs-accent-ink: color-mix(in srgb, var(--cs-accent) var(--cs-ink), var(--cs-text));
      --cs-accent-fill: color-mix(in srgb, var(--cs-accent) var(--cs-ink), #000);
    }
    :host([light]) { --cs-ink: 57%; }
    ha-card { padding: 16px; display: grid; gap: 16px; color: var(--cs-text); }
    .muted { color: var(--cs-muted); }
    .small { font-size: var(--ha-font-size-s, 12px); }
    .title { font-size: var(--ha-font-size-l, 16px); font-weight: var(--ha-font-weight-medium, 500); }
    /* Transient messages and hints. The line takes no room of its own: it draws in the gap
       above the next row (which moves up to cancel most of its own gap), so nothing shifts when a
       message appears and an empty one leaves no hole. */
    .msg {
      height: 0; font-size: var(--ha-font-size-s, 12px); line-height: 16px; text-align: center;
      color: var(--cs-muted); white-space: nowrap; transform: translateY(-16px);
    }
    .msg + * { margin-top: -8px; }
    .msg.toast { color: var(--cs-accent-ink); }
    /* Shared control sizes: every button, dropdown and text field is 36px tall. */
    .btn {
      font: inherit; font-size: var(--ha-font-size-m, 14px); border: 1px solid var(--cs-line); background: transparent;
      color: var(--cs-text); border-radius: 8px; height: 36px; padding: 0 14px; cursor: pointer; line-height: 1; white-space: nowrap;
      display: inline-flex; align-items: center; justify-content: center;
    }
    .btn:hover:not(:disabled) { background: var(--cs-soft); }
    .btn.primary { background: var(--cs-accent-fill); border-color: var(--cs-accent-fill); color: var(--text-primary-color, #fff); }
    .btn.primary:hover:not(:disabled) { background: var(--cs-accent-fill); filter: brightness(1.08); }
    .btn:disabled { opacity: .4; cursor: default; }
    button:focus-visible, select:focus-visible, input:focus-visible { outline: 2px solid var(--cs-accent); outline-offset: 2px; }
    select, input[type="text"] {
      font: inherit; font-size: var(--ha-font-size-m, 14px); border: 1px solid var(--cs-line); background: var(--cs-bg);
      color: var(--cs-text); border-radius: 8px; height: 36px; padding: 0 8px; box-sizing: border-box; min-width: 0;
    }
    /* +/- set point buttons: soft tonal squares tinted by --tc (heat or cool). */
    .sbtn {
      width: 36px; height: 36px; flex: none; border-radius: 10px; padding: 0; cursor: pointer;
      display: grid; place-items: center; --mdc-icon-size: 20px; color: color-mix(in srgb, var(--tc) var(--cs-ink), var(--cs-text));
      background: color-mix(in srgb, var(--tc) 14%, transparent);
      border: 1px solid color-mix(in srgb, var(--tc) 28%, transparent);
      transition: background .15s, border-color .15s;
    }
    .sbtn:hover:not(:disabled) { background: color-mix(in srgb, var(--tc) 24%, transparent); border-color: color-mix(in srgb, var(--tc) 45%, transparent); }
    .sbtn:active:not(:disabled) { background: color-mix(in srgb, var(--tc) 34%, transparent); }
    .sbtn:disabled { cursor: default; }
    .heat { --tc: var(--cs-heat); } .cool { --tc: var(--cs-cool); }
    .status {
      min-height: 56px; box-sizing: border-box; border-radius: 10px; padding: 10px 12px; font-size: var(--ha-font-size-m, 14px); background: var(--cs-soft);
      display: flex; align-items: center; justify-content: center; gap: 10px; flex-wrap: wrap; text-align: center;
    }
    .status.hold { background: color-mix(in srgb, var(--cs-warn) 16%, var(--cs-bg)); }
    .status b { font-weight: var(--ha-font-weight-medium, 500); }
    .warn { color: var(--cs-bad); }
    /* Tap-for-details popup. */
    .pop {
      position: absolute; z-index: 10; min-width: 190px; max-width: 260px; box-sizing: border-box; padding: 10px 12px;
      border-radius: 12px; border: 1px solid var(--cs-line); background: var(--cs-bg); color: var(--cs-text);
      box-shadow: 0 6px 20px rgba(0,0,0,.28); font-size: var(--ha-font-size-s, 13px); line-height: 1.35;
    }
    .pop .ph { display: flex; align-items: center; gap: 8px; font-weight: var(--ha-font-weight-medium, 500); font-size: var(--ha-font-size-m, 14px); }
    .pop .ph .sw { width: 10px; height: 10px; border-radius: 3px; flex: none; }
    .pop .ps { color: var(--cs-muted); margin: 2px 0 8px; font-variant-numeric: tabular-nums; }
    .pop dl { margin: 0; display: grid; grid-template-columns: auto auto; gap: 3px 14px; }
    .pop dt { color: var(--cs-muted); }
    .pop dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
    [data-pop] { cursor: pointer; }
    .heading { min-width: 0; }
    /* Runtime change vs the stretch before ("↑12%"). */
    .chg { display: inline-flex; align-items: center; color: var(--cs-muted); --mdc-icon-size: 13px; font-variant-numeric: tabular-nums; }
    .chg ha-icon { display: inline-flex; }
    /* Subtitle without a title: center it and the header's buttons on each other. */
    .head:has(> .heading.notitle) { align-items: center; }
    /* Small segmented switch (health 3h/12h/24h, runtime Day/Week/Month). Per viewer; the card
       option is only the starting point. */
    .range { display: inline-flex; gap: 2px; padding: 3px; border-radius: 8px; background: var(--cs-soft); }
    .range button {
      font: inherit; font-size: var(--ha-font-size-s, 12px); border: 0; border-radius: 6px; padding: 3px 10px; cursor: pointer;
      background: transparent; color: var(--cs-muted); font-variant-numeric: tabular-nums;
    }
    .range button:hover { color: var(--cs-text); }
    .range button.on { background: var(--cs-bg); color: var(--cs-text); box-shadow: 0 1px 2px rgba(0,0,0,.15); font-weight: var(--ha-font-weight-medium, 500); }
    /* A button in a card header, the same height as the .range switch beside other titles. */
    .btn.hbtn { height: 28px; padding: 0 10px; gap: 4px; font-size: var(--ha-font-size-s, 12px); --mdc-icon-size: 16px; }
  `;

  // Visual editor forms. Home Assistant renders these with its own form controls, so every
  // YAML option is also editable without YAML. Opt-outs are hide_* so "unchecked" always
  // means "shown", even for cards saved before the option existed.
  const FORM_LABELS = {
    entity: "Thermostat",
    name: "Name",
    title: "Title",
    hide_title: "Hide title",
    hide_subtitle: "Hide subtitle",
    hide_mode: "Hide mode",
    hide_fan: "Hide fan",
    hide_humidity: "Hide humidity",
    hide_battery: "Hide battery",
    battery_entity: "Battery sensor",
    humidity_entity: "Humidity sensor",
    read_only: "Read-only (no Edit button)",
    hours: "Hours of history",
    days: "Days shown",
    view: "Starting view",
    style: "Style",
    mode_layout: "Mode buttons",
    fan_layout: "Fan buttons",
    appearance: "Appearance",
    temperature: "Temperature and set points",
    controls: "Mode and fan",
    schedule: "Schedule",
    runtime: "Runtime",
    editing: "Editing",
    chart: "Chart",
    content: "What to show",
    hide_targets: "Hide set points",
    hide_target_buttons: "Hide set point − / + buttons",
    layout: "Layout",
    week_expanded: "Show schedule",
    week_position: "Schedule position",
    show: "Show",
    show_runtime: "Show runtime",
    runtime_style: "Runtime style",
    runtime_show: "Runtime shows",
    totals: "Totals",
    chart_style: "Chart bars",
    weeks: "Weeks shown",
    months: "Months shown",
    hide_view_switch: "Hide the Day/Week/Month switch",
    hide_compare: "Hide the comparison",
    hide_chart: "Hide the chart",
    hide_legend: "Hide the legend",
    hide_suggestions: "Hide schedule suggestions",
    hide_cost: "Hide the cost estimate",
    hide_outdoor: "Hide the outdoor temperature",
  };
  const FORM_HELP = {
    entity: "A Comfort Schedule thermostat.",
    name: "Defaults to the thermostat's name.",
    days: "Used by the Day view.",
    weeks: "Used by the Week view.",
    months: "Used by the Month view.",
    view: "Viewers can still switch between Day, Week and Month on the card, unless the switch is hidden.",
    show: "Only heating or only cooling, e.g. for a heat-only system.",
    show_runtime: "A heating and cooling time row under the schedule status.",
    hide_suggestions: "Suggestions come from manual changes that keep happening. Nothing changes until you apply one.",
    hide_cost: "Shown when a cost per hour is set in the integration's options.",
    runtime_style: "Today: time so far today and the 7-day change. Last 7 days: a small bar per day.",
    runtime_show: "Only heating or only cooling, e.g. for a heat-only system.",
    totals: "Compact shows only today's time in each tile.",
    chart_style: "Stacked shows the combined time for each day, week or month.",
    hide_target_buttons: "Set points become plain values, changed on the range bar or dial. In the compact classic layout, tap a value.",
    style: "Classic: the indoor temperature over a range bar. Dial: the indoor temperature inside a ring you drag the set points around.",
    layout: "Compact is smaller, has no range bar, and turns mode and fan into two menu buttons.",
    fan_layout: "Buttons shows every fan mode by name. Compact is one row: the current fan mode and an icon per mode.",
    mode_layout: "Compact is one row like the fan: the current mode and an icon per mode. Not used by the compact card layout, which has its own menus.",
    week_expanded: "Shows the week at a glance when the card loads. The calendar button in the header shows or hides it.",
    week_position: "Under the header, or at the bottom of the card below the schedule status.",
    battery_entity: "Leave empty to use the thermostat's own battery sensor.",
    humidity_entity: "Leave empty to use the thermostat's own humidity sensor.",
  };
  // Dropdown selector for the visual editor: choose([["full", "Full"], ["compact", "Compact"]]).
  const choose = (options) => ({ select: { mode: "dropdown", options: options.map(([value, label]) => ({ value, label })) } });
  const grid = (schema) => ({ type: "grid", name: "", schema });
  // A collapsible section of the visual editor. flatten keeps its options at the top level of
  // the card's YAML, so grouping them never changes the config.
  const section = (name, title, icon, schema, expanded = false) => ({ type: "expandable", name, title, icon, flatten: true, expanded, schema });
  const toggle = (name) => ({ name, selector: { boolean: {} } });
  // Every card's editor opens on the same first section: the title field (card-specific
  // extras after it), then hiding the title and subtitle.
  const appearanceSection = (titleField, extra = []) =>
    section("appearance", "Appearance", "mdi:palette-outline", [
      { name: titleField, selector: { text: {} } },
      ...extra,
      grid([toggle("hide_title"), toggle("hide_subtitle")]),
    ], true);

  function configForm(fields) {
    return {
      schema: [{ name: "entity", required: true, selector: { entity: { domain: "climate", integration: DOMAIN } } }, ...fields],
      computeLabel: (s) => FORM_LABELS[s.name],
      computeHelper: (s) => FORM_HELP[s.name],
    };
  }

  class ComfortBase extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this._msg = "";
      this.shadowRoot.addEventListener("click", (e) => {
        const el = e.target.closest("[data-action]");
        // Any tap that isn't on a details target (or inside the popup) closes an open popup.
        if (this._pop && !e.target.closest(".pop") && !(el && el.dataset.pop)) this._closePop();
        if (el && !el.disabled && el.tagName !== "SELECT" && el.tagName !== "INPUT") this._onAction(el.dataset.action, el, e);
      });
      this.shadowRoot.addEventListener("change", (e) => {
        const el = e.target.closest("[data-action]");
        if (el) this._onAction(el.dataset.action, el, e);
      });
      this.shadowRoot.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && this._pop) this._closePop();
        // Details targets that aren't real buttons still open with Enter/Space.
        if ((e.key === "Enter" || e.key === " ") && e.target.dataset && e.target.dataset.pop && e.target.tagName !== "BUTTON") {
          e.preventDefault();
          // SVG elements (chart bars, the health chart) have no .click(), so send the event.
          e.target.dispatchEvent(new MouseEvent("click", { bubbles: true, composed: true }));
        }
      });
    }

    // ---------------------------------------------------------------- tap-for-details popup

    // Open (or, tapping the same thing again, close) a details popup anchored to `key`,
    // a selector for the tapped element. Works by tap, so it's usable on phones.
    _togglePop(key, html) {
      if (this._pop && this._pop.key === key) return this._closePop();
      this._pop = { key, html, focus: true, ctx: this._popContext(this._st) };
      // Keep the popup attached to its anchor if the card changes size while it's open.
      if (!this._popResize) {
        // Redraw after the resize settles (never inside the observer callback, which would
        // trigger "ResizeObserver loop" warnings), and only when the width really changed.
        this._popResize = new ResizeObserver((entries) => {
          const w = Math.round(entries[0].contentRect.width);
          if (w === this._popWidth) return;
          this._popWidth = w;
          clearTimeout(this._popResizeTimer);
          this._popResizeTimer = setTimeout(() => {
            if (this._pop && !this._drag && !this._gripping) this._render();
          }, 60);
        });
        this._popResize.observe(this);
      }
      if (!this._outside) {
        this._outside = (e) => { if (!e.composedPath().includes(this)) this._closePop(); };
        document.addEventListener("pointerdown", this._outside, true);
      }
      this._render();
      return undefined;
    }

    _closePop() {
      if (!this._pop) return;
      this._dropPop();
      this._render();
    }

    // Forget the popup and its listeners, without redrawing.
    _dropPop() {
      this._pop = null;
      this._popResize?.disconnect();
      this._popResize = null;
      this._popWidth = undefined;
      clearTimeout(this._popResizeTimer);
      if (this._outside) document.removeEventListener("pointerdown", this._outside, true);
      this._outside = null;
    }

    disconnectedCallback() {
      this._dropPop();
    }

    // What a menu was built for. When it changes (a hold ends, the schedule is paused), the
    // menu's choices and times are out of date, so it closes.
    _popContext(st) {
      const a = st?.attributes || {};
      return JSON.stringify([st?.state, a.status, a.schedule_enabled, a.hold?.until, a.vacation?.start, a.vacation?.end]);
    }

    // Called after every paint: place the popup under its anchor, kept inside the card,
    // flipping above the anchor when there's no room below.
    _placePop() {
      const card = this.shadowRoot.querySelector("ha-card");
      if (!card || !this._pop) return;
      const pop = document.createElement("div");
      pop.className = "pop";
      pop.setAttribute("role", "dialog");
      pop.style.visibility = "hidden";
      pop.innerHTML = this._pop.html;
      card.appendChild(pop);
      // ha-card is a Lit element that renders its <slot> asynchronously; until then nothing
      // inside it has a layout box and every measurement reads 0,0. Measure once it's drawn.
      const place = () => {
        const anchor = this._pop && this.shadowRoot.querySelector(this._pop.key);
        if (!anchor || !pop.isConnected) return;
        const a = anchor.getBoundingClientRect();
        const c = card.getBoundingClientRect();
        const w = pop.offsetWidth, h = pop.offsetHeight;
        const left = Math.max(8, Math.min(c.width - w - 8, a.left + a.width / 2 - c.left - w / 2));
        let top = a.bottom - c.top + 6;
        if (top + h > c.height - 4 && a.top - c.top - h - 6 > 0) top = a.top - c.top - h - 6;
        pop.style.left = `${left}px`;
        pop.style.top = `${top}px`;
        pop.style.visibility = "";
        // Menus: put focus on the current choice when first opened (not on every redraw).
        if (this._pop.focus) {
          this._pop.focus = false;
          (pop.querySelector(".mi.on") || pop.querySelector(".mi"))?.focus({ preventScroll: true });
        }
      };
      // updateComplete resolves once Lit has put the <slot> in place; measuring then forces layout.
      Promise.resolve(card.updateComplete).then(place);
    }

    setConfig(config) {
      if (!config || !config.entity) throw new Error('Set "entity" to a Comfort Schedule thermostat (climate.…)');
      this._config = config;
      if (this._hass) this._st = this._hass.states[config.entity]; // the editor may have picked another entity
      this._render();
    }

    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      this.toggleAttribute("light", hass.themes?.darkMode === false);
      const st = this._config ? hass.states[this._config.entity] : undefined;
      if (first || st !== this._st) {
        this._st = st;
        if (this._pop && !this._pop.form && this._pop.ctx !== this._popContext(st)) this._dropPop();
        // A form in the popup would lose what's being typed; it redraws when it closes.
        if (!this._pop?.form) this._render();
      }
    }

    get stateObj() {
      return this._st;
    }

    static getStubConfig(hass) {
      const id = Object.keys(hass.states).find(
        (e) => e.startsWith("climate.") && "heat_setpoint" in hass.states[e].attributes && "schedule" in hass.states[e].attributes
      );
      return { entity: id || "" };
    }

    static getConfigForm() {
      return configForm([]);
    }

    getGridOptions() {
      return { columns: 12, min_columns: 6 };
    }

    _call(domain, service, data = {}) {
      return this._hass
        .callService(domain, service, { entity_id: this._config.entity, ...data })
        .catch((err) => this._toast(err?.message || String(err)));
    }

    _toast(msg) {
      this._msg = msg || "";
      clearTimeout(this._msgTimer);
      if (msg) this._msgTimer = setTimeout(() => { this._msg = ""; this._render(); }, 4000);
      this._render();
    }

    // Redraw the card. The <ha-card> element itself is kept and only its contents replaced:
    // a new ha-card shows nothing until Home Assistant renders it a moment later, and if the
    // browser measures the page in that moment the card is briefly ~0px tall, the page gets
    // shorter, and the dashboard's scroll position jumps (e.g. to the top while editing).
    _paint(css, body) {
      const root = this.shadowRoot;
      const tpl = document.createElement("template");
      tpl.innerHTML = body.trim();
      const next = tpl.content.firstElementChild;
      const style = [...root.children].find((el) => el.tagName === "STYLE");
      const card = [...root.children].find((el) => el.tagName === "HA-CARD");
      if (!style || !card || !next || next.tagName !== "HA-CARD" || tpl.content.childElementCount !== 1) {
        root.innerHTML = `<style>${BASE_CSS}${css}</style>${body}`;
      } else {
        const text = `${BASE_CSS}${css}`;
        if (style.textContent !== text) style.textContent = text;
        // Same attributes as the new card (e.g. class="compact"), then the new contents.
        for (const { name } of [...card.attributes]) if (!next.hasAttribute(name)) card.removeAttribute(name);
        for (const { name, value } of [...next.attributes]) if (card.getAttribute(name) !== value) card.setAttribute(name, value);
        card.replaceChildren(...next.childNodes);
      }
      if (this._pop) this._placePop();
    }

    // Focus something in the freshly painted card. Like measuring, this has to wait for
    // ha-card to put its <slot> in place; before that nothing inside can take focus.
    _focusAfterPaint(selector) {
      const card = this.shadowRoot.querySelector("ha-card");
      Promise.resolve(card && card.updateComplete).then(() => this.shadowRoot.querySelector(selector)?.focus({ preventScroll: true }));
    }

    _missing() {
      if (!this._config) return true;
      if (!this._hass) return true;
      if (!this.stateObj) {
        this._paint("", `<ha-card><div class="warn">Entity not found: ${esc(this._config.entity)}</div></ha-card>`);
        return true;
      }
      LABEL_COLORS = this._labelColors();
      return false;
    }

    // An unavailable entity has none of its attributes: say so, not "no schedule".
    _offline(css) {
      if (!["unavailable", "unknown"].includes(this.stateObj.state)) return false;
      const name = this._config.name || this.stateObj.attributes.friendly_name || this._config.entity;
      this._paint(css, `<ha-card><div class="warn">${esc(name)} is unavailable right now.</div></ha-card>`);
      return true;
    }

    // Title and subtitle block for a card header; hide_title drops the title. `sub` is HTML
    // (callers escape it) and may be empty. Returns "" when there's nothing to show.
    _heading(title, sub) {
      const t = this._config.hide_title ? "" : `<div class="title">${esc(title)}</div>`;
      if (!t && !sub) return "";
      return `<div class="heading ${t ? "" : "notitle"}">${t}${sub ? `<div class="muted small sub">${sub}</div>` : ""}</div>`;
    }

    // Label colors in effect for this card (the schedule editor overrides this with its draft).
    _labelColors() {
      return this.stateObj.attributes.label_colors || {};
    }

    _onAction() {}
    _render() {}
  }

  /* ------------------------------------------------------------------ thermostat card */

  const THERMO_CSS = `
    .head { display: grid; grid-template-columns: 40px 1fr 40px; align-items: center; gap: 8px; text-align: center; }
    .icon-btn {
      width: 40px; height: 40px; border-radius: 50%; border: 1px solid var(--cs-line); background: transparent;
      color: var(--cs-muted); display: grid; place-items: center; cursor: pointer; padding: 0; --mdc-icon-size: 20px;
    }
    .icon-btn:hover { background: var(--cs-soft); color: var(--cs-text); }
    .icon-btn.on { background: color-mix(in srgb, var(--cs-text) 88%, var(--cs-bg)); border-color: transparent; color: var(--cs-bg); }
    .icon-btn.sched.on { background: color-mix(in srgb, var(--cs-accent) 14%, var(--cs-bg)); border-color: color-mix(in srgb, var(--cs-accent) 40%, var(--cs-bg)); color: var(--cs-accent-ink); }
    /* Sizes below follow the card's width (cqw), so the indoor temperature fills a wide card
       and still fits a phone. */
    ha-card { container-type: inline-size; }
    /* Indoor temperature, center stage: label, number, humidity/battery, what it's doing. */
    .hero { display: grid; justify-items: center; gap: 4px; text-align: center; }
    .hero .num { font-size: clamp(56px, 16cqw, 72px); line-height: 1; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }
    .compact .hero .num { font-size: clamp(44px, 12cqw, 56px); }
    .hero .num small { font-size: .3em; color: var(--cs-muted); vertical-align: top; position: relative; top: .3em; margin-left: 2px; letter-spacing: 0; }
    .hero .pill { margin-top: 4px; }
    /* Set points, second: "− 70° +" in the heat or cool tint, the pair centered. */
    .tcs { display: flex; justify-content: center; gap: 10px; }
    .tc {
      flex: 0 1 150px; min-width: 0; display: grid; grid-template-columns: 36px minmax(0, 1fr) 36px; align-items: center; gap: 4px;
      height: 44px; box-sizing: border-box; padding: 3px; border-radius: 12px;
      background: color-mix(in srgb, var(--tc) 10%, var(--cs-bg)); border: 1px solid color-mix(in srgb, var(--tc) 32%, var(--cs-bg));
      transition: opacity .2s, filter .2s;
    }
    .tc.nob { flex-basis: 104px; grid-template-columns: minmax(0, 1fr); padding: 3px 12px; }
    .tc.off { opacity: .38; filter: saturate(.2); }
    .tval {
      display: flex; align-items: center; justify-content: center; gap: 4px; min-width: 0; height: 100%; white-space: nowrap;
      font: inherit; font-size: 17px; font-weight: var(--ha-font-weight-medium, 500); font-variant-numeric: tabular-nums;
      color: color-mix(in srgb, var(--tc) var(--cs-ink), var(--cs-text)); --mdc-icon-size: 16px;
    }
    .tval ha-icon { display: inline-flex; flex: none; }
    button.tval { width: 100%; padding: 0; border: 0; border-radius: 9px; background: none; cursor: pointer; }
    button.tval:hover { background: color-mix(in srgb, var(--tc) 14%, transparent); }
    .tstep { display: grid; grid-template-columns: 36px minmax(64px, auto) 36px; align-items: center; gap: 8px; margin-top: 8px; }
    .tstep .tval { font-size: 22px; }
    /* Dial style: a 270° ring (heat zone from the start, cool zone to the end) around the
       indoor temperature; set points sit in the opening at the bottom. */
    .dial { position: relative; width: min(100%, 300px); justify-self: center; }
    .compact .dial { width: min(100%, 232px); }
    .dsv { display: block; width: 100%; height: auto; touch-action: none; user-select: none; -webkit-user-select: none; overflow: visible; }
    .dsv .trk, .dsv .dz { fill: none; stroke-width: 14; stroke-linecap: round; }
    .dsv .trk { stroke: var(--cs-soft); }
    .dsv .dz.h { stroke: color-mix(in srgb, var(--cs-heat) 38%, var(--cs-soft)); }
    .dsv .dz.c { stroke: color-mix(in srgb, var(--cs-cool) 38%, var(--cs-soft)); }
    .dsv .cm { stroke: var(--cs-text); stroke-width: 3; stroke-linecap: round; pointer-events: none; }
    .dsv .dh { fill: var(--tc); stroke: var(--cs-bg); stroke-width: 3; cursor: grab; outline: none; }
    .dsv .dh:focus-visible { stroke: var(--cs-accent); }
    .dsv .dh.drag { cursor: grabbing; stroke-width: 4; }
    .dsv .tipv { font-size: 12px; font-weight: var(--ha-font-weight-medium, 500); fill: color-mix(in srgb, var(--tc) var(--cs-ink), var(--cs-text)); pointer-events: none; }
    .dial .hero { position: absolute; left: 0; right: 0; top: 59.8%; transform: translateY(-50%); pointer-events: none; }
    .dial .hero button { pointer-events: auto; }
    .dial .hero .num { font-size: 60px; }
    .compact .dial .hero .num { font-size: 46px; }
    .compact .dial .hero .stats { display: none; }
    /* Set points in the ring's opening (lined up under its two ends). On a narrow card or the
       compact layout there isn't room, so they sit under the dial instead. */
    .tcs.dialt { width: min(100%, 300px); justify-self: center; justify-content: space-between; margin-top: -14px; position: relative; }
    .tcs.dialt .tc { flex: 0 1 140px; }
    .tcs.dialt.nob { padding: 0 20px; box-sizing: border-box; }
    .tcs.dialt.nob .tc { flex-basis: 92px; }
    .compact .tcs.dialt { width: auto; justify-self: stretch; justify-content: center; margin-top: 0; padding: 0; }
    @container (max-width: 289px) {
      .tcs.dialt { width: auto; justify-self: stretch; justify-content: center; margin-top: 0; padding: 0; }
    }
    /* Humidity and battery: one small line under the indoor temperature. */
    .stats { display: flex; justify-content: center; gap: 10px; min-height: 18px; }
    .stat {
      display: inline-flex; align-items: center; gap: 2px; padding: 0 2px; border: 0; background: none; border-radius: 6px;
      font: inherit; font-size: var(--ha-font-size-s, 12px); font-variant-numeric: tabular-nums; color: var(--cs-muted);
      --mdc-icon-size: 15px;
    }
    button.stat { cursor: pointer; }
    button.stat:hover { color: var(--cs-text); }
    .stat.low { color: var(--cs-bad); }
    .pill {
      font-size: var(--ha-font-size-s, 12px); font-weight: var(--ha-font-weight-medium, 500); padding: 3px 10px; border-radius: 99px;
      background: var(--cs-soft); color: var(--cs-muted); display: inline-flex; align-items: center; gap: 4px; --mdc-icon-size: 14px;
    }
    .pill.heating { background: color-mix(in srgb, var(--cs-heat) 16%, var(--cs-bg)); color: var(--cs-heat-ink); }
    .pill.cooling { background: color-mix(in srgb, var(--cs-cool) 16%, var(--cs-bg)); color: var(--cs-cool-ink); }
    .pill.fan { background: color-mix(in srgb, var(--cs-fan) 16%, var(--cs-bg)); color: var(--cs-fan-ink); }
    /* Schedule status: "Now -> Next" with progress, or Hold / Paused. Same height in every state. */
    .st { border-radius: 12px; padding: 10px 14px; min-height: 72px; box-sizing: border-box; background: var(--cs-soft); display: grid; align-items: center; gap: 12px; }
    .st.follow { grid-template-columns: minmax(0, 1fr) minmax(84px, auto) minmax(0, 1fr); }
    .st.hold { background: color-mix(in srgb, var(--cs-warn) 12%, var(--cs-bg)); }
    .st .cap { font-size: 11px; font-weight: var(--ha-font-weight-medium, 500); letter-spacing: .09em; text-transform: uppercase; color: var(--cs-muted); }
    .st .who { display: flex; align-items: center; gap: 6px; margin-top: 3px; min-width: 0; font-size: var(--ha-font-size-m, 14px); }
    .st .who .sw { width: 10px; height: 10px; border-radius: 3px; flex: none; background: var(--bc); }
    .st .who .lbl { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .st .rng { margin-top: 1px; font-size: var(--ha-font-size-s, 12px); color: var(--cs-muted); font-variant-numeric: tabular-nums; }
    .st .now .rng { padding-left: 16px; } .st .next .rng { padding-right: 16px; }
    .st .now .lbl { font-weight: var(--ha-font-weight-medium, 500); }
    .st .next { text-align: right; } .st .next .who { flex-direction: row-reverse; } .st .next .lbl { color: var(--cs-muted); }
    .st .mid { text-align: center; font-size: var(--ha-font-size-s, 12px); color: var(--cs-muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
    .st .bar { height: 4px; margin-top: 6px; border-radius: 2px; overflow: hidden; background: color-mix(in srgb, var(--cs-text) 12%, transparent); }
    .st .bar i { display: block; height: 100%; border-radius: 2px; background: var(--bc); }
    .st.hold { --hc: color-mix(in srgb, var(--cs-warn) 75%, var(--cs-text)); }
    .st.hold .now .cap, .st.hold .now ha-icon { color: var(--hc); }
    .st .now .who ha-icon { --mdc-icon-size: 16px; flex: none; color: var(--cs-muted); }
    .st.hold .now .lbl { font-variant-numeric: tabular-nums; }
    /* "until 16:00 v": opens the hold-length menu. */
    .st .until {
      display: inline-flex; align-items: center; gap: 2px; padding: 2px 6px 2px 8px; border-radius: 99px; cursor: pointer;
      font: inherit; font-size: var(--ha-font-size-s, 12px); font-variant-numeric: tabular-nums; color: var(--cs-text);
      border: 1px solid color-mix(in srgb, var(--cs-text) 16%, transparent); background: color-mix(in srgb, var(--cs-bg) 55%, transparent);
    }
    .st .until:hover, .st .until.open { background: var(--cs-bg); border-color: color-mix(in srgb, var(--cs-text) 30%, transparent); }
    .st .until ha-icon { --mdc-icon-size: 16px; color: var(--cs-muted); transition: transform .15s; }
    /* Times have no descenders, so they look high next to the chevron; drop them a pixel. */
    .st .until > span { position: relative; top: 1px; }
    .st .until.open ha-icon { transform: rotate(180deg); }
    .st.away { background: color-mix(in srgb, var(--cs-muted) 10%, var(--cs-bg)); }
    .st.away .now .cap, .st.away .now ha-icon { color: var(--cs-muted); }
    .st.early .now .cap { color: var(--cs-accent-ink); }
    /* Vacation form, in the popup. */
    .pop:has(.vac) { padding: 12px; width: min(300px, calc(100% - 16px)); box-sizing: border-box; }
    .vac { display: grid; gap: 10px; margin: 0; }
    .vac .vh { display: flex; align-items: center; gap: 8px; font-weight: var(--ha-font-weight-medium, 500); --mdc-icon-size: 18px; }
    .vac .vh ha-icon { color: var(--cs-warn); }
    .vac label { display: grid; gap: 4px; min-width: 0; }
    .vac label span { font-size: 11px; font-weight: var(--ha-font-weight-medium, 500); color: var(--cs-muted); }
    .vac label.heat span { color: var(--cs-heat); }
    .vac label.cool span { color: var(--cs-cool); }
    .vac input {
      font: inherit; color: var(--cs-text); background: var(--cs-bg); min-width: 0; box-sizing: border-box; width: 100%;
      border: 1px solid var(--cs-line); border-radius: 8px; padding: 6px 8px; color-scheme: light dark;
    }
    .vac input:focus-visible { outline: 2px solid var(--cs-accent); outline-offset: -1px; }
    .vac .vr { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .vac .vnote { font-size: var(--ha-font-size-s, 12px); color: var(--cs-muted); }
    .vac .vb { display: flex; justify-content: flex-end; gap: 8px; }
    /* Hold-length menu, shown in the card's popup. */
    .pop:has(.menu) { padding: 4px; min-width: 280px; max-width: min(340px, calc(100% - 16px)); }
    .mi .ml { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .menu { display: grid; gap: 2px; }
    .mi {
      display: grid; grid-template-columns: 20px minmax(0, 1fr) auto 18px; align-items: center; gap: 10px; width: 100%;
      padding: 8px 10px; border: 0; border-radius: 8px; background: transparent; color: var(--cs-text); cursor: pointer;
      font: inherit; font-size: var(--ha-font-size-m, 14px); text-align: left;
    }
    .mi:hover, .mi:focus-visible { background: var(--cs-soft); outline: none; }
    .mi .mic { --mdc-icon-size: 18px; color: var(--cs-muted); }
    .mi .me { font-size: var(--ha-font-size-s, 12px); color: var(--cs-muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
    .mi .mck { --mdc-icon-size: 18px; color: var(--cs-accent-ink); visibility: hidden; }
    .mi.on { background: color-mix(in srgb, var(--cs-accent) 12%, var(--cs-bg)); }
    .mi.on .mic, .mi.on .ml { color: var(--cs-accent-ink); }
    .mi.on .mck { visibility: visible; }
    /* Resume: styled like the Next column, but a button. */
    .st .resume {
      justify-self: end; min-width: 0; max-width: 100%; box-sizing: border-box; font: inherit; color: inherit; text-align: right; cursor: pointer;
      padding: 6px 10px; margin: -6px -10px -6px 0; border-radius: 10px; border: 1px solid var(--cs-line); background: var(--cs-bg);
    }
    .st .resume:hover { border-color: color-mix(in srgb, var(--cs-accent) 50%, var(--cs-line)); }
    .st .resume .cap { color: var(--cs-accent-ink); }
    .st .resume .who { flex-direction: row-reverse; }
    /* Phone width: a little more room for the period names either side. */
    @container (max-width: 400px) {
      .st { gap: 8px; padding: 10px 12px; }
      .st .resume { padding: 6px 8px; margin-right: -6px; }
      .st.paused { grid-template-columns: minmax(0, 1fr) auto; }
      .st.paused .mid { display: none; }
    }
    .pill.waiting { background: transparent; box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--tc) 55%, transparent); color: color-mix(in srgb, var(--tc) var(--cs-ink), var(--cs-text)); }
    /* Range bar: orange zone below the heat point (where it heats), blue zone above the cool
       point (where it cools), comfort space between. Handles drag; the room temperature is
       marked above the bar. */
    .band { position: relative; height: 62px; margin: 0 14px; touch-action: none; user-select: none; -webkit-user-select: none; }
    .band .track {
      position: absolute; left: 0; right: 0; top: 26px; height: 12px; border-radius: 6px; overflow: hidden;
      background: var(--cs-soft); cursor: pointer;
    }
    .band .zone { position: absolute; top: 0; bottom: 0; }
    .band .zone.h { left: 0; background: color-mix(in srgb, var(--cs-heat) 30%, transparent); }
    .band .zone.c { right: 0; background: color-mix(in srgb, var(--cs-cool) 30%, transparent); }
    .band .thumb {
      position: absolute; top: 20px; width: 24px; height: 24px; margin-left: -12px; box-sizing: border-box; z-index: 2;
      border-radius: 50%; background: var(--tc); border: 3px solid var(--cs-bg); cursor: grab; touch-action: none;
      box-shadow: 0 1px 3px rgba(0,0,0,.35); transition: transform .12s, box-shadow .12s;
    }
    .band .thumb:hover { transform: scale(1.1); }
    .band .thumb:focus-visible { outline: none; box-shadow: 0 1px 3px rgba(0,0,0,.35), 0 0 0 4px color-mix(in srgb, var(--tc) 35%, transparent); }
    .band .thumb.drag { cursor: grabbing; transform: scale(1.18); box-shadow: 0 2px 8px rgba(0,0,0,.4), 0 0 0 5px color-mix(in srgb, var(--tc) 25%, transparent); }
    .band .thumb .tip {
      position: absolute; bottom: 28px; left: 50%; transform: translateX(-50%); padding: 1px 7px; border-radius: 6px;
      background: color-mix(in srgb, var(--tc) var(--cs-ink), #000); color: #fff; font-size: 11px; font-weight: var(--ha-font-weight-medium, 500); white-space: nowrap;
      font-variant-numeric: tabular-nums; opacity: 0; pointer-events: none; transition: opacity .12s;
    }
    .band .thumb.drag .tip, .band .thumb:focus-visible .tip { opacity: 1; }
    .band .cm { position: absolute; top: 22px; width: 3px; height: 20px; margin-left: -1.5px; border-radius: 2px; background: var(--cs-text); z-index: 1; pointer-events: none; }
    .band .cm span {
      position: absolute; bottom: 22px; left: 50%; transform: translateX(-50%); white-space: nowrap;
      font-size: 11px; font-weight: var(--ha-font-weight-medium, 500); color: var(--cs-text); font-variant-numeric: tabular-nums;
    }
    .band .tick { position: absolute; top: 44px; font-size: 10px; color: var(--cs-muted); transform: translateX(-50%); font-variant-numeric: tabular-nums; pointer-events: none; }
    .group { display: grid; gap: 6px; }
    .glabel { font-size: 11px; font-weight: var(--ha-font-weight-medium, 500); letter-spacing: .09em; text-transform: uppercase; color: var(--cs-muted); text-align: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .seg { display: grid; gap: 4px; padding: 4px; border-radius: 14px; background: var(--cs-soft); }
    .mbtn {
      --mc: var(--cs-text); font: inherit; font-size: var(--ha-font-size-s, 13px); font-weight: var(--ha-font-weight-medium, 500);
      border: 1px solid transparent; border-radius: 10px; background: transparent; color: var(--cs-muted);
      padding: 9px 4px 8px; display: flex; flex-direction: column; align-items: center; gap: 4px; cursor: pointer; min-width: 0;
      --mdc-icon-size: 22px;
    }
    .mbtn:hover { color: var(--cs-text); }
    .mbtn.heat { --mc: var(--cs-heat); } .mbtn.cool { --mc: var(--cs-cool); } .mbtn.fan { --mc: var(--cs-fan); }
    .mbtn.sel {
      color: color-mix(in srgb, var(--mc) var(--cs-ink), var(--cs-text)); background: color-mix(in srgb, var(--mc) 12%, var(--cs-bg));
      border-color: color-mix(in srgb, var(--mc) 38%, var(--cs-bg)); box-shadow: 0 1px 3px rgba(0,0,0,.08);
    }
    .mbtn.auto.sel {
      color: var(--cs-text); border-color: color-mix(in srgb, var(--cs-text) 22%, var(--cs-bg));
      background: linear-gradient(90deg, color-mix(in srgb, var(--cs-heat) 14%, var(--cs-bg)), color-mix(in srgb, var(--cs-cool) 14%, var(--cs-bg)));
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    .spin ha-icon, ha-icon.spin { animation: spin 2.2s linear infinite; }
    @media (prefers-reduced-motion: reduce) { .spin ha-icon, ha-icon.spin { animation: none; } }
    /* Compact fan row: the current fan mode on the left, one icon button per mode on the right. */
    .fanrow {
      display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 10px;
      padding: 4px 4px 4px 14px; border-radius: 14px; background: var(--cs-soft);
    }
    .fanrow .fl { display: flex; align-items: center; gap: 10px; min-width: 0; --mdc-icon-size: 20px; }
    .fanrow .fl > ha-icon { color: var(--cs-fan-ink); flex: none; }
    .fanrow .fl .glabel { text-align: left; }
    .fanrow .fv { font-size: var(--ha-font-size-m, 14px); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .fanrow .fv span { color: var(--cs-muted); font-size: var(--ha-font-size-s, 12px); }
    .fanrow .icons { display: flex; gap: 4px; }
    .ibtn {
      width: 44px; height: 40px; border-radius: 10px; border: 1px solid transparent; background: transparent; padding: 0;
      color: var(--cs-muted); display: grid; place-items: center; cursor: pointer; --mdc-icon-size: 20px;
    }
    .ibtn:hover:not(:disabled) { color: var(--cs-text); }
    .ibtn:disabled { opacity: .4; cursor: default; }
    .ibtn { --ic: var(--cs-fan); }
    .ibtn.m-heat { --ic: var(--cs-heat); } .ibtn.m-cool { --ic: var(--cs-cool); }
    .ibtn.sel {
      color: color-mix(in srgb, var(--ic) var(--cs-ink), var(--cs-text)); background: color-mix(in srgb, var(--ic) 12%, var(--cs-bg));
      border-color: color-mix(in srgb, var(--ic) 38%, var(--cs-bg)); box-shadow: 0 1px 3px rgba(0,0,0,.08);
    }
    .ibtn.m-auto.sel {
      color: var(--cs-text); border-color: color-mix(in srgb, var(--cs-text) 22%, var(--cs-bg));
      background: linear-gradient(90deg, color-mix(in srgb, var(--cs-heat) 14%, var(--cs-bg)), color-mix(in srgb, var(--cs-cool) 14%, var(--cs-bg)));
    }
    /* Compact mode row: same shape as the compact fan row, colored by the current mode. */
    .moderow .fl > ha-icon { color: color-mix(in srgb, var(--mc) var(--cs-ink), var(--cs-text)); }
    /* Week at a glance (header calendar button): one bar per day, colored by block label. */
    .wk { display: grid; gap: 5px; padding: 10px 12px 12px; border-radius: 12px; background: var(--cs-soft); }
    .wk .wr { display: grid; grid-template-columns: 30px minmax(0, 1fr); align-items: center; gap: 8px; }
    .wk .wd { font-size: 11px; font-weight: var(--ha-font-weight-medium, 500); color: var(--cs-muted); }
    .wk .wr.today .wd { justify-self: start; margin-left: -5px; padding: 0 5px; border-radius: 99px; color: var(--text-primary-color, #fff); background: var(--cs-accent-fill); }
    .wk .wb { position: relative; height: 16px; border-radius: 4px; overflow: hidden; background: var(--cs-bg); }
    .wk .b {
      position: absolute; top: 0; bottom: 0; box-sizing: border-box; cursor: pointer; border-right: 2px solid var(--cs-soft);
      background: color-mix(in srgb, var(--bc) 70%, var(--cs-bg)); transition: background .12s;
    }
    .wk .b:last-child { border-right: 0; }
    .wk .b:hover, .wk .b.open { background: var(--bc); }
    .wk .b:focus-visible { outline: 2px solid var(--cs-accent); outline-offset: -2px; }
    .wk .nowl { position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: var(--cs-accent); box-shadow: 0 0 4px color-mix(in srgb, var(--cs-accent) 55%, transparent); pointer-events: none; }
    .wk .axis { position: relative; height: 12px; font-size: 10px; color: var(--cs-muted); font-variant-numeric: tabular-nums; }
    .wk .axis span { position: absolute; top: 0; transform: translateX(-50%); white-space: nowrap; }
    .wk .axis span:first-child { transform: none; }
    .wk.paused .b { background: color-mix(in srgb, var(--bc) 30%, var(--cs-bg)); }
    .wk .wf { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 4px; }
    .wk .wf .btn { height: 32px; padding: 0 12px; gap: 6px; font-size: var(--ha-font-size-s, 13px); --mdc-icon-size: 16px; }
    .mi.sep { margin-top: 3px; position: relative; }
    .mi.sep::before { content: ""; position: absolute; left: 8px; right: 8px; top: -3px; border-top: 1px solid var(--cs-line); }
    /* Compact layout: a smaller number, no range bar, and mode and fan as two menu buttons. */
    ha-card.compact { gap: 12px; }
    .ctl { display: grid; gap: 8px; }
    .chip {
      display: flex; align-items: center; justify-content: center; gap: 6px; height: 40px; padding: 0 10px; min-width: 0;
      border-radius: 12px; border: 1px solid var(--cs-line); background: var(--cs-bg); color: var(--cs-text); cursor: pointer;
      font: inherit; font-size: var(--ha-font-size-m, 14px); --mdc-icon-size: 18px;
    }
    .chip:hover:not(:disabled) { background: var(--cs-soft); }
    .chip:disabled { opacity: .4; cursor: default; }
    .chip .ci { color: var(--mc, var(--cs-muted)); flex: none; }
    .chip .cv { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .chip .cd { color: var(--cs-muted); flex: none; transition: transform .15s; }
    .chip.open .cd { transform: rotate(180deg); }
    .pop:has(.menu.narrow) { min-width: 210px; }
    /* Runtime row (show_runtime): "today" is one tappable line; "week" adds a bar per day.
       Same shape as the schedule status strip above it. */
    .rtrow {
      display: grid; align-items: center; justify-content: space-between; gap: 12px; min-height: 52px; box-sizing: border-box; width: 100%;
      padding: 8px 14px; border-radius: 12px; border: 0; background: var(--cs-soft); color: inherit;
      font: inherit; text-align: left; font-variant-numeric: tabular-nums;
    }
    button.rtrow { cursor: pointer; }
    button.rtrow:hover, button.rtrow.open { background: color-mix(in srgb, var(--cs-text) 8%, var(--cs-soft)); }
    .rtrow .cap { font-size: 11px; font-weight: var(--ha-font-weight-medium, 500); letter-spacing: .09em; text-transform: uppercase; color: var(--cs-muted); line-height: 1.3; }
    .rtrow .v { display: flex; align-items: center; gap: 6px; min-width: 0; font-size: var(--ha-font-size-m, 14px); white-space: nowrap; --mdc-icon-size: 18px; }
    .rtrow .v ha-icon { color: var(--tc); flex: none; }
    .rtrow .cmpx { text-align: right; font-size: var(--ha-font-size-s, 12px); color: var(--cs-muted); line-height: 1.3; white-space: normal; }
    .rtrow .tot { display: grid; gap: 2px; justify-items: end; }
    .rtrow .tot .v { font-size: var(--ha-font-size-s, 13px); --mdc-icon-size: 15px; }
    .spark { display: flex; align-items: stretch; gap: 4px; height: 34px; min-width: 0; }
    .spk {
      flex: 1 1 0; max-width: 16px; min-width: 6px; padding: 0; border: 0; border-radius: 3px; background: none; cursor: pointer;
      display: flex; flex-direction: column-reverse; gap: 1px; outline-offset: 2px;
    }
    .spk i { display: block; flex: none; border-radius: 2px; background: var(--tc); opacity: .75; }
    .spk .empty { height: 2px; background: color-mix(in srgb, var(--cs-text) 18%, transparent); opacity: 1; }
    .spk.today i { opacity: 1; }
    .spk:hover i, .spk.open i { opacity: 1; }
    .spk.open { background: color-mix(in srgb, var(--cs-text) 8%, transparent); }
    .pop dl.rtpop { grid-template-columns: auto repeat(var(--n), auto); }
    .pop dl.rtpop .hd { text-align: right; --mdc-icon-size: 15px; }
  `;

  // Dial geometry (SVG units): a 270° ring from bottom-left, over the top, to bottom-right.
  // The viewBox ends just under the ring; set point controls (if shown) go below it.
  const DIAL = { W: 280, H: 224, CX: 140, CY: 134, R: 110 };
  // Point on the ring at fraction f (0 = start, 1 = end), at radius r.
  function dialPoint(f, r = DIAL.R) {
    const a = ((135 + 270 * f) * Math.PI) / 180;
    return [DIAL.CX + r * Math.cos(a), DIAL.CY + r * Math.sin(a)];
  }
  // SVG path along the ring from f0 to f1.
  function dialArc(f0, f1) {
    const [x0, y0] = dialPoint(f0), [x1, y1] = dialPoint(f1);
    const large = (f1 - f0) * 270 > 180 ? 1 : 0;
    return `M${x0.toFixed(1)} ${y0.toFixed(1)} A${DIAL.R} ${DIAL.R} 0 ${large} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
  }

  function fanInfo(mode, modes) {
    const s = String(mode).toLowerCase();
    if (s.includes("circ")) return { label: "Circulate", icon: "mdi:fan-clock" };
    if (s.includes("auto")) return { label: "Auto", icon: "mdi:fan-auto" };
    if (s.includes("off")) return { label: "Off", icon: "mdi:fan-off" };
    const others = modes.filter((m) => !/auto|circ|off/i.test(m));
    const label = others.length === 1 ? "On" : String(mode).charAt(0).toUpperCase() + String(mode).slice(1);
    return { label, icon: "mdi:fan" };
  }

  class ComfortThermostatCard extends ComfortBase {
    constructor() {
      super();
      this.shadowRoot.addEventListener("pointerdown", (e) => this._pointerDown(e));
      this.shadowRoot.addEventListener("keydown", (e) => this._keyDown(e));
      // Keep what's typed in the vacation form, so a redraw (a message, a resize) keeps it.
      this.shadowRoot.addEventListener("input", (e) => {
        const form = e.target.closest?.(".pop form.vac");
        if (form && this._pop?.form && this.stateObj) {
          const v = (n) => form.elements[n]?.value;
          this._pop.html = this._vacForm(this.stateObj.attributes, { start: v("start"), end: v("end"), heat: v("heat"), cool: v("cool") });
        }
      });
    }

    getCardSize() {
      return 7 + (this._config?.show_runtime ? 1 : 0);
    }

    // ---------------------------------------------------------------- dragging the range bar

    _pointerDown(e) {
      if (!this.stateObj || e.button > 0) return;
      const dial = e.target.closest(".dsv");
      if (dial) {
        this._dialDown(e, dial);
        return;
      }
      const thumb = e.target.closest(".thumb");
      const onTrack = e.target.closest(".track");
      if (!thumb && !onTrack) return;
      const band = this.shadowRoot.querySelector(".band");
      const handles = [...band.querySelectorAll(".thumb")];
      if (!handles.length) return;
      e.preventDefault();
      const rect = band.getBoundingClientRect();
      // Pressed in the instant the card was redrawn (ha-card not laid out yet): ignore it.
      if (!rect.width) return;
      const lo = Number(band.dataset.lo), hi = Number(band.dataset.hi);
      const toTemp = (ev) => lo + Math.max(0, Math.min(1, (ev.clientX - rect.left) / rect.width)) * (hi - lo);
      // Grab the handle pressed, or (tapping the bar) the one nearest the tap.
      const el = thumb || this._nearest(handles, toTemp(e));
      el.classList.add("drag");
      this._startDrag(e, el, el.dataset.side, toTemp);
    }

    // Dial: drag a handle along the ring, or press anywhere on the ring to move the nearer
    // handle there. Presses in the middle (the temperature) are ignored.
    _dialDown(e, svg) {
      const handles = [...svg.querySelectorAll(".dh")];
      if (!handles.length) return;
      const rect = svg.getBoundingClientRect();
      if (!rect.width) return;
      const k = DIAL.W / rect.width;
      const at = (ev) => [(ev.clientX - rect.left) * k - DIAL.CX, (ev.clientY - rect.top) * k - DIAL.CY];
      const onHandle = e.target.closest(".dh");
      const [dx, dy] = at(e);
      if (!onHandle && Math.abs(Math.hypot(dx, dy) - DIAL.R) > 26) return;
      e.preventDefault();
      const lo = Number(svg.dataset.lo), hi = Number(svg.dataset.hi);
      const toTemp = (ev) => {
        const [x, y] = at(ev);
        // Angle along the ring: 0 at its start (bottom-left), 270 at its end (bottom-right).
        let a = ((Math.atan2(y, x) * 180) / Math.PI - 135 + 720) % 360;
        if (a > 270) a = a > 315 ? 0 : 270; // in the opening at the bottom: the nearer end
        return lo + (a / 270) * (hi - lo);
      };
      const el = onHandle || this._nearest(handles, toTemp(e));
      // Capture on the whole dial: its handles are redrawn while dragging.
      this._startDrag(e, svg, el.dataset.side, toTemp);
    }

    _nearest(handles, temp) {
      return handles
        .map((h) => ({ h, d: Math.abs(Number(h.getAttribute("aria-valuenow")) - temp) }))
        .sort((a, b) => a.d - b.d)[0].h;
    }

    // Follow the pointer until it's released, then send one command (battery-powered thermostat).
    _startDrag(e, el, side, toTemp) {
      this._drag = { side, toTemp };
      try { el.setPointerCapture(e.pointerId); } catch (_err) { /* older browsers: moves still arrive */ }
      const move = (ev) => this._dragTo(ev);
      const end = () => {
        el.removeEventListener("pointermove", move);
        el.removeEventListener("pointerup", end);
        el.removeEventListener("pointercancel", end);
        this._drag = null;
        clearTimeout(this._sendTimer);
        this._sendTimer = setTimeout(() => this._send(), 300);
        this._render();
      };
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", end);
      el.addEventListener("pointercancel", end);
      this._dragTo(e);
    }

    _dragTo(ev) {
      const a = this.stateObj.attributes;
      const unit = this._hass.config.unit_system.temperature;
      const step = num(a.target_temp_step) || (unit === "°C" ? 0.5 : 1);
      const { side, toTemp } = this._drag;
      const value = Math.round(toTemp(ev) / step) * step;
      if (!Number.isFinite(value)) return; // never send a non-number
      let heat = this._pending ? this._pending.heat : num(a.heat_setpoint);
      let cool = this._pending ? this._pending.cool : (num(a.cool_setpoint) ?? num(a.max_temp));
      if (side === "heat") heat = value;
      else cool = value;
      const g = guard(heat, cool, side, a.cooling_enabled === false ? 0 : num(a.deadband) ?? 3, num(a.min_temp) ?? 40, num(a.max_temp) ?? 95);
      if (this._pending && g.heat === this._pending.heat && g.cool === this._pending.cool) return;
      // Pressing or nudging a handle without moving it off its value isn't a change: don't
      // start a (pending) hold for it.
      if (!this._pending && g.heat === num(a.heat_setpoint) && (a.cooling_enabled === false || g.cool === num(a.cool_setpoint))) return;
      this._pending = { heat: g.heat, cool: g.cool };
      this._msg = g.note;
      this._updateBand();
    }

    // Move the handles, zones and tile numbers in place while dragging (a full redraw would
    // replace the element under your finger).
    _updateBand() {
      const root = this.shadowRoot;
      if (!this._pending) return;
      const { heat, cool } = this._pending;
      for (const [side, v] of [["heat", heat], ["cool", cool]]) {
        const tv = root.querySelector(`.tc.${side} .tv`);
        if (tv) tv.textContent = `${fmtTemp(v)}°`;
      }
      const dial = root.querySelector(".dsv");
      if (dial) {
        const mode = this.stateObj.state;
        dial.querySelector(".dyn").innerHTML = this._dialDyn(
          Number(dial.dataset.lo), Number(dial.dataset.hi), heat, cool,
          mode === "heat" || mode === "heat_cool", mode === "cool" || mode === "heat_cool",
          num(this.stateObj.attributes.current_temperature), this._drag && this._drag.side
        );
      }
      const band = root.querySelector(".band");
      if (band) {
        const lo = Number(band.dataset.lo), hi = Number(band.dataset.hi);
        const pct = (t) => Math.max(0, Math.min(100, ((t - lo) / (hi - lo)) * 100));
        for (const [side, v] of [["heat", heat], ["cool", cool]]) {
          const h = band.querySelector(`.thumb[data-side="${side}"]`);
          if (h) {
            h.style.left = `${pct(v)}%`;
            h.setAttribute("aria-valuenow", v);
            h.querySelector(".tip").textContent = `${fmtTemp(v)}°`;
          }
        }
        const zh = band.querySelector(".zone.h");
        if (zh) zh.style.width = `${pct(heat)}%`;
        const zc = band.querySelector(".zone.c");
        if (zc) zc.style.left = `${pct(cool)}%`;
      }
      const msg = root.querySelector(".msg");
      if (msg) {
        msg.textContent = this._msg || "";
        msg.classList.toggle("toast", !!this._msg);
      }
    }

    // The dial's moving parts: heat and cool zones, the room temperature tick, the handles, and
    // (while dragging) the value next to the handle being moved.
    _dialDyn(lo, hi, heat, cool, useH, useC, cur, dragSide, unavailable = false) {
      const f = (t) => Math.max(0, Math.min(1, (t - lo) / (hi - lo)));
      let s = "";
      if (useH && f(heat) > 0.002) s += `<path class="dz h" d="${dialArc(0, f(heat))}"/>`;
      if (useC && f(cool) < 0.998) s += `<path class="dz c" d="${dialArc(f(cool), 1)}"/>`;
      if (cur !== null) {
        const [x0, y0] = dialPoint(f(cur), DIAL.R - 12), [x1, y1] = dialPoint(f(cur), DIAL.R + 12);
        s += `<line class="cm" x1="${x0.toFixed(1)}" y1="${y0.toFixed(1)}" x2="${x1.toFixed(1)}" y2="${y1.toFixed(1)}"/>`;
      }
      if (unavailable) return s;
      for (const [side, v, on] of [["heat", heat, useH], ["cool", cool, useC]]) {
        if (!on) continue;
        const [x, y] = dialPoint(f(v));
        s += `<circle class="dh ${side} ${dragSide === side ? "drag" : ""}" data-side="${side}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="12"
          role="slider" tabindex="0" aria-label="${side === "heat" ? "Heat to" : "Cool to"}" aria-valuemin="${lo}" aria-valuemax="${hi}" aria-valuenow="${v}"/>`;
        if (dragSide === side) {
          const [tx, ty] = dialPoint(f(v), DIAL.R - 30);
          s += `<text class="tipv ${side}" x="${tx.toFixed(1)}" y="${(ty + 4).toFixed(1)}" text-anchor="middle">${fmtTemp(v)}°</text>`;
        }
      }
      return s;
    }

    // "− 70° +" in a popup, for set points shown without their own buttons.
    _stepperHtml(side, v) {
      const k = side === "heat" ? "h" : "c";
      return `<div class="ph">${side === "heat" ? "Heat to" : "Cool to"}</div>
        <div class="tstep ${side}">
          <button class="sbtn" data-action="${k}-dn" aria-label="Lower ${side} set point"><ha-icon icon="mdi:minus"></ha-icon></button>
          <span class="tval"><span class="tv">${fmtTemp(v)}°</span></span>
          <button class="sbtn" data-action="${k}-up" aria-label="Raise ${side} set point"><ha-icon icon="mdi:plus"></ha-icon></button>
        </div>`;
    }

    // Which hold length is in effect, worked out from the hold's start and end.
    _holdKind(a) {
      const hold = this._holdNow || a.hold;
      if (!hold) return null;
      if (!hold.until) return "indefinite";
      const until = new Date(hold.until).getTime();
      if (a.next_change && Math.abs(until - new Date(a.next_change).getTime()) < 90e3) return "next_period";
      if (hold.started) {
        const minutes = Math.round((until - new Date(hold.started).getTime()) / 60e3);
        if (Math.abs(minutes - 120) <= 1) return "120";
        if (Math.abs(minutes - 240) <= 1) return "240";
      }
      return null;
    }

    // Styled hold-length menu (replaces the browser's native dropdown). Each choice shows
    // when the hold would end if picked now.
    _holdMenu(a) {
      const hass = this._hass;
      const current = this._holdKind(a);
      const at = (ms) => fmtWhen(hass, new Date(Date.now() + ms).toISOString());
      const next = a.next_period ? ` · ${esc(a.next_period.label)}` : "";
      const choices = [
        ["next_period", "mdi:calendar-arrow-right", "Until the next period", a.next_change ? `${esc(fmtWhen(hass, a.next_change))}${next}` : "no next period"],
        ["120", "mdi:timer-outline", "For 2 hours", esc(at(2 * 3600e3))],
        ["240", "mdi:timer-outline", "For 4 hours", esc(at(4 * 3600e3))],
        ["indefinite", "mdi:infinity", "Until I resume", "no end"],
      ].map(([v, icon, label, end]) => ({ action: "hold-set", v, icon, label, end, on: v === current }));
      // Not hold lengths, but the other ways to stop following the schedule.
      choices.push(...this._planItems(a, true));
      return this._menu("How long to hold", choices);
    }

    // Vacation and Pause, for the hold menu and the schedule strip's menu.
    _planItems(a, sep) {
      const hass = this._hass;
      const v = a.vacation;
      const items = v
        ? [
            { action: "vac-edit", v: "", icon: "mdi:bag-suitcase-outline", label: "Vacation…", end: esc(`${fmtWhenFar(hass, v.start)} – ${fmtWhenFar(hass, v.end)}`), plain: true },
            { action: "vac-cancel", v: "", icon: "mdi:close", label: "Cancel the vacation", plain: true },
          ]
        : [{ action: "vac-edit", v: "", icon: "mdi:bag-suitcase-outline", label: "Vacation…", end: "pick the dates", plain: true }];
      items[0].sep = sep;
      items.push({ action: "hold-set", v: "pause", icon: "mdi:calendar-remove-outline", label: "Pause the schedule", end: "keeps these set points", plain: true });
      return items;
    }

    // From/until and the set points while away. Kept in the popup until saved or closed.
    _vacForm(a, draft) {
      const v = a.vacation;
      const cooling = a.cooling_enabled !== false;
      const f = this._hass?.config?.unit_system?.temperature === "°C";
      let start = v ? new Date(v.start) : new Date(Math.ceil(Date.now() / 9e5) * 9e5);
      if (start < Date.now()) start = new Date(Math.ceil(Date.now() / 6e4) * 6e4);
      const end = v ? new Date(v.end) : new Date(start.getTime() + 3 * 864e5);
      const heat = v ? v.heat : f ? 16 : 60;
      const cool = v ? v.cool : f ? 28 : 82;
      const step = f ? 0.5 : 1;
      // What was typed wins (the form is redrawn when a message shows).
      const d = { start: localInput(start), end: localInput(end), heat: fmtTemp(heat), cool: fmtTemp(cool), ...draft };
      return `<form class="vac" aria-label="Vacation" onsubmit="return false" data-start="${esc(localInput(start))}">
          <div class="vh"><ha-icon icon="mdi:bag-suitcase-outline"></ha-icon>Vacation</div>
          <label><span>From</span><input type="datetime-local" name="start" value="${esc(d.start)}"></label>
          <label><span>Until</span><input type="datetime-local" name="end" value="${esc(d.end)}"></label>
          <div class="vr">
            <label class="heat"><span>Heat</span><input type="number" name="heat" step="${step}" value="${esc(d.heat)}"></label>
            ${cooling ? `<label class="cool"><span>Cool</span><input type="number" name="cool" step="${step}" value="${esc(d.cool)}"></label>` : ""}
          </div>
          <div class="vnote">Then back to the schedule.</div>
          <div class="vb"><button type="button" class="btn" data-action="vac-close">Cancel</button>
            <button type="submit" class="btn primary" data-action="vac-save">Save</button></div>
        </form>`;
    }

    _vacSave() {
      const form = this.shadowRoot.querySelector(".pop form.vac");
      if (!form) return undefined;
      const val = (n) => form.elements[n]?.value;
      const start = new Date(val("start")), end = new Date(val("end"));
      const a = this.stateObj.attributes;
      const cooling = a.cooling_enabled !== false;
      const heat = Number(val("heat")), cool = cooling ? Number(val("cool")) : null;
      const gap = num(a.deadband) || 0;
      const problem = Number.isNaN(end.getTime()) || end <= Math.max(Date.now(), start.getTime() || 0)
        ? "The vacation has to end after it starts, and in the future"
        : val("heat") === "" || !Number.isFinite(heat) || (cooling && (val("cool") === "" || !Number.isFinite(cool))) ? (cooling ? "Set both set points" : "Set a heating temperature")
        : cooling && cool - heat < gap ? `Cool has to be at least ${fmtTemp(gap)}° above heat`
        : "";
      if (problem) {
        this._pop.html = this._vacForm(a, { start: val("start"), end: val("end"), heat: val("heat"), cool: val("cool") });
        return this._toast(problem);
      }
      const data = { end: end.toISOString(), heat, ...(cooling ? { cool } : {}) };
      const running = a.vacation && new Date(a.vacation.start) <= Date.now();
      if (running && val("start") === form.dataset.start) {
        // "From" shows the next minute for a vacation that's already on; left as it was, it keeps its real start.
        data.start = a.vacation.start;
      } else if (!Number.isNaN(start.getTime()) && start > Date.now()) data.start = start.toISOString();
      this._closePop();
      return this._call(DOMAIN, "set_vacation", data);
    }

    // Card-styled menu, shown in the popup. Items: { action, v, icon, label, end, on, sep, plain }.
    _menu(label, items, narrow = false) {
      return `<div class="menu ${narrow ? "narrow" : ""}" role="menu" aria-label="${esc(label)}">${items
        .map((it) => `<button class="mi ${it.on ? "on" : ""} ${it.sep ? "sep" : ""}" role="${it.plain ? "menuitem" : "menuitemradio"}"
            ${it.plain ? "" : `aria-checked="${!!it.on}"`} data-action="${it.action}" data-v="${esc(it.v)}">
            <ha-icon class="mic" icon="${it.icon}"></ha-icon><span class="ml">${esc(it.label)}</span><span class="me">${it.end || ""}</span>
            <ha-icon class="mck" icon="mdi:check"></ha-icon></button>`)
        .join("")}</div>`;
    }

    _modeChoices(a) {
      return [
        ["heat", "heat", "mdi:fire", "Heat"],
        ["heat_cool", "auto", "mdi:sun-snowflake-variant", "Heat/Cool"],
        ["cool", "cool", "mdi:snowflake", "Cool"],
      ].filter(([m]) => (a.hvac_modes || []).includes(m));
    }

    // Week at a glance: one bar per day (Monday first), blocks colored by label, a line at now.
    _week(a) {
      const hass = this._hass;
      const week = Array.isArray(a.schedule) && a.schedule.length === 7 ? a.schedule : null;
      if (!week) return "";
      const { day: today, minute } = homeClock(hass);
      const lang = hass?.locale?.language || undefined;
      const hourLabel = (h) => new Date(2000, 0, 1, h).toLocaleTimeString(lang, { hour: "numeric", hour12: hour12(hass) });
      const axis = [0, 6, 12, 18].map((h) => `<span style="left:${(h / 24) * 100}%">${esc(hourLabel(h))}</span>`).join("");
      const rows = week.map((day, d) => {
        const blocks = (day || []).map((p, i) => {
          const end = i + 1 < day.length ? day[i + 1].start : 1440;
          const key = `.wk .b[data-d="${d}"][data-i="${i}"]`;
          const open = this._pop && this._pop.key === key;
          return `<div class="b ${open ? "open" : ""}" style="left:${(p.start / 14.4).toFixed(2)}%;width:${((end - p.start) / 14.4).toFixed(2)}%;--bc:${labelColor(p.label)}"
            data-action="info" data-pop="1" data-d="${d}" data-i="${i}" role="button" tabindex="0"
            aria-label="${esc(`${DAYS[d]} ${p.label}, ${fmtMinutes(hass, p.start)} to ${fmtMinutes(hass, end)}`)}"></div>`;
        }).join("");
        const nowLine = d === today ? `<div class="nowl" style="left:${(minute / 14.4).toFixed(2)}%"></div>` : "";
        return `<div class="wr ${d === today ? "today" : ""}"><div class="wd">${DAYS[d]}</div><div class="wb">${blocks}${nowLine}</div></div>`;
      }).join("");
      const on = !!a.schedule_enabled;
      return `<div class="wk ${on ? "" : "paused"}">
        <div class="wr"><div></div><div class="axis">${axis}</div></div>
        ${rows}
        <div class="wf"><span class="muted small">${on ? "Tap a block for details" : "Schedule paused"}</span>
          <button class="btn" data-action="sched"><ha-icon icon="${on ? "mdi:pause" : "mdi:play"}"></ha-icon>${on ? "Pause schedule" : "Resume schedule"}</button></div>
      </div>`;
    }

    _keyDown(e) {
      // Hold menu: arrow keys move between choices (Enter/Space pick, Escape closes).
      const item = e.target.closest?.(".mi");
      if (item && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        const items = [...this.shadowRoot.querySelectorAll(".mi")];
        const k = items.indexOf(item) + (e.key === "ArrowDown" ? 1 : -1);
        items[(k + items.length) % items.length].focus();
        return;
      }
      const thumb = e.target.closest?.(".thumb, .dh");
      if (!thumb) return;
      const dir = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[e.key];
      if (!dir) return;
      e.preventDefault();
      this._refocus = thumb.dataset.side;
      this._bump(thumb.dataset.side, dir);
    }

    _render() {
      if (this._missing() || this._drag) return;
      const hass = this._hass;
      const st = this.stateObj;
      const a = st.attributes;
      const mode = st.state;
      const unit = hass.config.unit_system.temperature;
      const unavailable = mode === "unavailable" || mode === "unknown";
      const cooling = a.cooling_enabled !== false;
      const heat = this._pending ? this._pending.heat : num(a.heat_setpoint);
      const cool = this._pending ? this._pending.cool : (num(a.cool_setpoint) ?? num(a.max_temp));
      const cur = num(a.current_temperature);
      const useH = mode === "heat" || mode === "heat_cool";
      const useC = cooling && (mode === "cool" || mode === "heat_cool");
      const fanModes = a.fan_modes || [];
      const action = a.hvac_action;
      const fanRunning = action === "heating" || action === "cooling" || action === "fan";
      const src = hass.states[a.source_entity];
      const title = this._config.name || a.friendly_name || this._config.entity;
      const sub = this._config.hide_subtitle ? "" : this._subtitle(a, mode, unavailable);

      // The room is on the wrong side of a set point but the thermostat hasn't started yet.
      // Many thermostats report whole degrees and wait until the room is a bit past the set point,
      // so "70° with heat at 71°" can stay idle for a while; say so instead of just "Idle".
      const side = a.device_mode || mode; // the real thermostat's Heat or Cool side
      const settled = !unavailable && !this._pending && cur !== null && action !== "heating" && action !== "cooling";
      let waiting = null;
      if (settled && useH && cur < heat) {
        waiting = side === "cool"
          ? { cls: "heat", icon: "mdi:fire", pill: "Switching to heat", hint: "Switching the thermostat to Heat…" }
          : { cls: "heat", icon: "mdi:fire", pill: "Waiting to heat", hint: `Heat starts once the room is a bit below ${fmtTemp(heat)}°` };
      } else if (settled && useC && cur > cool) {
        waiting = side === "heat"
          ? { cls: "cool", icon: "mdi:snowflake", pill: "Switching to cool", hint: "Switching the thermostat to Cool…" }
          : { cls: "cool", icon: "mdi:snowflake", pill: "Waiting to cool", hint: `Cooling starts once the room is a bit above ${fmtTemp(cool)}°` };
      }

      // What it's doing, with the target it's working toward, so the set points can stay small.
      const idle = mode === "heat" ? `Idle · heat to ${fmtTemp(heat)}°`
        : mode === "cool" ? `Idle · cool to ${fmtTemp(cool)}°`
        : mode === "heat_cool" ? `Idle · ${fmtTemp(heat)}–${fmtTemp(cool)}°`
        : "Idle";
      const pill =
        mode === "off" && action !== "fan"
          ? ["", "mdi:power", "Off"]
          : action === "heating"
          ? ["heating", "mdi:fire", `Heating to ${fmtTemp(heat)}°`]
          : action === "cooling"
          ? ["cooling", "mdi:snowflake", `Cooling to ${fmtTemp(cool)}°`]
          : waiting
          ? [`waiting ${waiting.cls}`, waiting.icon, waiting.pill]
          : action === "fan"
          ? ["fan", "mdi:fan", "Fan only"]
          : unavailable
          ? ["", "mdi:alert-circle-outline", "Unavailable"]
          : ["", "", idle];
      const message = this._msg
        ? `<div class="msg toast" role="status">${esc(this._msg)}</div>`
        : `<div class="msg" role="status">${waiting ? esc(waiting.hint) : ""}</div>`;

      // Range bar: a fixed scale so the numbers stay put; only widen (by whole ticks) when a
      // set point or the room temperature falls outside it.
      const [tick, baseLo, baseHi] = unit === "°C" ? [2, 16, 28] : [5, 60, 80];
      const vals = [heat, cool, cur].filter((v) => v !== null);
      const lo = Math.min(baseLo, ...vals.map((v) => Math.floor(v / tick) * tick));
      const hi = Math.max(baseHi, ...vals.map((v) => Math.ceil(v / tick) * tick));
      const pct = (t) => Math.max(0, Math.min(100, ((t - lo) / (hi - lo)) * 100));
      let ticks = "";
      for (let t = lo; t <= hi; t += tick) ticks += `<span class="tick" style="left:${pct(t)}%">${t}°</span>`;
      const handle = (side, v) =>
        `<div class="thumb ${side}" data-side="${side}" role="slider" tabindex="0" aria-label="${side === "heat" ? "Heat to" : "Cool to"}"
          aria-valuemin="${lo}" aria-valuemax="${hi}" aria-valuenow="${v}" style="left:${pct(v)}%"><span class="tip">${fmtTemp(v)}°</span></div>`;
      const band = `<div class="band" data-lo="${lo}" data-hi="${hi}">
          <div class="track">
            ${useH ? `<div class="zone h" style="width:${pct(heat)}%"></div>` : ""}
            ${useC ? `<div class="zone c" style="left:${pct(cool)}%"></div>` : ""}
          </div>
          ${cur !== null ? `<div class="cm" style="left:${pct(cur)}%"><span>${fmtTemp(cur)}°</span></div>` : ""}
          ${useH && !unavailable ? handle("heat", heat) : ""}
          ${useC && !unavailable ? handle("cool", cool) : ""}
          ${ticks}
        </div>`;

      const modeOrder = this._modeChoices(a);
      const modeButtons = modeOrder
        .map(
          ([m, cls, icon, label]) =>
            `<button class="mbtn ${cls} ${mode === m ? "sel" : ""}" data-action="mode" data-mode="${m}" aria-pressed="${mode === m}" ${unavailable ? "disabled" : ""}>
              <ha-icon icon="${icon}"></ha-icon>${label}</button>`
        )
        .join("");
      const fanCompact = this._config.fan_layout === "compact";
      const fanButtons = fanModes
        .map((m) => {
          const f = fanInfo(m, fanModes);
          const sel = a.fan_mode === m;
          if (fanCompact) {
            return `<button class="ibtn ${sel ? "sel" : ""}" data-action="fan" data-fan="${esc(m)}" aria-pressed="${sel}" title="${esc(f.label)}" aria-label="Fan ${esc(f.label)}" ${unavailable ? "disabled" : ""}>
              <ha-icon icon="${f.icon}"></ha-icon></button>`;
          }
          return `<button class="mbtn fan ${sel ? "sel" : ""} ${sel && fanRunning ? "spin" : ""}" data-action="fan" data-fan="${esc(m)}" aria-pressed="${sel}" ${unavailable ? "disabled" : ""}>
            <ha-icon icon="${f.icon}"></ha-icon>${esc(f.label)}</button>`;
        })
        .join("");
      const fanNow = a.fan_mode ? fanInfo(a.fan_mode, fanModes) : null;
      let fanRow = "";
      if (fanButtons && !this._config.hide_fan) {
        fanRow = fanCompact
          ? `<div class="fanrow"><div class="fl"><ha-icon class="${fanRunning ? "spin" : ""}" icon="${fanNow ? fanNow.icon : "mdi:fan"}"></ha-icon>
              <div style="min-width:0"><div class="glabel">Fan</div><div class="fv">${fanNow ? esc(fanNow.label) : "–"}${fanRunning ? ` <span>· running</span>` : ""}</div></div></div>
              <div class="icons" role="group" aria-label="Fan mode">${fanButtons}</div></div>`
          : `<div class="group"><div class="glabel">Fan</div><div class="seg" style="grid-template-columns:repeat(${fanModes.length},minmax(0,1fr))">${fanButtons}</div></div>`;
      }
      let modeRow = "";
      if (modeButtons && !this._config.hide_mode) {
        if (this._config.mode_layout === "compact") {
          // Like the compact fan row: the current mode on the left, an icon per mode on the right.
          const icons = modeOrder
            .map(([m, cls, icon, label]) => `<button class="ibtn m-${cls} ${mode === m ? "sel" : ""}" data-action="mode" data-mode="${m}" aria-pressed="${mode === m}"
                title="${label}" aria-label="Mode ${label}" ${unavailable ? "disabled" : ""}><ha-icon icon="${icon}"></ha-icon></button>`)
            .join("");
          const mc = { heat: "var(--cs-heat)", cool: "var(--cs-cool)", heat_cool: "var(--cs-text)" }[mode] || "var(--cs-muted)";
          const switching = a.heat_cool_switching && (a.device_mode === "heat" || a.device_mode === "cool") ? ` <span>· ${a.device_mode} side</span>` : "";
          modeRow = `<div class="fanrow moderow" style="--mc:${mc}"><div class="fl"><ha-icon icon="${MODE_ICONS[mode] || "mdi:thermostat"}"></ha-icon>
              <div style="min-width:0"><div class="glabel">Mode</div><div class="fv">${esc(MODE_LABELS[mode] || mode)}${switching}</div></div></div>
              <div class="icons" role="group" aria-label="Mode">${icons}</div></div>`;
        } else {
          modeRow = `<div class="group"><div class="glabel">Mode</div><div class="seg" style="grid-template-columns:repeat(${modeOrder.length},minmax(0,1fr))">${modeButtons}</div></div>`;
        }
      }

      // Compact layout: mode and fan collapse into two menu buttons, and there's no range bar.
      const compact = this._config.layout === "compact";
      let controls = "";
      if (compact) {
        const chips = [];
        const popOpen = (k) => (this._pop && this._pop.key === k ? "open" : "");
        if (modeOrder.length && !this._config.hide_mode) {
          const mc = { heat: "var(--cs-heat)", cool: "var(--cs-cool)", heat_cool: "var(--cs-text)" }[mode] || "var(--cs-muted)";
          chips.push(`<button class="chip m ${popOpen(".chip.m")}" style="--mc:${mc}" data-action="mode-menu" data-pop="1" aria-haspopup="menu" ${unavailable ? "disabled" : ""}>
            <ha-icon class="ci" icon="${MODE_ICONS[mode] || "mdi:thermostat"}"></ha-icon><span class="cv">${esc(MODE_LABELS[mode] || mode)}</span><ha-icon class="cd" icon="mdi:chevron-down"></ha-icon></button>`);
        }
        if (fanModes.length && !this._config.hide_fan) {
          chips.push(`<button class="chip f ${popOpen(".chip.f")}" style="--mc:var(--cs-fan)" data-action="fan-menu" data-pop="1" aria-haspopup="menu" ${unavailable ? "disabled" : ""}>
            <ha-icon class="ci ${fanRunning ? "spin" : ""}" icon="${fanNow ? fanNow.icon : "mdi:fan"}"></ha-icon><span class="cv">Fan · ${fanNow ? esc(fanNow.label) : "–"}</span><ha-icon class="cd" icon="mdi:chevron-down"></ha-icon></button>`);
        }
        if (chips.length) controls = `<div class="ctl" style="grid-template-columns:repeat(${chips.length},minmax(0,1fr))">${chips.join("")}</div>`;
      } else {
        controls = `${modeRow}${fanRow}`;
      }

      const status = this._status(a);
      const weekOpen = this._weekOpen ?? !!this._config.week_expanded;
      const weekTop = this._config.week_position === "top";
      const week = weekOpen ? this._week(a) : "";

      const dis = (on) => (!on || unavailable ? "disabled" : "");
      const dial = this._config.style === "dial";

      // Indoor temperature, center stage (inside the ring for the dial style).
      const hero = `<div class="hero">
          <div class="muted small">Indoor</div>
          <div class="num">${cur === null ? "–" : fmtTemp(cur)}<small>${esc(unit)}</small></div>
          ${this._stats(a)}
          <span class="pill ${pill[0]}">${pill[1] ? `<ha-icon icon="${pill[1]}"></ha-icon>` : ""}${pill[2]}</span>
        </div>`;

      // Set points: "− 70° +" for each side, dimmed when the mode doesn't use it. Without
      // the buttons they're just values; where there's nothing else to adjust them with (the
      // compact classic layout has no range bar), tapping a value opens a stepper.
      const noButtons = !!this._config.hide_target_buttons;
      const tapToStep = noButtons && !dial && compact;
      const target = (side) => {
        const on = side === "heat" ? useH : useC;
        const v = side === "heat" ? heat : cool;
        const name = side === "heat" ? "Heat to" : "Cool to";
        const inner = `<ha-icon icon="${side === "heat" ? "mdi:fire" : "mdi:snowflake"}"></ha-icon><span class="tv">${fmtTemp(v)}°</span>`;
        if (noButtons) {
          const val = tapToStep && on && !unavailable
            ? `<button class="tval" data-action="tstep" data-side="${side}" data-pop="1" aria-label="${name} ${fmtTemp(v)}°. Change">${inner}</button>`
            : `<span class="tval" aria-label="${name} ${fmtTemp(v)}°">${inner}</span>`;
          return `<div class="tc ${side} nob ${on ? "" : "off"}">${val}</div>`;
        }
        const k = side === "heat" ? "h" : "c";
        return `<div class="tc ${side} ${on ? "" : "off"}" role="group" aria-label="${name} ${fmtTemp(v)}°">
            <button class="sbtn" data-action="${k}-dn" aria-label="Lower ${side} set point" ${dis(on)}><ha-icon icon="mdi:minus"></ha-icon></button>
            <span class="tval">${inner}</span>
            <button class="sbtn" data-action="${k}-up" aria-label="Raise ${side} set point" ${dis(on)}><ha-icon icon="mdi:plus"></ha-icon></button>
          </div>`;
      };
      const targets = this._config.hide_targets
        ? ""
        : `<div class="tcs ${dial ? "dialt" : ""} ${noButtons ? "nob" : ""}">${target("heat")}${cooling ? target("cool") : ""}</div>`;

      let top;
      if (dial) {
        const ring = `<svg class="dsv" viewBox="0 0 ${DIAL.W} ${DIAL.H}" data-lo="${lo}" data-hi="${hi}" role="group" aria-label="Set points">
            <path class="trk" d="${dialArc(0, 1)}"/>
            <g class="dyn">${this._dialDyn(lo, hi, heat, cool, useH, useC, cur, null, unavailable)}</g>
          </svg>`;
        top = `<div class="dial">${ring}${hero}</div>${targets}`;
      } else {
        top = `${hero}${targets}${compact ? "" : band}`;
      }
      // A stepper opened from a set point (tap-to-step) shows the live value.
      if (this._pop && this._pop.key.startsWith(".tc.")) {
        const side = this._pop.key.includes("heat") ? "heat" : "cool";
        this._pop.html = this._stepperHtml(side, side === "heat" ? heat : cool);
      }

      this._paint(
        THERMO_CSS,
        `<ha-card class="${compact ? "compact" : ""}">
          <div class="head">
            <button class="icon-btn sched ${weekOpen ? "on" : ""}" data-action="week" title="${weekOpen ? "Hide the week" : "Show the week"}" aria-expanded="${weekOpen}">
              <ha-icon icon="${a.schedule_enabled ? "mdi:calendar-clock" : "mdi:calendar-remove-outline"}"></ha-icon></button>
            ${this._heading(title, sub ? esc(sub) : "") || "<div></div>"}
            <button class="icon-btn ${mode === "off" ? "on" : ""}" data-action="power" title="${mode === "off" ? "Turn on" : "Turn off"}" aria-pressed="${mode === "off"}" ${unavailable || (mode !== "off" && !(a.hvac_modes || []).includes("off")) ? "disabled" : ""}>
              <ha-icon icon="mdi:power"></ha-icon></button>
          </div>
          ${weekOpen && weekTop ? week : ""}
          ${top}
          ${message}
          ${controls}
          ${status}
          ${weekOpen && !weekTop ? week : ""}
          ${this._runtime(a)}
        </ha-card>`
      );
      if (this._refocus) {
        this._focusAfterPaint(`[role="slider"][data-side="${this._refocus}"]`);
        this._refocus = null;
      }
    }

    _onAction(action, el) {
      const a = this.stateObj?.attributes;
      if (!a) return;
      switch (action) {
        case "h-up": return this._bump("heat", 1);
        case "h-dn": return this._bump("heat", -1);
        case "c-up": return this._bump("cool", 1);
        case "c-dn": return this._bump("cool", -1);
        case "mode": return this._call("climate", "set_hvac_mode", { hvac_mode: el.dataset.mode });
        case "fan": return this._call("climate", "set_fan_mode", { fan_mode: el.dataset.fan });
        case "power": return this._call("climate", this.stateObj.state === "off" ? "turn_on" : "turn_off");
        case "sched": return this._call(DOMAIN, a.schedule_enabled ? "pause_schedule" : "resume_schedule");
        case "resume": return this._call(DOMAIN, "resume_schedule");
        case "rt": return this._togglePop(".rtrow", this._runtimePop(a, "totals"));
        case "tstep": {
          const side = el.dataset.side;
          const v = this._pending ? this._pending[side] : num(a[`${side}_setpoint`]);
          return this._togglePop(`.tc.${side} .tval`, this._stepperHtml(side, v));
        }
        case "rt-day": {
          const k = Number(el.dataset.k);
          return this._togglePop(`.spk[data-k="${k}"]`, this._runtimePop(a, "day", k));
        }
        case "week":
          this._weekOpen = !(this._weekOpen ?? !!this._config.week_expanded);
          if (this._pop && this._pop.key.startsWith(".wk")) this._dropPop();
          return this._render();
        case "info": {
          const d = Number(el.dataset.d), i = Number(el.dataset.i);
          if (!Array.isArray(a.schedule) || !a.schedule[d] || !a.schedule[d][i]) return undefined;
          return this._togglePop(`.wk .b[data-d="${d}"][data-i="${i}"]`, blockInfoHtml(this._hass, a.schedule, d, i, a.fan_modes));
        }
        case "mode-menu": {
          const mode = this.stateObj.state;
          const items = this._modeChoices(a).map(([m, , icon, label]) => ({ action: "mode-set", v: m, icon, label, on: m === mode }));
          return this._togglePop(".chip.m", this._menu("Mode", items, true));
        }
        case "fan-menu": {
          const fans = a.fan_modes || [];
          const items = fans.map((m) => {
            const f = fanInfo(m, fans);
            return { action: "fan-set", v: m, icon: f.icon, label: f.label, on: m === a.fan_mode };
          });
          return this._togglePop(".chip.f", this._menu("Fan", items, true));
        }
        case "mode-set":
          this._closePop();
          if (el.dataset.v !== this.stateObj.state) this._call("climate", "set_hvac_mode", { hvac_mode: el.dataset.v });
          return undefined;
        case "fan-set":
          this._closePop();
          if (el.dataset.v !== a.fan_mode) this._call("climate", "set_fan_mode", { fan_mode: el.dataset.v });
          return undefined;
        case "more-info":
          // Opens HA's standard detail dialog (history, settings) for that sensor.
          this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: el.dataset.entity }, bubbles: true, composed: true }));
          return undefined;
        case "hold-menu":
          return this._togglePop(".until", this._holdMenu(a));
        case "plan-menu":
          return this._togglePop(".until", this._menu("Schedule", this._planItems(a, false)));
        case "vac-menu":
          return this._togglePop(".until", this._menu("Vacation", [
            { action: "vac-edit", v: "", icon: "mdi:calendar-edit-outline", label: "Change the dates…", plain: true },
            { action: "vac-cancel", v: "", icon: "mdi:close", label: "End the vacation now", plain: true },
          ]));
        case "vac-edit":
          // Replaces the menu in the same spot.
          this._dropPop();
          this._togglePop(".until", this._vacForm(a));
          if (this._pop) this._pop.form = true;
          this._focusAfterPaint(".pop input[name=end]");
          return undefined;
        case "vac-close":
          return this._closePop();
        case "vac-save":
          return this._vacSave();
        case "vac-cancel":
          this._closePop();
          return this._call(DOMAIN, "cancel_vacation");
        case "hold-set": {
          const value = el.dataset.v;
          this._closePop();
          if (value === "pause") this._call(DOMAIN, "pause_schedule");
          else if (value !== this._holdKind(a)) this._call(DOMAIN, "set_hold", { duration: value });
          return undefined;
        }
        default: return undefined;
      }
    }

    set hass(hass) {
      // Battery and humidity sensors set in the card config aren't part of our thermostat's
      // state, so redraw when they change too.
      const extra = [this._config?.battery_entity, this._config?.humidity_entity].map((e) => (e ? hass.states[e] : undefined));
      const extraChanged = (this._extraStates || []).some((st, i) => st !== extra[i]) || (!this._extraStates && extra.some(Boolean));
      this._extraStates = extra;
      super.hass = hass;
      if (extraChanged && this.stateObj) this._render();
    }

    // Keep the progress bar moving while nothing else changes; skip while a dropdown is open.
    connectedCallback() {
      this._tick = setInterval(() => {
        if (this.stateObj && !this._pop) this._render(); // don't redraw under an open menu
      }, 60000);
    }

    disconnectedCallback() {
      super.disconnectedCallback();
      clearInterval(this._tick);
    }

    // Schedule status: now → next with progress through the current block, or Hold / Paused.
    _status(a) {
      const hass = this._hass;
      const range = (heat, cool) => cool === undefined || cool === null ? `${fmtTemp(heat)}°` : `${fmtTemp(heat)}–${fmtTemp(cool)}°`;
      const who = (period) =>
        `<div class="who" style="--bc:${labelColor(period.label)}"><span class="sw"></span><span class="lbl" title="${esc(period.label)}">${esc(period.label)}</span></div><div class="rng">${range(period.heat, period.cool)}</div>`;
      const p = a.period || {};
      // Right column for Hold and Paused: a button showing what Resume returns to.
      const resume = `<button class="resume" data-action="resume" title="Resume the schedule">
          <span class="cap">Resume</span>${p.label ? who(p) : `<div class="who"><span class="lbl">Schedule</span></div>`}</button>`;
      const lead = (cap, icon, text) =>
        `<div class="now"><div class="cap">${cap}</div><div class="who"><ha-icon icon="${icon}"></ha-icon><span class="lbl">${text}</span></div></div>`;

      const popOpen = this._pop && this._pop.key === ".until";
      const untilBtn = (action, text, label) => `<button class="until ${popOpen ? "open" : ""}" data-action="${action}" data-pop="1"
          aria-haspopup="menu" aria-expanded="${!!popOpen}" aria-label="${esc(label)}"><span>${esc(text)}</span><ha-icon icon="mdi:chevron-down"></ha-icon></button>`;
      const progress = (from, to, color) => {
        const s = new Date(from), e = new Date(to);
        const done = Math.max(0, Math.min(100, ((Date.now() - s) / Math.max(1, e - s)) * 100));
        return `<div class="bar" style="--bc:${color}"><i style="width:${done.toFixed(1)}%"></i></div>`;
      };

      const vac = a.vacation;
      if (a.status === "vacation" && vac && !this._pending) {
        const untilText = `until ${fmtWhenFar(hass, vac.end)}`;
        return `<div class="st follow hold vacation">
          ${lead("Vacation", "mdi:bag-suitcase-outline", range(vac.heat, vac.cool))}
          <div class="mid">${untilBtn("vac-menu", untilText, `Vacation ${untilText}. Change`)}${progress(vac.start, vac.end, "var(--cs-warn)")}</div>
          <button class="resume" data-action="vac-cancel" title="End the vacation now">
            <span class="cap">End</span>${p.label ? who(p) : `<div class="who"><span class="lbl">Schedule</span></div>`}</button></div>`;
      }

      if (["unavailable", "unknown"].includes(this.stateObj.state) && a.schedule_enabled === undefined) {
        // No attributes to go on (and Resume would end a hold the thermostat may still have).
        return `<div class="st follow paused">
          ${lead("Unavailable", "mdi:lan-disconnect", "Offline")}
          <div class="mid">back to the plan when it returns</div><div></div></div>`;
      }
      if (!a.schedule_enabled) {
        return `<div class="st follow paused">
          ${lead("Paused", "mdi:calendar-remove-outline", "Schedule off")}
          <div class="mid">set points stay put</div>
          ${resume}</div>`;
      }

      // A set point change becomes a hold; show it right away instead of waiting for the
      // debounce and the round trip (the real hold replaces this moments later).
      let hold = a.hold;
      if (!hold && this._pending) {
        const d = a.hold_default;
        const until = d === "indefinite" ? null
          : /^\d+$/.test(String(d)) ? new Date(Date.now() + Number(d) * 60e3).toISOString()
          : a.next_change;
        hold = { heat: this._pending.heat, cool: this._pending.cool, until, started: new Date().toISOString() };
      }
      this._holdNow = hold; // for the hold-length menu
      if (hold) {
        const untilText = hold.until ? `until ${fmtWhen(hass, hold.until)}` : "until you resume";
        let bar = "";
        if (hold.until && hold.started) {
          const s = new Date(hold.started), e = new Date(hold.until);
          const done = Math.max(0, Math.min(100, ((Date.now() - s) / Math.max(1, e - s)) * 100));
          bar = `<div class="bar" style="--bc:var(--cs-warn)"><i style="width:${done.toFixed(1)}%"></i></div>`;
        }
        return `<div class="st follow hold">
          ${lead("Hold", "mdi:hand-back-right-outline", range(hold.heat, hold.cool))}
          <div class="mid">${untilBtn("hold-menu", untilText, `Hold ${untilText}. Change how long`)}${bar}</div>
          ${resume}</div>`;
      }

      const nxt = a.next_period;
      const nextCol = `<div class="next"><div class="cap">Next</div>${nxt ? who(nxt) : `<div class="who"><span class="lbl">No changes</span></div>`}</div>`;
      if (a.status === "away") {
        return `<div class="st follow away">
          ${lead("Away", "mdi:home-export-outline", range(a.heat_setpoint, a.cool_setpoint))}
          <div class="mid">nobody home</div>
          <div class="next"><div class="cap">When back</div>${p.label ? who(p) : `<div class="who"><span class="lbl">Schedule</span></div>`}</div></div>`;
      }
      if (a.status === "early" && a.early_start && nxt && a.next_change) {
        // The next block is running already so the room is there when it starts.
        return `<div class="st follow early">
          <div class="now"><div class="cap">Starting early</div>${who(nxt)}</div>
          <div class="mid">${untilBtn("plan-menu", `ready by ${fmtWhen(hass, a.next_change)}`, `Ready by ${fmtWhen(hass, a.next_change)}. Vacation or pause`)}
            <div class="bar" style="--bc:${labelColor(nxt.label)}"><i style="width:100%"></i></div></div>
          <div class="next"><div class="cap">Instead of</div>${who(p)}</div></div>`;
      }
      let pct = 0;
      if (a.next_change && p.start !== undefined) {
        // When the block began, from the clock at home (it may not be the browser's).
        const now = Date.now();
        const start = now - (((homeClock(hass, now).minute - p.start + 1440) % 1440) * 60 + new Date(now).getSeconds()) * 1000;
        const end = new Date(a.next_change);
        pct = Math.max(0, Math.min(100, ((now - start) / Math.max(1, end - start)) * 100));
      }
      const untilText = a.next_change ? `until ${fmtWhen(hass, a.next_change)}` : "all week";
      return `<div class="st follow">
        <div class="now"><div class="cap">Now</div>${who(p)}</div>
        <div class="mid">${untilBtn("plan-menu", untilText, `${untilText}. Vacation or pause`)}
          <div class="bar" style="--bc:${labelColor(p.label)}"><i style="width:${pct.toFixed(1)}%"></i></div></div>
        ${nextCol}
      </div>`;
    }

    static getConfigForm() {
      const sensor = (name, device_class) => ({ name, selector: { entity: { domain: "sensor", device_class } } });
      const layouts = [["buttons", "Buttons"], ["compact", "Compact"]];
      return configForm([
        appearanceSection("name", [
          grid([
            { name: "style", selector: choose([["classic", "Classic"], ["dial", "Dial"]]) },
            { name: "layout", selector: choose([["full", "Full"], ["compact", "Compact"]]) },
          ]),
        ]),
        section("temperature", "Temperature and set points", "mdi:thermometer", [
          grid([toggle("hide_targets"), toggle("hide_target_buttons")]),
          grid([toggle("hide_humidity"), toggle("hide_battery")]),
          sensor("humidity_entity", "humidity"),
          sensor("battery_entity", "battery"),
        ]),
        section("controls", "Mode and fan", "mdi:tune-variant", [
          grid([
            { name: "mode_layout", selector: choose(layouts) },
            { name: "fan_layout", selector: choose(layouts) },
          ]),
          grid([toggle("hide_mode"), toggle("hide_fan")]),
        ]),
        section("schedule", "Schedule", "mdi:calendar-clock", [
          grid([toggle("week_expanded"), { name: "week_position", selector: choose([["bottom", "Bottom"], ["top", "Top"]]) }]),
        ]),
        section("runtime", "Runtime", "mdi:chart-bar", [
          toggle("show_runtime"),
          grid([
            { name: "runtime_style", selector: choose([["today", "Today"], ["week", "Last 7 days"]]) },
            { name: "runtime_show", selector: choose([["both", "Heating and cooling"], ["heat", "Heating only"], ["cool", "Cooling only"]]) },
          ]),
        ]),
      ]);
    }

    // "Heat/Cool · auto-switching · last heated 2:14 PM" — what the thermostat is doing,
    // rather than repeating the device name.
    _subtitle(a, mode, unavailable) {
      if (unavailable) return "Unavailable";
      const parts = [];
      const labels = { off: "Off", heat: "Heat", cool: "Cool", heat_cool: "Heat/Cool" };
      parts.push(labels[mode] || mode);
      if (mode === "heat_cool" && a.heat_cool_switching) parts.push("auto-switching");
      const action = a.hvac_action;
      const minutes = a.health && a.health.run_minutes;
      if (action === "heating" || action === "cooling") {
        const verb = action === "heating" ? "heating" : "cooling";
        parts.push(minutes ? `${verb} for ${fmtDur(minutes / 60)}` : `${verb} now`);
      } else if (a.last_run && a.last_run.end) {
        parts.push(`last ${a.last_run.kind === "cool" ? "cooled" : "heated"} ${fmtWhen(this._hass, a.last_run.end)}`);
      }
      const followers = (a.followers || []).length;
      if (followers) parts.push(`+${followers} thermostat${followers > 1 ? "s" : ""}`);
      if (a.vacation && a.status !== "vacation" && new Date(a.vacation.start) > Date.now()) {
        parts.push(`vacation from ${fmtWhenFar(this._hass, a.vacation.start)}`);
      }
      return parts.join(" · ");
    }

    // Humidity and battery under the indoor temperature; each opens its sensor's details.
    _stats(a) {
      const hass = this._hass;
      const stat = (entity, icon, text, label, low) => {
        const inner = `<ha-icon icon="${icon}"></ha-icon>${esc(text)}`;
        return entity
          ? `<button class="stat ${low ? "low" : ""}" data-action="more-info" data-entity="${esc(entity)}" title="${esc(label)}" aria-label="${esc(label)}">${inner}</button>`
          : `<span class="stat ${low ? "low" : ""}" title="${esc(label)}">${inner}</span>`;
      };
      const items = [];
      const humidity = this._config.humidity_entity ? num(hass.states[this._config.humidity_entity]?.state) : num(a.current_humidity);
      if (humidity !== null && !this._config.hide_humidity) {
        const h = Math.round(humidity);
        items.push(stat(this._config.humidity_entity || a.humidity_entity || a.source_entity, "mdi:water-percent", `${h}%`, `Humidity ${h}%`, false));
      }
      const batteryEntity = this._config.battery_entity || a.battery_entity;
      const battery = this._config.battery_entity ? num(hass.states[this._config.battery_entity]?.state) : num(a.battery_level);
      if (battery !== null && !this._config.hide_battery) {
        const level = Math.max(0, Math.min(100, Math.round(battery)));
        const tens = Math.round(level / 10) * 10;
        const icon = level < 20 ? "mdi:battery-alert-variant-outline" : tens >= 100 ? "mdi:battery" : `mdi:battery-${tens}`;
        items.push(stat(batteryEntity, icon, `${level}%`, level < 20 ? `Battery low: ${level}%` : `Battery ${level}%`, level < 20));
      }
      return `<div class="stats">${items.join("")}</div>`;
    }

    // Runtime row (show_runtime). "today": time so far today plus the 7-day change, tap for
    // more. "week": a small stacked bar per day for the last 7 days, tap a bar for that day.
    _runtime(a) {
      const rt = a.runtime;
      if (!this._config.show_runtime || !rt) return "";
      const kinds = a.cooling_enabled === false ? ["heat"] : runtimeKinds(this._config.runtime_show);
      const value = (kind, hours) =>
        `<span class="v ${kind}"><ha-icon icon="${RUNTIME_KINDS[kind].icon}"></ha-icon>${fmtDur(hours)}</span>`;

      if (this._config.runtime_style === "week") {
        const days = (rt.daily || []).slice(-7);
        const max = Math.max(0.01, ...days.map((d) => kinds.reduce((t, kind) => t + (d[kind] || 0), 0)));
        const H = 34;
        const bars = days.map((d, k) => {
          const today = k === days.length - 1;
          const open = this._pop && this._pop.key === `.spk[data-k="${k}"]`;
          const segs = kinds
            .map((kind) => (d[kind] > 0 ? `<i class="${kind}" style="height:${Math.max(2, (d[kind] / max) * H).toFixed(1)}px"></i>` : ""))
            .join("");
          const said = kinds.map((kind) => `${RUNTIME_KINDS[kind].label.toLowerCase()} ${fmtDur(d[kind])}`).join(", ");
          return `<button class="spk ${today ? "today" : ""} ${open ? "open" : ""}" data-action="rt-day" data-pop="1" data-k="${k}"
            aria-label="${esc(`${this._dayTitle(d.date)}: ${said}`)}">${segs || `<i class="empty"></i>`}</button>`;
        }).join("");
        return `<div class="rtrow" style="grid-template-columns:auto minmax(0,1fr) auto">
          <span class="cap">Last<br>7 days</span>
          <div class="spark" role="group" aria-label="Runtime, last 7 days">${bars}</div>
          <div class="tot">${kinds.map((kind) => value(kind, (rt[kind] || {}).last_7_days)).join("")}</div>
        </div>`;
      }

      // "today": the change shown is for whichever ran more over the last 7 days (heating in
      // winter, cooling in summer), since one percentage fits on the line.
      const c = (rt.compare || {}).day;
      let change = "";
      if (c) {
        const main = kinds.length > 1 && (c.cool || 0) > (c.heat || 0) ? "cool" : kinds.length > 1 ? "heat" : kinds[0];
        const which = kinds.length > 1 ? ` ${RUNTIME_KINDS[main].label.toLowerCase()}` : "";
        // A percentage only means something with a real week before to compare with (tracked,
        // and not zero). Until then, show the 7-day total instead.
        change = c.complete !== false && c[`prev_${main}`] > 0
          ? `<span class="cmpx">vs the 7 days before<br>${runtimeChange(c[main], c[`prev_${main}`])}${which}</span>`
          : `<span class="cmpx">last 7 days<br>${fmtDur(c[main])}${which}</span>`;
      }
      const open = this._pop && this._pop.key === ".rtrow";
      return `<button class="rtrow ${open ? "open" : ""}" style="grid-template-columns:auto repeat(${kinds.length},auto) auto"
          data-action="rt" data-pop="1" aria-haspopup="dialog" aria-label="Runtime today: ${esc(kinds.map((kind) => `${RUNTIME_KINDS[kind].label.toLowerCase()} ${fmtDur((rt[kind] || {}).today)}`).join(", "))}. Show more">
        <span class="cap">Today</span>
        ${kinds.map((kind) => value(kind, (rt[kind] || {}).today)).join("")}
        ${change}
      </button>`;
    }

    _dayTitle(iso) {
      return new Date(`${iso}T12:00:00`).toLocaleDateString(this._hass?.locale?.language || undefined, { weekday: "long", month: "short", day: "numeric" });
    }

    // Popups for the runtime row: the totals table ("today" style) or one day ("week" style).
    _runtimePop(a, which, k) {
      const rt = a.runtime || {};
      const kinds = a.cooling_enabled === false ? ["heat"] : runtimeKinds(this._config.runtime_show);
      const head = kinds.map((kind) => `<dd class="hd ${kind}"><ha-icon icon="${RUNTIME_KINDS[kind].icon}" style="color:var(--tc)"></ha-icon></dd>`).join("");
      if (which === "day") {
        const d = (rt.daily || []).slice(-7)[k];
        if (!d) return "";
        const rows = kinds.map((kind) => `<dt>${RUNTIME_KINDS[kind].label}</dt><dd>${fmtDur(d[kind])}</dd>`).join("");
        return `<div class="ph">${esc(this._dayTitle(d.date))}</div>
          <dl style="margin-top:8px">${rows}${kinds.length > 1 ? `<dt>Total</dt><dd>${fmtDur(d.heat + d.cool)}</dd>` : ""}</dl>`;
      }
      const rows = [["today", "Today"], ["yesterday", "Yesterday"], ["last_7_days", "Last 7 days"], ["this_month", "This month"], ["this_year", "This year"]]
        .map(([key, label]) => `<dt>${label}</dt>${kinds.map((kind) => `<dd>${fmtDur((rt[kind] || {})[key])}</dd>`).join("")}`)
        .join("");
      return `<div class="ph">Runtime</div>
        <dl class="rtpop" style="margin-top:8px;--n:${kinds.length}"><dt></dt>${head}${rows}</dl>`;
    }

    _bump(side, dir) {
      const a = this.stateObj.attributes;
      const unit = this._hass.config.unit_system.temperature;
      const step = num(a.target_temp_step) || (unit === "°C" ? 0.5 : 1);
      let heat = this._pending ? this._pending.heat : num(a.heat_setpoint);
      let cool = this._pending ? this._pending.cool : (num(a.cool_setpoint) ?? num(a.max_temp));
      if (heat === null || cool === null) return;
      if (side === "heat") heat += dir * step;
      else cool += dir * step;
      const g = guard(heat, cool, side, a.cooling_enabled === false ? 0 : num(a.deadband) ?? 3, num(a.min_temp) ?? 40, num(a.max_temp) ?? 95);
      this._pending = { heat: g.heat, cool: g.cool };
      this._toast(g.note);
      // The thermostat is battery powered: wait for the taps to stop, then send once.
      clearTimeout(this._sendTimer);
      this._sendTimer = setTimeout(() => this._send(), 1200);
    }

    _send() {
      const p = this._pending;
      if (!p) return;
      const now = this.stateObj.attributes;
      const cooling = now.cooling_enabled !== false;
      if (p.heat === num(now.heat_setpoint) && (!cooling || p.cool === num(now.cool_setpoint))) {
        // Ended where it started (dragged back, or + then -): nothing to send, no hold.
        this._pending = null;
        this._msg = "";
        this._render();
        return;
      }
      const mode = this.stateObj.state;
      let call;
      if (!cooling) call = this._call(DOMAIN, "set_hold", { heat: p.heat });
      else if (mode === "heat_cool") call = this._call("climate", "set_temperature", { target_temp_low: p.heat, target_temp_high: p.cool });
      else if (mode === "heat") call = this._call("climate", "set_temperature", { temperature: p.heat });
      else if (mode === "cool") call = this._call("climate", "set_temperature", { temperature: p.cool });
      else call = this._call(DOMAIN, "set_hold", { heat: p.heat, cool: p.cool });
      call.finally(() => {
        this._pending = null;
        this._render();
      });
    }
  }

  /* ------------------------------------------------------------------ schedule card */

  const SCHED_CSS = `
    ha-card { container-type: inline-size; }
    .head { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: start; gap: 12px; }
    .sugs { display: grid; gap: 8px; }
    .sug {
      display: grid; grid-template-columns: 20px minmax(0, 1fr) auto; gap: 10px; align-items: center;
      padding: 10px 12px; border-radius: 12px; background: color-mix(in srgb, var(--cs-accent) 10%, var(--cs-bg));
    }
    .sug > ha-icon { --mdc-icon-size: 20px; color: var(--cs-accent-ink); align-self: start; margin-top: 1px; }
    .sug .stx { font-weight: var(--ha-font-weight-medium, 500); }
    .sug .swy { font-size: var(--ha-font-size-s, 12px); color: var(--cs-muted); margin-top: 2px; }
    .sug .sa { display: flex; gap: 6px; }
    @container (max-width: 420px) {
      .sug { grid-template-columns: 20px minmax(0, 1fr); }
      .sug .sa { grid-column: 2; }
    }
    .head .sub { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .grid { display: grid; gap: 8px; }
    .axis, .row { display: grid; grid-template-columns: 44px 1fr; gap: 10px; align-items: center; }
    .ax { position: relative; height: 16px; font-size: 10px; color: var(--cs-muted); }
    .ax span { position: absolute; top: 1px; transform: translateX(-50%); white-space: nowrap; }
    .ax span:first-child { transform: none; } .ax span:last-of-type { transform: translateX(-100%); }
    /* Narrow card: every 6 hours instead of every 3. */
    @container (max-width: 440px) { .ax span.odd { display: none; } }
    /* Current time as a small pill on the axis, directly above the time line. */
    .ax .nowlabel {
      position: absolute; top: -1px; transform: translateX(-50%); z-index: 1; white-space: nowrap;
      font-size: 10px; font-weight: var(--ha-font-weight-medium, 500); font-variant-numeric: tabular-nums; line-height: 16px;
      padding: 0 7px; border-radius: 99px; color: var(--text-primary-color, #fff); background: var(--cs-accent-fill);
    }
    .day {
      justify-self: start; font-size: var(--ha-font-size-s, 13px); font-weight: var(--ha-font-weight-medium, 500);
      color: var(--cs-muted); padding: 2px 8px; border-radius: 99px;
    }
    .day.today { color: var(--text-primary-color, #fff); background: var(--cs-accent-fill); }
    /* Blocks are absolutely positioned by start time, so 3:00 AM is at the same x on every
       day no matter how many blocks a day has. The 1px inset on each side draws the gaps. */
    .bar { position: relative; height: 44px; border-radius: 8px; overflow: hidden; background: var(--cs-soft); }
    .blk {
      position: absolute; top: 0; bottom: 0; box-sizing: border-box; border-radius: 3px;
      border: 0; font: inherit; font-size: 11px; line-height: 1.15; padding: 0 6px; text-align: left; color: var(--cs-text);
      background: color-mix(in srgb, var(--bc) 28%, var(--cs-bg));
      overflow: hidden; white-space: nowrap; text-overflow: ellipsis; min-width: 0; display: flex; flex-direction: column; justify-content: center; gap: 3px;
      transition: opacity .15s, box-shadow .15s, background .15s; cursor: default;
    }
    /* Temperature line reads as one title: the mode/fan icons are inline with the text, same
       colour, sized to it and sitting on its baseline (ha-icon otherwise centres on the
       lowercase x-height, which drops it below the digits). */
    .blk .t {
      display: block; overflow: hidden; text-overflow: ellipsis; line-height: 14px;
      font-variant-numeric: tabular-nums; font-weight: var(--ha-font-weight-medium, 500);
    }
    .blk .t ha-icon { display: inline-flex; vertical-align: -0.18em; margin-left: 0.3em; --mdc-icon-size: 1.1em; }
    .blk .t ha-icon + ha-icon { margin-left: 0.15em; }
    .blk .n { color: var(--cs-muted); font-size: 10px; overflow: hidden; text-overflow: ellipsis; }
    /* Text sits above the current-time line, with a halo in the block's own colour so the line
       doesn't run through the letters. */
    .blk { --blkbg: color-mix(in srgb, var(--bc) 28%, var(--cs-bg)); container-type: inline-size; }
    .blk .t, .blk .n { position: relative; z-index: 5; text-shadow: 0 0 2px var(--blkbg), 0 0 4px var(--blkbg), 0 0 6px var(--blkbg); }
    /* Too narrow for the set points (sizes are the text area, inside the padding): show the label
       alone, and nothing at all when even that won't fit. */
    @container (max-width: 44px) { .blk .t { display: none; } }
    @container (max-width: 20px) { .blk .n { display: none; } }
    /* Read-only view: the block whose details are open. */
    .blk.peek { z-index: 5; background: color-mix(in srgb, var(--bc) 42%, var(--cs-bg)); box-shadow: inset 0 0 0 2px var(--bc); }
    .blk:focus-visible { outline: 2px solid var(--cs-accent); outline-offset: -2px; }
    /* Editing: blocks become clickable, the others recede and the selected one stands out. */
    .editing .blk { cursor: pointer; opacity: .55; }
    .editing .blk:hover { opacity: .8; }
    .editing .blk.sel {
      opacity: 1; z-index: 5; background: color-mix(in srgb, var(--bc) 45%, var(--cs-bg)); /* above the current-time line */
      box-shadow: inset 0 0 0 2px var(--bc), inset 0 0 0 3px color-mix(in srgb, var(--cs-bg) 55%, transparent);
    }
    .editing .blk.sel .n { color: var(--cs-text); }
    /* Drag grips on the selected block's edges, and the time shown while dragging. */
    .grip {
      position: absolute; top: 7px; bottom: 7px; width: 8px; margin-left: -4px; z-index: 6; box-sizing: border-box;
      border-radius: 4px; background: var(--cs-bg); border: 1.5px solid var(--cs-text); cursor: ew-resize; touch-action: none;
      box-shadow: 0 1px 3px rgba(0,0,0,.35); transition: transform .12s;
    }
    .grip::before { content: ""; position: absolute; inset: -6px -10px; } /* bigger touch target */
    .grip:hover, .grip.drag { transform: scaleX(1.25); background: var(--cs-accent); border-color: var(--cs-accent); }
    .dragtime {
      position: absolute; z-index: 7; transform: translateX(-50%); padding: 1px 8px; border-radius: 99px; pointer-events: none;
      background: var(--cs-accent-fill); color: var(--text-primary-color, #fff); font-size: 11px; font-weight: var(--ha-font-weight-medium, 500);
      font-variant-numeric: tabular-nums; white-space: nowrap;
    }
    .guide { position: absolute; top: 0; bottom: 0; width: 1px; background: color-mix(in srgb, var(--cs-text) 6%, transparent); pointer-events: none; z-index: 2; }
    /* Current time: a soft line down the whole week (drawn once over the grid), and a
       brighter, gently glowing segment on today's row. */
    .grid { position: relative; }
    .nowcol {
      position: absolute; top: 15px; bottom: 0; width: 2px; margin-left: -1px; border-radius: 1px; pointer-events: none; z-index: 3;
      background: color-mix(in srgb, var(--cs-accent) 35%, transparent);
    }
    .now {
      position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; pointer-events: none; z-index: 4;
      background: var(--cs-accent); box-shadow: 0 0 6px 1px color-mix(in srgb, var(--cs-accent) 55%, transparent);
    }
    .editor { border: 1px solid var(--cs-line); border-radius: 12px; padding: 14px; display: grid; gap: 14px; }
    .ehead { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 12px; }
    .ehead .title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
    .ehead .swatch { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 8px; background: var(--bc); vertical-align: 0; }
    .actions { display: flex; gap: 6px; }
    /* Two fixed columns: when + name on the first row, heat + cool on the second. */
    .fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 14px; }
    .field { display: grid; gap: 4px; font-size: var(--ha-font-size-s, 12px); color: var(--cs-muted); min-width: 0; }
    .field select, .field input { width: 100%; }
    /* Heat/cool set point tiles, mirroring the thermostat card (heat left, cool right). */
    .temp {
      display: grid; grid-template-columns: 36px minmax(0, 1fr) 36px; grid-template-rows: auto 36px; align-items: center; row-gap: 6px;
      padding: 10px 12px; border-radius: 14px;
      background: color-mix(in srgb, var(--tc) 10%, var(--cs-bg)); border: 1px solid color-mix(in srgb, var(--tc) 32%, var(--cs-bg));
    }
    .temp .lab {
      grid-column: 1 / -1; display: flex; align-items: center; gap: 4px; --mdc-icon-size: 16px;
      font-size: 11px; font-weight: var(--ha-font-weight-medium, 500); letter-spacing: .09em; text-transform: uppercase; color: color-mix(in srgb, var(--tc) var(--cs-ink), var(--cs-text));
    }
    .temp .v { text-align: center; font-size: 26px; line-height: 1; font-variant-numeric: tabular-nums; color: var(--cs-text); }
    .copyrow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    /* Per-label color: Auto (keyword default) or a palette swatch. */
    .colorrow { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .colorrow .lbl { font-size: var(--ha-font-size-s, 12px); color: var(--cs-muted); margin-right: 4px; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .colorrow .auto {
      font: inherit; font-size: var(--ha-font-size-s, 12px); height: 26px; padding: 0 10px; border-radius: 99px; cursor: pointer;
      border: 1px solid var(--cs-line); background: transparent; color: var(--cs-text);
    }
    .colorrow .auto.on { border-color: var(--cs-accent); color: var(--cs-accent-ink); }
    .colorrow .swatch {
      width: 24px; height: 24px; padding: 0; border-radius: 7px; border: 0; cursor: pointer; background: var(--sc);
      box-shadow: inset 0 0 0 1px color-mix(in srgb, #000 15%, transparent);
    }
    .colorrow .swatch.on { box-shadow: 0 0 0 2px var(--cs-bg), 0 0 0 4px var(--sc); }
    .copyrow .lbl { font-size: var(--ha-font-size-s, 12px); color: var(--cs-muted); margin: 0 2px; }
    /* Two-way toggle: copy the selected block, or the whole day. */
    .scope { display: inline-flex; height: 36px; padding: 3px; box-sizing: border-box; border-radius: 8px; background: var(--cs-soft); gap: 2px; }
    .scope button {
      font: inherit; font-size: var(--ha-font-size-s, 13px); border: 0; border-radius: 6px; padding: 0 10px; cursor: pointer;
      background: transparent; color: var(--cs-muted); white-space: nowrap;
    }
    .scope button:hover { color: var(--cs-text); }
    .scope button.on { background: var(--cs-bg); color: var(--cs-text); box-shadow: 0 1px 2px rgba(0,0,0,.15); font-weight: var(--ha-font-weight-medium, 500); }
    .savebar {
      display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 10px;
      padding: 8px 8px 8px 12px; border-radius: 10px; background: var(--cs-soft);
    }
    .savebar.dirty { background: color-mix(in srgb, var(--cs-accent) 12%, var(--cs-bg)); }
    .savebar .state { font-size: var(--ha-font-size-m, 14px); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .savebar .state.toast { color: var(--cs-accent-ink); }
  `;

  class ComfortScheduleCard extends ComfortBase {
    constructor() {
      super();
      this.shadowRoot.addEventListener("pointerdown", (e) => this._gripDown(e));
      // The label goes into the draft as it's typed (enabling Save), without a redraw.
      this.shadowRoot.addEventListener("input", (e) => {
        if (e.target.dataset?.action === "label") this._labelTyped(e.target);
      });
      // Clicking Save straight from the label field: keep the focus there, or the field's
      // change event redraws the card under the pointer and the click is lost.
      this.shadowRoot.addEventListener("mousedown", (e) => {
        if (e.target.closest('[data-action="save"]') && this.shadowRoot.activeElement?.dataset?.action === "label") e.preventDefault();
      });
    }

    _labelTyped(el) {
      const sel = this._sel;
      if (!sel) return;
      if (!this._draft) this._draft = JSON.parse(JSON.stringify(this.stateObj.attributes.schedule || []));
      const p = this._draft[sel.d]?.[sel.i];
      if (!p) return;
      p.label = el.value.trim().slice(0, 40) || "Period";
      if (this._draftKey() === this._savedKey()) {
        this._draft = null;
        this._draftColors = null;
      }
      const save = this.shadowRoot.querySelector('[data-action="save"]');
      if (save) save.disabled = !this._draft;
      this.shadowRoot.querySelector(".savebar")?.classList.toggle("dirty", !!this._draft);
      const state = this.shadowRoot.querySelector(".savebar .state:not(.toast)");
      if (state) state.textContent = this._draft ? "Unsaved changes" : "No changes yet";
    }

    getCardSize() {
      return 9;
    }

    // ---------------------------------------------------------------- dragging block edges

    // Edit mode: drag the selected block's edge to move the boundary with its neighbour.
    // Snaps to 5 minutes; midnight edges don't move. Blocks resize live and the result goes
    // into the unsaved draft on release (so Cancel still undoes it).
    _gripDown(e) {
      const grip = e.target.closest(".grip");
      if (!grip || !this._sel || e.button > 0) return;
      e.preventDefault();
      e.stopPropagation();
      const bar = grip.closest(".bar");
      const grid = this.shadowRoot.querySelector(".grid");
      const pill = this.shadowRoot.querySelector(".dragtime");
      const day = this._week()[this._sel.d];
      const b = grip.dataset.edge === "start" ? this._sel.i : this._sel.i + 1; // block whose start moves
      const endOf = (k) => (k + 1 < day.length ? day[k + 1].start : 1440);
      const min = day[b - 1].start + STEP_MIN;
      const max = endOf(b) - STEP_MIN;
      const rect = bar.getBoundingClientRect();
      const toMin = (x) =>
        Math.max(min, Math.min(max, Math.round((((x - rect.left) / rect.width) * 1440) / STEP_MIN) * STEP_MIN));
      const before = bar.querySelector(`.blk[data-i="${b - 1}"]`);
      const after = bar.querySelector(`.blk[data-i="${b}"]`);
      const place = (el, start, end) => {
        el.style.left = `calc(${(start / 1440) * 100}% + 1px)`;
        el.style.width = `calc(${((end - start) / 1440) * 100}% - 2px)`;
      };
      let value = day[b].start;
      const show = () => {
        place(before, day[b - 1].start, value);
        place(after, value, endOf(b));
        grip.style.left = `${(value / 1440) * 100}%`;
        const g = grid.getBoundingClientRect();
        pill.textContent = fmtMinutes(this._hass, value);
        pill.style.left = `${rect.left - g.left + (value / 1440) * rect.width}px`;
        pill.style.top = `${rect.top - g.top - 22}px`;
        pill.hidden = false;
      };
      this._gripping = true;
      grip.classList.add("drag");
      try { grip.setPointerCapture(e.pointerId); } catch (_err) { /* moves still arrive */ }
      const move = (ev) => { value = toMin(ev.clientX); show(); };
      const end = () => {
        grip.removeEventListener("pointermove", move);
        grip.removeEventListener("pointerup", end);
        grip.removeEventListener("pointercancel", end);
        this._gripping = false;
        if (value !== day[b].start) {
          const d = this._sel.d;
          this._edit((w) => { w[d][b].start = value; }, `Moved to ${fmtMinutes(this._hass, value)}`);
        } else {
          this._render();
        }
      };
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", end);
      grip.addEventListener("pointercancel", end);
      show();
    }

    static getConfigForm() {
      return configForm([
        appearanceSection("title"),
        section("editing", "Editing", "mdi:pencil-outline", [toggle("read_only"), toggle("hide_suggestions")]),
      ]);
    }

    _week() {
      return this._draft || this.stateObj?.attributes?.schedule || [];
    }

    _labelColors() {
      return this._draftColors || this.stateObj.attributes.label_colors || {};
    }

    _limits() {
      const a = this.stateObj.attributes;
      return { db: a.cooling_enabled === false ? 0 : num(a.deadband) ?? 3, lo: num(a.min_temp) ?? 40, hi: num(a.max_temp) ?? 95 };
    }

    _edit(fn, msg = "") {
      const before = this._draftKey();
      if (!this._draft) this._draft = JSON.parse(JSON.stringify(this.stateObj.attributes.schedule || []));
      fn(this._draft);
      const after = this._draftKey();
      // "Unsaved changes" means different from what's saved: an edit that lands back on the
      // saved schedule (a value set back, Auto picked when it's already Auto) drops the draft.
      if (after === this._savedKey()) {
        this._draft = null;
        this._draftColors = null;
      }
      // Say what happened only if something did.
      this._toast(after === before ? "" : msg);
    }

    // Comparable form of a week and its label colors (missing mode/fan = null, numbers as
    // numbers, colors in label order), so equal schedules compare equal however they were built.
    _scheduleKey(week, colors) {
      const days = (week || []).map((day) => (day || []).map((p) =>
        [Number(p.start), Number(p.heat), p.cool === undefined ? null : Number(p.cool), String(p.label ?? ""), p.mode || null, p.fan || null]));
      const pairs = Object.entries(colors || {}).map(([k, v]) => [k, String(v).toUpperCase()]).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
      return JSON.stringify([days, pairs]);
    }

    _savedKey() {
      const a = this.stateObj.attributes;
      const cooling = a.cooling_enabled !== false;
      return this._scheduleKey(a.schedule, a.label_colors);
    }

    _draftKey() {
      return this._scheduleKey(this._week(), this._labelColors());
    }

    _render() {
      if (this._missing() || this._gripping) return; // don't replace the grip mid-drag
      const hass = this._hass;
      const week = this._week();
      if (week.length !== 7) {
        if (this._offline(SCHED_CSS)) return;
        this._paint(SCHED_CSS, `<ha-card><div class="warn">This entity has no Comfort Schedule.</div></ha-card>`);
        return;
      }
      const { day: today, minute: nowMin } = homeClock(hass);
      if (!this._sel || !week[this._sel.d] || !week[this._sel.d][this._sel.i]) {
        const day = week[today];
        let i = 0;
        day.forEach((p, k) => { if (p.start <= nowMin) i = k; });
        this._sel = { d: today, i };
      }
      const a = this.stateObj.attributes;

      const editing = !!this._editing;
      const nowPct = (nowMin / 1440) * 100;
      const hours = [0, 3, 6, 9, 12, 15, 18, 21, 24];
      // Hide hour labels the current-time pill would sit on top of: about 40px either side of
      // it, as a share of the bars' width (the card less its padding and the day column).
      const width = this.clientWidth;
      const clear = width ? Math.max(5.5, (40 / Math.max(200, width - 86)) * 100) : 5.5;
      const axis = `<div class="axis"><span></span><div class="ax">${hours
        .map((h) => {
          const pct = (h / 24) * 100;
          const hidden = Math.abs(pct - nowPct) < clear ? "visibility:hidden;" : "";
          return `<span class="${h % 6 ? "odd" : ""}" style="${hidden}left:${pct}%">${esc(fmtMinutes(hass, h * 60).replace(":00", ""))}</span>`;
        })
        .join("")}<i class="nowlabel" style="left:clamp(26px, ${nowPct}%, calc(100% - 26px))">${esc(fmtMinutes(hass, nowMin))}</i></div></div>`;
      // Soft line through every day at the current time (bars start after the 44px day column + 10px gap).
      const nowCol = `<div class="nowcol" style="left:calc(54px + (100% - 54px) * ${nowPct / 100})"></div>`;

      // Faint lines at the same 3-hour marks as the axis, drawn over every day.
      const guides = hours
        .slice(1, -1)
        .map((h) => `<div class="guide" style="left:${(h / 24) * 100}%"></div>`)
        .join("");

      const rows = week
        .map((day, d) => {
          const blocks = day
            .map((p, i) => {
              const end = i + 1 < day.length ? day[i + 1].start : 1440;
              const len = end - p.start;
              const sel = editing && this._sel.d === d && this._sel.i === i;
              const color = labelColor(p.label);
              // Small icons for blocks that switch the mode or fan when they start.
              const icons = [
                p.mode ? `<ha-icon icon="${MODE_ICONS[p.mode]}"></ha-icon>` : "",
                p.fan ? `<ha-icon icon="${fanInfo(p.fan, a.fan_modes || []).icon}"></ha-icon>` : "",
              ].join("");
              // Both lines always; the block's width decides what shows (see .blk in SCHED_CSS).
              const temps = cooling ? `${fmtTemp(p.heat)}–${fmtTemp(p.cool)}°` : `${fmtTemp(p.heat)}°`;
              const text = `<span class="t">${temps}${icons}</span><span class="n">${esc(p.label)}</span>`;
              const extras = [
                p.mode ? `mode ${MODE_LABELS[p.mode]}` : "",
                p.fan ? `fan ${fanInfo(p.fan, a.fan_modes || []).label}` : "",
              ].filter(Boolean).join(", ");
              const left = (p.start / 1440) * 100;
              const width = (len / 1440) * 100;
              const style = `left:calc(${left}% + 1px);width:calc(${width}% - 2px);--bc:${color}`;
              const title = esc(`${p.label}: ${fmtMinutes(hass, p.start)}–${fmtMinutes(hass, end)}, heat ${p.heat}°${cooling ? `, cool ${p.cool}°` : ""}${extras ? `, ${extras}` : ""}`);
              // Read-only until Edit is pressed: plain blocks, not buttons.
              return editing
                ? `<button class="blk ${sel ? "sel" : ""}" data-action="select" data-d="${d}" data-i="${i}" style="${style}" title="${title}" aria-pressed="${sel}">${text}</button>`
                : `<div class="blk ${this._pop && this._pop.key === `.blk[data-d="${d}"][data-i="${i}"]` ? "peek" : ""}" style="${style}" data-action="info" data-pop="1" data-d="${d}" data-i="${i}" role="button" tabindex="0" aria-label="${title}">${text}</div>`;
            })
            .join("");
          const nowLine = d === today ? `<div class="now" style="left:${nowPct}%"></div>` : "";
          // Grips on the selected block's movable edges (midnight edges stay put).
          let grips = "";
          if (editing && this._sel.d === d) {
            const si = this._sel.i;
            const endMin = si + 1 < day.length ? day[si + 1].start : 1440;
            if (si > 0) grips += `<div class="grip" data-edge="start" style="left:${(day[si].start / 1440) * 100}%" title="Drag to change the start" role="presentation"></div>`;
            if (si + 1 < day.length) grips += `<div class="grip" data-edge="end" style="left:${(endMin / 1440) * 100}%" title="Drag to change the end" role="presentation"></div>`;
          }
          return `<div class="row"><div class="day ${d === today ? "today" : ""}">${DAYS[d]}</div><div class="bar">${blocks}${guides}${nowLine}${grips}</div></div>`;
        })
        .join("");

      const { d, i } = this._sel;
      const day = week[d];
      const p = day[i];
      const end = i + 1 < day.length ? day[i + 1].start : 1440;
      let startOpts = "";
      if (i === 0) startOpts = `<option>${esc(fmtMinutes(hass, 0))}</option>`;
      else {
        for (let m = day[i - 1].start + STEP_MIN; m <= end - STEP_MIN; m += STEP_MIN)
          startOpts += `<option value="${m}" ${m === p.start ? "selected" : ""}>${esc(fmtMinutes(hass, m))}</option>`;
      }
      const labels = [...new Set(week.flat().map((x) => x.label))];
      const copyDay = this._copyScope === "day";
      const customColor = this._labelColors()[p.label] || "";
      const modeChoices = ["off", "heat", "cool", "heat_cool"].filter((m) => (a.hvac_modes || []).includes(m));
      const fanModes = a.fan_modes || [];

      const editor = `<div class="editor">
        <div class="ehead">
          <div class="title"><span class="swatch" style="--bc:${labelColor(p.label)}"></span>${DAYS[d]} · ${esc(fmtMinutes(hass, p.start))} – ${esc(fmtMinutes(hass, end))}</div>
          <div class="actions">
            <button class="btn" data-action="split" ${end - p.start < STEP_MIN * 2 ? "disabled" : ""}>Split</button>
            <button class="btn" data-action="delete" ${day.length < 2 ? "disabled" : ""}>Delete</button>
          </div>
        </div>
        <div class="fields">
          <label class="field">Starts<select data-action="start" ${i === 0 ? "disabled" : ""}>${startOpts}</select></label>
          <label class="field">Label<input type="text" data-action="label" list="cs-labels" maxlength="40" value="${esc(p.label)}"></label>
          <label class="field">Mode when this starts<select data-action="pmode">
            <option value="" ${p.mode ? "" : "selected"}>Use current</option>
            ${modeChoices.map((m) => `<option value="${m}" ${p.mode === m ? "selected" : ""}>${MODE_LABELS[m]}</option>`).join("")}
          </select></label>
          <label class="field">Fan when this starts<select data-action="pfan" ${fanModes.length ? "" : "disabled"}>
            <option value="" ${p.fan ? "" : "selected"}>Use current</option>
            ${fanModes.map((f) => `<option value="${esc(f)}" ${p.fan === f ? "selected" : ""}>${esc(fanInfo(f, fanModes).label)}</option>`).join("")}
          </select></label>
          <div class="temp heat">
            <div class="lab"><ha-icon icon="mdi:fire"></ha-icon>Heat to</div>
            <button class="sbtn" data-action="eh-dn" aria-label="Lower heat"><ha-icon icon="mdi:minus"></ha-icon></button>
            <span class="v">${fmtTemp(p.heat)}°</span>
            <button class="sbtn" data-action="eh-up" aria-label="Raise heat"><ha-icon icon="mdi:plus"></ha-icon></button>
          </div>
          ${cooling ? `<div class="temp cool">
            <div class="lab"><ha-icon icon="mdi:snowflake"></ha-icon>Cool to</div>
            <button class="sbtn" data-action="ec-dn" aria-label="Lower cool"><ha-icon icon="mdi:minus"></ha-icon></button>
            <span class="v">${fmtTemp(p.cool)}°</span>
            <button class="sbtn" data-action="ec-up" aria-label="Raise cool"><ha-icon icon="mdi:plus"></ha-icon></button>
          </div>` : ""}
        </div>
        <datalist id="cs-labels">${labels.map((l) => `<option value="${esc(l)}"></option>`).join("")}</datalist>
        <div class="colorrow" role="group" aria-label="Color for ${esc(p.label)}">
          <span class="lbl">Color for “${esc(p.label)}”</span>
          <button class="auto ${customColor ? "" : "on"}" data-action="lcolor" data-c="" aria-pressed="${!customColor}">Auto</button>
          ${PALETTE.map((c) => `<button class="swatch ${customColor === c ? "on" : ""}" style="--sc:${c}" data-action="lcolor" data-c="${c}" aria-label="${c}" aria-pressed="${customColor === c}"></button>`).join("")}
        </div>
        <div class="copyrow">
          <span class="lbl">Copy</span>
          <div class="scope" role="group" aria-label="What to copy">
            <button class="${copyDay ? "" : "on"}" data-action="scope" data-scope="block" aria-pressed="${!copyDay}">This block</button>
            <button class="${copyDay ? "on" : ""}" data-action="scope" data-scope="day" aria-pressed="${copyDay}">All of ${DAYS[d]}</button>
          </div>
          <span class="lbl">to</span>
          <button class="btn" data-action="copy" data-to="wk">Weekdays</button>
          <button class="btn" data-action="copy" data-to="we">Weekend</button>
          <button class="btn" data-action="copy" data-to="all">Every day</button>
        </div>
      </div>`;

      const nxt = a.next_period
        ? `Next change ${esc(fmtWhen(hass, a.next_change))} → ${esc(a.next_period.label)} ${fmtTemp(a.next_period.heat)}${cooling ? `–${fmtTemp(a.next_period.cool)}` : ""}°`
        : a.schedule_enabled ? "&nbsp;" : "Schedule paused";

      // Shown while editing; stays the same size whether or not anything has changed.
      const dirty = !!this._draft;
      const stateText = this._msg
        ? `<span class="state toast" role="status">${esc(this._msg)}</span>`
        : `<span class="state" role="status">${dirty ? "Unsaved changes" : "No changes yet"}</span>`;
      const savebar = `<div class="savebar ${dirty ? "dirty" : ""}">${stateText}<span class="actions">
          <button class="btn" data-action="cancel">Cancel</button>
          <button class="btn primary" data-action="save" ${dirty ? "" : "disabled"}>Save schedule</button></span></div>`;
      // Outside the editor, brief messages ("Schedule saved") replace the subtitle for a
      // moment instead of adding a line, so the card keeps its height.
      // (With the subtitle hidden, the message still shows there briefly.)
      const subtitle = !editing && this._msg
        ? `<span style="color:var(--cs-accent-ink)" role="status">${esc(this._msg)}</span>`
        : this._config.hide_subtitle ? "" : nxt;
      const heading = this._heading(this._config.title || "Weekly schedule", subtitle);
      const editBtn = editing || this._config.read_only
        ? ""
        : `<button class="btn hbtn" data-action="edit"><ha-icon icon="mdi:pencil"></ha-icon>Edit</button>`;

      // Suggestions from repeated manual changes; the strongest one or two, outside editing.
      const groups = { weekdays: "weekdays", weekend: "the weekend" };
      const sugs = editing || this._config.read_only || this._config.hide_suggestions ? [] : (a.suggestions || []).slice(0, 2);
      const sugHtml = sugs.length
        ? `<div class="sugs">${sugs.map((g) => {
            const days = groups[g.days] || g.days;
            const text = g.kind === "start"
              ? `Start ${g.label} at ${fmtMinutes(hass, g.start)} on ${days}`
              : `${g.side === "heat" ? "Heat" : "Cool"} to ${fmtTemp(g.value)}° for ${g.label} on ${days}`;
            const why = g.kind === "start"
              ? `You switched to ${g.label}'s set points before ${fmtMinutes(hass, g.was)} on ${g.days_seen} days in the last three weeks.`
              : `You set it to ${fmtTemp(g.value)}° on ${g.days_seen} days in the last three weeks.`;
            return `<div class="sug"><ha-icon icon="mdi:lightbulb-on-outline"></ha-icon>
              <div class="sx"><div class="stx">${esc(text)}</div><div class="swy">${esc(why)}</div></div>
              <div class="sa"><button class="btn hbtn" data-action="sug-dismiss" data-id="${esc(g.id)}">Dismiss</button>
                <button class="btn hbtn primary" data-action="sug-apply" data-id="${esc(g.id)}">Apply</button></div></div>`;
          }).join("")}</div>`
        : "";

      this._paint(
        SCHED_CSS,
        `<ha-card>
          ${heading || editBtn ? `<div class="head">${heading || "<div></div>"}${editBtn}</div>` : ""}
          <div class="grid ${editing ? "editing" : ""}">${axis}${rows}${nowCol}<div class="dragtime" hidden></div></div>
          ${sugHtml}
          ${editing ? editor + savebar : ""}
        </ha-card>`
      );
    }

    _onAction(action, el) {
      if (!this.stateObj) return;
      const { db, lo, hi } = this._limits();
      const unit = this._hass.config.unit_system.temperature;
      const step = num(this.stateObj.attributes.target_temp_step) || (unit === "°C" ? 0.5 : 1);
      const { d, i } = this._sel || { d: 0, i: 0 };
      const adjust = (side, dir) =>
        this._edit((w) => {
          const p = w[d][i];
          if (this.stateObj.attributes.cooling_enabled === false) {
            p.heat = Math.max(lo, Math.min(hi, round1(p.heat + dir * step)));
            return;
          }
          const g = guard(side === "heat" ? p.heat + dir * step : p.heat, side === "cool" ? p.cool + dir * step : p.cool, side, db, lo, hi);
          p.heat = g.heat;
          p.cool = g.cool;
          this._pendingNote = g.note;
        }, "");
      switch (action) {
        case "sug-apply":
          return this._call(DOMAIN, "apply_suggestion", { id: el.dataset.id }).then(() => this._toast("Schedule updated"));
        case "sug-dismiss":
          return this._call(DOMAIN, "dismiss_suggestion", { id: el.dataset.id });
        case "info": {
          // Read-only view: tap a block for its details (hover tooltips don't exist on phones).
          const bd = Number(el.dataset.d), bi = Number(el.dataset.i);
          const html = blockInfoHtml(this._hass, this._week(), bd, bi, this.stateObj.attributes.fan_modes);
          return this._togglePop(`.blk[data-d="${bd}"][data-i="${bi}"]`, html);
        }
        case "edit":
          this._dropPop();
          this._editing = true;
          this._sel = null; // start on the block that's active now
          this._copyScope = "block"; // copying a whole day is always an explicit choice
          this._msg = "";
          return this._render();
        case "lcolor": {
          // Colors belong to the label, so every block with this label changes together.
          const label = this._week()[this._sel.d][this._sel.i].label;
          return this._edit(() => {
            this._draftColors = { ...this._labelColors() };
            if (el.dataset.c) this._draftColors[label] = el.dataset.c;
            else delete this._draftColors[label];
          }, el.dataset.c ? `“${label}” color set` : `“${label}” back to its default color`);
        }
        case "cancel": {
          const hadChanges = !!this._draft;
          this._editing = false;
          this._draft = null;
          this._draftColors = null;
          this._sel = null;
          return this._toast(hadChanges ? "Changes discarded" : "");
        }
        case "select":
          this._sel = { d: Number(el.dataset.d), i: Number(el.dataset.i) };
          this._msg = "";
          return this._render();
        case "eh-up": adjust("heat", 1); break;
        case "eh-dn": adjust("heat", -1); break;
        case "ec-up": adjust("cool", 1); break;
        case "ec-dn": adjust("cool", -1); break;
        case "start":
          return this._edit((w) => { w[d][i].start = Number(el.value); });
        case "pmode":
          return this._edit((w) => { w[d][i].mode = el.value || null; });
        case "pfan":
          return this._edit((w) => { w[d][i].fan = el.value || null; });
        case "label": {
          const value = el.value.trim().slice(0, 40) || "Period";
          return this._edit((w) => { w[d][i].label = value; });
        }
        case "split":
          return this._edit((w) => {
            const day = w[d];
            const end = i + 1 < day.length ? day[i + 1].start : 1440;
            const mid = Math.round((day[i].start + (end - day[i].start) / 2) / STEP_MIN) * STEP_MIN;
            day.splice(i + 1, 0, { ...day[i], start: mid });
            this._sel = { d, i: i + 1 };
          }, "Split. Adjust the new block's start time and temperatures.");
        case "delete":
          return this._edit((w) => {
            const day = w[d];
            if (day.length < 2) return;
            if (i === 0) {
              day.splice(0, 1);
              day[0].start = 0;
              this._sel = { d, i: 0 };
            } else {
              day.splice(i, 1);
              this._sel = { d, i: i - 1 };
            }
          }, "Deleted. The neighbouring block now covers that time.");
        case "scope":
          this._copyScope = el.dataset.scope;
          return this._render();
        case "copy": {
          const to = el.dataset.to === "wk" ? [0, 1, 2, 3, 4] : el.dataset.to === "we" ? [5, 6] : [0, 1, 2, 3, 4, 5, 6];
          const target = el.textContent.trim().toLowerCase();
          if (this._copyScope === "day") {
            return this._edit((w) => {
              to.forEach((t) => { if (t !== d) w[t] = JSON.parse(JSON.stringify(w[d])); });
            }, `Copied all of ${DAYS[d]} to ${target}.`);
          }
          const week = this._week();
          const src = week[d][i];
          const end = i + 1 < week[d].length ? week[d][i + 1].start : 1440;
          return this._edit((w) => {
            to.forEach((t) => { if (t !== d) w[t] = insertBlock(w[t], src, end); });
          }, `Copied ${fmtMinutes(this._hass, src.start)}–${fmtMinutes(this._hass, end)} to ${target}.`);
        }
        case "save":
          return this._save();
        default:
          return undefined;
      }
      if (this._pendingNote) {
        this._toast(this._pendingNote);
        this._pendingNote = "";
      }
      return undefined;
    }

    _save() {
      const schedule = this._draft;
      if (!schedule) return;
      this._hass
        .callService(DOMAIN, "set_schedule", {
          entity_id: this._config.entity,
          schedule,
          ...(this._draftColors ? { label_colors: this._draftColors } : {}),
        })
        .then(() => {
          this._draft = null;
          this._draftColors = null;
          this._editing = false;
          this._sel = null;
          this._toast("Schedule saved");
        })
        .catch((err) => this._toast(err?.message || String(err)));
    }
  }

  /* ------------------------------------------------------------------ health card */

  const HEALTH_CSS = `
    svg text.ol { fill: var(--cs-muted); font-size: 10px; }
    .detail.slow { display: flex; gap: 8px; align-items: flex-start; --mdc-icon-size: 18px; }
    .detail.slow ha-icon { color: var(--cs-warn); flex: none; }
    .flt { display: grid; grid-template-columns: 20px minmax(0, 1fr) auto; gap: 10px; align-items: center; --mdc-icon-size: 20px; }
    .flt > ha-icon { color: var(--cs-muted); }
    .flt.due > ha-icon { color: var(--cs-warn); }
    .flt .fx { display: grid; gap: 6px; min-width: 0; font-size: var(--ha-font-size-m, 14px); }
    .flt .bar { height: 4px; border-radius: 2px; background: var(--cs-soft); overflow: hidden; }
    .flt .bar i { display: block; height: 100%; background: var(--bc); border-radius: 2px; }
    .head { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: start; gap: 12px; }
    /* Status strip, same shape as the thermostat card's. */
    .hs {
      --sc: var(--cs-ok); display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 12px;
      min-height: 52px; box-sizing: border-box; padding: 8px 14px; border-radius: 12px;
      background: color-mix(in srgb, var(--sc) 12%, var(--cs-bg));
    }
    .hs.bad { --sc: var(--cs-bad); }
    .hs.notice { --sc: var(--cs-warn); }
    .hs .lead { display: flex; align-items: center; gap: 6px; color: color-mix(in srgb, var(--sc) var(--cs-ink), var(--cs-text)); font-weight: var(--ha-font-weight-medium, 500); --mdc-icon-size: 20px; white-space: nowrap; }
    .hs .what { min-width: 0; font-size: var(--ha-font-size-m, 14px); overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
    .hs .lim { font-size: var(--ha-font-size-s, 12px); color: var(--cs-muted); white-space: nowrap; }
    /* On a narrow card the limit moves under the run summary, and the summary wraps instead of being cut off. */
    ha-card { container-type: inline-size; }
    @container (max-width: 440px) {
      .hs { grid-template-columns: auto minmax(0, 1fr); row-gap: 2px; }
      .hs .lim { grid-column: 2; }
      .hs .lim:empty { display: none; }
    }
    svg { width: 100%; height: auto; display: block; }
    svg text { fill: var(--cs-muted); font-size: 11px; font-family: inherit; }
    svg text.bl { fill: var(--cs-text); font-size: 10px; }
    svg .probe { cursor: crosshair; outline: none; }
    svg .probe:focus-visible { stroke: var(--cs-accent); stroke-width: 2; }
    /* The chart area keeps one height in every state (loading, empty, switching timeframes). */
    .chartbox { position: relative; min-height: 222px; }
    .chartbox svg { transition: opacity .2s; }
    .chartbox.loading svg { opacity: .35; }
    .chartbox .empty { height: 222px; display: grid; place-items: center; color: var(--cs-muted); font-size: var(--ha-font-size-s, 12px); }
    .chartbox .loadmsg {
      position: absolute; left: 50%; top: 45%; transform: translate(-50%, -50%); padding: 3px 10px; border-radius: 99px;
      background: var(--cs-bg); border: 1px solid var(--cs-line); color: var(--cs-muted); font-size: var(--ha-font-size-s, 12px);
    }
    .detail { font-size: var(--ha-font-size-m, 14px); }
    .rules { font-size: var(--ha-font-size-s, 12px); color: var(--cs-muted); border-top: 1px solid var(--cs-line); padding-top: 10px; display: grid; gap: 3px; }
  `;

  class ComfortHealthCard extends ComfortBase {
    constructor() {
      super();
      this.shadowRoot.addEventListener("keydown", (e) => {
        if (!e.target.classList?.contains("probe")) return;
        const dir = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
        if (!dir) return;
        e.preventDefault();
        this._probe(null, dir);
      });
    }

    getCardSize() {
      return 6;
    }

    static getConfigForm() {
      return configForm([
        appearanceSection("title"),
        section("chart", "Chart", "mdi:chart-line", [
          { name: "hours", selector: { number: { min: 1, max: 24, step: 1, mode: "slider", unit_of_measurement: "h" } } },
          toggle("hide_outdoor"),
        ]),
      ]);
    }

    set hass(hass) {
      super.hass = hass;
      this._maybeFetch();
    }

    setConfig(config) {
      const old = this._config;
      super.setConfig(config);
      if (old && ["entity", "hours", "hide_outdoor"].some((k) => old[k] !== config[k])) {
        // Changed in the card editor: load the new window now, not at the next refresh.
        if (old.entity !== config.entity) this._hist = null;
        this._hours = null;
        this._loading = !!this._hist;
        this._fetchedAt = 0;
        if (this._hass) this._maybeFetch();
      }
    }

    // Draw the chart at the card's real pixel width so its text stays a fixed size
    // instead of scaling up with the card.
    connectedCallback() {
      if (!this._resizeObserver) {
        this._resizeObserver = new ResizeObserver((entries) => {
          const width = Math.round(entries[0].contentRect.width);
          if (width && Math.abs(width - (this._width || 0)) > 2) {
            this._width = width;
            cancelAnimationFrame(this._resizeFrame);
            this._resizeFrame = requestAnimationFrame(() => this._render());
          }
        });
      }
      this._resizeObserver.observe(this);
    }

    disconnectedCallback() {
      super.disconnectedCallback();
      this._resizeObserver?.disconnect();
    }

    _maybeFetch() {
      const a = this.stateObj?.attributes;
      if (!a || !a.source_entity || this._fetching) return;
      const src = this._hass.states[a.source_entity];
      const changed = src && src.last_updated !== this._srcUpdated;
      const age = Date.now() - (this._fetchedAt || 0);
      if (!this._hist || age > 5 * 60e3 || (changed && age > 60e3)) {
        this._srcUpdated = src?.last_updated;
        this._fetch(a.source_entity);
      }
    }

    _outdoorId() {
      return this._config?.hide_outdoor ? null : this.stateObj?.attributes?.outdoor_entity || null;
    }

    // Every chart state sits in the same fixed-height box; while a new timeframe loads the
    // previous chart stays, dimmed, so the card never collapses and re-expands.
    _chartBox(inner) {
      const note = this._loading ? `<div class="loadmsg" role="status">Loading ${this._hoursNow()}h…</div>` : "";
      return `<div class="chartbox ${this._loading ? "loading" : ""}">${inner}${note}</div>`;
    }

    // Hours shown: the viewer's 3h/12h/24h pick, else the card's configured "hours".
    _hoursNow() {
      return this._hours || Math.max(1, Math.min(24, Number(this._config.hours) || 3));
    }

    _onAction(action, el, e) {
      if (action === "probe") return this._probe(e);
      if (action === "filter") {
        return this._call(DOMAIN, "filter_changed").then(() => this._toast("Filter count started over"));
      }
      if (action !== "hours") return;
      const hours = Number(el.dataset.h);
      if (hours === this._hoursNow()) return;
      this._hours = hours;
      this._loading = true; // keep the current chart, dimmed, until the new window arrives
      this._render();
      const src = this.stateObj?.attributes?.source_entity;
      if (src && !this._fetching) this._fetch(src);
    }

    // Outdoor temperature history, from a sensor's state or a weather entity's attribute.
    _outdoorPts(rows, start, isWeather) {
      const pts = [];
      let attrs = {};
      for (const r of rows || []) {
        if (r.a) attrs = r.a;
        const v = num(isWeather ? attrs.temperature : r.s);
        if (v !== null) pts.push({ t: Math.max(start, (r.lu ?? r.lc ?? 0) * 1000), v });
      }
      return pts;
    }

    // Tap the chart: what was happening at that moment. Tapping elsewhere on the chart moves
    // the marker; tapping outside it closes the details. Keyboard: Enter opens at the latest
    // point, arrow keys step through time.
    _probe(e, stepDir = 0) {
      const g = this._geo, h = this._hist;
      if (!g || !h) return undefined;
      let t;
      if (stepDir) {
        t = (this._probeT ?? h.end) + stepDir * ((h.end - h.start) / 48);
      } else if (e && (e.clientX || e.detail)) {
        const svg = this.shadowRoot.querySelector(".chartbox svg");
        const r = svg && svg.getBoundingClientRect();
        if (!r || !r.width) return undefined;
        const px = ((e.clientX - r.left) * g.W) / r.width;
        t = h.start + ((px - g.x0) / (g.x1 - g.x0)) * (h.end - h.start);
      } else {
        t = this._probeT ?? h.end; // keyboard "click"
      }
      this._probeT = Math.max(h.start, Math.min(h.end, t));
      const html = this._probeHtml(this._probeT);
      if (this._pop && this._pop.key === ".pa") {
        this._pop.html = html;
        this._render();
      } else {
        this._togglePop(".pa", html);
      }
      // The redraw replaced the chart; keep keyboard users on it so arrows keep working.
      const keyboard = stepDir || !(e && (e.clientX || e.detail));
      if (keyboard) this._focusAfterPaint(".probe");
      return undefined;
    }

    _probeHtml(t) {
      const h = this._hist;
      const pts = h.pts;
      let k = 0;
      pts.forEach((p, n) => { if (p.t <= t) k = n; });
      const p = pts[k] || {};
      // Last known indoor temperature at or before t.
      let temp = null;
      for (let n = k; n >= 0 && temp === null; n--) temp = pts[n].temp;
      const heat = p.s === "heat_cool" ? p.low : p.s === "heat" ? p.target : null;
      const cooling = this.stateObj.attributes.cooling_enabled !== false;
      const cool = cooling ? (p.s === "heat_cool" ? p.high : p.s === "cool" ? p.target : null) : null;
      // How far into a run this moment was.
      const action = !cooling && p.action === "cooling" ? "idle" : p.action;
      let doing = { heating: "Heating", cooling: "Cooling", fan: "Fan only", idle: "Idle", off: "Off" }[action] || (action ? String(action) : "–");
      if (action === "heating" || action === "cooling") {
        let n = k;
        while (n > 0 && pts[n - 1].action === action) n--;
        const mins = (t - pts[n].t) / 60e3;
        if (n > 0 || pts[n].t > h.start) doing += ` · ${fmtDur(mins / 60)} in`;
      }
      const block = this._scheduleBand(t, t + 60e3)[0];
      const unit = this._hass.config.unit_system.temperature;
      let outside = null;
      (h.outdoor || []).forEach((o) => { if (o.t <= t) outside = o.v; });
      return `<div class="ph">${esc(fmtWhen(this._hass, new Date(t).toISOString()))}</div>
        <dl style="margin-top:8px">
          <dt>Indoor</dt><dd>${temp === null ? "–" : `${fmtTemp(temp)}${esc(unit)}`}</dd>
          ${outside !== null ? `<dt>Outdoor</dt><dd>${fmtTemp(outside)}${esc(unit)}</dd>` : ""}
          ${heat !== null ? `<dt>Heat to</dt><dd style="color:var(--cs-heat-ink)">${fmtTemp(heat)}°</dd>` : ""}
          ${cool !== null ? `<dt>Cool to</dt><dd style="color:var(--cs-cool-ink)">${fmtTemp(cool)}°</dd>` : ""}
          <dt>Thermostat</dt><dd>${esc(MODE_LABELS[p.s] || p.s || "–")}</dd>
          <dt>Doing</dt><dd>${esc(doing)}</dd>
          ${block ? `<dt>Schedule</dt><dd><span class="sw" style="display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px;background:${labelColor(block.label)}"></span>${esc(block.label)}</dd>` : ""}
        </dl>`;
    }

    // Which schedule block was in effect across the chart's time window, as segments.
    _scheduleBand(start, end) {
      const week = this.stateObj.attributes.schedule;
      if (!Array.isArray(week) || week.length !== 7) return [];
      const segs = [];
      let t = start;
      while (t < end) {
        const { day, minute } = homeClock(this._hass, t);
        const periods = week[day];
        let k = 0;
        periods.forEach((p, n) => { if (p.start <= minute) k = n; });
        const p = periods[k];
        // The block ends at the next one's start, or at midnight (1440), on the clock at home.
        const nextMin = k + 1 < periods.length ? periods[k + 1].start : 1440;
        const next = t - (t % 60e3) + (nextMin - minute) * 60e3;
        const segEnd = Math.min(end, Math.max(next, t + 60e3));
        const last = segs[segs.length - 1];
        if (last && last.label === p.label && last.heat === p.heat && last.cool === p.cool) last.t1 = segEnd;
        else segs.push({ t0: t, t1: segEnd, label: p.label, heat: p.heat, cool: p.cool });
        t = segEnd;
      }
      return segs;
    }

    async _fetch(source) {
      this._fetching = true;
      const hours = this._hoursNow();
      const end = new Date();
      const start = new Date(end.getTime() - hours * 3600e3);
      try {
        const res = await this._hass.callWS({
          type: "history/history_during_period",
          start_time: start.toISOString(),
          end_time: end.toISOString(),
          entity_ids: [source, ...(this._outdoorId() ? [this._outdoorId()] : [])],
          minimal_response: false,
          no_attributes: false,
          significant_changes_only: false,
        });
        const rows = (res && res[source]) || [];
        let attrs = {};
        const pts = [];
        for (const r of rows) {
          if (r.a) attrs = r.a;
          const t = Math.max(start.getTime(), (r.lu ?? r.lc ?? 0) * 1000);
          pts.push({
            t,
            s: r.s,
            temp: num(attrs.current_temperature),
            action: attrs.hvac_action,
            low: num(attrs.target_temp_low),
            high: num(attrs.target_temp_high),
            target: num(attrs.temperature),
          });
        }
        const out = this._outdoorId();
        const outdoor = out ? this._outdoorPts(res && res[out], start.getTime(), out.startsWith("weather.")) : [];
        this._hist = { start: start.getTime(), end: end.getTime(), pts, outdoor };
        this._histErr = "";
      } catch (err) {
        this._histErr = err?.message || String(err);
      }
      this._fetching = false;
      const wanted = this.stateObj?.attributes?.source_entity;
      if (wanted && (hours !== this._hoursNow() || wanted !== source)) {
        // Another timeframe (or entity) was picked while this one loaded: fetch that instead.
        this._fetch(wanted);
        return;
      }
      this._loading = false;
      this._fetchedAt = Date.now();
      this._render();
    }

    _chart() {
      const h = this._hist;
      if (!h || !h.pts.length) return this._chartBox(`<div class="empty">${esc(this._histErr || "Loading history…")}</div>`);
      const hass = this._hass;
      // Card content width (host width minus ha-card's 16px padding on each side).
      const hostWidth = Math.round(this.getBoundingClientRect().width) || this._width || 560;
      // B = time labels at the bottom; BAND = the schedule band just above them.
      // Outdoor temperature gets its own scale, labeled on the right.
      const outdoor = (h.outdoor || []).filter((p) => p.v !== null);
      const W = Math.max(260, hostWidth - 32), H = 222, L = 36, R = outdoor.length ? 34 : 12, T = 12, BAND = 16, B = 26 + BAND + 6;
      const pts = h.pts;
      const heatAt = (p) => (p.s === "heat_cool" ? p.low : p.s === "heat" ? p.target : null);
      const cooling = this.stateObj.attributes.cooling_enabled !== false;
      const coolAt = (p) => cooling ? (p.s === "heat_cool" ? p.high : p.s === "cool" ? p.target : null) : null;
      const values = [];
      pts.forEach((p) => { [p.temp, heatAt(p), coolAt(p)].forEach((v) => v !== null && values.push(v)); });
      if (!values.length) return this._chartBox(`<div class="empty">No temperature history yet.</div>`);
      let yMin = Math.floor(Math.min(...values) - 0.5);
      let yMax = Math.ceil(Math.max(...values) + 0.5);
      if (yMax - yMin < 4) { yMin -= 1; yMax += 1; }
      const yStep = yMax - yMin > 12 ? 2 : 1;
      const x = (t) => L + ((t - h.start) / (h.end - h.start)) * (W - L - R);
      const y = (v) => T + ((yMax - v) / (yMax - yMin)) * (H - T - B);
      let s = "";

      // Runs (heating/cooling) as shaded bands.
      for (let k = 0; k < pts.length; k++) {
        const p = pts[k];
        if (p.action !== "heating" && (p.action !== "cooling" || !cooling)) continue;
        const t1 = k + 1 < pts.length ? pts[k + 1].t : h.end;
        const color = p.action === "heating" ? "var(--cs-heat)" : "var(--cs-cool)";
        s += `<rect x="${x(p.t)}" y="${T}" width="${Math.max(0, x(t1) - x(p.t))}" height="${H - T - B}" fill="${color}" fill-opacity=".12"/>`;
      }
      for (let v = yMin; v <= yMax; v += yStep) {
        s += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--cs-line)"/>`;
        s += `<text x="${L - 6}" y="${y(v) + 3}" text-anchor="end">${v}°</text>`;
      }
      const spanH = (h.end - h.start) / 3600e3;
      // Keep time labels at least ~70px apart: 30 min, 1 h, 2 h, … as space allows.
      let tickMin = 30;
      while (((W - L - R) / (spanH * 60)) * tickMin < 70 && tickMin < 24 * 60) tickMin *= 2;
      const tickMs = tickMin * 60e3;
      const first = Math.ceil(h.start / tickMs) * tickMs;
      for (let t = first; t <= h.end; t += tickMs) {
        const label = new Date(t).toLocaleTimeString(hass?.locale?.language || undefined, { hour: "numeric", minute: "2-digit", hour12: hour12(hass), ...zone(hass) });
        s += `<text x="${x(t)}" y="${H - 8}" text-anchor="middle">${esc(label)}</text>`;
      }
      // Schedule band: which block was active, in the schedule card's colors.
      const bandY = H - B + 4;
      this._scheduleBand(h.start, h.end).forEach((seg) => {
        const x0 = x(seg.t0), x1 = x(seg.t1), w = Math.max(0, x1 - x0 - 2);
        const color = labelColor(seg.label);
        s += `<rect x="${(x0 + 1).toFixed(1)}" y="${bandY}" width="${w.toFixed(1)}" height="${BAND}" rx="3" style="fill:color-mix(in srgb, ${color} 45%, var(--cs-bg))"><title>${esc(seg.label)}</title></rect>`;
        if (w > seg.label.length * 5.6 + 12) s += `<text class="bl" x="${(x0 + 7).toFixed(1)}" y="${bandY + 11.5}">${esc(seg.label)}</text>`;
      });
      // Set points as dashed step lines.
      const stepPath = (fn) => {
        let d = "", prev = null;
        pts.forEach((p, k) => {
          const v = fn(p);
          const t1 = k + 1 < pts.length ? pts[k + 1].t : h.end;
          if (v === null) { prev = null; return; }
          d += `${prev === null ? "M" : "L"}${x(p.t).toFixed(1)},${y(v).toFixed(1)}L${x(t1).toFixed(1)},${y(v).toFixed(1)}`;
          prev = v;
        });
        return d;
      };
      const hp = stepPath(heatAt), cp = stepPath(coolAt);
      if (hp) s += `<path d="${hp}" fill="none" stroke="var(--cs-heat)" stroke-width="1.5" stroke-dasharray="4 4"/>`;
      if (cp) s += `<path d="${cp}" fill="none" stroke="var(--cs-cool)" stroke-width="1.5" stroke-dasharray="4 4"/>`;
      // Outdoor: a dotted line on its own scale (it's usually far from the indoor range).
      if (outdoor.length) {
        const ov = outdoor.map((p) => p.v);
        let oMin = Math.floor(Math.min(...ov) - 1), oMax = Math.ceil(Math.max(...ov) + 1);
        if (oMax - oMin < 6) { const mid = (oMax + oMin) / 2; oMin = Math.floor(mid - 3); oMax = Math.ceil(mid + 3); }
        const oy = (v) => T + ((oMax - v) / (oMax - oMin)) * (H - T - B);
        let d = outdoor.map((p, k) => `${k ? "L" : "M"}${x(p.t).toFixed(1)},${oy(p.v).toFixed(1)}`).join("");
        d += `L${x(h.end).toFixed(1)},${oy(outdoor[outdoor.length - 1].v).toFixed(1)}`;
        s += `<path class="out" d="${d}" fill="none" stroke="var(--cs-muted)" stroke-width="1.5" stroke-dasharray="1 3" stroke-linecap="round"/>`;
        s += `<text class="ol" x="${W - R + 6}" y="${T + 8}">${oMax}°</text><text class="ol" x="${W - R + 6}" y="${H - B - 2}">${oMin}°</text>`;
        s += `<text class="ol" x="${W - R + 6}" y="${(T + H - B) / 2 + 3}">out</text>`;
      }
      // Indoor temperature.
      const tp = pts.filter((p) => p.temp !== null);
      if (tp.length) {
        let d = tp.map((p, k) => `${k ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.temp).toFixed(1)}`).join("");
        const last = tp[tp.length - 1];
        d += `L${x(h.end).toFixed(1)},${y(last.temp).toFixed(1)}`;
        s += `<path d="${d}" fill="none" stroke="var(--cs-text)" stroke-width="2" stroke-linejoin="round"/>`;
        s += `<circle cx="${x(h.end)}" cy="${y(last.temp)}" r="4" fill="var(--cs-text)"/>`;
      }
      // Tapped moment: a marker line (and a dot on the temperature line) with its details popup.
      this._geo = { W, x0: L, x1: W - R };
      const probing = this._pop && this._pop.key === ".pa" && this._probeT !== undefined;
      if (probing) {
        const t = Math.max(h.start, Math.min(h.end, this._probeT));
        this._pop.html = this._probeHtml(t); // new history may have arrived since the tap
        const px = x(t).toFixed(1);
        s += `<line x1="${px}" x2="${px}" y1="${T}" y2="${H - B + 4 + BAND}" stroke="var(--cs-text)" stroke-width="1" stroke-opacity=".55"/>`;
        let temp = null;
        tp.forEach((p) => { if (p.t <= t) temp = p.temp; });
        if (temp !== null) s += `<circle cx="${px}" cy="${y(temp).toFixed(1)}" r="4.5" fill="var(--cs-bg)" stroke="var(--cs-text)" stroke-width="2"/>`;
        s += `<rect class="pa" x="${px}" y="${T}" width="1" height="1" fill="none"/>`;
      }
      s += `<rect class="probe" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B + 4 + BAND}" fill="transparent"
        data-action="probe" data-pop="1" tabindex="0" role="button" aria-label="Chart details. Enter shows the latest point; arrow keys move through time."/>`;
      return this._chartBox(`<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Indoor temperature and set points">${s}</svg>`);
    }

    _render() {
      if (this._missing()) return;
      const a = this.stateObj.attributes;
      if (!a.health && this._offline(HEALTH_CSS)) return;
      const unit = this._hass.config.unit_system.temperature;
      const health = a.health || {};
      const rules = a.health_rules || {};
      const cooling = a.cooling_enabled !== false;
      const running = health.action === "heating" || (cooling && health.action === "cooling");
      const verb = health.action === "heating" ? "Heating" : "Cooling";
      const progress = health.progress === null || health.progress === undefined ? null : Number(health.progress);
      const hours = this._hoursNow();

      // Status strip: Normal / Problem, what's happening now, and the limit it's measured against.
      // The run comes first; with nothing wrong with it, an open slowdown or filter warning
      // still keeps the strip from saying all is well.
      const slowKinds = Object.keys((a.learning || {}).slowdown || {});
      const notice = health.problem ? "" : slowKinds.length ? "Getting slower" : (a.filter || {}).due ? "Filter due" : "";
      const lead = health.problem
        ? `<ha-icon icon="mdi:alert-circle-outline"></ha-icon>${health.problem === "wrong_way" ? "Wrong way" : "No progress"}`
        : notice
          ? `<ha-icon icon="mdi:alert-outline"></ha-icon>${notice}`
          : `<ha-icon icon="mdi:check-circle-outline"></ha-icon>Normal`;
      let what;
      if (running) {
        const sign = progress !== null ? `${progress >= 0 ? "+" : ""}${progress}${unit} so far` : "";
        what = `${verb} for ${fmtDur((health.run_minutes || 0) / 60)}${sign ? ` · ${sign}` : ""}`;
      } else if (a.last_run && a.last_run.end) {
        what = `Idle · last ${a.last_run.kind === "cool" ? "cooled" : "heated"} ${fmtWhen(this._hass, a.last_run.end)}`;
      } else {
        what = "Idle";
      }
      const limit = running ? `${health.action === "heating" ? rules.heat_minutes ?? 45 : rules.cool_minutes ?? 60} min limit` : "";
      const choices = [...new Set([3, 12, 24, hours])].sort((m, n) => m - n);

      // Runs getting slower than they used to, in similar weather.
      const learning = a.learning || {};
      const slow = Object.values(learning.slowdown || {}).map((w) => {
        const name = w.kind === "heat" ? "Heating" : "Cooling";
        return `${name} is ${w.drop}% slower than it was in similar weather: ${fmtTemp(w.recent_rate)}${unit} an hour over the last two weeks, against ${fmtTemp(w.baseline_rate)}${unit} before. A dirty filter or a system that needs service are the usual causes.`;
      });
      // Filter: hours since it was changed, against the reminder.
      const flt = a.filter || {};
      let filterRow = "";
      if (flt.limit > 0) {
        const pct = Math.max(0, Math.min(100, (flt.hours / flt.limit) * 100));
        const since = flt.since ? new Date(`${flt.since}T00:00`).toLocaleDateString(this._hass?.locale?.language || undefined, { month: "short", day: "numeric" }) : "";
        filterRow = `<div class="flt ${flt.due ? "due" : ""}">
          <ha-icon icon="mdi:air-filter"></ha-icon>
          <div class="fx"><div>${flt.due ? "Time to check the filter" : "Filter"} <span class="muted">· ${fmtDur(flt.hours)} of ${fmtDur(flt.limit)}${since ? ` since ${esc(since)}` : ""}</span></div>
            <div class="bar" style="--bc:${flt.due ? "var(--cs-warn)" : "var(--cs-accent)"}"><i style="width:${pct.toFixed(1)}%"></i></div></div>
          <button class="btn hbtn" data-action="filter">Changed it</button></div>`;
      }
      const rates = [
        learning.heat_rate ? `heating moves the room about ${fmtTemp(learning.heat_rate)}${unit} an hour` : "",
        cooling && learning.cool_rate ? `cooling about ${fmtTemp(learning.cool_rate)}${unit} an hour` : "",
      ].filter(Boolean);

      this._paint(
        HEALTH_CSS,
        `<ha-card>
          <div class="head">
            ${this._heading(this._config.title || "HVAC health", this._config.hide_subtitle ? "" : "Indoor temperature, set points and runs") || "<div></div>"}
            <div class="range" role="group" aria-label="Hours shown">${choices
              .map((c) => `<button class="${c === hours ? "on" : ""}" data-action="hours" data-h="${c}" aria-pressed="${c === hours}">${c}h</button>`)
              .join("")}</div>
          </div>
          <div class="hs ${health.problem ? "bad" : notice ? "notice" : ""}" role="status">
            <div class="lead">${lead}</div>
            <div class="what">${esc(what)}</div>
            <div class="lim">${esc(limit)}</div>
          </div>
          ${this._chart()}
          ${health.problem && (health.advice || health.message) ? `<div class="detail">${esc(health.advice || health.message)}</div>` : ""}
          ${slow.map((w) => `<div class="detail slow"><ha-icon icon="mdi:speedometer-slow"></ha-icon><span>${esc(w)}</span></div>`).join("")}
          ${filterRow}
          <div class="rules">
            <div>${cooling ? `Warns after ${rules.heat_minutes ?? 45} min heating or ${rules.cool_minutes ?? 60} min cooling` : `Warns after ${rules.heat_minutes ?? 45} min heating`} with under ${rules.min_progress ?? 1}${esc(unit)} of progress,
              or ${rules.wrong_way ?? 0.8}${esc(unit)} the wrong way for over 10 min.</div>
            <div>${rules.notify_entity ? `Warnings go to ${esc(rules.notify_entity)} and Settings › Repairs.` : "Warnings appear under Settings › Repairs."}</div>
            ${rates.length ? `<div>Lately ${rates.join(", ")}.</div>` : ""}
          </div>
        </ha-card>`
      );
    }
  }

  /* ------------------------------------------------------------------ runtime card */

  // Hours -> "45m", "1h 12m", "3h"; from a day up, whole hours ("812h").
  function fmtDur(hours) {
    const mins = Math.round((Number(hours) || 0) * 60);
    if (mins < 60) return `${mins}m`;
    if (mins >= 24 * 60) return `${Math.round(mins / 60)}h`;
    const h = Math.floor(mins / 60), m = mins % 60;
    return m ? `${h}h ${m}m` : `${h}h`;
  }

  const RUNTIME_CSS = `
    .extra { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: var(--ha-font-size-s, 12px); color: var(--cs-muted); margin-top: -4px; padding: 0 12px; }
    .extra > span { display: inline-flex; align-items: center; gap: 4px; }
    .extra .chg { --mdc-icon-size: 12px; }
    .head { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: start; gap: 12px; }
    .legend { display: flex; gap: 12px; font-size: var(--ha-font-size-s, 12px); color: var(--cs-muted); }
    .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 5px; vertical-align: -1px; }
    .sums { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .sum {
      border-radius: 14px; padding: 12px 14px; display: grid; gap: 8px;
      background: color-mix(in srgb, var(--tc) 10%, var(--cs-bg)); border: 1px solid color-mix(in srgb, var(--tc) 32%, var(--cs-bg));
    }
    .sum .lab {
      display: flex; align-items: center; gap: 4px; --mdc-icon-size: 16px; color: color-mix(in srgb, var(--tc) var(--cs-ink), var(--cs-text));
      font-size: 11px; font-weight: var(--ha-font-weight-medium, 500); letter-spacing: .09em; text-transform: uppercase;
    }
    .sum .big { font-size: 28px; line-height: 1; font-variant-numeric: tabular-nums; white-space: nowrap; }
    /* Phones: keep "1h 55m today" on one line so both tiles stay the same height. */
    ha-card { container-type: inline-size; }
    @container (max-width: 420px) { .sum .big { font-size: 22px; } .sum .big small { margin-left: 4px; } }
    .sum .big small { font-size: 12px; color: var(--cs-muted); margin-left: 6px; }
    .sum dl { margin: 0; display: grid; grid-template-columns: auto auto; gap: 3px 12px; font-size: var(--ha-font-size-s, 12px); }
    .sum dt { color: var(--cs-muted); }
    .sum dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
    svg { width: 100%; height: auto; display: block; }
    svg text { fill: var(--cs-muted); font-size: 11px; font-family: inherit; }
    .hit { outline: none; }
    /* Period-over-period row. Arrows are neutral: more heating isn't "bad" when it's colder out. */
    .cmp {
      display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 4px 16px;
      padding: 8px 14px; border-radius: 10px; background: var(--cs-soft); font-size: var(--ha-font-size-s, 12px);
    }
    .cmp .cl { min-width: 0; line-height: 1.35; }
    .cmp .cl div { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .cmp .it { display: inline-flex; align-items: center; gap: 6px; font-variant-numeric: tabular-nums; }
    .cmp .it i { width: 8px; height: 8px; border-radius: 2px; }
  `;

  const RUNTIME_KINDS = {
    heat: { label: "Heating", icon: "mdi:fire", color: "var(--cs-heat)" },
    cool: { label: "Cooling", icon: "mdi:snowflake", color: "var(--cs-cool)" },
  };

  // Which of heating and cooling to show: "heat", "cool", or anything else for both.
  const runtimeKinds = (show) => (show === "heat" ? ["heat"] : show === "cool" ? ["cool"] : ["heat", "cool"]);

  // "↑12%" / "↓5%" / "same" / "new" for a stretch against the one before. Neutral colors:
  // more heating isn't "bad" when it's colder out.
  function runtimeChange(cur, prev) {
    if (!prev && !cur) return `<span class="chg">no change</span>`;
    if (!prev) return `<span class="chg up">new</span>`;
    const pct = Math.round(((cur - prev) / prev) * 100);
    if (pct === 0) return `<span class="chg">same</span>`;
    return `<span class="chg ${pct > 0 ? "up" : "down"}"><ha-icon icon="mdi:arrow-${pct > 0 ? "up" : "down"}"></ha-icon>${Math.abs(pct)}%</span>`;
  }

  class ComfortRuntimeCard extends ComfortBase {
    getCardSize() {
      const c = this._config || {};
      let size = 2;
      if (c.totals !== "hidden") size += c.totals === "compact" ? 2 : 3;
      if (!c.hide_compare) size += 1;
      if (!c.hide_chart) size += 4;
      return size;
    }

    static getConfigForm() {
      const count = (name, min, max) => ({ name, selector: { number: { min, max, step: 1, mode: "slider" } } });
      return configForm([
        appearanceSection("title"),
        section("content", "What to show", "mdi:eye-outline", [
          grid([
            { name: "show", selector: choose([["both", "Heating and cooling"], ["heat", "Heating only"], ["cool", "Cooling only"]]) },
            { name: "totals", selector: choose([["full", "Full"], ["compact", "Compact"], ["hidden", "Hidden"]]) },
          ]),
          grid([toggle("hide_compare"), toggle("hide_chart")]),
          toggle("hide_cost"),
        ]),
        section("chart", "Chart", "mdi:chart-bar", [
          grid([
            { name: "view", selector: choose([["day", "Days"], ["week", "Weeks"], ["month", "Months"]]) },
            { name: "chart_style", selector: choose([["grouped", "Side by side"], ["stacked", "Stacked"]]) },
          ]),
          grid([toggle("hide_view_switch"), toggle("hide_legend")]),
          count("days", 7, 31),
          count("weeks", 4, 12),
          count("months", 3, 12),
        ]),
      ]);
    }

    // Day / Week / Month: the viewer's pick, else the card's "view" option.
    _viewNow() {
      const v = this._view || this._config.view;
      return v === "week" || v === "month" ? v : "day";
    }

    // Which of heating and cooling this card shows ("show" option).
    _kinds() {
      return this.stateObj?.attributes?.cooling_enabled === false ? ["heat"] : runtimeKinds(this._config.show);
    }

    // How many bars a view shows: the card option, kept to what the thermostat keeps.
    _count(name, min, max, fallback) {
      return Math.max(min, Math.min(max, Math.round(Number(this._config[name])) || fallback));
    }

    // Bars for the current view, oldest first: { label, title, heat, cool }.
    _items(rt) {
      const lang = this._hass?.locale?.language || undefined;
      const view = this._viewNow();
      if (view === "week") {
        return (rt.weekly || []).slice(-this._count("weeks", 4, 12, 12)).map((w, k, all) => {
          const d = new Date(`${w.start}T12:00:00`);
          const current = k === all.length - 1;
          return {
            label: current ? "This week" : d.toLocaleDateString(lang, { month: "numeric", day: "numeric" }),
            title: `Week of ${d.toLocaleDateString(lang, { month: "short", day: "numeric" })}${current ? " (so far)" : ""}`,
            heat: w.heat, cool: w.cool, current,
          };
        });
      }
      if (view === "month") {
        return (rt.monthly || []).slice(-this._count("months", 3, 12, 12)).map((m, k, all) => {
          const d = new Date(`${m.start}T12:00:00`);
          const current = k === all.length - 1;
          return {
            label: d.toLocaleDateString(lang, { month: "short" }),
            title: `${d.toLocaleDateString(lang, { month: "long", year: "numeric" })}${current ? " (so far)" : ""}`,
            heat: m.heat, cool: m.cool, current,
          };
        });
      }
      const daily = (rt.daily || []).slice(-this._count("days", 7, 31, 14));
      return daily.map((d, k) => {
        const date = new Date(`${d.date}T12:00:00`);
        const current = k === daily.length - 1;
        return {
          label: current ? "Today" : daily.length <= 10
            ? date.toLocaleDateString(lang, { weekday: "short" })
            : date.toLocaleDateString(lang, { month: "numeric", day: "numeric" }),
          title: date.toLocaleDateString(lang, { weekday: "long", month: "short", day: "numeric" }),
          heat: d.heat, cool: d.cool, current,
        };
      });
    }

    _onAction(action, el) {
      if (action === "view") {
        this._view = el.dataset.v;
        this._dropPop();
        return this._render();
      }
      if (action !== "bar" || !this._itemsNow) return undefined;
      const k = Number(el.dataset.k);
      const d = this._itemsNow[k];
      if (!d) return undefined;
      const kinds = this._kinds();
      const dot = (c) => `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px;background:${c}"></span>`;
      const rows = kinds.map((kind) => `<dt>${dot(RUNTIME_KINDS[kind].color)}${RUNTIME_KINDS[kind].label}</dt><dd>${fmtDur(d[kind])}</dd>`).join("");
      const html = `<div class="ph">${esc(d.title)}</div>
        <dl style="margin-top:8px">${rows}${kinds.length > 1 ? `<dt>Total</dt><dd>${fmtDur(d.heat + d.cool)}</dd>` : ""}</dl>`;
      return this._togglePop(`.hit[data-k="${k}"]`, html);
    }

    // Like the health card: draw the chart at the card's real width so text stays 11px.
    connectedCallback() {
      if (!this._resizeObserver) {
        this._resizeObserver = new ResizeObserver((entries) => {
          const width = Math.round(entries[0].contentRect.width);
          if (width && Math.abs(width - (this._width || 0)) > 2) {
            this._width = width;
            cancelAnimationFrame(this._resizeFrame);
            this._resizeFrame = requestAnimationFrame(() => this._render());
          }
        });
      }
      this._resizeObserver.observe(this);
    }

    disconnectedCallback() {
      super.disconnectedCallback();
      this._resizeObserver?.disconnect();
    }

    _sum(kind, data, compact) {
      const s = data || {};
      const k = RUNTIME_KINDS[kind];
      return `<div class="sum ${kind}">
        <div class="lab"><ha-icon icon="${k.icon}"></ha-icon>${k.label}</div>
        <div class="big">${fmtDur(s.today)}<small>today</small></div>
        ${compact ? "" : `<dl>
          <dt>Yesterday</dt><dd>${fmtDur(s.yesterday)}</dd>
          <dt>Last 7 days</dt><dd>${fmtDur(s.last_7_days)}</dd>
          <dt>This month</dt><dd>${fmtDur(s.this_month)}</dd>
          <dt>This year</dt><dd>${fmtDur(s.this_year)}</dd>
        </dl>`}
      </div>`;
    }

    // "This week so far: heating 18h 45m ▲12% · cooling 6h 30m ▼5% vs last week" — compared up
    // to the same point, so a half-finished week isn't measured against a whole one.
    _compare(rt) {
      const view = this._viewNow();
      const c = (rt.compare || {})[view];
      if (!c) return "";
      const kinds = this._kinds();
      const lead = { day: "Last 7 days", week: "This week so far", month: "This month so far" }[view];
      // Until tracking covers the earlier stretch there's nothing to compare with: say so once
      // and show just the totals. (Older integrations don't send "complete": full history.)
      const complete = c.complete !== false;
      const vs = complete
        ? { day: "vs the 7 days before", week: "vs last week", month: "vs last month" }[view]
        : "no earlier data yet";
      const change = (cur, prev) => (complete ? runtimeChange(cur, prev) : "");
      const prev = `Before: ${kinds.map((kind) => `${RUNTIME_KINDS[kind].label.toLowerCase()} ${fmtDur(c[`prev_${kind}`])}`).join(", ")}`;
      const values = kinds
        .map((kind) => `<span class="it"><i style="background:${RUNTIME_KINDS[kind].color}"></i>${fmtDur(c[kind])} ${change(c[kind], c[`prev_${kind}`])}</span>`)
        .join("");
      return `<div class="cmp" style="grid-template-columns:minmax(0,1fr) repeat(${kinds.length},auto)" title="${esc(prev)}">
        <div class="cl"><div>${lead}</div><div class="muted">${vs}</div></div>${values}</div>${this._extra(c, complete)}`;
    }

    // Under the comparison: the cost so far, and the change per degree-day (the same
    // comparison with the weather taken out), when there's an outdoor sensor.
    _extra(c, complete) {
      const a = this.stateObj.attributes;
      const kinds = this._kinds();
      const parts = [];
      const costs = a.costs;
      if (costs && !this._config.hide_cost) {
        const total = kinds.reduce((sum, kind) => sum + (Number(c[kind]) || 0) * (Number(costs[kind]) || 0), 0);
        let money;
        try {
          money = new Intl.NumberFormat(this._hass?.locale?.language || undefined, { style: "currency", currency: costs.currency || "USD" }).format(total);
        } catch (_err) {
          money = total.toFixed(2);
        }
        parts.push(`<span title="From the cost per hour in the integration's options">Cost ≈ ${esc(money)}</span>`);
      }
      const dd = { heat: "hdd", cool: "cdd" };
      if (complete) {
        const adj = kinds
          .filter((kind) => c[dd[kind]] > 0.5 && c[`prev_${dd[kind]}`] > 0.5 && c[`prev_${kind}`] > 0)
          .map((kind) => `${RUNTIME_KINDS[kind].label.toLowerCase()} ${runtimeChange(c[kind] / c[dd[kind]], c[`prev_${kind}`] / c[`prev_${dd[kind]}`])}`);
        if (adj.length) parts.push(`<span title="Runtime per degree-day: the change once colder or warmer weather is taken out">Weather-adjusted: ${adj.join(" · ")}</span>`);
      }
      return parts.length ? `<div class="extra">${parts.join("")}</div>` : "";
    }

    _chart(items) {
      const kinds = this._kinds();
      const stacked = this._config.chart_style === "stacked" && kinds.length > 1;
      const hostWidth = Math.round(this.getBoundingClientRect().width) || this._width || 560;
      const max = Math.max(0, ...items.map((d) => (stacked ? d.heat + d.cool : Math.max(...kinds.map((kind) => d[kind])))));
      // Pick a tick step that gives at most ~4 gridlines.
      const step = [0.25, 0.5, 1, 2, 3, 4, 6, 8, 12, 24, 48, 72, 96, 120, 168, 240, 360, 480, 720].find((s) => max / s <= 4) || 1440;
      const top = Math.max(step, Math.ceil(max / step) * step);
      // Left margin wide enough for the longest axis label ("1h 30m", "240h").
      const longest = Math.max(1, ...Array.from({ length: Math.round(top / step) + 1 }, (_, n) => fmtDur(n * step).length));
      const W = Math.max(260, hostWidth - 32), H = 170, L = Math.max(30, Math.ceil(longest * 6.2) + 10), R = 6, T = 8, B = 22;
      const y = (v) => T + (1 - v / top) * (H - T - B);
      const slot = (W - L - R) / Math.max(1, items.length);
      // One bar per slot (stacked, or a single kind) is wider than each of a side-by-side pair.
      const single = stacked || kinds.length === 1;
      const barW = single ? Math.max(4, Math.min(22, slot * 0.5)) : Math.max(3, Math.min(14, slot * 0.34));
      let s = "";
      for (let v = 0; v <= top + 1e-9; v += step) {
        s += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--cs-line)"/>`;
        s += `<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v === 0 ? "0" : fmtDur(v)}</text>`;
      }
      // Label every bar when there's room, otherwise every few (always the latest).
      const every = Math.ceil(46 / slot);
      items.forEach((d, k) => {
        const cx = L + slot * (k + 0.5);
        const rect = (x, from, to, color) => {
          const h = Math.max(0, y(from) - y(to));
          return h > 0.2
            ? `<rect x="${x.toFixed(1)}" y="${y(to).toFixed(1)}" width="${barW}" height="${h.toFixed(1)}" rx="2" fill="${color}" fill-opacity="${d.current ? 1 : 0.8}"/>`
            : "";
        };
        const open = this._pop && this._pop.key === `.hit[data-k="${k}"]`;
        if (open) s += `<rect x="${(cx - slot / 2 + 1).toFixed(1)}" y="${T}" width="${(slot - 2).toFixed(1)}" height="${H - T - B}" rx="4" fill="var(--cs-text)" fill-opacity=".07"/>`;
        if (stacked) {
          // Heating at the bottom, cooling on top; the bar's height is the total.
          s += rect(cx - barW / 2, 0, d.heat, RUNTIME_KINDS.heat.color);
          s += rect(cx - barW / 2, d.heat, d.heat + d.cool, RUNTIME_KINDS.cool.color);
        } else if (single) {
          s += rect(cx - barW / 2, 0, d[kinds[0]], RUNTIME_KINDS[kinds[0]].color);
        } else {
          s += rect(cx - barW - 1, 0, d.heat, RUNTIME_KINDS.heat.color) + rect(cx + 1, 0, d.cool, RUNTIME_KINDS.cool.color);
        }
        // A full-height tap target per bar, so tiny (or empty) bars are still easy to tap.
        const said = kinds.map((kind) => `${RUNTIME_KINDS[kind].label.toLowerCase()} ${fmtDur(d[kind])}`).join(", ");
        s += `<rect class="hit" x="${(cx - slot / 2).toFixed(1)}" y="${T}" width="${slot.toFixed(1)}" height="${H - T - B}" fill="transparent"
          data-action="bar" data-pop="1" data-k="${k}" tabindex="0" role="button" aria-label="${esc(`${d.title}: ${said}`)}"/>`;
        if ((items.length - 1 - k) % every === 0) {
          // Right-align a label that would run off the edge ("This week" under a narrow bar).
          const half = (d.label.length * 6) / 2;
          const at = cx + half > W - 1 ? `x="${W - 1}" text-anchor="end"` : `x="${cx}" text-anchor="middle"`;
          s += `<text ${at} y="${H - 6}"${d.current ? ' style="fill:var(--cs-text)"' : ""}>${esc(d.label)}</text>`;
        }
      });
      const what = kinds.map((kind) => RUNTIME_KINDS[kind].label).join(" and ");
      return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${what} runtime">${s}</svg>`;
    }

    _render() {
      if (this._missing()) return;
      const a = this.stateObj.attributes;
      const rt = a.runtime;
      if (!rt) {
        if (this._offline(RUNTIME_CSS)) return;
        this._paint(RUNTIME_CSS, `<ha-card><div class="warn">This entity has no runtime statistics.</div></ha-card>`);
        return;
      }
      const c = this._config;
      const kinds = this._kinds();
      const view = this._viewNow();
      const items = this._items(rt);
      this._itemsNow = items;
      const showChart = !c.hide_chart;
      const showCompare = !c.hide_compare;
      // The switch only matters when something on the card follows it.
      const showSwitch = !c.hide_view_switch && (showChart || showCompare);

      const noun = kinds.length > 1 ? "Heating and cooling time" : `${RUNTIME_KINDS[kinds[0]].label} time`;
      const span = { day: "days", week: "weeks", month: "months" }[view];
      const sub = c.hide_subtitle ? "" : showChart ? `Last ${items.length} ${span}` : noun;
      const switcher = showSwitch
        ? `<div class="range" role="group" aria-label="Group by">${[["day", "Day"], ["week", "Week"], ["month", "Month"]]
          .map(([v, t]) => `<button class="${v === view ? "on" : ""}" data-action="view" data-v="${v}" aria-pressed="${v === view}">${t}</button>`)
          .join("")}</div>`
        : "";
      const totals = c.totals === "hidden"
        ? ""
        : `<div class="sums" style="grid-template-columns:repeat(${kinds.length},minmax(0,1fr))">${kinds
          .map((kind) => this._sum(kind, rt[kind], c.totals === "compact"))
          .join("")}</div>`;
      const heading = this._heading(c.title || "HVAC runtime", esc(sub));
      const legend = showChart && !c.hide_legend
        ? `<div class="legend">${kinds.map((kind) => `<span><i style="background:${RUNTIME_KINDS[kind].color}"></i>${RUNTIME_KINDS[kind].label}</span>`).join("")}</div>`
        : "";
      this._paint(
        RUNTIME_CSS,
        `<ha-card>
          ${heading || switcher ? `<div class="head">${heading || "<div></div>"}${switcher}</div>` : ""}
          ${totals}
          ${showCompare ? this._compare(rt) : ""}
          ${showChart ? this._chart(items) : ""}
          ${legend}
        </ha-card>`
      );
    }
  }

  // HA loads this file (via add_extra_js_url) in parallel with its own app code, and the
  // app installs a scoped custom-element-registry polyfill first thing. Anything defined
  // before that polyfill is invisible to HA ("custom element doesn't exist"), so wait for
  // HA's root element to exist, i.e. the polyfill is in place. Always read
  // window.customElements fresh: the polyfill swaps the registry in.
  const appReady = () =>
    new Promise((resolve) => {
      const started = Date.now();
      const check = () => {
        if (window.customElements.get("home-assistant") || Date.now() - started > 30000) resolve();
        else setTimeout(check, 50);
      };
      check();
    });

  // A card whose element isn't defined yet when the dashboard draws (slow reload, busy HA
  // box) becomes Home Assistant's error card: hidden for ~2 s, then "Custom element doesn't
  // exist". HA asks for a rebuild once the element is defined, but that request is a single
  // event and can be lost (e.g. if the element gets defined while that part of the dashboard
  // is still being put together), leaving the red error until the next refresh. So after
  // defining our cards, find any of those errors that are ours and ask for the rebuild again,
  // the same way HA does. A few passes cover dashboards that finish drawing a little later.
  const OUR_CARD = /comfort-(thermostat|schedule|health|runtime)-card/;
  function rebuildOurErrorCards() {
    const stack = [document.body];
    while (stack.length) {
      const node = stack.pop();
      if (node.tagName === "HUI-ERROR-CARD") {
        const config = node._config || node.config || {};
        const said = `${config.origConfig?.type || ""} ${config.error || ""} ${node.shadowRoot?.textContent || ""}`;
        if (OUR_CARD.test(said)) node.dispatchEvent(new CustomEvent("ll-rebuild", { bubbles: true, composed: true }));
      }
      if (node.shadowRoot) stack.push(node.shadowRoot);
      for (const child of node.children || []) stack.push(child);
    }
  }

  appReady().then(() => {
    [0, 500, 2500, 5000, 10000].forEach((ms) => setTimeout(rebuildOurErrorCards, ms));
    const define = (name, cls) => {
      if (!window.customElements.get(name)) window.customElements.define(name, cls);
    };
    define("comfort-thermostat-card", ComfortThermostatCard);
    define("comfort-schedule-card", ComfortScheduleCard);
    define("comfort-health-card", ComfortHealthCard);
    define("comfort-runtime-card", ComfortRuntimeCard);

    window.customCards = window.customCards || [];
    const register = (type, name, description) => {
      if (!window.customCards.some((c) => c.type === type)) window.customCards.push({ type, name, description, preview: false });
    };
    register("comfort-thermostat-card", "Comfort thermostat", "Heat and cool set points, mode and fan for a Comfort Schedule thermostat.");
    register("comfort-schedule-card", "Comfort weekly schedule", "Edit the weekly heat/cool schedule.");
    register("comfort-health-card", "Comfort HVAC health", "Recent temperature, runs and HVAC problem warnings.");
    register("comfort-runtime-card", "Comfort HVAC runtime", "Heating and cooling time: today, recent days, month and year.");
  });
})();
