/* MetarParse Live — in-browser METAR/TAF decoder.
 *
 * Runs the real `metar_parse` Python package via Pyodide. The page is usable
 * before Python has loaded: input, examples, share links and live fetch work
 * immediately, and decoding starts as soon as the interpreter is ready.
 */
(() => {
  "use strict";

  const PYODIDE_INDEX = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
  const DEFAULT_WHEEL = "pkg/metar_parse-0.1.0-py3-none-any.whl";
  const DEBOUNCE_MS = 250;
  const MAX_HASH = 8000; // longer texts are not mirrored into the URL
  const MAX_CARDS = 200; // decode/render at most this many reports at once

  const EXAMPLES = [
    { label: "Prague, fine day", text: "METAR LKPR 121430Z 28008KT 250V320 9999 FEW045 SCT250 14/06 Q1022 NOSIG" },
    { label: "New York + remarks", text: "KJFK 121651Z 31015G25KT 1 1/2SM -RA BR BKN008 OVC015 18/16 A2992 RMK AO2 PK WND 32030/1622 SLP132 P0012 T01830161" },
    { label: "Thunderstorm", text: "METAR EDDF 121520Z 24018G35KT 4000 +TSRA SCT015 FEW030CB BKN060 22/19 Q1008 RERA BECMG 3000 TSRA" },
    { label: "Moscow, m/s wind", text: "METAR UUEE 121500Z 18005G10MPS 9999 BKN020 OVC100 08/04 Q1015 R24L/290050 NOSIG" },
    { label: "Fog, LIFR + RVR", text: "METAR EGLL 120550Z 00000KT 0150 R27L/0350N R27R/0300D FG VV001 07/07 Q1025 BECMG 0800" },
    { label: "Freezing rain & snow", text: "METAR CYUL 121500Z 04012KT 2SM -FZRA SN BKN008 OVC015 M02/M03 A2988 RMK NS5SC3" },
    { label: "TAF with TEMPO/PROB", text: "TAF EGLL 121100Z 1212/1318 24012KT 9999 SCT030\n  TEMPO 1214/1218 7000 -SHRA BKN020\n  PROB30 TEMPO 1218/1222 4000 TSRA BKN012CB\n  BECMG 1300/1303 VRB03KT\n  FM131200 30015G25KT 9999 FEW040" },
    { label: "Sydney TAF, INTER", text: "TAF YSSY 120500Z 1206/1312 16015KT 9999 FEW030\n  INTER 1206/1210 4000 SHRA BKN012\n  FM121400 20010KT CAVOK" },
  ];

  // Python glue: turns parsed reports into display-ready JSON for the page.
  const PY_HELPER = String.raw`
import json
import metar_parse
from metar_parse import parse, Metar, Taf
from metar_parse.cli import (
    format_metar, format_taf, report_to_dict, fmt_wind, fmt_visibility,
    fmt_weather, fmt_cloud, _fmt_time, _fmt_period, _fmt_num,
)


def _rvr(r):
    unit = "ft" if r.unit == "FT" else "m"
    pre = {"P": "more than ", "M": "less than "}
    if r.value is None:
        text = "not reported"
    else:
        text = pre.get(r.modifier or "", "") + f"{r.value} {unit}"
    if r.variable_high is not None:
        text += " to " + pre.get(r.variable_high_modifier or "", "") + f"{r.variable_high} {unit}"
    tend = {"U": " (improving)", "D": " (deteriorating)", "N": " (no change)"}.get(r.tendency or "", "")
    return f"RWY {r.runway}: {text}{tend}"


def _remarks(rm):
    rows = []
    if rm is None:
        return rows
    if rm.station_type:
        rows.append(["Station", {"AO1": "automated, no precip sensor", "AO2": "automated with precip sensor"}.get(rm.station_type, rm.station_type)])
    if rm.sea_level_pressure_hpa is not None:
        rows.append(["Sea-level pressure", f"{rm.sea_level_pressure_hpa:g} hPa"])
    if rm.precise_temperature_c is not None:
        t = f"{rm.precise_temperature_c:g} °C"
        if rm.precise_dewpoint_c is not None:
            t += f" / dew point {rm.precise_dewpoint_c:g} °C"
        rows.append(["Precise temp", t])
    if rm.hourly_precip_in is not None:
        rows.append(["Precip last hour", f"{rm.hourly_precip_in:g} in"])
    for attr, label in (("precip_3_6h_in", "Precip 3/6 h"), ("precip_24h_in", "Precip 24 h")):
        v = getattr(rm, attr, None)
        if v is not None:
            rows.append([label, f"{v:g} in"])
    for attr, label in (("max_temp_6h_c", "Max temp 6 h"), ("min_temp_6h_c", "Min temp 6 h"),
                        ("max_temp_24h_c", "Max temp 24 h"), ("min_temp_24h_c", "Min temp 24 h")):
        v = getattr(rm, attr, None)
        if v is not None:
            rows.append([label, f"{v:g} °C"])
    if rm.pressure_tendency is not None:
        rows.append(["Pressure tendency", f"{rm.pressure_tendency.change_hpa:g} hPa (code {rm.pressure_tendency.code})"])
    if rm.peak_wind is not None:
        pw = rm.peak_wind
        rows.append(["Peak wind", f"{pw.direction_deg:03d}° {pw.speed_kt} kt at {_fmt_time(pw.time)}"])
    if rm.wind_shift is not None:
        rows.append(["Wind shift", _fmt_time(rm.wind_shift) + (" (frontal passage)" if rm.wind_shift_frontal else "")])
    if rm.snow_depth_in is not None:
        rows.append(["Snow depth", f"{rm.snow_depth_in} in"])
    if rm.sunshine_minutes is not None:
        rows.append(["Sunshine", f"{rm.sunshine_minutes} min"])
    if rm.pressure_rising_rapidly:
        rows.append(["Pressure", "rising rapidly"])
    if rm.pressure_falling_rapidly:
        rows.append(["Pressure", "falling rapidly"])
    if rm.lightning:
        rows.append(["Lightning", " ".join(rm.lightning)])
    if rm.sensor_status:
        rows.append(["Sensors out", ", ".join(rm.sensor_status)])
    if rm.maintenance_needed:
        rows.append(["Maintenance", "station needs maintenance"])
    if rm.unparsed:
        rows.append(["Other remarks", " ".join(rm.unparsed)])
    return rows


def _group_category(g):
    if g.visibility is None and not g.clouds and not g.cavok:
        return None
    return Metar(raw="", visibility=g.visibility, clouds=g.clouds, cavok=g.cavok).flight_category


def _forecast_parts(fc):
    wind = fmt_wind(fc.wind)
    vis = fmt_visibility(fc.visibility, fc.cavok)
    wx = [fmt_weather(w) for w in fc.weather]
    if fc.nsw:
        wx.append("no significant weather")
    return {
        "wind": wind or "",
        "vis": ("CAVOK" if fc.cavok else vis) or "",
        "wx": ", ".join(wx),
        "clouds": ", ".join(fmt_cloud(c) for c in fc.clouds),
    }


def _desc(parts):
    bits = []
    if parts["wind"]:
        bits.append("wind " + parts["wind"])
    if parts["vis"]:
        bits.append(parts["vis"] if parts["vis"] == "CAVOK" else "vis " + parts["vis"])
    bits += [parts["wx"], parts["clouds"]]
    return ", ".join(b for b in bits if b)


def _issues(r):
    return [{"token": i.token, "message": i.message} for i in r.issues]


def _metar(m):
    rows = []
    if m.nil:
        rows.append(["Report", "NIL (missing)"])
    w = fmt_wind(m.wind)
    if w:
        if m.wind and m.wind.unit != "KT" and m.wind.speed_kt is not None and not m.wind.calm:
            w += f"  ≈ {m.wind.speed_kt:.0f} kt"
            if m.wind.gust_kt is not None:
                w += f", gusts {m.wind.gust_kt:.0f} kt"
        rows.append(["Wind", w])
    vis = fmt_visibility(m.visibility, m.cavok)
    if vis:
        if m.min_visibility is not None:
            vis += f", min {fmt_visibility(m.min_visibility)}"
        rows.append(["Visibility", vis])
    if m.runway_visual_range:
        rows.append(["Runway vis.", "; ".join(_rvr(r) for r in m.runway_visual_range)])
    if m.weather:
        rows.append(["Weather", ", ".join(fmt_weather(x) for x in m.weather)])
    if m.recent_weather:
        rows.append(["Recent", ", ".join(fmt_weather(x) for x in m.recent_weather)])
    if m.clouds:
        rows.append(["Clouds", ", ".join(fmt_cloud(c) for c in m.clouds)])
    if m.clouds or m.cavok:
        rows.append(["Ceiling", f"{m.ceiling_ft:,} ft" if m.ceiling_ft is not None else "none"])
    if m.temperature_c is not None:
        t = f"{_fmt_num(m.temperature_c)} °C"
        if m.dewpoint_c is not None:
            t += f" / dew point {_fmt_num(m.dewpoint_c)} °C"
        rows.append(["Temperature", t])
    rh = m.relative_humidity
    if rh is not None:
        rows.append(["Humidity", f"{rh:g} %"])
    if m.pressure is not None:
        if m.pressure.unit == "inHg":
            p = f"{m.pressure.inhg:.2f} inHg ({m.pressure.hpa:g} hPa)"
        else:
            p = f"{m.pressure.hpa:g} hPa ({m.pressure.inhg:.2f} inHg)"
        rows.append(["Pressure", p])
    if m.windshear:
        rows.append(["Wind shear", "; ".join(m.windshear)])
    if m.runway_state:
        rows.append(["Runway state", " ".join(m.runway_state)])
    trends = []
    for t in m.trends:
        parts = _forecast_parts(t)
        times = []
        for attr, word in (("from_time", "from"), ("until_time", "until"), ("at_time", "at")):
            v = getattr(t, attr)
            if v is not None:
                times.append(f"{word} {_fmt_time(v)}")
        trends.append({"kind": t.kind, "when": " ".join(times), "desc": _desc(parts)})
    flags = [f for f, on in (("AUTO", m.auto), ("COR", m.corrected)) if on]
    return {
        "type": m.report_type, "station": m.station, "time": _fmt_time(m.time) if m.time else None,
        "flags": flags, "cat": m.flight_category, "rows": rows, "trends": trends,
        "remarks": _remarks(m.remarks), "remarks_raw": m.remarks.raw if m.remarks else "",
        "text": format_metar(m),
    }


def _taf(t):
    groups = []
    for g in t.groups:
        label = g.kind
        if g.probability is not None:
            label = f"PROB{g.probability}" + (" TEMPO" if g.kind == "TEMPO" else "")
        item = {"kind": g.kind, "label": label, "period": _fmt_period(g.valid_from, g.valid_to),
                "cat": _group_category(g), "raw": g.raw}
        item.update(_forecast_parts(g))
        groups.append(item)
    rows = []
    if t.nil:
        rows.append(["Report", "NIL (missing)"])
    if t.cancelled:
        rows.append(["Report", "cancelled"])
    if t.valid_from is not None:
        rows.append(["Valid", _fmt_period(t.valid_from, t.valid_to)])
    for tt in t.temperatures:
        kind = "Max temp" if tt.kind == "TX" else "Min temp"
        rows.append([kind, f"{tt.temperature_c} °C at {_fmt_time(tt.time)}"])
    if t.remarks_raw:
        rows.append(["Remarks", t.remarks_raw])
    flags = [f for f, on in (("AMD", t.amended), ("COR", t.corrected)) if on]
    return {
        "type": "TAF", "station": t.station, "time": _fmt_time(t.issued) if t.issued else None,
        "flags": flags, "cat": groups[0]["cat"] if groups else None, "rows": rows,
        "groups": groups, "text": format_taf(t),
    }


def decode_all(texts_json):
    out = []
    for raw in json.loads(texts_json):
        try:
            r = parse(raw)
            d = _taf(r) if isinstance(r, Taf) else _metar(r)
            d["raw"] = r.raw
            d["issues"] = _issues(r)
            d["json"] = json.dumps(report_to_dict(r), indent=2, ensure_ascii=False)
        except Exception as exc:  # never break the page
            d = {"raw": raw, "error": f"{type(exc).__name__}: {exc}"}
        out.append(d)
    return json.dumps(out, ensure_ascii=False)


VERSION = metar_parse.__version__
`;

  // ------------------------------------------------------------------ DOM refs
  const $ = (id) => document.getElementById(id);
  const textarea = $("reports");
  const results = $("results");
  const statusBox = $("py-status");
  const statusText = statusBox.querySelector(".py-status-text");
  const chipsBox = $("example-chips");
  const liveForm = $("live-form");
  const liveInput = $("live-icao");
  const liveMsg = $("live-msg");
  const actionMsg = $("action-msg");
  const snippet = $("code-snippet").querySelector("code");

  let decodeAll = null; // Python function once ready
  let pyError = null;
  let cardSeq = 0;
  const tabMemory = new Map(); // remembers the selected tab per report text

  // ------------------------------------------------------------------ helpers
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  function debounce(fn, ms) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  function flash(el, msg) {
    el.textContent = msg;
    clearTimeout(el._t);
    el._t = setTimeout(() => (el.textContent = ""), 2500);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch { ok = false; }
      ta.remove();
      return ok;
    }
  }

  // base64url of UTF-8 text, for the URL hash
  function encodeHash(text) {
    const bytes = new TextEncoder().encode(text);
    let bin = "";
    bytes.forEach((b) => (bin += String.fromCharCode(b)));
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function decodeHash(s) {
    try {
      let b64 = s.replace(/-/g, "+").replace(/_/g, "/");
      while (b64.length % 4) b64 += "=";
      const bin = atob(b64);
      const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
      return new TextDecoder().decode(bytes);
    } catch {
      return null;
    }
  }

  function readHash() {
    const m = location.hash.match(/^#r=([A-Za-z0-9_-]*)$/);
    return m ? decodeHash(m[1]) : null;
  }

  let hashLive = false; // only mirror the text into the URL once the user has changed it

  function writeHash(text) {
    if (!hashLive) return true;
    const encoded = text.trim() ? encodeHash(text) : "";
    const fits = encoded.length <= MAX_HASH;
    const url = encoded && fits ? `#r=${encoded}` : location.pathname + location.search;
    if (url !== location.hash) history.replaceState(null, "", url);
    return fits;
  }

  // ------------------------------------------------------------------ splitting
  const STARTS_REPORT = /^(TAF|METAR|SPECI)\b/;
  const TAF_CONTINUATION = /^(FM\d{4,6}|BECMG|TEMPO|INTER|PROB\d{2}|TX|TN|RMK)\b/;

  /** Split textarea content into individual reports. */
  function splitReports(text) {
    const reports = [];
    let current = null;
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) {
        current = null; // blank line always ends a report
        continue;
      }
      const indented = /^\s/.test(line);
      const words = line.trim().toUpperCase();
      const isTaf = current && /^(TAF|AMD TAF|COR TAF)\b/.test(current);
      const continues =
        current !== null &&
        !STARTS_REPORT.test(words) &&
        (indented || (isTaf && TAF_CONTINUATION.test(words)));
      if (continues) {
        current += " " + line.trim();
        reports[reports.length - 1] = current;
      } else {
        current = line.trim();
        reports.push(current);
      }
    }
    return reports.map((r) => r.replace(/\s+/g, " ").replace(/\s*=+$/, "").trim()).filter(Boolean);
  }

  // ------------------------------------------------------------------ token highlighting
  const TOKEN_CLASSES = [
    [/^(METAR|SPECI|TAF|AMD|COR|AUTO|NIL|CNL)$/, "t-type", "report type / flag"],
    [/^\d{6}Z$/, "t-time", "date & time"],
    [/^\d{4}\/\d{4}$/, "t-time", "validity period"],
    [/^(\d{3}|VRB|\/\/\/)(\d{2,3}|\/\/)(G\d{2,3})?(KT|MPS|KMH)$/, "t-wind", "wind"],
    [/^\d{3}V\d{3}$/, "t-wind", "variable wind direction"],
    [/^\d{1,2}$/, "t-vis", "visibility (whole statute miles)"],
    [/^(CAVOK|\d{4}(NDV|[NSEW]{1,2})?|[PM]?\d+(\/\d+)?SM|\d+\/\d+SM)$/, "t-vis", "visibility"],
    [/^R\d{2}[LCR]?\/[PM]?\d{4}/, "t-rvr", "runway"],
    [/^(RE)?[-+]?(VC)?(MI|BC|PR|DR|BL|SH|TS|FZ)?(DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)+$|^(RE)?(TS|SH)$|^NSW$/, "t-wx", "weather"],
    [/^((FEW|SCT|BKN|OVC)(\d{3}|\/\/\/)(CB|TCU|\/\/\/)?|VV(\d{3}|\/\/\/)|SKC|CLR|NSC|NCD)$/, "t-cloud", "cloud"],
    [/^M?\d{2}\/(M?\d{2})?$/, "t-temp", "temperature / dew point"],
    [/^(T[XN]M?\d{2}\/\d{4}Z)$/, "t-temp", "forecast temperature"],
    [/^[QA]\d{4}$/, "t-press", "pressure"],
    [/^(NOSIG|BECMG|TEMPO|INTER|PROB\d{2}|FM\d{4,6}|TL\d{4}|AT\d{4})$/, "t-trend", "change group"],
  ];

  function classify(token, index, isTaf) {
    if (index <= 1 && /^[A-Z][A-Z0-9]{3}$/.test(token) && !/^(METAR|SPECI|TAF|AMD|COR|AUTO)$/.test(token)) {
      return ["t-station", "station"];
    }
    for (const [re, cls, title] of TOKEN_CLASSES) if (re.test(token)) return [cls, title];
    return ["", ""];
  }

  function highlightRaw(raw, issues, isTaf) {
    const tokens = raw.split(/\s+/).filter(Boolean);
    const notes = tokens.map(() => []);
    // Attach each issue to the first matching token (or token sequence), scanning forward.
    let cursor = 0;
    for (const issue of issues) {
      const parts = String(issue.token || "").split(/\s+/).filter(Boolean);
      if (!parts.length) continue;
      const find = (from) => {
        for (let i = from; i <= tokens.length - parts.length; i++) {
          if (parts.every((p, k) => tokens[i + k] === p)) return i;
        }
        return -1;
      };
      let at = find(cursor);
      if (at < 0) at = find(0);
      if (at < 0) continue;
      for (let k = 0; k < parts.length; k++) notes[at + k].push(issue.message);
      cursor = at;
    }
    let inRemarks = false;
    return tokens
      .map((tok, i) => {
        if (tok === "RMK") inRemarks = true;
        let [cls, title] = inRemarks ? ["t-rmk", tok === "RMK" ? "remarks follow" : "remark"] : classify(tok, i, isTaf);
        if (notes[i].length) {
          const msg = notes[i].join("; ");
          return `<span class="tok tok-issue" tabindex="0" title="${esc(msg)}" aria-label="${esc(tok)}: ${esc(msg)}">${esc(tok)}</span>`;
        }
        return `<span class="tok ${cls}"${title ? ` title="${esc(title)}"` : ""}>${esc(tok)}</span>`;
      })
      .join(" ");
  }

  // ------------------------------------------------------------------ rendering
  const catBadge = (cat, small) =>
    cat
      ? `<span class="cat cat-${esc(cat.toLowerCase())}${small ? " cat-sm" : ""}" title="Flight category">${esc(cat)}</span>`
      : small
        ? `<span class="cat cat-none cat-sm" title="Not enough information for a flight category">—</span>`
        : "";

  const dl = (rows) =>
    `<dl class="kv">${rows.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>`;

  function renderTafTable(groups) {
    const head = ["Change", "Period", "Wind", "Visibility", "Weather", "Clouds", "Cat."];
    const body = groups
      .map((g) => {
        const cells = [
          `<span class="grp grp-${esc(g.kind.toLowerCase())}">${esc(g.label)}</span>`,
          g.period
            .split(" – ")
            .map((p) => `<span class="nw">${esc(p)}</span>`)
            .join(" – "),
          esc(g.wind),
          esc(g.vis),
          esc(g.wx),
          esc(g.clouds),
          catBadge(g.cat, true),
        ];
        return `<tr title="${esc(g.raw)}">${cells
          .map((c, i) => `<td data-label="${head[i]}"${c ? "" : ' class="empty"'}><span class="cell">${c || "—"}</span></td>`)
          .join("")}</tr>`;
      })
      .join("");
    return `<div class="table-wrap"><table class="taf-table">
      <caption class="sr-only">Forecast change groups</caption>
      <thead><tr>${head.map((h) => `<th scope="col">${h}</th>`).join("")}</tr></thead>
      <tbody>${body}</tbody></table></div>`;
  }

  function renderDecoded(d) {
    let html = "";
    if (d.rows.length) html += dl(d.rows);
    if (d.type === "TAF" && d.groups.length) html += renderTafTable(d.groups);
    if (d.trends && d.trends.length) {
      html += `<h4 class="sub">Trend</h4><ul class="trends">${d.trends
        .map(
          (t) =>
            `<li><span class="grp grp-${esc(t.kind.toLowerCase())}">${esc(t.kind)}</span> ${esc(t.when)} ${
              t.desc ? esc(t.desc) : t.kind === "NOSIG" ? "no significant change expected" : ""
            }</li>`,
        )
        .join("")}</ul>`;
    }
    if (d.remarks && d.remarks.length) {
      html += `<h4 class="sub">Remarks <code>${esc(d.remarks_raw)}</code></h4>${dl(d.remarks)}`;
    }
    if (d.issues.length) {
      html += `<h4 class="sub sub-warn">Not understood</h4><ul class="issues">${d.issues
        .map((i) => `<li><code>${esc(i.token)}</code> ${esc(i.message)}</li>`)
        .join("")}</ul>`;
    }
    if (!html) html = `<p class="muted">Nothing could be decoded from this report.</p>`;
    return html;
  }

  function renderCard(d, index) {
    const id = `card${++cardSeq}`;
    if (d.error) {
      return `<article class="card card-error"><p><strong>Could not decode:</strong> ${esc(d.error)}</p><pre class="raw">${esc(d.raw)}</pre></article>`;
    }
    const isTaf = d.type === "TAF";
    const tabs = [
      ["decoded", "Decoded"],
      ["text", "Plain text"],
      ["json", "JSON"],
    ];
    const active = tabMemory.get(d.raw) || "decoded";
    const issueNote = d.issues.length
      ? `<span class="issue-count" title="Tokens the parser did not understand">${d.issues.length} issue${d.issues.length > 1 ? "s" : ""}</span>`
      : `<span class="clean" title="Every token was understood">clean</span>`;
    return `<article class="card" data-raw="${esc(d.raw)}" aria-labelledby="${id}-title">
      <header class="card-head">
        <div class="card-title">
          <h3 id="${id}-title"><span class="station">${esc(d.station || "????")}</span>
            <span class="rtype">${esc(d.type)}</span></h3>
          <span class="when">${isTaf ? "issued " : ""}${esc(d.time || "time unknown")}</span>
          ${d.flags.map((f) => `<span class="flag">${esc(f)}</span>`).join("")}
        </div>
        <div class="card-badges">${issueNote}${catBadge(d.cat)}</div>
      </header>
      <p class="raw" aria-label="Raw report with highlighted tokens">${highlightRaw(d.raw, d.issues, isTaf)}</p>
      <div class="tabs" role="tablist" aria-label="View">
        ${tabs
          .map(
            ([key, label]) =>
              `<button type="button" role="tab" id="${id}-tab-${key}" aria-controls="${id}-panel-${key}" data-tab="${key}"
                aria-selected="${key === active}" tabindex="${key === active ? 0 : -1}">${label}</button>`,
          )
          .join("")}
      </div>
      <div class="panel" role="tabpanel" id="${id}-panel-decoded" aria-labelledby="${id}-tab-decoded"${active === "decoded" ? "" : " hidden"}>
        ${renderDecoded(d)}
      </div>
      <div class="panel" role="tabpanel" id="${id}-panel-text" aria-labelledby="${id}-tab-text"${active === "text" ? "" : " hidden"}>
        <p class="panel-note">Output of <code>metar_parse.cli.${isTaf ? "format_taf" : "format_metar"}()</code>, the same as the <code>metar</code> command.</p>
        <pre class="pre">${esc(d.text)}</pre>
      </div>
      <div class="panel" role="tabpanel" id="${id}-panel-json" aria-labelledby="${id}-tab-json"${active === "json" ? "" : " hidden"}>
        <div class="code-block">
          <button type="button" class="btn btn-small copy-json">Copy JSON</button>
          <pre class="pre json">${esc(d.json)}</pre>
        </div>
      </div>
    </article>`;
  }

  function renderPending(reports) {
    const msg = pyError ? "Python failed to load, so this report can't be decoded." : "Waiting for Python to finish loading…";
    results.innerHTML = reports
      .map(
        (r) => `<article class="card card-pending">
          <p class="raw">${esc(r)}</p>
          <p class="muted"><span class="spinner${pyError ? " hidden" : ""}" aria-hidden="true"></span> ${msg}</p>
        </article>`,
      )
      .join("");
  }

  function renderEmpty() {
    results.innerHTML = `<div class="empty-state"><p>Paste a report above, pick an example or fetch a live one by ICAO code.</p></div>`;
  }

  function updateSnippet(reports) {
    const first = reports[0] || EXAMPLES[0].text;
    const isTaf = /^(TAF|AMD TAF|COR TAF)\b/i.test(first);
    const lit = JSON.stringify(first);
    const lines = [
      "from metar_parse import parse",
      "",
      `report = parse(${lit})`,
      "",
      "print(report.station)",
    ];
    if (isTaf) {
      lines.push("for group in report.groups:", "    print(group.kind, group.valid_from, group.valid_to, group.visibility)");
    } else {
      lines.push(
        "print(report.wind.speed_kt, report.wind.gust_kt)",
        "print(report.flight_category, report.ceiling_ft)",
        "print(report.temperature_c, report.relative_humidity)",
      );
    }
    lines.push("print(report.issues)          # tokens that weren't understood", "data = report.to_dict()        # plain dict, ready for json.dumps");
    snippet.textContent = lines.join("\n");
  }

  // ------------------------------------------------------------------ decode loop
  let lastRendered = null;

  function decodeNow() {
    const text = textarea.value;
    writeHash(text);
    const allReports = splitReports(text);
    const reports = allReports.slice(0, MAX_CARDS);
    updateSnippet(reports);
    const key = JSON.stringify(allReports) + (decodeAll ? "|ready" : "|pending");
    if (key === lastRendered) return;
    lastRendered = key;
    if (!reports.length) return renderEmpty();
    if (!decodeAll) return renderPending(reports);
    let decoded;
    try {
      decoded = JSON.parse(decodeAll(JSON.stringify(reports)));
    } catch (err) {
      console.warn("decode failed", err);
      decoded = reports.map((raw) => ({ raw, error: String(err && err.message ? err.message : err) }));
    }
    cardSeq = 0;
    const more = allReports.length - reports.length;
    results.innerHTML =
      decoded.map(renderCard).join("") +
      (more > 0 ? `<p class="muted more-note">Showing the first ${MAX_CARDS} reports; ${more} more not decoded.</p>` : "");
  }

  const decodeSoon = debounce(decodeNow, DEBOUNCE_MS);

  // ------------------------------------------------------------------ Pyodide
  function setStatus(state, html) {
    statusBox.dataset.state = state;
    statusText.innerHTML = html;
  }

  async function wheelUrl() {
    let path = DEFAULT_WHEEL;
    try {
      const res = await fetch(new URL("data/meta.json", location.href), { cache: "no-cache" });
      if (res.ok) {
        const meta = await res.json();
        if (meta && typeof meta.wheel === "string" && meta.wheel) path = meta.wheel;
      }
    } catch {
      /* meta.json is optional */
    }
    return new URL(path, location.href).href;
  }

  async function bootPython() {
    const t0 = performance.now();
    pyError = null;
    setStatus("loading", "Loading Python in your browser… (about 10&nbsp;MB the first time)");
    try {
      if (typeof loadPyodide !== "function") throw new Error("Pyodide script did not load (offline or blocked?)");
      const [pyodide, wheel] = await Promise.all([loadPyodide({ indexURL: PYODIDE_INDEX }), wheelUrl()]);
      setStatus("loading", "Installing MetarParse…");
      await pyodide.loadPackage("micropip");
      const micropip = pyodide.pyimport("micropip");
      await micropip.install(wheel);
      pyodide.runPython(PY_HELPER);
      decodeAll = pyodide.globals.get("decode_all");
      const version = pyodide.globals.get("VERSION");
      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      setStatus("ready", `Python ready · MetarParse ${esc(version)} · loaded in ${secs}&nbsp;s`);
      window.__metarParseLoadSeconds = Number(secs);
    } catch (err) {
      console.error(err);
      pyError = err;
      setStatus("error", `Couldn't load Python: ${esc(err && err.message ? err.message : err)} <button type="button" class="btn btn-small" id="retry-py">Retry</button>`);
      const retry = $("retry-py");
      if (retry) retry.addEventListener("click", () => { lastRendered = null; bootPython().then(decodeNow); });
    }
    lastRendered = null;
    decodeNow();
  }

  // ------------------------------------------------------------------ live data
  let liveData = null;

  function loadLive() {
    if (!liveData) {
      const get = (p) =>
        fetch(new URL(p, location.href), { cache: "no-cache" }).then((r) => {
          if (!r.ok) throw new Error(`${p}: HTTP ${r.status}`);
          return r.json();
        });
      liveData = Promise.all([get("data/metars.json"), get("data/tafs.json").catch(() => ({}))]).then(([metars, tafs]) => {
        const byId = new Map();
        for (const m of metars || []) if (m && m.id) byId.set(String(m.id).toUpperCase(), m);
        return { byId, tafs: tafs || {} };
      });
      liveData.catch(() => (liveData = null)); // allow retry
    }
    return liveData;
  }

  async function fetchLive(icao) {
    icao = icao.trim().toUpperCase();
    if (!/^[A-Z0-9]{3,4}$/.test(icao)) {
      liveMsg.textContent = "Enter a 4-letter ICAO code, e.g. EGLL.";
      liveMsg.dataset.kind = "error";
      return;
    }
    liveMsg.dataset.kind = "";
    liveMsg.textContent = "Fetching…";
    try {
      const { byId, tafs } = await loadLive();
      const metar = byId.get(icao);
      const taf = tafs[icao];
      if (!metar && !taf) {
        liveMsg.dataset.kind = "error";
        liveMsg.textContent = `No current report for ${icao} in the latest snapshot.`;
        return;
      }
      const parts = [];
      if (metar && metar.raw) parts.push(metar.raw);
      if (taf && taf.raw) parts.push(taf.raw);
      textarea.value = parts.join("\n");
      hashLive = true;
      const name = metar && metar.name ? ` (${metar.name})` : "";
      liveMsg.textContent = `Loaded ${parts.length === 2 ? "METAR + TAF" : metar ? "METAR" : "TAF"} for ${icao}${name}.`;
      decodeNow();
    } catch (err) {
      console.warn(err);
      liveMsg.dataset.kind = "error";
      liveMsg.textContent = "Live data isn't available right now.";
    }
  }

  // ------------------------------------------------------------------ events
  function initChips() {
    chipsBox.innerHTML = EXAMPLES.map(
      (ex, i) => `<button type="button" class="chip" data-example="${i}">${esc(ex.label)}</button>`,
    ).join("");
    chipsBox.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-example]");
      if (!btn) return;
      textarea.value = EXAMPLES[Number(btn.dataset.example)].text;
      hashLive = true;
      decodeNow();
    });
  }

  results.addEventListener("click", async (e) => {
    const tab = e.target.closest('[role="tab"]');
    if (tab) return selectTab(tab);
    const copy = e.target.closest(".copy-json");
    if (copy) {
      const pre = copy.parentElement.querySelector("pre");
      const ok = await copyText(pre.textContent);
      copy.textContent = ok ? "Copied!" : "Copy failed";
      setTimeout(() => (copy.textContent = "Copy JSON"), 1500);
    }
  });

  results.addEventListener("keydown", (e) => {
    const tab = e.target.closest('[role="tab"]');
    if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    const list = [...tab.parentElement.querySelectorAll('[role="tab"]')];
    let i = list.indexOf(tab);
    if (e.key === "ArrowLeft") i = (i - 1 + list.length) % list.length;
    else if (e.key === "ArrowRight") i = (i + 1) % list.length;
    else if (e.key === "Home") i = 0;
    else i = list.length - 1;
    e.preventDefault();
    selectTab(list[i]);
    list[i].focus();
  });

  function selectTab(tab) {
    const card = tab.closest(".card");
    for (const t of card.querySelectorAll('[role="tab"]')) {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      $(t.getAttribute("aria-controls")).hidden = !on;
    }
    tabMemory.set(card.dataset.raw, tab.dataset.tab);
  }

  document.addEventListener("click", async (e) => {
    const btn = e.target.closest(".copy-btn");
    if (!btn) return;
    const ok = await copyText($(btn.dataset.copyTarget).textContent);
    btn.textContent = ok ? "Copied!" : "Copy failed";
    setTimeout(() => (btn.textContent = "Copy"), 1500);
  });

  textarea.addEventListener("input", () => {
    hashLive = true;
    decodeSoon();
  });

  $("share-btn").addEventListener("click", async () => {
    hashLive = true;
    const fits = writeHash(textarea.value);
    if (!textarea.value.trim()) return flash(actionMsg, "Nothing to share yet.");
    if (!fits) return flash(actionMsg, "Too much text for a link — share fewer reports.");
    const ok = await copyText(location.href);
    flash(actionMsg, ok ? "Link copied — the report is stored in the link itself." : "Copy failed — use the address bar.");
  });

  $("clear-btn").addEventListener("click", () => {
    textarea.value = "";
    hashLive = true;
    decodeNow();
    textarea.focus();
  });

  liveForm.addEventListener("submit", (e) => {
    e.preventDefault();
    fetchLive(liveInput.value);
  });

  window.addEventListener("hashchange", () => {
    const text = readHash();
    if (text !== null && text !== textarea.value) {
      textarea.value = text;
      decodeNow();
    }
  });

  // ------------------------------------------------------------------ start
  initChips();
  const fromHash = readHash();
  hashLive = fromHash !== null;
  textarea.value = fromHash !== null ? fromHash : EXAMPLES[0].text;
  decodeNow();
  bootPython();
})();
