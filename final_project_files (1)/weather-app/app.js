const fs = require('fs');
const express = require('express');
const { Pool } = require('pg');
const { SSMClient, GetParameterCommand } = require('@aws-sdk/client-ssm');

const app = express();
const PORT = process.env.PORT || 3000;
const ZIP_API = process.env.ZIP_API_BASE || 'https://api.zippopotam.us';
const NWS_API = process.env.NWS_API_BASE || 'https://api.weather.gov';
const METEO_API = process.env.METEO_API_BASE || 'https://api.open-meteo.com';
// The National Weather Service asks every client to identify itself.
const USER_AGENT = process.env.NWS_USER_AGENT || '(itmo-444-544 weather lab, student@example.com)';

// Database settings. With no DB_HOST the service still works, it just does not store anything.
const DB_HOST = process.env.DB_HOST;
const DB_PORT = Number(process.env.DB_PORT || 5432);
const DB_NAME = process.env.DB_NAME || 'weather';
const DB_USER = process.env.DB_USER || 'weatherapp';
const DB_PASSWORD_PARAM = process.env.DB_PASSWORD_PARAM || '/itmo-444-544/db-password';
const DB_CA_FILE = process.env.DB_CA_FILE || '/opt/app/rds-ca.pem';
const CACHE_SECONDS = Number(process.env.CACHE_SECONDS || 1800);
const STALE_SECONDS = Number(process.env.STALE_SECONDS || 21600);
const DB_RETRY_MS = 30000; // after a database failure, leave the database alone for 30 seconds

const ssm = new SSMClient({ region: process.env.AWS_REGION });

const log = (event, fields = {}) =>
  console.log(JSON.stringify({ ts: new Date().toISOString(), event, ...fields }));

class UpstreamError extends Error {
  constructor(stage, status) {
    super(`${stage} failed`);
    this.stage = stage;
    this.status = status;
  }
}

// Fetch JSON with a timeout and one retry. Returns null on a 404, throws UpstreamError otherwise.
async function fetchJson(stage, url, headers = {}) {
  let status = 0;
  for (let attempt = 1; attempt <= 2; attempt++) {
    try {
      const res = await fetch(url, { headers, signal: AbortSignal.timeout(6000) });
      status = res.status;
      if (res.ok) return await res.json();
      if (res.status === 404) return null;
      if (res.status < 500 && res.status !== 429) break; // a client error will not fix itself
    } catch (err) {
      status = 0; // timeout or network failure
    }
    if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new UpstreamError(stage, status);
}

// ---- Units -----------------------------------------------------------------------------
// Every value leaves this section in US units. An unknown unit is an error, never a guess.

const SPEED_TO_MPH = { 'km_h-1': 0.621371, 'km/h': 0.621371, 'm_s-1': 2.236936, 'm/s': 2.236936, mph: 1, 'mp/h': 1, kn: 1.150779 };
const DISTANCE_TO_MILES = { m: 1 / 1609.344, km: 0.621371, ft: 1 / 5280, mi: 1 };
const DEPTH_TO_INCHES = { mm: 1 / 25.4, cm: 1 / 2.54, m: 100 / 2.54, in: 1, inch: 1 };
const KIND = {
  temperature: 'temperature', wind: 'speed', gust: 'speed', visibility: 'distance',
  humidity: 'percent', chance: 'percent', rain: 'depth', snow: 'depth', uv: 'plain',
};

function toUsUnits(kind, value, unit) {
  const u = String(unit || '').replace('wmoUnit:', '').trim();
  const n = Number(value);
  if (kind === 'plain') return n;
  if (kind === 'percent') {
    if (u !== 'percent' && u !== '%') throw new Error(`unknown unit ${u}`);
    return n;
  }
  if (kind === 'temperature') {
    if (['degC', '°C', 'C'].includes(u)) return n * 9 / 5 + 32;
    if (['degF', '°F', 'F'].includes(u)) return n;
    throw new Error(`unknown unit ${u}`);
  }
  const table = { speed: SPEED_TO_MPH, distance: DISTANCE_TO_MILES, depth: DEPTH_TO_INCHES }[kind];
  if (!(u in table)) throw new Error(`unknown unit ${u}`);
  return n * table[u];
}

// ---- Dates, time zones and periods -----------------------------------------------------
// Morning, Night and the 7 day window are always in the local time of the zip code's location.

const PERIODS = {
  morning: { name: 'Morning', from: 6, to: 12, window: '06:00 to 11:59 local time' },
  night: { name: 'Night', from: 18, to: 24, window: '18:00 to 23:59 local time' },
};
const DAYS_AHEAD = 6;
const pad = (n) => String(n).padStart(2, '0');

// A real calendar date written as YYYY-MM-DD, or false.
function isRealDate(text) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(text))) return false;
  const d = new Date(`${text}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === text;
}

// The local hour of an instant in a time zone, as 'YYYY-MM-DDTHH'. Daylight saving is handled here.
function localHourKey(instant, timeZone) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', hourCycle: 'h23',
  }).formatToParts(instant);
  const get = (type) => parts.find((p) => p.type === type).value;
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}`;
}

const localDay = (instant, timeZone) => localHourKey(instant, timeZone).slice(0, 10);

function addDays(day, count) {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + count);
  return d.toISOString().slice(0, 10);
}

// Today through today plus six days, counted at the zip code's location.
const dayInWindow = (day, today) => day >= today && day <= addDays(today, DAYS_AHEAD);

const periodKeys = (day, key) => {
  const keys = [];
  for (let hour = PERIODS[key].from; hour < PERIODS[key].to; hour++) keys.push(`${day}T${pad(hour)}`);
  return keys;
};

// Turn '2026-11-03T18:00:00+00:00/PT3H' into the start instant and the number of hours it covers.
function parseValidTime(text) {
  const [startText, duration] = String(text).split('/');
  const m = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$/.exec(duration || '');
  if (!m) throw new Error(`bad validTime ${text}`);
  const hours = Number(m[1] || 0) * 24 + Number(m[2] || 0) + (m[3] ? 1 : 0);
  return { start: new Date(startText), hours };
}

// One NWS gridpoint layer as a map of local hour to value. Amounts are shared out over their hours.
function expandNwsLayer(layer, timeZone, spread) {
  const hours = new Map();
  for (const item of (layer && layer.values) || []) {
    if (item.value === null || item.value === undefined) continue;
    const { start, hours: count } = parseValidTime(item.validTime);
    const share = spread && count ? item.value / count : item.value;
    for (let step = 0; step < count; step++) {
      hours.set(localHourKey(new Date(start.getTime() + step * 3600000), timeZone), share);
    }
  }
  return hours;
}

// One Open-Meteo hourly list as a map of local hour to value. Its times are already local.
function expandMeteo(hourly, name) {
  const hours = new Map();
  (hourly.time || []).forEach((time, i) => {
    const value = hourly[name][i];
    if (value !== null && value !== undefined) hours.set(String(time).slice(0, 13), value);
  });
  return hours;
}

// ---- Categories ------------------------------------------------------------------------
// Rain follows the American Meteorological Society glossary: light up to 0.10 in per hour,
// moderate 0.11 to 0.30 in, heavy more than 0.30 in. Light and moderate are both shown as Mild.
// Snow follows the National Weather Service glossary: heavy snow is 4 in or more in 12 hours or
// less. A period is 6 hours, so 2 in or more is Heavy. Anything from a trace (0.1 in) up is Mild.
// A chance under 20 percent is always No. With an amount, the amount decides. With only a chance,
// 60 percent or more gives Mild, never Heavy, because the strength is not known.
// The forecast amount is liquid water, which includes melted snow. When the whole period stays at
// or below 32 F the precipitation is counted as snow, so Rain is No.

const RAIN_HEAVY_ABOVE = 0.30;
const RAIN_MILD_FROM = 0.01;
const SNOW_HEAVY_FROM = 2.0;
const SNOW_MILD_FROM = 0.1;
const CHANCE_NONE_BELOW = 20;
const CHANCE_ONLY_MILD_FROM = 60;

function rainCategory(chance, peakRate, highTemperature) {
  if (chance === null && peakRate === null) return null;
  if (chance !== null && chance < CHANCE_NONE_BELOW) return { category: 'No', basis: 'chance of precipitation is under 20 percent' };
  if (highTemperature !== null && highTemperature !== undefined && highTemperature <= 32) {
    return { category: 'No', basis: 'the period stays at or below freezing, so precipitation is counted as snow' };
  }
  if (peakRate !== null) {
    if (peakRate > RAIN_HEAVY_ABOVE) return { category: 'Heavy', basis: 'peak rate is over 0.30 in per hour' };
    if (peakRate >= RAIN_MILD_FROM) return { category: 'Mild', basis: 'peak rate is 0.01 to 0.30 in per hour' };
    return { category: 'No', basis: 'forecast amount is under 0.01 in per hour' };
  }
  const basis = 'estimated from the chance alone, the amount is not available';
  return { category: chance >= CHANCE_ONLY_MILD_FROM ? 'Mild' : 'No', basis };
}

function snowCategory(chance, totalInches, lowTemperature) {
  if (chance === null && totalInches === null) return null;
  if (chance !== null && chance < CHANCE_NONE_BELOW) return { category: 'No', basis: 'chance of precipitation is under 20 percent' };
  if (totalInches !== null) {
    if (totalInches >= SNOW_HEAVY_FROM) return { category: 'Heavy', basis: '2 in or more of snow in this period' };
    if (totalInches >= SNOW_MILD_FROM) return { category: 'Mild', basis: '0.1 to 2 in of snow in this period' };
    return { category: 'No', basis: 'forecast snowfall is under 0.1 in' };
  }
  const freezing = lowTemperature !== null && lowTemperature !== undefined && lowTemperature <= 32;
  if (chance >= CHANCE_ONLY_MILD_FROM && freezing) {
    return { category: 'Mild', basis: 'estimated from the chance and freezing temperatures, the amount is not available' };
  }
  return { category: 'No', basis: 'estimated from the chance alone, the amount is not available' };
}

// The World Health Organization name for a UV index value.
function uvLevel(index) {
  if (index < 3) return 'Low';
  if (index < 6) return 'Moderate';
  if (index < 8) return 'High';
  if (index < 11) return 'Very high';
  return 'Extreme';
}

// ---- Facts -----------------------------------------------------------------------------
// The National Weather Service is used first. When it covers fewer than 3 of the 6 hours of a
// period, Open-Meteo is used instead. UV comes only from Open-Meteo. If neither has a value the
// item says "not available" and gives the reason. Nothing is ever invented.

const NWS_LAYERS = {
  temperature: 'temperature', wind: 'windSpeed', gust: 'windGust', visibility: 'visibility',
  humidity: 'relativeHumidity', chance: 'probabilityOfPrecipitation',
  rain: 'quantitativePrecipitation', snow: 'snowfallAmount',
};
const METEO_VARIABLES = {
  temperature: 'temperature_2m', wind: 'wind_speed_10m', gust: 'wind_gusts_10m',
  visibility: 'visibility', humidity: 'relative_humidity_2m',
  chance: 'precipitation_probability', rain: 'precipitation', snow: 'snowfall', uv: 'uv_index',
};
const AMOUNTS = ['rain', 'snow'];
const MIN_HOURS = 3;
const LABELS = {
  temperature: 'Temperature', wind: 'Wind speed', visibility: 'Visibility', uv: 'UV',
  humidity: 'Humidity', rain: 'Rain', snow: 'Snow',
};
const NWS_CREDIT = 'National Weather Service (api.weather.gov)';
const METEO_CREDIT = 'Weather data by Open-Meteo.com (CC BY 4.0)';
const ZIP_CREDIT = 'Zip code lookup by zippopotam.us';

function convertSeries(target, item, hours, unit) {
  try {
    const converted = new Map();
    for (const [key, value] of hours) converted.set(key, toUsUnits(KIND[item], value, unit));
    target[item] = converted;
  } catch (err) {
    log('unknown_unit', { item, message: err.message });
  }
}

// Both raw responses as { NWS: { item: Map }, 'Open-Meteo': { item: Map } }, in US units.
function loadSeries(grid, meteo, timeZone) {
  const series = { NWS: {}, 'Open-Meteo': {} };
  for (const [item, layerName] of Object.entries(NWS_LAYERS)) {
    const layer = grid && grid[layerName];
    if (layer) convertSeries(series.NWS, item, expandNwsLayer(layer, timeZone, AMOUNTS.includes(item)), layer.uom);
  }
  if (meteo && meteo.hourly) {
    const units = meteo.hourly_units || {};
    for (const [item, variable] of Object.entries(METEO_VARIABLES)) {
      if (Array.isArray(meteo.hourly[variable])) {
        convertSeries(series['Open-Meteo'], item, expandMeteo(meteo.hourly, variable), units[variable]);
      }
    }
  }
  return series;
}

// The values of one item in one period and the source they came from.
function pick(series, item, keys) {
  for (const minimum of [MIN_HOURS, 1]) {
    for (const source of ['NWS', 'Open-Meteo']) {
      const data = series[source][item];
      const values = data ? keys.filter((k) => data.has(k)).map((k) => data.get(k)) : [];
      if (values.length >= minimum) return { values, source };
    }
  }
  return { values: null, source: null };
}

const missing = (label, reason) => ({ label, available: false, text: 'not available', reason, source: null });
const done = (label, text, values, sources) => ({
  label, available: true, text, values,
  source: [...new Set(sources.filter(Boolean))].sort().join(' and '),
});
const round = (n, places = 0) => Number(n.toFixed(places));

function describe(label, result, chance, amount, amountText, sources, reason) {
  if (!result) return missing(label, reason);
  const parts = [];
  if (chance !== null) parts.push(`chance ${chance}%`);
  if (amount !== null) parts.push(amountText);
  const text = result.category + (parts.length ? ` (${parts.join(', ')})` : '');
  return { ...done(label, text, { category: result.category, chance, amount }, sources), category: result.category, basis: result.basis };
}

// The seven measured and derived items for one period of one day.
function buildPeriod(series, day, key, nowKey) {
  const keys = periodKeys(day, key);
  if (keys[keys.length - 1] < nowKey) {
    const passed = {};
    for (const [item, label] of Object.entries(LABELS)) passed[item] = missing(label, 'this period has already passed');
    return passed;
  }
  const reason = 'neither the National Weather Service nor Open-Meteo returned this value for this period';
  const out = {};

  const temp = pick(series, 'temperature', keys);
  if (temp.values) {
    const low = round(Math.min(...temp.values));
    const high = round(Math.max(...temp.values));
    out.temperature = done(LABELS.temperature, `${low} to ${high} F`, { low, high }, [temp.source]);
  } else out.temperature = missing(LABELS.temperature, reason);

  const wind = pick(series, 'wind', keys);
  if (wind.values) {
    const sustained = round(Math.max(...wind.values));
    const gusts = pick(series, 'gust', keys);
    const gust = gusts.values ? round(Math.max(...gusts.values)) : null;
    const text = `up to ${sustained} mph` + (gust !== null ? `, gusts to ${gust} mph` : '');
    out.wind = done(LABELS.wind, text, { sustained, gust }, [wind.source, gusts.source]);
  } else out.wind = missing(LABELS.wind, reason);

  const vis = pick(series, 'visibility', keys);
  if (vis.values) {
    const miles = round(Math.min(...vis.values), 1);
    out.visibility = done(LABELS.visibility, `${miles.toFixed(1)} miles`, { miles }, [vis.source]);
  } else out.visibility = missing(LABELS.visibility, reason);

  const uv = pick(series, 'uv', keys);
  if (uv.values) {
    const index = round(Math.max(...uv.values), 1);
    out.uv = done(LABELS.uv, `${index.toFixed(1)} (${uvLevel(index)})`, { index }, [uv.source]);
  } else out.uv = missing(LABELS.uv, reason);

  const hum = pick(series, 'humidity', keys);
  if (hum.values) {
    const percent = round(hum.values.reduce((a, b) => a + b, 0) / hum.values.length);
    out.humidity = done(LABELS.humidity, `${percent}%`, { percent }, [hum.source]);
  } else out.humidity = missing(LABELS.humidity, reason);

  const chances = pick(series, 'chance', keys);
  const chance = chances.values ? round(Math.max(...chances.values)) : null;

  const low = temp.values ? Math.min(...temp.values) : null;
  const high = temp.values ? Math.max(...temp.values) : null;

  const rain = pick(series, 'rain', keys);
  const peakRate = rain.values ? round(Math.max(...rain.values), 3) : null;
  out.rain = describe(LABELS.rain, rainCategory(chance, peakRate, high), chance, peakRate,
    peakRate === null ? '' : `${peakRate.toFixed(2)} in per hour at most`, [chances.source, rain.source], reason);

  const snow = pick(series, 'snow', keys);
  const total = snow.values && snow.values.length >= MIN_HOURS ? round(snow.values.reduce((a, b) => a + b, 0), 2) : null;
  out.snow = describe(LABELS.snow, snowCategory(chance, total, low), chance, total,
    total === null ? '' : `${total.toFixed(1)} in of snow in total`, [chances.source, total === null ? null : snow.source], reason);
  return out;
}

// The facts for both periods of one day, plus the credit lines for the sources actually used.
function buildFacts(grid, meteo, timeZone, day, now) {
  const series = loadSeries(grid, meteo, timeZone);
  const nowKey = localHourKey(now, timeZone);
  const periods = {};
  for (const key of Object.keys(PERIODS)) {
    periods[key] = { name: PERIODS[key].name, window: PERIODS[key].window, items: buildPeriod(series, day, key, nowKey) };
  }
  const used = Object.values(periods).flatMap((p) => Object.values(p.items)).map((i) => i.source || '');
  const credits = [];
  if (used.some((s) => s.includes('NWS'))) credits.push(NWS_CREDIT);
  if (used.some((s) => s.includes('Open-Meteo'))) credits.push(METEO_CREDIT);
  credits.push(ZIP_CREDIT);
  return { periods, credits };
}

// ---- Database helpers ------------------------------------------------------------------

let poolPromise = null;
let dbRetryAt = 0;

async function readDbPassword() {
  if (process.env.DB_PASSWORD) return process.env.DB_PASSWORD; // local development only
  const out = await ssm.send(new GetParameterCommand({ Name: DB_PASSWORD_PARAM, WithDecryption: true }));
  return out.Parameter.Value;
}

async function createPool() {
  const password = await readDbPassword();
  // Verify the server certificate against the RDS CA bundle, so the connection is encrypted
  // and we know we are talking to RDS and not to something in the middle.
  const ssl = process.env.DB_SSL === 'off' ? false : { ca: fs.readFileSync(DB_CA_FILE, 'utf8') };
  const pool = new Pool({
    host: DB_HOST, port: DB_PORT, database: DB_NAME, user: DB_USER, password, ssl,
    max: 4, connectionTimeoutMillis: 3000, idleTimeoutMillis: 30000, statement_timeout: 3000,
  });
  pool.on('error', (err) => log('db_error', { stage: 'pool', message: err.message }));
  try {
    await pool.query(`CREATE TABLE IF NOT EXISTS facts (
      zip CHAR(5) NOT NULL,
      day DATE NOT NULL,
      payload JSONB NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (zip, day))`);
  } catch (err) {
    await pool.end().catch(() => {});
    throw err;
  }
  return pool;
}

// Returns a ready pool, or throws. A failed attempt is not remembered, so the next try starts fresh.
function getPool() {
  if (!poolPromise) {
    poolPromise = createPool().catch((err) => {
      poolPromise = null;
      throw err;
    });
  }
  return poolPromise;
}

// Run one database call. Any failure is logged, and the database is skipped for a short time,
// so a broken database costs each request nothing instead of a 3 second wait.
async function withDb(stage, fn) {
  if (!DB_HOST) return { state: 'disabled' };
  if (Date.now() < dbRetryAt) return { state: 'unavailable' };
  try {
    return { state: 'ok', value: await fn(await getPool()) };
  } catch (err) {
    dbRetryAt = Date.now() + DB_RETRY_MS;
    log('db_error', { stage, message: err.message });
    return { state: 'unavailable' };
  }
}

// The saved facts for a zip and a day with their age in seconds. Rows older than STALE_SECONDS do not count.
const findFacts = (zip, day) => withDb('read', async (pool) => {
  const r = await pool.query(
    `SELECT payload, EXTRACT(EPOCH FROM now() - created_at)::int AS age FROM facts
     WHERE zip = $1 AND day = $2 AND created_at > now() - make_interval(secs => $3)`,
    [zip, day, STALE_SECONDS]);
  return r.rows.length ? { payload: r.rows[0].payload, age: r.rows[0].age } : null;
});

// Keep one row per zip and day, and remove every row that is too old to be used again.
const saveFacts = (zip, day, payload) => withDb('write', async (pool) => {
  await pool.query(
    `INSERT INTO facts (zip, day, payload) VALUES ($1, $2, $3)
     ON CONFLICT (zip, day) DO UPDATE SET payload = EXCLUDED.payload, created_at = now()`,
    [zip, day, JSON.stringify(payload)]);
  await pool.query('DELETE FROM facts WHERE created_at < now() - make_interval(secs => $1)', [STALE_SECONDS]);
});

// Everything that reaches outside this process. The tests replace these.
const deps = { fetchJson, findFacts, saveFacts, now: () => new Date() };

// ---- Routes ----------------------------------------------------------------------------

app.get('/', (req, res) => {
  res.json({ service: 'weather', usage: '/weather?zip=60616&date=YYYY-MM-DD' });
});

// The zip code as a place: coordinates, city, state, time zone and the NWS grid address.
async function lookupPlace(zip) {
  const lookup = await deps.fetchJson('zip_lookup', `${ZIP_API}/us/${zip}`);
  if (!lookup || !lookup.places || lookup.places.length === 0) return { error: 'zip_not_found' };
  const place = lookup.places[0];
  const lat = Number(place.latitude).toFixed(4);
  const lon = Number(place.longitude).toFixed(4);
  const headers = { 'User-Agent': USER_AGENT, Accept: 'application/geo+json' };
  const point = await deps.fetchJson('nws_points', `${NWS_API}/points/${lat},${lon}`, headers);
  if (!point) return { error: 'no_nws_coverage' };
  return {
    city: place['place name'], state: place['state abbreviation'], latitude: lat, longitude: lon,
    timezone: point.properties.timeZone, gridUrl: point.properties.forecastGridData, headers,
  };
}

// Both forecast sources at the same time. One failing source is allowed, both failing is not.
async function fetchForecasts(place) {
  const query = new URLSearchParams({
    latitude: place.latitude, longitude: place.longitude, hourly: Object.values(METEO_VARIABLES).join(','),
    timezone: place.timezone, forecast_days: '7',
  });
  const [grid, meteo] = await Promise.allSettled([
    deps.fetchJson('nws_grid', place.gridUrl, place.headers),
    deps.fetchJson('open_meteo', `${METEO_API}/v1/forecast?${query}`),
  ]);
  for (const result of [grid, meteo]) {
    if (result.status === 'rejected') log('source_error', { stage: result.reason.stage, status: result.reason.status });
  }
  const gridData = grid.status === 'fulfilled' && grid.value ? grid.value.properties : null;
  const meteoData = meteo.status === 'fulfilled' && meteo.value && meteo.value.hourly ? meteo.value : null;
  if (!gridData && !meteoData) throw new UpstreamError('forecast', 0);
  return { grid: gridData, meteo: meteoData };
}

const outOfRange = (res) => res.status(400).json({
  error: 'date_out_of_range', message: "The date must be today or one of the next six days at the zip code's location.",
});

app.get('/weather', async (req, res) => {
  const zip = String(req.query.zip || '');
  const day = String(req.query.date || '');
  if (!/^\d{5}$/.test(zip)) {
    log('weather_rejected', { reason: 'invalid_zip' });
    return res.status(400).json({ error: 'invalid_zip', message: 'Give a 5 digit US zip code.' });
  }
  if (!isRealDate(day)) {
    log('weather_rejected', { reason: 'invalid_date' });
    return res.status(400).json({ error: 'invalid_date', message: 'Give a date as YYYY-MM-DD.' });
  }
  const started = Date.now();
  log('weather_request', { zip, date: day });

  // 0. Has anyone asked for this zip and day in the last few minutes? Then answer from the database.
  const cached = await deps.findFacts(zip, day);
  const saved = cached.state === 'ok' ? cached.value : null;
  if (saved && saved.age < CACHE_SECONDS) {
    if (!dayInWindow(day, localDay(deps.now(), saved.payload.timezone))) return outOfRange(res);
    log('weather_success', { zip, cache: 'hit', ms: Date.now() - started });
    return res.json({ ...saved.payload, cache: 'hit', ageSeconds: saved.age });
  }

  try {
    // 1. The weather API wants coordinates, not zip codes, and the periods need the local time zone.
    const place = await lookupPlace(zip);
    if (place.error) {
      log('weather_rejected', { reason: place.error, zip });
      return res.status(404).json({ error: place.error });
    }
    if (!dayInWindow(day, localDay(deps.now(), place.timezone))) {
      log('weather_rejected', { reason: 'date_out_of_range', zip });
      return outOfRange(res);
    }

    // 2. Fetch the raw grid from NWS and the hourly values from Open-Meteo, then build the facts.
    const { grid, meteo } = await fetchForecasts(place);
    const facts = buildFacts(grid, meteo, place.timezone, day, deps.now());
    const body = {
      zip, date: day, city: place.city, state: place.state, timezone: place.timezone,
      periods: facts.periods, credits: facts.credits,
    };

    // 3. Keep a copy. It is the cache for the next caller and the older copy used when a source is down.
    const stored = await deps.saveFacts(zip, day, body);
    const cache = stored.state === 'ok' ? 'miss' : stored.state; // 'miss', 'disabled' or 'unavailable'
    log('weather_success', { zip, cache, ms: Date.now() - started });
    return res.json({ ...body, cache, ageSeconds: 0 });
  } catch (err) {
    if (err instanceof UpstreamError) {
      log('weather_error', { zip, stage: err.stage, status: err.status });
      if (saved) {
        log('weather_success', { zip, cache: 'stale', ms: Date.now() - started });
        return res.json({ ...saved.payload, cache: 'stale', ageSeconds: saved.age });
      }
      return res.status(502).json({ error: 'upstream_error', stage: err.stage });
    }
    log('weather_error', { zip, stage: 'internal', message: err.message });
    return res.status(500).json({ error: 'internal_error' });
  }
});

if (require.main === module) {
  app.listen(PORT, () => log('weather_started', { port: PORT }));
}

module.exports = {
  app, deps, UpstreamError, toUsUnits, isRealDate, localHourKey, localDay, dayInWindow, periodKeys,
  parseValidTime, expandNwsLayer, expandMeteo, rainCategory, snowCategory, uvLevel, buildFacts,
};
