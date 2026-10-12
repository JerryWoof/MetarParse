// MetarParse Live — world airport weather map.
// Plain ES module, no build step. Leaflet is loaded globally from cdnjs.

const L = window.L;
const REFRESH_MS = 10 * 60 * 1000;
const STALE_MIN = 180;            // reports older than this are excluded from "Right now" extremes
const CATS = ['VFR', 'MVFR', 'IFR', 'LIFR'];
const CAT_INFO = {
  VFR: ['Visual flight rules', 'Ceiling above 3,000 ft and visibility above 5 SM'],
  MVFR: ['Marginal VFR', 'Ceiling 1,000–3,000 ft and/or visibility 3–5 SM'],
  IFR: ['Instrument flight rules', 'Ceiling 500–999 ft and/or visibility 1–3 SM'],
  LIFR: ['Low IFR', 'Ceiling below 500 ft and/or visibility below 1 SM'],
};
const LAYERS = ['cat', 'temp', 'wind', 'wx', 'age'];

/* ------------------------------------------------------------------ utils */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);
const fmtInt = (n) => Math.round(n).toLocaleString('en-US');

const store = {
  get(k, d) { try { const v = localStorage.getItem('mpl.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('mpl.' + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

const state = {
  meta: null,
  stations: [],
  byId: new Map(),
  tafs: null,
  tafsPromise: null,
  layer: LAYERS.includes(store.get('layer')) ? store.get('layer') : 'cat',
  units: Object.assign({ temp: 'C', wind: 'kt', vis: 'm' }, store.get('units', {}) || {}),
  favs: new Set(Array.isArray(store.get('favs')) ? store.get('favs') : []),
  selected: null,
  catFilter: null,
  rnTab: 'gust',
};

function ageMin(iso) { const t = Date.parse(iso); return Number.isFinite(t) ? (Date.now() - t) / 60000 : null; }
function ago(iso) {
  const m = ageMin(iso);
  if (m == null) return 'unknown';
  if (m < 1) return 'just now';
  if (m < 60) return `${Math.floor(m)} min ago`;
  const h = Math.floor(m / 60), r = Math.floor(m % 60);
  if (h < 24) return r ? `${h} h ${r} min ago` : `${h} h ago`;
  return `${Math.floor(h / 24)} d ago`;
}
function hhmmZ(iso) { const d = new Date(iso); return isNaN(d) ? '' : `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}Z`; }
function dayHHZ(iso) {
  const d = new Date(iso); if (isNaN(d)) return '';
  return `${d.toLocaleDateString('en-GB', { weekday: 'short', timeZone: 'UTC' })} ${String(d.getUTCDate()).padStart(2, '0')} ${hhmmZ(iso)}`;
}
function flag(cc) {
  if (!cc || !/^[A-Z]{2}$/.test(cc)) return '';
  return String.fromCodePoint(...[...cc].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
}
const regionNames = (() => { try { return new Intl.DisplayNames(['en'], { type: 'region' }); } catch { return null; } })();
function countryName(cc) { if (!cc) return ''; try { return regionNames?.of(cc) || cc; } catch { return cc; } }

/* ------------------------------------------------------------------ units */
const U = {
  temp(c, dec = 0) {
    if (!isNum(c)) return '—';
    return state.units.temp === 'F' ? `${((c * 9) / 5 + 32).toFixed(dec)} °F` : `${c.toFixed(dec)} °C`;
  },
  tempNum(c) { return state.units.temp === 'F' ? (c * 9) / 5 + 32 : c; },
  wind(kt) {
    if (!isNum(kt)) return '—';
    return state.units.wind === 'kmh' ? `${Math.round(kt * 1.852)} km/h` : `${Math.round(kt)} kt`;
  },
  windAlt(kt) {
    if (!isNum(kt)) return '';
    return state.units.wind === 'kmh' ? `${Math.round(kt)} kt` : `${Math.round(kt * 1.852)} km/h · ${Math.round(kt * 0.5144)} m/s`;
  },
  windUnit() { return state.units.wind === 'kmh' ? 'km/h' : 'kt'; },
  windNum(kt) { return state.units.wind === 'kmh' ? kt * 1.852 : kt; },
  visKm(m) {
    if (!isNum(m)) return '—';
    if (m >= 9999) return '10 km or more';
    if (m < 1000) return `${Math.round(m)} m`;
    return `${(m / 1000).toFixed(m < 5000 ? 1 : 0)} km`;
  },
  visSm(m) {
    if (!isNum(m)) return '—';
    const sm = m / 1609.344;
    if (m >= 9999) return sm >= 9.9 ? '10+ SM' : '6+ SM';
    if (sm < 1) {
      const fr = [[1 / 16, '1/16'], [1 / 8, '1/8'], [3 / 16, '3/16'], [1 / 4, '1/4'], [5 / 16, '5/16'], [3 / 8, '3/8'], [1 / 2, '1/2'], [5 / 8, '5/8'], [3 / 4, '3/4'], [7 / 8, '7/8']];
      let best = fr[0];
      for (const f of fr) if (Math.abs(f[0] - sm) < Math.abs(best[0] - sm)) best = f;
      return `${best[1]} SM`;
    }
    return `${sm < 3 ? (Math.round(sm * 4) / 4).toString() : Math.round(sm)} SM`;
  },
  vis(m) { return state.units.vis === 'sm' ? U.visSm(m) : U.visKm(m); },
  visAlt(m) { return state.units.vis === 'sm' ? U.visKm(m) : U.visSm(m); },
  visShort(m) {
    if (!isNum(m)) return '—';
    if (state.units.vis === 'sm') return U.visSm(m);
    if (m >= 9999) return '10 km+';
    return m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(m < 5000 ? 1 : 0)} km`;
  },
};

/* ------------------------------------------------------------------ colours */
let COL = {};
function readColours() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n) => cs.getPropertyValue(n).trim();
  COL = { VFR: v('--vfr'), MVFR: v('--mvfr'), IFR: v('--ifr'), LIFR: v('--lifr'), UNK: v('--unk'), stroke: v('--dot-stroke') };
}
const catColour = (c) => COL[c] || COL.UNK;

function hexToRgb(h) { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function ramp(stops) {
  const s = stops.map(([v, c]) => [v, hexToRgb(c)]);
  return (x) => {
    if (x <= s[0][0]) return `rgb(${s[0][1]})`;
    for (let i = 1; i < s.length; i++) {
      if (x <= s[i][0]) {
        const t = (x - s[i - 1][0]) / (s[i][0] - s[i - 1][0]);
        const c = s[i - 1][1].map((a, k) => Math.round(a + (s[i][1][k] - a) * t));
        return `rgb(${c})`;
      }
    }
    return `rgb(${s[s.length - 1][1]})`;
  };
}
const TEMP_STOPS = [[-40, '#3d0d6b'], [-25, '#3b3fbf'], [-10, '#3f8fd9'], [0, '#8fd0ef'], [10, '#bfe39a'], [20, '#f7cf47'], [30, '#f2782f'], [40, '#cc2028'], [48, '#7a0b2b']];
const WIND_STOPS = [[0, '#b7c4d1'], [5, '#8ec3e6'], [10, '#3f97d6'], [17, '#2a9d6c'], [25, '#e9b11c'], [34, '#ea5b25'], [48, '#c3123f'], [64, '#6d1a7a']];
const tempColour = ramp(TEMP_STOPS);
const windColour = ramp(WIND_STOPS);
const WX_CLASSES = [
  ['ts', 'Thunderstorm', '#8b3dff'],
  ['fz', 'Freezing', '#ff3d9a'],
  ['sn', 'Snow / ice', '#14c8e6'],
  ['ra', 'Rain', '#1f5fe0'],
  ['dz', 'Drizzle', '#6aa7ff'],
  ['fg', 'Fog / mist', '#e2c044'],
  ['ot', 'Haze, dust, smoke', '#b8642a'],
  ['no', 'None', null],
];
const WX_RANK = Object.fromEntries(WX_CLASSES.map((w, i) => [w[0], WX_CLASSES.length - i]));
function wxClass(s) {
  const codes = s.wx || [];
  if (!codes.length) return 'no';
  const j = codes.join(' ');
  if (/TS/.test(j)) return 'ts';
  if (/FZ/.test(j)) return 'fz';
  if (/SN|SG|PL|GR|GS|IC/.test(j)) return 'sn';
  if (/RA|UP|SH/.test(j)) return 'ra';
  if (/DZ/.test(j)) return 'dz';
  if (/FG|BR/.test(j)) return 'fg';
  return 'ot';
}
const AGE_BINS = [[30, '< 30 min', '#10b981'], [60, '30–60 min', '#84cc16'], [90, '1–1.5 h', '#eab308'], [180, '1.5–3 h', '#f97316'], [Infinity, '> 3 h', '#dc2626']];
function ageColour(m) { for (const b of AGE_BINS) if (m == null || m < b[0]) return m == null ? COL.UNK : b[2]; return COL.UNK; }
const maxWind = (s) => Math.max(s.wspd ?? -1, s.gust ?? -1);

/* style for one station on the current layer: [fill, rank, small] */
function look(s) {
  switch (state.layer) {
    case 'temp': return isNum(s.temp) ? [tempColour(s.temp), Math.abs(s.temp - 12), false] : [COL.UNK, -1, true];
    case 'wind': { const w = maxWind(s); return w >= 0 ? [windColour(w), w, false] : [COL.UNK, -1, true]; }
    case 'wx': { const c = wxClass(s); const def = WX_CLASSES.find((w) => w[0] === c); return c === 'no' ? [COL.UNK, 0, true] : [def[2], WX_RANK[c], false]; }
    case 'age': { const m = ageMin(s.time); return [ageColour(m), m == null ? -1 : m, false]; }
    default: return [catColour(s.cat), s.cat ? CATS.indexOf(s.cat) + 1 : 0, !s.cat];
  }
}

/* ------------------------------------------------------------------ map */
const isTouch = matchMedia('(pointer: coarse)').matches;
const darkMQ = matchMedia('(prefers-color-scheme: dark)');
const narrowMQ = matchMedia('(max-width: 720px)');
readColours();

const renderer = L.canvas({ padding: 0.5, tolerance: isTouch ? 10 : 4 });
const map = L.map('map', {
  preferCanvas: true,
  renderer,
  zoomControl: false,
  worldCopyJump: true,
  minZoom: 2,
  maxZoom: 13,
  zoomSnap: 0.5,
  wheelPxPerZoomLevel: 90,
}).setView([28, narrowMQ.matches ? 10 : -5], clamp(Math.floor(Math.log2((document.getElementById('map').clientWidth || 1000) / 300) * 2) / 2, 2, 3.5));
L.control.zoom({ position: 'topright' }).addTo(map);
map.attributionControl.setPrefix('<a href="https://leafletjs.com" rel="noopener">Leaflet</a> · Weather: <a href="https://aviationweather.gov" rel="noopener">aviationweather.gov</a> (NOAA) · Not for operational use');

// Basemap: Esri Gray Canvas (keyless). CARTO basemaps now answer every request with an
// "API KEY REQUIRED" placeholder tile, so they cannot be used without a key.
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_{style}/MapServer/tile/{z}/{y}/{x}';
const ESRI_ATTR = 'Tiles &copy; <a href="https://www.esri.com" rel="noopener">Esri</a> — Esri, HERE, Garmin, &copy; <a href="https://www.openstreetmap.org/copyright" rel="noopener">OpenStreetMap</a> contributors';
const tileUrl = (kind) => ESRI.replace('{style}', `${darkMQ.matches ? 'Dark' : 'Light'}_Gray_${kind}`);
const tiles = L.tileLayer(tileUrl('Base'), { maxZoom: 16, maxNativeZoom: 16, attribution: ESRI_ATTR }).addTo(map);
map.createPane('labels');
map.getPane('labels').style.zIndex = 250;
map.getPane('labels').style.pointerEvents = 'none';
const labels = L.tileLayer(tileUrl('Reference'), { maxZoom: 16, maxNativeZoom: 16, pane: 'labels', opacity: 0.9 }).addTo(map);
darkMQ.addEventListener('change', () => {
  tiles.setUrl(tileUrl('Base'));
  labels.setUrl(tileUrl('Reference'));
  readColours();
  restyleAll(true);
  renderLegend();
  renderRightNow();
  if (state.selected) renderStation(state.selected, { keepScroll: true });
});

const group = L.featureGroup().addTo(map);
const markers = new Map();
let selRing = null;
let meMarker = null;

function radiusFor(z, small) { const r = clamp(1.3 + z * 0.8, 2.6, 9); return small ? Math.max(1.8, r * 0.6) : r; }

function restyleAll(reorder) {
  const z = map.getZoom();
  const items = [];
  for (const s of state.stations) {
    const m = markers.get(s.id);
    if (!m) continue;
    const [fill, rank, small] = look(s);
    m.setStyle({
      fillColor: fill,
      color: COL.stroke,
      weight: z >= 4 ? 1 : 0.6,
      fillOpacity: small ? 0.55 : 0.92,
      opacity: small ? 0.4 : 0.9,
    });
    m.setRadius(radiusFor(z, small));
    m._small = small;
    items.push([rank, m, s]);
  }
  if (reorder) {
    // canvas draws in insertion order: add the most significant stations last so they sit on top
    group.clearLayers();
    items.sort((a, b) => a[0] - b[0]);
    for (const [, m, s] of items) if (passesFilter(s)) group.addLayer(m);
  }
}
function passesFilter(s) { return !state.catFilter || (s.cat || 'UNK') === state.catFilter; }

map.on('zoomend', () => {
  const z = map.getZoom();
  for (const m of markers.values()) { m.setRadius(radiusFor(z, m._small)); m.setStyle({ weight: z >= 4 ? 1 : 0.6 }); }
});

function buildMarkers() {
  markers.clear();
  group.clearLayers();
  for (const s of state.stations) {
    const m = L.circleMarker([s.lat, s.lon], { renderer, radius: 3, stroke: true, bubblingMouseEvents: false });
    m._sid = s.id;
    markers.set(s.id, m);
  }
  restyleAll(true);
}

const tip = L.tooltip({ className: 'st-tip', direction: 'top', offset: [0, -6], opacity: 1 });
group.on('mouseover', (e) => {
  if (isTouch) return;
  const s = state.byId.get(e.layer._sid);
  if (!s) return;
  tip.setLatLng(e.layer.getLatLng()).setContent(`<span class="tid">${esc(s.id)}</span> ${esc(s.name || '')}<br><span class="tval">${esc(tipValue(s))}</span>`);
  map.openTooltip(tip);
});
group.on('mouseout', () => map.closeTooltip(tip));
group.on('click', (e) => { map.closeTooltip(tip); openStation(e.layer._sid, { fly: false }); });

function tipValue(s) {
  switch (state.layer) {
    case 'temp': return isNum(s.temp) ? `${U.temp(s.temp)} · dew ${U.temp(s.dew)}` : 'No temperature';
    case 'wind': return windText(s);
    case 'wx': return s.wx_text ? cap(s.wx_text) : 'No significant weather';
    case 'age': return `Observed ${ago(s.time)}`;
    default: return `${s.cat || 'Unknown category'} · vis ${U.visShort(s.vis_m)}${isNum(s.ceil_ft) ? ` · ceiling ${fmtInt(s.ceil_ft)} ft` : ''}`;
  }
}
const cap = (t) => (t ? t[0].toUpperCase() + t.slice(1) : t);

function windText(s) {
  if (!isNum(s.wspd)) return 'Wind not reported';
  if (s.wspd === 0 && !s.gust) return 'Calm';
  const dir = s.vrb || !isNum(s.wdir) ? 'Variable' : `${String(s.wdir).padStart(3, '0')}°`;
  return `${dir} ${U.wind(s.wspd)}${isNum(s.gust) ? ` gust ${U.wind(s.gust)}` : ''}`;
}

/* ------------------------------------------------------------------ data */
async function getJSON(path, bust) {
  const r = await fetch(`${path}?v=${encodeURIComponent(bust)}`, { cache: 'no-cache' });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

async function loadAll(initial) {
  const meta = await getJSON('data/meta.json', Date.now());
  if (!initial && state.meta && meta.generated === state.meta.generated) { state.meta = meta; renderHeader(); return false; }
  const list = await getJSON('data/metars.json', meta.generated);
  state.meta = meta;
  state.stations = list.filter((s) => s && s.id && isNum(s.lat) && isNum(s.lon));
  state.byId = new Map(state.stations.map((s) => [s.id, s]));
  for (const s of state.stations) {
    s._q = `${s.id} ${s.iata || ''} ${s.name || ''}`.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
    s._name = (s.name || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  }
  state.tafs = null; state.tafsPromise = null;
  buildMarkers();
  renderHeader();
  renderRightNow();
  renderFavs();
  renderLegend();
  return true;
}

function loadTafs() {
  if (state.tafs) return Promise.resolve(state.tafs);
  if (!state.tafsPromise) {
    state.tafsPromise = getJSON('data/tafs.json', state.meta?.generated || Date.now())
      .then((t) => (state.tafs = t))
      .catch((e) => { state.tafsPromise = null; throw e; });
  }
  return state.tafsPromise;
}

/* ------------------------------------------------------------------ header */
function renderHeader() {
  const m = state.meta;
  if (!m) return;
  const status = $('#status');
  $('#updated').textContent = `Updated ${ago(m.generated)}`;
  $('#updated').title = `Data generated ${dayHHZ(m.generated)}`;
  $('#count').textContent = `${fmtInt(state.stations.length || m.metar_count || 0)} stations`;
  status.classList.toggle('stale', (ageMin(m.generated) ?? 0) > 45);

  const pct = (x) => (isNum(x) ? `${(x * 100).toFixed(1)}%` : '—');
  const html = `
    <a class="lib-badge" href="https://github.com/JerryWoof/MetarParse" rel="noopener" title="${esc(`MetarParse ${m.library_version || ''}: ${pct(m.clean_rate)} of ${fmtInt(m.metar_count || 0)} METARs decoded with no issues; flight category matches NOAA's for ${pct(m.category_agreement)} of ${fmtInt(m.category_compared || 0)} reports compared`)}">
      decoded by <b>MetarParse</b> · <span class="ok">${pct(m.clean_rate)}</span> clean · <span class="ok">${pct(m.category_agreement)}</span> agrees with NOAA</a>
    <button class="pip-chip" type="button" data-copy="pip install metar-parse" title="Copy install command">pip install metar-parse
      <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/></svg></button>`;
  $('#lib-badge-top').innerHTML = html;
  $('#lib-badge-side').innerHTML = html;
}
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-copy]');
  if (b) copyText(b.dataset.copy, 'Copied “' + b.dataset.copy + '”');
});

async function copyText(text, msg = 'Copied') {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch { /* ignore */ }
    ta.remove();
  }
  toast(msg);
}
let toastTimer;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2200);
}

/* ------------------------------------------------------------------ legend + layers */
function renderLegend() {
  const el = $('#legend');
  const counts = {};
  const sw = (items) => `<div class="sw">${items.map(([c, l, n]) => `<span><i style="background:${c}"></i>${esc(l)}${n != null ? ` <b>${fmtInt(n)}</b>` : ''}</span>`).join('')}</div>`;
  if (state.layer === 'cat') {
    for (const s of state.stations) counts[s.cat || 'UNK'] = (counts[s.cat || 'UNK'] || 0) + 1;
    el.innerHTML = sw([...CATS.map((c) => [catColour(c), c, counts[c] || 0]), [COL.UNK, 'Unknown', counts.UNK || 0]])
      + (state.catFilter ? `<div class="filter-note">Showing only ${state.catFilter === 'UNK' ? 'unknown' : state.catFilter} · <button type="button" class="linkish" id="clear-filter">Show all</button></div>` : '');
  } else if (state.layer === 'temp' || state.layer === 'wind') {
    const stops = state.layer === 'temp' ? TEMP_STOPS : WIND_STOPS;
    const lo = stops[0][0], hi = stops[stops.length - 1][0];
    const grad = stops.map(([v, c]) => `${c} ${(((v - lo) / (hi - lo)) * 100).toFixed(1)}%`).join(',');
    const ticks = state.layer === 'temp' ? [-40, -20, 0, 20, 40] : [0, 10, 20, 34, 48, 64];
    const lab = (v) => (state.layer === 'temp' ? `${Math.round(U.tempNum(v))}°` : `${Math.round(U.windNum(v))}`);
    el.innerHTML = `<div class="cap">${state.layer === 'temp' ? `Temperature (°${state.units.temp})` : `Strongest of wind / gust (${U.windUnit()})`}</div>
      <div class="ramp" style="background:linear-gradient(90deg,${grad})"></div>
      <div class="ticks">${ticks.map((t) => `<span style="left:${(((t - lo) / (hi - lo)) * 100).toFixed(1)}%">${lab(t)}</span>`).join('')}</div>`;
  } else if (state.layer === 'wx') {
    for (const s of state.stations) { const c = wxClass(s); counts[c] = (counts[c] || 0) + 1; }
    el.innerHTML = sw(WX_CLASSES.map(([k, l, c]) => [c || COL.UNK, l, counts[k] || 0]));
  } else {
    for (const s of state.stations) { const m = ageMin(s.time); const i = AGE_BINS.findIndex((b) => m != null && m < b[0]); counts[i] = (counts[i] || 0) + 1; }
    el.innerHTML = `<div class="cap">Time since observation</div>` + sw(AGE_BINS.map(([, l, c], i) => [c, l, counts[i] || 0]));
  }
}
function setLayer(layer, focus) {
  if (!LAYERS.includes(layer)) return;
  state.layer = layer;
  store.set('layer', layer);
  for (const b of $$('#layer-seg button')) {
    const on = b.dataset.layer === layer;
    b.setAttribute('aria-checked', on);
    b.tabIndex = on ? 0 : -1;
    if (on && focus) b.focus();
  }
  restyleAll(true);
  renderLegend();
}
$('#layer-seg').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) setLayer(b.dataset.layer); });
$('#layer-seg').addEventListener('keydown', (e) => {
  const i = LAYERS.indexOf(state.layer);
  if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); setLayer(LAYERS[(i + 1) % LAYERS.length], true); }
  if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { e.preventDefault(); setLayer(LAYERS[(i + LAYERS.length - 1) % LAYERS.length], true); }
});

/* ------------------------------------------------------------------ right now */
function topN(arr, key, n, dir = -1) {
  return arr.filter((s) => isNum(key(s))).sort((a, b) => dir * (key(a) - key(b))).slice(0, n);
}
function renderRightNow() {
  const all = state.stations;
  const counts = { VFR: 0, MVFR: 0, IFR: 0, LIFR: 0, UNK: 0 };
  for (const s of all) counts[s.cat || 'UNK']++;
  const total = all.length || 1;
  $('#catbar').innerHTML = [...CATS, 'UNK'].map((c) => `<span style="width:${(counts[c] / total) * 100}%;background:${catColour(c)}" title="${c === 'UNK' ? 'Unknown' : c}: ${counts[c]}"></span>`).join('');
  $('#catchips').classList.toggle('filtering', !!state.catFilter);
  $('#catchips').innerHTML = [...CATS, 'UNK'].map((c) => `<button type="button" class="catchip" data-cat="${c}" aria-pressed="${state.catFilter === c}" title="Show only ${c === 'UNK' ? 'unknown' : c} stations on the map">
      <span class="k"><i style="background:${catColour(c)}"></i>${c === 'UNK' ? '?' : c}</span><span class="n">${fmtInt(counts[c])}</span></button>`).join('');
  const ifrPlus = counts.IFR + counts.LIFR;
  $('#side-summary').textContent = `${fmtInt(ifrPlus)} IFR/LIFR · ${fmtInt(all.filter((s) => wxClass(s) === 'ts').length)} storms`;
  renderRnList();
}
function setCatFilter(cat) {
  state.catFilter = cat;
  if (state.catFilter && state.layer !== 'cat') setLayer('cat'); else { restyleAll(true); renderLegend(); }
  renderRightNow();
}
$('#legend').addEventListener('click', (e) => { if (e.target.closest('#clear-filter')) { setCatFilter(null); toast('Showing all stations'); } });
$('#catchips').addEventListener('click', (e) => {
  const b = e.target.closest('.catchip');
  if (!b) return;
  setCatFilter(state.catFilter === b.dataset.cat ? null : b.dataset.cat);
  toast(state.catFilter ? `Showing only ${state.catFilter === 'UNK' ? 'unknown-category' : state.catFilter} stations` : 'Showing all stations');
});

function renderRnList() {
  const fresh = state.stations.filter((s) => (ageMin(s.time) ?? 1e9) < STALE_MIN);
  let items = [];
  let more = '';
  const t = state.rnTab;
  if (t === 'gust') {
    items = topN(fresh, (s) => s.gust, 10).map((s) => [s, `${U.wind(s.gust)}`, windText(s)]);
  } else if (t === 'ts') {
    const ts = fresh.filter((s) => wxClass(s) === 'ts').sort((a, b) => (b.wx.join().includes('+') - a.wx.join().includes('+')) || a.id.localeCompare(b.id));
    items = ts.slice(0, 40).map((s) => [s, s.wx.filter((w) => /TS/.test(w)).join(' '), s.wx_text]);
    if (ts.length > 40) more = `+ ${ts.length - 40} more — switch to the Weather layer to see all`;
    else if (ts.length) more = `${ts.length} station${ts.length > 1 ? 's' : ''} reporting thunderstorms`;
  } else if (t === 'cold') {
    items = topN(fresh.filter((s) => s.temp > -90), (s) => s.temp, 10, 1).map((s) => [s, U.temp(s.temp), '']);
  } else if (t === 'hot') {
    items = topN(fresh.filter((s) => s.temp < 60), (s) => s.temp, 10).map((s) => [s, U.temp(s.temp), '']);
  } else if (t === 'vis') {
    items = fresh.filter((s) => isNum(s.vis_m)).sort((a, b) => a.vis_m - b.vis_m || (a.ceil_ft ?? 1e6) - (b.ceil_ft ?? 1e6)).slice(0, 10)
      .map((s) => [s, U.visShort(s.vis_m), s.wx_text]);
  }
  for (const b of $$('#rn-tabs button')) { const on = b.dataset.tab === t; b.setAttribute('aria-selected', on); b.tabIndex = on ? 0 : -1; b.id = `rn-tab-${b.dataset.tab}`; }
  $('#rn-list').setAttribute('aria-labelledby', `rn-tab-${t}`);
  $('#rn-list').innerHTML = items.length
    ? items.map(([s, val, extra]) => `<li><button type="button" class="rn-item" data-id="${esc(s.id)}" title="${esc(extra || s.name)}">
        <span class="dot" style="background:${catColour(s.cat)}"></span><span class="id">${esc(s.id)}</span>
        <span class="nm">${esc(s.name || '')}${s.country ? ` · ${esc(s.country)}` : ''}</span><span class="val">${esc(val)}</span></button></li>`).join('')
      + (more ? `<li class="rn-more">${esc(more)}</li>` : '')
    : `<li class="rn-empty">${t === 'ts' ? 'No thunderstorms reported right now.' : 'Nothing to show.'}</li>`;
}
$('#rn-tabs').addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) { state.rnTab = b.dataset.tab; renderRnList(); } });
$('#rn-tabs').addEventListener('keydown', (e) => {
  const tabs = $$('#rn-tabs button').map((b) => b.dataset.tab);
  const i = tabs.indexOf(state.rnTab);
  let j = null;
  if (e.key === 'ArrowRight') j = (i + 1) % tabs.length;
  if (e.key === 'ArrowLeft') j = (i + tabs.length - 1) % tabs.length;
  if (j != null) { e.preventDefault(); state.rnTab = tabs[j]; renderRnList(); $(`#rn-tab-${tabs[j]}`).focus(); }
});
$('.side').addEventListener('click', (e) => {
  const b = e.target.closest('[data-id]');
  if (b) { openStation(b.dataset.id, { fly: true }); if (narrowMQ.matches) setSide(false); }
});
function setSide(open) {
  $('#side-toggle').setAttribute('aria-expanded', open);
  $('#side-body').hidden = !open;
  if (!narrowMQ.matches) store.set('sideOpen', open);
}
$('#side-toggle').addEventListener('click', () => setSide($('#side-toggle').getAttribute('aria-expanded') !== 'true'));
setSide(narrowMQ.matches ? false : store.get('sideOpen', true) !== false);

/* ------------------------------------------------------------------ favourites */
function saveFavs() { store.set('favs', [...state.favs]); }
function renderFavs() {
  const list = [...state.favs].map((id) => state.byId.get(id) || { id, missing: true });
  $('#fav-list').innerHTML = list.length
    ? list.map((s) => `<li><button type="button" class="fav-item" data-id="${esc(s.id)}" ${s.missing ? 'disabled' : ''}>
        <span class="dot" style="background:${catColour(s.cat)}"></span><span class="id">${esc(s.id)}</span>
        <span class="nm">${s.missing ? 'no current report' : esc(s.name || '')}</span>
        <span class="val">${s.missing ? '' : `${esc(s.cat || '—')} · ${esc(U.temp(s.temp))}`}</span></button></li>`).join('')
    : `<li class="fav-empty">Star a station to pin it here.</li>`;
}
function toggleFav(id) {
  if (state.favs.has(id)) state.favs.delete(id); else state.favs.add(id);
  saveFavs();
  renderFavs();
  const b = $('#star-btn');
  if (b) { const on = state.favs.has(id); b.setAttribute('aria-pressed', on); b.title = on ? 'Remove from favourites' : 'Add to favourites'; }
  toast(state.favs.has(id) ? `${id} added to favourites` : `${id} removed from favourites`);
}

/* ------------------------------------------------------------------ units */
function renderUnitButtons() {
  for (const seg of $$('#units-pop .seg')) {
    for (const b of $$('button', seg)) b.setAttribute('aria-pressed', state.units[seg.dataset.unit] === b.dataset.v);
  }
}
$('#units-pop').addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  state.units[b.closest('.seg').dataset.unit] = b.dataset.v;
  store.set('units', state.units);
  renderUnitButtons();
  renderLegend(); renderRnList(); renderFavs();
  if (state.selected) renderStation(state.selected, { keepScroll: true });
});
function setUnitsPop(open) {
  $('#units-pop').hidden = !open;
  $('#units-btn').setAttribute('aria-expanded', open);
}
$('#units-btn').addEventListener('click', (e) => { e.stopPropagation(); setUnitsPop($('#units-pop').hidden); });
document.addEventListener('click', (e) => { if (!e.target.closest('#units-menu')) setUnitsPop(false); });
renderUnitButtons();

/* ------------------------------------------------------------------ search */
const input = $('#search-input');
const listEl = $('#search-list');
let results = [];
let active = -1;
function searchStations(q) {
  q = q.trim().normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  if (!q) return [];
  const Q = q.toUpperCase();
  const out = [];
  for (const s of state.stations) {
    let sc = 0;
    if (s.id === Q) sc = 100;
    else if (s.iata && s.iata === Q) sc = 90;
    else if (s.id.startsWith(Q)) sc = 70;
    else if (s.iata && s.iata.startsWith(Q)) sc = 60;
    else if (s._name.startsWith(q)) sc = 50;
    else if (s._name.includes(' ' + q) || s._name.includes('/' + q)) sc = 40;
    else if (q.length >= 3 && s._q.includes(q)) sc = 20;
    if (sc) out.push([sc, s]);
  }
  out.sort((a, b) => b[0] - a[0] || a[1].id.localeCompare(b[1].id));
  return out.slice(0, 8).map((x) => x[1]);
}
function renderResults() {
  const open = document.activeElement === input && input.value.trim() !== '';
  listEl.hidden = !open;
  input.setAttribute('aria-expanded', open);
  if (!open) return;
  if (!results.length) {
    listEl.innerHTML = `<li class="empty" role="option" aria-disabled="true">No station matches “${esc(input.value.trim())}”</li>`;
    input.removeAttribute('aria-activedescendant');
    return;
  }
  listEl.innerHTML = results.map((s, i) => `<li role="option" id="opt-${i}" data-id="${esc(s.id)}" aria-selected="${i === active}">
      <span class="dot" style="width:9px;height:9px;border-radius:50%;background:${catColour(s.cat)}"></span>
      <span><span class="sid">${esc(s.id)}</span>${s.iata ? `<span class="siata">${esc(s.iata)}</span>` : ''}</span>
      <span class="sname">${esc(s.name || '')}</span><span class="scc">${flag(s.country)} ${esc(s.country || '')}</span></li>`).join('');
  if (active >= 0) { input.setAttribute('aria-activedescendant', `opt-${active}`); $(`#opt-${active}`)?.scrollIntoView({ block: 'nearest' }); }
  else input.removeAttribute('aria-activedescendant');
}
input.addEventListener('input', () => { results = searchStations(input.value); active = results.length ? 0 : -1; renderResults(); });
input.addEventListener('focus', () => { results = searchStations(input.value); renderResults(); });
input.addEventListener('blur', () => setTimeout(renderResults, 150));
input.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowDown') { e.preventDefault(); if (results.length) { active = (active + 1) % results.length; renderResults(); } }
  else if (e.key === 'ArrowUp') { e.preventDefault(); if (results.length) { active = (active - 1 + results.length) % results.length; renderResults(); } }
  else if (e.key === 'Enter') {
    e.preventDefault();
    const s = results[active >= 0 ? active : 0];
    if (s) pick(s.id);
  } else if (e.key === 'Escape') {
    if (input.value) { input.value = ''; results = []; renderResults(); } else input.blur();
  }
});
listEl.addEventListener('mousedown', (e) => { const li = e.target.closest('li[data-id]'); if (li) { e.preventDefault(); pick(li.dataset.id); } });
function pick(id) {
  input.value = '';
  results = [];
  input.blur();
  renderResults();
  openStation(id, { fly: true, focus: true });
}
document.addEventListener('keydown', (e) => {
  if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)) { e.preventDefault(); input.focus(); }
  if (e.key === 'Escape') {
    if (!$('#units-pop').hidden) { setUnitsPop(false); $('#units-btn').focus(); return; }
    if (state.selected && document.activeElement !== input) closeStation();
  }
});

/* ------------------------------------------------------------------ near me */
function distKm(a, b, c, d) {
  const r = Math.PI / 180;
  const x = Math.sin(((c - a) * r) / 2) ** 2 + Math.cos(a * r) * Math.cos(c * r) * Math.sin(((d - b) * r) / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(x));
}
$('#near-btn').addEventListener('click', () => {
  const btn = $('#near-btn');
  if (!navigator.geolocation) { toast('Geolocation is not available in this browser'); return; }
  btn.setAttribute('aria-busy', 'true');
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      btn.removeAttribute('aria-busy');
      const { latitude: la, longitude: lo } = pos.coords;
      let best = null, bd = Infinity;
      for (const s of state.stations) { const d = distKm(la, lo, s.lat, s.lon); if (d < bd) { bd = d; best = s; } }
      if (meMarker) meMarker.remove();
      meMarker = L.marker([la, lo], { icon: L.divIcon({ className: 'me-dot', iconSize: [16, 16] }), interactive: false, keyboard: false }).addTo(map);
      if (best) { openStation(best.id, { fly: true, focus: true }); toast(`Nearest station: ${best.id}, ${Math.round(bd)} km away`); }
    },
    (err) => { btn.removeAttribute('aria-busy'); toast(err.code === 1 ? 'Location permission denied' : 'Could not determine your location'); },
    { enableHighAccuracy: false, timeout: 15000, maximumAge: 300000 },
  );
});

/* ------------------------------------------------------------------ station panel */
const panel = $('#station');
const body = $('#station-body');

// screen-space shift from the map centre to where a focused station should sit (clear of overlays)
function panelOffset() {
  if (panel.hidden) return [0, 0];
  const size = map.getSize();
  if (narrowMQ.matches) {
    const top = 64; // below the collapsed "Right now" card
    const bottom = size.y - panel.offsetHeight;
    return [0, Math.min(0, (top + bottom) / 2 - size.y / 2)];
  }
  return [-(panel.offsetWidth + 16) / 2, 0];
}
function focusOn(s, zoom) {
  const z = zoom ?? Math.max(map.getZoom(), 7);
  const [dx, dy] = panelOffset();
  const p = map.project([s.lat, s.lon], z).subtract([dx, dy]);
  const target = map.unproject(p, z);
  const far = map.getCenter().distanceTo(target) > 3e6 || Math.abs(map.getZoom() - z) > 4;
  map.flyTo(target, z, { duration: far ? 1.6 : 0.8 });
}
function ensureVisible(s) {
  const pt = map.latLngToContainerPoint([s.lat, s.lon]);
  const size = map.getSize();
  let dx = 0, dy = 0;
  if (narrowMQ.matches) {
    const bottom = size.y - panel.offsetHeight;
    if (pt.y > bottom - 24 || pt.y < 64) dy = pt.y - (64 + bottom) / 2;
  } else {
    const right = size.x - panel.offsetWidth - 32;
    if (pt.x > right - 20) dx = pt.x - right / 2;
  }
  if (dx || dy) map.panBy([dx, dy]);
}

function openStation(id, { fly = false, push = true, focus = false } = {}) {
  id = String(id || '').toUpperCase();
  const s = state.byId.get(id);
  if (!s) { if (state.stations.length) toast(`No current report for ${id}`); return; }
  state.selected = id;
  panel.hidden = false;
  document.body.classList.add('panel-open');
  renderStation(id);
  panel.scrollTop = 0;
  if (selRing) selRing.remove();
  selRing = L.marker([s.lat, s.lon], { icon: L.divIcon({ className: 'sel-ring', iconSize: [22, 22] }), interactive: false, keyboard: false, zIndexOffset: 1000 }).addTo(map);
  if (push && location.hash.slice(1).toUpperCase() !== id) history.pushState({ id }, '', `#${id}`);
  document.title = `${id}${s.name ? ` · ${s.name}` : ''} — MetarParse Live`;
  if (fly) focusOn(s); else ensureVisible(s);
  if (focus) $('#sp-name')?.focus({ preventScroll: true });
}
function closeStation({ push = true } = {}) {
  if (!state.selected) return;
  state.selected = null;
  panel.hidden = true;
  document.body.classList.remove('panel-open');
  if (selRing) { selRing.remove(); selRing = null; }
  document.title = 'MetarParse Live';
  if (push && location.hash) history.pushState(null, '', location.pathname + location.search);
}
function applyHash() {
  const id = decodeURIComponent(location.hash.slice(1)).trim().toUpperCase();
  if (id && state.byId.has(id)) { if (id !== state.selected) openStation(id, { fly: true, push: false }); }
  else if (!id) closeStation({ push: false });
  else if (state.stations.length) toast(`No current report for ${id}`);
}
window.addEventListener('popstate', applyHash);
window.addEventListener('hashchange', applyHash);

const ARROW = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 19 20 12 16 5 20Z"/></svg>';
const ICON = {
  close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  star: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/></svg>',
  copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/></svg>',
  link: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>',
};

function windRow(s) {
  if (!isNum(s.wspd)) return '<span class="muted">Not reported</span>';
  if (s.wspd === 0 && !isNum(s.gust)) return '<span class="main">Calm</span>';
  const hasDir = !s.vrb && isNum(s.wdir);
  const arrow = hasDir ? `<span class="wind-arrow" title="Wind from ${s.wdir}°"><span style="display:grid;transform:rotate(${(s.wdir + 180) % 360}deg)">${ARROW}</span></span>` : '';
  const dir = hasDir ? `${String(s.wdir).padStart(3, '0')}° ${compass(s.wdir)}` : 'Variable';
  return `<div class="wind-line">${arrow}<div><span class="main">${dir} · ${U.wind(s.wspd)}</span>
    ${isNum(s.gust) ? ` <span class="gust">gusts ${U.wind(s.gust)}</span>` : ''}
    <div class="alt">${esc(U.windAlt(s.wspd))}${s.wvar ? ` · varying ${String(s.wvar[0]).padStart(3, '0')}°–${String(s.wvar[1]).padStart(3, '0')}°` : ''}</div></div></div>`;
}
function compass(d) { return ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'][Math.round(d / 22.5) % 16]; }
function cloudsHtml(clouds, cavok) {
  if (cavok) return '<span class="main">CAVOK</span> <span class="alt">no cloud below 5,000 ft</span>';
  if (!clouds || !clouds.length) return '<span class="muted">None reported</span>';
  return `<div class="clouds">${clouds.map(([c, h, t]) => `<span class="cloud${t ? ' cb' : ''}" title="${esc(coverName(c))}">${esc(c)} ${isNum(h) ? `${fmtInt(h)} ft` : ''}${t ? ` ${esc(t)}` : ''}</span>`).join('')}</div>`;
}
function coverName(c) {
  return { FEW: 'Few (1–2 oktas)', SCT: 'Scattered (3–4 oktas)', BKN: 'Broken (5–7 oktas)', OVC: 'Overcast (8 oktas)', VV: 'Vertical visibility (sky obscured)', SKC: 'Sky clear', CLR: 'Clear below 12,000 ft', NSC: 'No significant cloud', NCD: 'No cloud detected' }[c] || c;
}
function catBadge(cat, s) {
  if (!cat) return `<div class="catbadge unk"><span class="big">?</span><span class="what"><b>Unknown category</b>Not enough ceiling / visibility data</span></div>`;
  const why = [];
  if (isNum(s.ceil_ft)) why.push(`ceiling ${fmtInt(s.ceil_ft)} ft`); else if (s.cavok || s.clouds) why.push('no ceiling');
  if (isNum(s.vis_m)) why.push(`vis ${U.visShort(s.vis_m)}`);
  return `<div class="catbadge" style="background:${catColour(cat)}" title="${esc(CAT_INFO[cat][1])}"><span class="big">${cat}</span>
    <span class="what"><b>${CAT_INFO[cat][0]}</b>${esc(why.join(' · ') || CAT_INFO[cat][1])}</span></div>`;
}

function renderStation(id, { keepScroll = false } = {}) {
  const s = state.byId.get(id);
  if (!s) return;
  const st = panel.scrollTop;
  const age = ageMin(s.time);
  const fav = state.favs.has(s.id);
  const tokens = explainTokens(s.raw || '');
  const pressure = isNum(s.qnh) ? `<span class="main">${Math.round(s.qnh)} hPa</span> <span class="alt">· ${(s.qnh * 0.0295300).toFixed(2)} inHg</span>` : '<span class="muted">Not reported</span>';
  body.innerHTML = `
    <div class="sp-head">
      <div class="sp-title">
        <div class="sp-ids"><span class="icao">${esc(s.id)}</span>${s.iata ? `<span class="iata" title="IATA code">${esc(s.iata)}</span>` : ''}
          ${s.country ? `<span>${flag(s.country)} ${esc(countryName(s.country))}</span>` : ''}</div>
        <h2 id="sp-name" tabindex="-1">${esc(s.name || s.id)}</h2>
        <div class="sp-sub">${esc(s.type || 'METAR')} · ${esc(hhmmZ(s.time))} · <span class="${age > 90 ? 'age-old' : ''}" data-ago="${esc(s.time)}">${esc(ago(s.time))}</span>${isNum(s.elev_m) ? ` · elev ${fmtInt(s.elev_m)} m / ${fmtInt(s.elev_m * 3.28084)} ft` : ''}</div>
      </div>
      <div class="sp-actions">
        <button class="btn icon-only star-btn" id="star-btn" type="button" aria-pressed="${fav}" title="${fav ? 'Remove from favourites' : 'Add to favourites'}" aria-label="Favourite ${esc(s.id)}">${ICON.star}</button>
        <button class="btn icon-only" id="close-btn" type="button" title="Close (Esc)" aria-label="Close station details">${ICON.close}</button>
      </div>
    </div>
    ${catBadge(s.cat, s)}
    <dl class="rows">
      <dt>Wind</dt><dd>${windRow(s)}</dd>
      <dt>Visibility</dt><dd>${s.cavok ? '<span class="main">10 km or more</span> <span class="alt">· CAVOK</span>' : `<span class="main">${esc(U.vis(s.vis_m))}</span> <span class="alt">· ${esc(U.visAlt(s.vis_m))}</span>`}</dd>
      <dt>Weather</dt><dd>${s.wx_text ? `<span class="main">${esc(cap(s.wx_text))}</span> <span class="alt">· ${esc((s.wx || []).join(' '))}</span>` : '<span class="muted">No significant weather</span>'}</dd>
      <dt>Clouds</dt><dd>${cloudsHtml(s.clouds, s.cavok)}<div class="alt" style="margin-top:3px">${isNum(s.ceil_ft) ? `Ceiling <b>${fmtInt(s.ceil_ft)} ft</b> AGL` : 'No ceiling'}</div></dd>
      <dt>Temperature</dt><dd><span class="main">${esc(U.temp(s.temp))}</span> <span class="alt">· dew point ${esc(U.temp(s.dew))}${isNum(s.rh) ? ` · RH ${Math.round(s.rh)}%` : ''}</span></dd>
      <dt>Pressure</dt><dd>${pressure}${isNum(s.qnh) ? ' <span class="alt">QNH</span>' : ''}</dd>
    </dl>

    <section class="sec" aria-labelledby="raw-h">
      <div class="sec-h"><h3 id="raw-h">Raw ${esc(s.type || 'METAR')}</h3>
        <div style="display:flex;gap:6px">
          <button class="btn small" type="button" id="link-btn" title="Copy a link to this station">${ICON.link}<span>Link</span></button>
          <button class="btn small" type="button" id="copy-btn">${ICON.copy}<span>Copy raw</span></button>
        </div></div>
      <p class="raw" id="raw-metar">${tokens.map((t, i) => `<span class="tok${t.cls ? ' ' + t.cls : ''}" tabindex="0" data-i="${i}">${esc(t.text)}</span>`).join(' ')}</p>
      <div class="explain hint" id="explain" aria-live="polite">Hover or tap any group above to see what it means.</div>
      ${s.issues && s.issues.length ? `<div class="issues"><b>MetarParse flagged ${s.issues.length} issue${s.issues.length > 1 ? 's' : ''}</b><ul>${s.issues.map((x) => `<li><code>${esc(x)}</code></li>`).join('')}</ul></div>` : ''}
    </section>

    ${s.taf ? `<section class="sec" aria-labelledby="taf-h"><div class="sec-h"><h3 id="taf-h">Forecast (TAF)</h3><span class="meta" id="taf-meta"></span></div><div id="taf-box"><div class="tl-detail hint">Loading TAF…</div></div></section>` : ''}
  `;
  if (keepScroll) panel.scrollTop = st;

  $('#close-btn').onclick = () => closeStation();
  $('#star-btn').onclick = () => toggleFav(s.id);
  $('#copy-btn').onclick = () => copyText(s.raw || '', 'Raw METAR copied');
  $('#link-btn').onclick = () => copyText(`${location.origin}${location.pathname}#${s.id}`, 'Link copied');
  const ex = $('#explain');
  const raw = $('#raw-metar');
  const show = (el) => {
    const t = tokens[+el.dataset.i];
    if (!t) return;
    $$('.tok.on', raw).forEach((x) => x.classList.remove('on'));
    el.classList.add('on');
    ex.classList.remove('hint');
    ex.innerHTML = `<code>${esc(t.text)}</code>${esc(t.desc)}`;
  };
  raw.addEventListener('mouseover', (e) => { const el = e.target.closest('.tok'); if (el) show(el); });
  raw.addEventListener('focusin', (e) => { const el = e.target.closest('.tok'); if (el) show(el); });
  raw.addEventListener('click', (e) => { const el = e.target.closest('.tok'); if (el) show(el); });

  if (s.taf) {
    loadTafs().then((tafs) => {
      if (state.selected !== id) return;
      const taf = tafs[id];
      const box = $('#taf-box');
      if (!box) return;
      if (!taf) { box.innerHTML = '<div class="tl-detail hint">No TAF available right now.</div>'; return; }
      $('#taf-meta').textContent = `issued ${ago(taf.issued)} · valid ${dayHHZ(taf.from)} – ${dayHHZ(taf.to)}`;
      renderTaf(taf, box);
    }).catch(() => { const box = $('#taf-box'); if (box) box.innerHTML = '<div class="tl-detail hint">Could not load forecasts.</div>'; });
  }
}

/* ------------------------------------------------------------------ TAF timeline */
const KIND_LABEL = { BASE: 'Initial conditions', FM: 'From', BECMG: 'Becoming', TEMPO: 'Temporarily', INTER: 'Intermittently', PROB: 'Probability' };
function groupSummary(g) {
  const parts = [];
  if (isNum(g.wspd)) {
    if (g.wspd === 0 && !g.gust) parts.push('wind calm');
    else parts.push(`wind ${g.vrb || !isNum(g.wdir) ? 'variable' : String(g.wdir).padStart(3, '0') + '°'} ${U.wind(g.wspd)}${isNum(g.gust) ? ` gusting ${U.wind(g.gust)}` : ''}`);
  }
  if (g.cavok) parts.push('CAVOK');
  else if (isNum(g.vis_m)) parts.push(`visibility ${U.visShort(g.vis_m)}`);
  if (g.wx_text) parts.push(g.wx_text);
  else if (g.wx && g.wx.length) parts.push(g.wx.join(' '));
  if (g.clouds && g.clouds.length) parts.push(g.clouds.map(([c, h, t]) => `${c}${isNum(h) ? ' ' + fmtInt(h) + ' ft' : ''}${t ? ' ' + t : ''}`).join(', '));
  if (isNum(g.ceil_ft)) parts.push(`ceiling ${fmtInt(g.ceil_ft)} ft`);
  return cap(parts.join(' · ')) || 'No change to stated elements';
}

function renderTaf(taf, box) {
  const t0 = Date.parse(taf.from), t1 = Date.parse(taf.to);
  if (!(t1 > t0)) { box.innerHTML = `<pre class="raw">${esc(taf.raw)}</pre>`; return; }
  const groups = (taf.groups || []).map((g, i) => ({ ...g, i, a: Date.parse(g.from) || t0, b: Date.parse(g.to) || t1 }));
  const prevailing = groups.filter((g) => ['BASE', 'FM', 'BECMG'].includes(g.kind)).sort((x, y) => x.a - y.a || (x.kind === 'BASE' ? -1 : 1));
  const temp = groups.filter((g) => !['BASE', 'FM', 'BECMG'].includes(g.kind));

  // prevailing segments: each lasts until the next prevailing change; null cat inherits
  const segs = [];
  let lastCat = null;
  prevailing.forEach((g, k) => {
    const next = prevailing[k + 1];
    const end = next ? next.a : t1;
    const cat = g.cat || lastCat;
    lastCat = cat;
    segs.push({ g, a: Math.max(g.a, t0), b: Math.min(Math.max(end, g.a), t1), cat, inherited: !g.cat, trans: g.kind === 'BECMG' ? Math.min(g.b, end) : null });
  });
  // temporary lanes
  const lanes = [];
  for (const g of temp.sort((x, y) => x.a - y.a)) {
    let li = lanes.findIndex((end) => end <= g.a);
    if (li < 0) { li = lanes.length; lanes.push(0); }
    lanes[li] = g.b;
    g.lane = li;
  }

  const W = Math.max(280, box.clientWidth || 340);
  const padL = 2, padR = 2, axisH = 30, rowH = 28, laneH = 20, gap = 8;
  const X = (t) => padL + ((clamp(t, t0, t1) - t0) / (t1 - t0)) * (W - padL - padR);
  const yP = axisH + 12;
  const yT = yP + rowH + gap + 12;
  const yEnd = yT + Math.max(1, lanes.length) * (laneH + 4);
  const H = yEnd + 14;

  const spanH = (t1 - t0) / 3.6e6;
  const step = spanH / (W / 46) <= 1 ? 1 : spanH / (W / 46) <= 2 ? 2 : spanH / (W / 46) <= 3 ? 3 : 6;
  const ticks = [];
  const first = Math.ceil(t0 / 3.6e6) * 3.6e6;
  for (let t = first; t <= t1; t += 3.6e6) {
    const h = new Date(t).getUTCHours();
    if (h % step === 0) ticks.push([t, h]);
  }
  const cats = [...CATS, 'UNK'];
  const patterns = cats.map((c) => `<pattern id="tlh-${c}" patternUnits="userSpaceOnUse" width="7" height="7" patternTransform="rotate(45)">
      <rect width="7" height="7" fill="${catColour(c === 'UNK' ? null : c)}" fill-opacity=".22"/><rect width="3" height="7" fill="${catColour(c === 'UNK' ? null : c)}" fill-opacity=".85"/></pattern>`).join('');
  const label = (x0, x1, txt, y, dark) => (x1 - x0 > txt.length * 7 + 8 ? `<text x="${(x0 + x1) / 2}" y="${y}" text-anchor="middle" class="${dark ? 'dark' : ''}">${esc(txt)}</text>` : '');

  let svg = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="TAF timeline from ${esc(dayHHZ(taf.from))} to ${esc(dayHHZ(taf.to))}"><defs>${patterns}</defs><g class="axis">`;
  for (const [t, h] of ticks) {
    const x = X(t);
    const isDay = h === 0;
    svg += `<line x1="${x}" x2="${x}" y1="${axisH - 4}" y2="${axisH}" stroke-opacity="${isDay ? 0.9 : 0.5}"/><line x1="${x}" x2="${x}" y1="${yP}" y2="${yP + rowH}" stroke-opacity="${isDay ? 0.9 : 0.35}"/><line x1="${x}" x2="${x}" y1="${yT}" y2="${yEnd}" stroke-opacity="${isDay ? 0.9 : 0.35}"/>`;
    if (x > 10 && x < W - 10) svg += `<text x="${x}" y="${axisH - 8}" text-anchor="middle" class="${isDay ? 'day' : ''}">${isDay ? new Date(t).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', timeZone: 'UTC' }) : String(h).padStart(2, '0') + 'Z'}</text>`;
  }
  svg += `</g><text class="rowlab" x="${padL}" y="${yP - 3}">Prevailing</text>`;
  if (temp.length) svg += `<text class="rowlab" x="${padL}" y="${yT - 3}">Temporary / probability</text>`;
  for (const sg of segs) {
    const x0 = X(sg.a), x1 = X(sg.b);
    if (x1 - x0 < 0.5) continue;
    const c = catColour(sg.cat);
    const tr = sg.trans ? X(sg.trans) : null;
    svg += `<g class="bar" tabindex="0" data-i="${sg.g.i}" role="button" aria-label="${esc(sg.g.kind)} ${esc(hhmmZ(sg.g.from))}: ${esc(sg.cat || 'unknown')}">
      <rect class="hit" x="${x0 + 0.5}" y="${yP}" width="${Math.max(1, x1 - x0 - 1)}" height="${rowH}" rx="5" fill="${c}" fill-opacity="${sg.inherited ? 0.75 : 1}"/>
      ${tr && tr > x0 + 2 ? `<rect x="${x0 + 0.5}" y="${yP}" width="${tr - x0}" height="${rowH}" rx="5" fill="url(#tlh-${sg.cat || 'UNK'})" pointer-events="none"/>` : ''}
      ${label(x0, x1, `${sg.g.kind === 'BASE' ? '' : sg.g.kind + ' '}${sg.cat || '?'}`, yP + rowH / 2 + 4, !sg.cat)}</g>`;
  }
  for (const g of temp) {
    const x0 = X(g.a), x1 = X(g.b);
    const y = yT + g.lane * (laneH + 4);
    const kind = g.kind === 'PROB' || g.prob ? `PROB${g.prob || ''}${g.kind !== 'PROB' ? ' ' + g.kind : ''}` : g.kind;
    svg += `<g class="bar" tabindex="0" data-i="${g.i}" role="button" aria-label="${esc(kind)} ${esc(hhmmZ(g.from))}–${esc(hhmmZ(g.to))}: ${esc(g.cat || 'unknown')}">
      <rect class="hit" x="${x0 + 0.5}" y="${y}" width="${Math.max(2, x1 - x0 - 1)}" height="${laneH}" rx="4" fill="url(#tlh-${g.cat || 'UNK'})"/>
      ${label(x0, x1, `${kind} ${g.cat || (g.wx || []).join(' ')}`.trim(), y + laneH / 2 + 4, true)}</g>`;
  }
  const now = Date.now();
  if (now > t0 && now < t1) {
    const x = X(now);
    svg += `<g class="now"><line x1="${x}" x2="${x}" y1="${axisH - 2}" y2="${yEnd}"/><text x="${clamp(x, 14, W - 14)}" y="${H - 2}" text-anchor="middle">now</text></g>`;
  }
  svg += '</svg>';

  box.innerHTML = `<div class="tl">${svg}</div>
    <div class="tl-detail hint" id="tl-detail" aria-live="polite">Hover or tap a bar for details.</div>
    <div class="tl-key"><span><i class="solid"></i>Prevailing (BASE / FM / BECMG)</span><span><i class="hatch"></i>Temporary or probable (TEMPO / PROB / INTER), and BECMG transition</span></div>
    <details class="rawtaf"><summary>Raw TAF</summary><p class="raw">${explainTokens(taf.raw, { taf: true }).map((t) => `<span class="tok${t.cls ? ' ' + t.cls : ''}" title="${esc(t.desc)}">${esc(t.text)}</span>`).join(' ')}</p></details>`;
  const detail = $('#tl-detail', box);
  const show = (el) => {
    const g = groups[+el.dataset.i];
    if (!g) return;
    $$('.bar.on', box).forEach((b) => b.classList.remove('on'));
    el.classList.add('on');
    detail.classList.remove('hint');
    const kind = g.prob ? `PROB${g.prob}${g.kind !== 'PROB' ? ' ' + g.kind : ''}` : g.kind;
    const seg = segs.find((x) => x.g === g);
    const cat = g.cat || (seg && seg.cat);
    detail.innerHTML = `<div><span class="k">${esc(kind)}</span> <span class="muted">${esc(KIND_LABEL[g.kind] || '')} · ${esc(dayHHZ(g.from))} – ${esc(g.to.slice(0, 10) === g.from.slice(0, 10) ? hhmmZ(g.to) : dayHHZ(g.to))}</span>
      ${cat ? `<span class="pill" style="background:${catColour(cat)}">${cat}${!g.cat ? ' (unchanged)' : ''}</span>` : ''}</div>
      <div>${esc(groupSummary(g))}</div><div class="raw-g">${esc(g.raw || '')}</div>`;
  };
  const svgEl = $('svg', box);
  svgEl.addEventListener('mouseover', (e) => { const b = e.target.closest('.bar'); if (b) show(b); });
  svgEl.addEventListener('focusin', (e) => { const b = e.target.closest('.bar'); if (b) show(b); });
  svgEl.addEventListener('click', (e) => { const b = e.target.closest('.bar'); if (b) show(b); });
  svgEl.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { const b = e.target.closest('.bar'); if (b) { e.preventDefault(); show(b); } } });
  // show the group in force now, if any
  const cur = segs.find((x) => now >= x.a && now < x.b);
  if (cur) { const el = $(`.bar[data-i="${cur.g.i}"]`, box); if (el) show(el); }
}

/* ------------------------------------------------------------------ refresh + ticking clocks */
function tick() {
  renderHeader();
  for (const el of $$('[data-ago]')) el.textContent = ago(el.dataset.ago);
}
setInterval(tick, 30000);
let lastCheck = Date.now();
async function refresh() {
  lastCheck = Date.now();
  try {
    const changed = await loadAll(false);
    if (changed) {
      if (state.selected) {
        if (state.byId.has(state.selected)) {
          const s = state.byId.get(state.selected);
          if (selRing) selRing.setLatLng([s.lat, s.lon]);
          renderStation(state.selected, { keepScroll: true });
        }
      }
      toast('Weather updated');
    }
  } catch (e) { console.warn('Refresh failed', e); }
}
setInterval(refresh, REFRESH_MS);
document.addEventListener('visibilitychange', () => { if (!document.hidden && Date.now() - lastCheck > REFRESH_MS) refresh(); });

let resizeT;
window.addEventListener('resize', () => {
  clearTimeout(resizeT);
  resizeT = setTimeout(() => {
    const box = $('#taf-box');
    if (box && state.tafs && state.selected && state.tafs[state.selected]) renderTaf(state.tafs[state.selected], box);
  }, 200);
});

/* ------------------------------------------------------------------ boot */
(async function boot() {
  for (const b of $$('#layer-seg button')) { b.setAttribute('aria-checked', b.dataset.layer === state.layer); b.tabIndex = b.dataset.layer === state.layer ? 0 : -1; }
  renderFavs();
  const loading = $('#loading');
  try {
    await loadAll(true);
    loading.classList.add('gone');
    setTimeout(() => loading.remove(), 400);
    const id = decodeURIComponent(location.hash.slice(1)).trim().toUpperCase();
    if (id) {
      if (state.byId.has(id)) { history.replaceState({ id }, '', `#${id}`); openStation(id, { fly: true, push: false }); }
      else toast(`No current report for ${id}`);
    }
    // warm the TAF cache in the background
    setTimeout(() => loadTafs().catch(() => {}), 2500);
  } catch (e) {
    console.error(e);
    loading.classList.add('err');
    loading.innerHTML = `<span>Could not load weather data.</span><button class="btn" type="button" onclick="location.reload()">Retry</button>`;
  }
})();

/* ------------------------------------------------------------------ METAR / TAF group explainer */
const WX_DESC = { MI: 'shallow', PR: 'partial', BC: 'patches of', DR: 'low drifting', BL: 'blowing', SH: 'showers', TS: 'thunderstorm', FZ: 'freezing' };
const WX_PHEN = {
  DZ: 'drizzle', RA: 'rain', SN: 'snow', SG: 'snow grains', IC: 'ice crystals', PL: 'ice pellets', GR: 'hail', GS: 'small hail / snow pellets',
  UP: 'unknown precipitation', BR: 'mist', FG: 'fog', FU: 'smoke', VA: 'volcanic ash', DU: 'widespread dust', SA: 'sand', HZ: 'haze',
  PY: 'spray', PO: 'dust / sand whirls', SQ: 'squalls', FC: 'funnel cloud', SS: 'sandstorm', DS: 'duststorm', NSW: 'no significant weather',
};
const WX_RE = /^(\+|-|VC)?(MI|PR|BC|DR|BL|SH|TS|FZ)?((?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)*)$/;
function wxPhrase(code) {
  const m = WX_RE.exec(code);
  if (!m || (!m[2] && !m[3])) return null;
  const [, int, desc, ph] = m;
  const phen = (ph.match(/../g) || []).map((p) => WX_PHEN[p]);
  let s;
  if (desc === 'TS') s = phen.length ? `thunderstorm with ${phen.join(' and ')}` : 'thunderstorm';
  else if (desc === 'SH') s = phen.length ? `showers of ${phen.join(' and ')}` : 'showers';
  else if (desc) s = `${WX_DESC[desc]} ${phen.join(' and ')}`.trim();
  else s = phen.join(' and ');
  if (ph === 'FC' && int === '+') s = 'tornado or waterspout';
  else if (int === '-') s = `light ${s}`;
  else if (int === '+') s = `heavy ${s}`;
  else if (int === 'VC') s = `${s} in the vicinity (8–16 km)`;
  else if (phen.length && !['BR', 'FG', 'HZ', 'FU', 'DU', 'SA', 'VA', 'PY', 'PO', 'SQ', 'FC', 'SS', 'DS'].includes(ph) && desc !== 'TS') s = `moderate ${s}`;
  return s;
}
const COVER = { FEW: 'Few clouds (1–2 oktas)', SCT: 'Scattered clouds (3–4 oktas)', BKN: 'Broken clouds (5–7 oktas)', OVC: 'Overcast (8 oktas)', VV: 'Sky obscured, vertical visibility' };
const signedT = (t) => (t.startsWith('M') ? -Number(t.slice(1)) : Number(t));
const dirName = (d) => ({ N: 'north', NE: 'northeast', E: 'east', SE: 'southeast', S: 'south', SW: 'southwest', W: 'west', NW: 'northwest' }[d] || d);
const timeDesc = (dd, hh, mm) => `day ${Number(dd)} of the month, ${hh}:${mm ?? '00'} UTC`;

function explainGroup(tok, ctx) {
  let m;
  const T = tok;
  if (/^\/+$/.test(T)) return 'Group not available (sensor missing or value not reported)';
  if (T === 'METAR') return 'Routine weather observation (METAR), issued every 30 or 60 minutes';
  if (T === 'SPECI') return 'Special observation, issued when conditions change significantly between routine reports';
  if (T === 'TAF') return 'Terminal Aerodrome Forecast — the airport forecast';
  if (T === 'AMD') return 'Amended forecast (replaces the previous TAF)';
  if (T === 'COR') return 'Corrected report';
  if (T === 'AUTO') return 'Fully automated observation — no human observer';
  if (T === 'NIL') return 'Missing report';
  if (T === 'CNL') return 'Cancelled forecast';
  if (T === 'CAVOK') return 'Ceiling and visibility OK: visibility 10 km or more, no cloud below 5,000 ft, no CB/TCU and no significant weather';
  if (T === 'NOSIG') return 'Trend: no significant change expected in the next 2 hours';
  if (T === 'BECMG') return ctx.taf ? 'Becoming: conditions change gradually during the period that follows' : 'Trend: conditions becoming (gradual change) within the next 2 hours';
  if (T === 'TEMPO') return 'Temporarily: fluctuations, each lasting less than an hour, during the period that follows';
  if (T === 'INTER') return 'Intermittently: frequent brief changes, each lasting less than 30 minutes';
  if ((m = /^PROB(\d{2})$/.exec(T))) return `Probability ${m[1]}% that the following conditions occur`;
  if ((m = /^FM(\d{2})(\d{2})(\d{2})$/.exec(T))) return `From ${timeDesc(m[1], m[2], m[3])}: conditions change completely`;
  if ((m = /^(FM|TL|AT)(\d{2})(\d{2})$/.exec(T))) return `${{ FM: 'From', TL: 'Until', AT: 'At' }[m[1]]} ${m[2]}:${m[3]} UTC`;
  if ((m = /^(\d{2})(\d{2})\/(\d{2})(\d{2})$/.exec(T))) return `Valid from ${timeDesc(m[1], m[2])} until ${timeDesc(m[3], m[4])}`;
  if ((m = /^T([XN])(M?\d{2})\/(\d{2})(\d{2})Z$/.exec(T))) return `Forecast ${m[1] === 'X' ? 'maximum' : 'minimum'} temperature ${signedT(m[2])} °C on ${timeDesc(m[3], m[4])}`;
  if ((m = /^(\d{2})(\d{2})(\d{2})Z$/.exec(T))) return `${ctx.taf ? 'Issued' : 'Observed'} on ${timeDesc(m[1], m[2], m[3])}`;
  if (ctx.idx <= 2 && /^[A-Z][A-Z0-9]{3}$/.test(T) && !ctx.seenStation) { ctx.seenStation = true; const s = state.byId.get(T); return `Station identifier (ICAO code)${s ? `: ${s.name}` : ''}`; }
  if ((m = /^(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?(KT|MPS|KMH)$/.exec(T))) {
    const unit = { KT: 'knots', MPS: 'metres per second', KMH: 'km/h' }[m[4]];
    if (m[1] === '000' && Number(m[2]) === 0) return 'Wind calm';
    const dir = m[1] === 'VRB' ? 'Wind direction variable' : `Wind from ${m[1]}° (${compass(Number(m[1]))})`;
    return `${dir} at ${Number(m[2])} ${unit}${m[3] ? `, gusting to ${Number(m[3])} ${unit}` : ''}`;
  }
  if ((m = /^(\d{3})V(\d{3})$/.exec(T))) return `Wind direction varying between ${m[1]}° and ${m[2]}°`;
  if (T === '9999') return 'Visibility 10 km or more';
  if ((m = /^(\d{4})(NDV)?$/.exec(T))) return `Visibility ${Number(m[1]).toLocaleString('en-US')} m${m[2] ? ' (no directional variation available)' : ''}`;
  if ((m = /^(\d{4})(N|NE|E|SE|S|SW|W|NW)$/.exec(T))) return `Minimum visibility ${Number(m[1]).toLocaleString('en-US')} m towards the ${dirName(m[2])}`;
  if ((m = /^([PM])?(\d+(?: \d)?(?:\/\d+)?)SM$/.exec(T))) return `Visibility ${m[1] === 'P' ? 'more than ' : m[1] === 'M' ? 'less than ' : ''}${m[2]} statute mile${m[2] === '1' ? '' : 's'}`;
  if ((m = /^R(\d{2}[LCR]?)\/([PM])?(\d{4})(?:V([PM])?(\d{4}))?(FT)?\/?([UDN])?$/.exec(T))) {
    const u = m[6] ? 'ft' : 'm';
    const v = (p, n) => `${p === 'P' ? 'more than ' : p === 'M' ? 'less than ' : ''}${Number(n).toLocaleString('en-US')} ${u}`;
    return `Runway visual range on runway ${m[1]}: ${v(m[2], m[3])}${m[5] ? ` varying to ${v(m[4], m[5])}` : ''}${m[7] ? `, trend ${{ U: 'improving', D: 'deteriorating', N: 'no change' }[m[7]]}` : ''}`;
  }
  if (/^R\d{2}[LCR]?\/[\d\/]{6}$/.test(T) || /^R\d{2}[LCR]?\/CLRD\d{2}$/.test(T)) return 'Runway state group: deposit type, extent, depth and braking action on the runway';
  if (/^WS /.test(T)) return `Wind shear reported ${/ALL/.test(T) ? 'on all runways' : `on runway ${T.replace(/^WS R(WY)?/, '')}`}`;
  if ((m = /^(FEW|SCT|BKN|OVC|VV)(\d{3}|\/\/\/)(CB|TCU|\/\/\/)?$/.exec(T))) {
    const h = m[2] === '///' ? 'unknown height' : `${(Number(m[2]) * 100).toLocaleString('en-US')} ft above ground`;
    const type = m[3] === 'CB' ? ' — cumulonimbus (thunderstorm cloud)' : m[3] === 'TCU' ? ' — towering cumulus' : '';
    return `${COVER[m[1]]} at ${h}${type}`;
  }
  if (T === 'SKC') return 'Sky clear';
  if (T === 'CLR') return 'No clouds detected below 12,000 ft (automated)';
  if (T === 'NSC') return 'No significant cloud (none below 5,000 ft, no CB/TCU)';
  if (T === 'NCD') return 'No cloud detected by the automatic system';
  if ((m = /^(M?\d{2})\/(M?\d{2})?$/.exec(T))) return `Temperature ${signedT(m[1])} °C${m[2] ? `, dew point ${signedT(m[2])} °C` : ', dew point missing'}`;
  if ((m = /^Q(\d{4})$/.exec(T))) return `QNH (altimeter setting) ${Number(m[1])} hPa = ${(Number(m[1]) * 0.02953).toFixed(2)} inHg`;
  if ((m = /^A(\d{4})$/.exec(T))) return `Altimeter setting ${m[1].slice(0, 2)}.${m[1].slice(2)} inHg = ${Math.round(Number(m[1]) / 100 * 33.8639)} hPa`;
  if ((m = /^RE(\w+)$/.exec(T)) && wxPhrase(m[1])) return `Recent weather (during the past hour, now ended): ${wxPhrase(m[1]).replace(/^moderate /, '')}`;
  if ((m = /^W(M?\d{2})\/(S\d|H\d+)$/.exec(T))) return `Sea surface temperature ${signedT(m[1])} °C and sea state / wave height`;
  if (T === 'NSW') return 'No significant weather (forecast end of previous weather)';
  const w = wxPhrase(T);
  if (w) return `Present weather: ${w}`;
  if (T === '$') return 'Station needs maintenance (automated system flag)';
  if (ctx.taf && /^5\d{5}$/.test(T)) return 'Turbulence forecast: intensity, base height and layer thickness';
  if (ctx.taf && /^6\d{5}$/.test(T)) return 'Icing forecast: intensity, base height and layer thickness';
  if (T === 'M') return 'Missing value';
  if (/^CC[A-Z]$/.test(T)) return `Corrected report (correction ${T.charCodeAt(2) - 64})`;
  if (/^(BLU|WHT|GRN|YLO\d?|AMB|RED|BLACK)\+?((BLU|WHT|GRN|YLO\d?|AMB|RED|BLACK)\+?)*$/.test(T)) return 'Military colour state: airfield condition code from base visibility and cloud (BLU best → WHT → GRN → YLO → AMB → RED worst)';
  if (T === 'FCST') return 'Military colour-state forecast follows';
  if ((m = /^QNH(\d{2})(\d{2})INS$/.exec(T))) return `Lowest forecast altimeter setting ${m[1]}.${m[2]} inHg`;
  if ((m = /^WS(\d{3})\/(\d{3})(\d{2,3})KT$/.exec(T))) return `Low-level wind shear: at ${Number(m[1]) * 100} ft above ground the wind is ${m[2]}° at ${Number(m[3])} kt`;
  if (/\/\//.test(T)) return 'Group partly or fully missing (value not reported)';
  if (/^[A-Z]{2,12}$/.test(T)) return 'Plain-language text';
  return null;
}
function explainRemark(T) {
  let m;
  if (T === 'AO1') return 'Automated station without a precipitation-type sensor';
  if (T === 'AO2') return 'Automated station with a precipitation-type sensor';
  if ((m = /^SLP(\d{3})$/.exec(T))) { const v = Number(m[1]) / 10; return `Sea-level pressure ${(v < 50 ? 1000 + v : 900 + v).toFixed(1)} hPa`; }
  if (T === 'SLPNO') return 'Sea-level pressure not available';
  if ((m = /^T([01])(\d{3})([01])(\d{3})$/.exec(T))) return `Exact temperature ${(m[1] === '1' ? -1 : 1) * Number(m[2]) / 10} °C, dew point ${(m[3] === '1' ? -1 : 1) * Number(m[4]) / 10} °C`;
  if ((m = /^([12])([01])(\d{3})$/.exec(T))) return `${m[1] === '1' ? 'Maximum' : 'Minimum'} temperature in the past 6 hours: ${(m[2] === '1' ? -1 : 1) * Number(m[3]) / 10} °C`;
  if (/^4[01]\d{3}[01]\d{3}$/.test(T)) return '24-hour maximum and minimum temperature';
  if (/^5\d{4}$/.test(T)) return 'Pressure tendency over the past 3 hours';
  if ((m = /^P(\d{4})$/.exec(T))) return `Precipitation in the past hour: ${Number(m[1]) / 100} in`;
  if (/^6\d{4}$/.test(T) || /^6\/{4}$/.test(T)) return 'Precipitation amount over the past 3 or 6 hours';
  if (/^7\d{4}$/.test(T)) return 'Precipitation over the past 24 hours';
  if (/^8\/\d{3}$/.test(T)) return 'Cloud types (low / middle / high)';
  if (/^PK WND/.test(T)) return 'Peak wind since the last hourly report: direction, speed and time';
  if (T === 'PRESFR') return 'Pressure falling rapidly';
  if (T === 'PRESRR') return 'Pressure rising rapidly';
  if (T === 'TSNO') return 'Thunderstorm sensor not available';
  if (T === 'PNO') return 'Precipitation gauge not available';
  if (T === 'FZRANO') return 'Freezing-rain sensor not available';
  if (T === 'RVRNO') return 'Runway visual range not available';
  if (T === '$') return 'Station needs maintenance (automated system flag)';
  if (/^(RA|SN|DZ|TS|FZRA|SHRA|PL|GR)(B\d{2,4})?(E\d{2,4})?(B\d{2,4})?$/.test(T) && /[BE]\d/.test(T)) return 'Weather began (B) / ended (E) at the given minutes past the hour';
  const g = explainGroup(T, { idx: 99, seenStation: true, taf: false });
  if (g) return `Remark: ${g[0].toLowerCase()}${g.slice(1)}`;
  return 'Remark — national or local information, free format';
}

function explainTokens(raw, { taf = false } = {}) {
  const parts = String(raw || '').replace(/=+\s*$/, '').trim().split(/\s+/).filter(Boolean);
  // merge multi-word groups: "1 1/2SM", "WS R24"/"WS ALL RWY", "PK WND 28045/15"
  const toks = [];
  for (let i = 0; i < parts.length; i++) {
    const p = parts[i], n = parts[i + 1];
    if (/^\d$/.test(p) && n && /^\d\/\d+SM$/.test(n)) { toks.push(`${p} ${n}`); i++; }
    else if (p === 'WS' && n === 'ALL' && parts[i + 2] === 'RWY') { toks.push('WS ALL RWY'); i += 2; }
    else if (p === 'WS' && n && /^R(WY)?\d{2}[LCR]?$/.test(n)) { toks.push(`WS ${n}`); i++; }
    else if (p === 'PK' && n === 'WND' && parts[i + 2]) { toks.push(`PK WND ${parts[i + 2]}`); i += 2; }
    else toks.push(p);
  }
  const ctx = { idx: 0, seenStation: false, taf };
  let inRmk = false, inTrend = false;
  return toks.map((t, i) => {
    ctx.idx = i;
    if (t === 'RMK') { inRmk = true; return { text: t, desc: 'Start of remarks — supplementary, mostly national information', cls: 'rmk' }; }
    if (inRmk) return { text: t, desc: explainRemark(t), cls: 'rmk' };
    if (!taf && ['NOSIG', 'BECMG', 'TEMPO'].includes(t)) inTrend = true;
    let d = explainGroup(t, ctx);
    const cls = !taf && inTrend ? 'trend' : (d && d !== 'Plain-language text' ? '' : d ? 'rmk' : 'bad');
    if (d && !taf && inTrend && !['NOSIG', 'BECMG', 'TEMPO'].includes(t)) d = `Trend forecast: ${d[0].toLowerCase()}${d.slice(1)}`;
    return { text: t, desc: d || 'Unrecognised group', cls };
  });
}
