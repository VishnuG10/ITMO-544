const fs = require('fs');
const crypto = require('crypto');
const express = require('express');
const { Pool } = require('pg');
const { SSMClient, GetParameterCommand } = require('@aws-sdk/client-ssm');
const { SNSClient, ListSubscriptionsByTopicCommand, SubscribeCommand, PublishCommand } = require('@aws-sdk/client-sns');

const app = express();
const PORT = process.env.PORT || 3000;
const WEATHER_URL = process.env.WEATHER_URL; // for example http://<alb-dns>:8080
const GEMINI_API_BASE = process.env.GEMINI_API_BASE || 'https://generativelanguage.googleapis.com';
const GEMINI_MODEL = process.env.GEMINI_MODEL || 'gemini-3.1-flash-lite';
const KEY_PARAM = process.env.GEMINI_KEY_PARAM || '/itmo-444-544/gemini-api-key';
const KEY_CACHE_MS = Number(process.env.KEY_CACHE_SECONDS || 300) * 1000;
const SNS_TOPIC_ARN = process.env.SNS_TOPIC_ARN;

// Limits. All of them come from .env through the boot script.
const GEMINI_DAILY_CAP = Number(process.env.GEMINI_DAILY_CAP || 200);
const GEMINI_PER_MINUTE = Number(process.env.GEMINI_PER_MINUTE || 10);
const VISITOR_PER_MINUTE = Number(process.env.VISITOR_PER_MINUTE || 20);
const EMAILS_PER_VISITOR_HOUR = Number(process.env.EMAILS_PER_VISITOR_HOUR || 5);
const REPORT_SECONDS = Number(process.env.REPORT_SECONDS || 3600);

// Database settings. With no DB_HOST the service still works, it just does not store or count anything.
const DB_HOST = process.env.DB_HOST;
const DB_PORT = Number(process.env.DB_PORT || 5432);
const DB_NAME = process.env.DB_NAME || 'weather';
const DB_USER = process.env.DB_USER || 'weatherapp';
const DB_PASSWORD_PARAM = process.env.DB_PASSWORD_PARAM || '/itmo-444-544/db-password';
const DB_CA_FILE = process.env.DB_CA_FILE || '/opt/app/rds-ca.pem';
const DB_RETRY_MS = 30000; // after a database failure, leave the database alone for 30 seconds

const ssm = new SSMClient({ region: process.env.AWS_REGION });
const sns = new SNSClient({ region: process.env.AWS_REGION });
let cachedKey = { value: null, at: 0 };

// One JSON line per event. The email address and the gender choice are never written here.
const log = (event, fields = {}) =>
  console.log(JSON.stringify({ ts: new Date().toISOString(), event, ...fields }));

const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ---- Validation ------------------------------------------------------------------------
// Every field is checked here, on the server. The messages never repeat what the user typed.

const GENDERS = { female: 'womenswear', male: 'menswear', neutral: 'neutral' };
const GENDER_LABELS = { female: 'Female', male: 'Male', neutral: 'Neutral or prefer not to say' };
const EMAIL_PATTERN = /^[a-z0-9._%+-]{1,64}@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$/;
const US_ONLY = 'This service covers US locations only. Enter a five digit US ZIP code, for example 60616. '
  + 'Postal codes from other countries are not supported.';

const fail = (field, error, message) => ({ ok: false, field, error, message });

function addDays(day, count) {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + count);
  return d.toISOString().slice(0, 10);
}

// The calendar day of an instant in a time zone, as YYYY-MM-DD.
function localDay(instant, timeZone) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(instant);
  const get = (type) => parts.find((p) => p.type === type).value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}

const dayInWindow = (day, today) => day >= today && day <= addDays(today, 6);

function validateZip(raw) {
  if (typeof raw !== 'string' || !/^[0-9]{5}$/.test(raw)) return fail('zip', 'invalid_zip', US_ONLY);
  return { ok: true, value: raw };
}

// A real calendar date. The exact seven days are checked at the zip code's location by the weather
// tier, so here the window is one day wider on each side to allow for every US time zone.
function validateDate(raw, now) {
  if (typeof raw !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    return fail('date', 'invalid_date', 'Choose a date from the calendar, for example 2026-11-17.');
  }
  const d = new Date(`${raw}T00:00:00Z`);
  if (Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== raw) {
    return fail('date', 'invalid_date', 'That date does not exist.');
  }
  const today = now.toISOString().slice(0, 10);
  if (raw < addDays(today, -1) || raw > addDays(today, 7)) {
    return fail('date', 'date_out_of_range', 'Choose today or one of the next six days.');
  }
  return { ok: true, value: raw };
}

// One of a fixed list. The value passed on is a clothing category, not an identity.
function validateGender(raw) {
  if (typeof raw !== 'string' || !Object.prototype.hasOwnProperty.call(GENDERS, raw)) {
    return fail('gender', 'invalid_gender', 'Choose one of the listed options.');
  }
  return { ok: true, value: GENDERS[raw] };
}

// A normal looking address of limited length, lowercased so it is always handled the same way.
function validateEmail(raw) {
  if (typeof raw !== 'string') return fail('email', 'invalid_email', 'Enter a valid email address.');
  const email = raw.trim().toLowerCase();
  if (email.length > 254 || !EMAIL_PATTERN.test(email)) return fail('email', 'invalid_email', 'Enter a valid email address.');
  return { ok: true, value: email };
}

// Check the fields in a fixed order and stop at the first bad one.
function validateRequest(fields, now, withEmail) {
  const zip = validateZip(fields.zip);
  if (!zip.ok) return zip;
  const date = validateDate(fields.date, now);
  if (!date.ok) return date;
  const gender = validateGender(fields.gender);
  if (!gender.ok) return gender;
  const email = withEmail ? validateEmail(fields.email) : { ok: true, value: null };
  if (!email.ok) return email;
  return { ok: true, zip: zip.value, date: date.value, clothing: gender.value, email: email.value };
}

// ---- Gemini ----------------------------------------------------------------------------

// The API key lives in SSM Parameter Store, never in user-data or an environment file.
// GEMINI_API_KEY is for running this app on your own laptop only.
async function getApiKey() {
  if (process.env.GEMINI_API_KEY) return process.env.GEMINI_API_KEY;
  if (cachedKey.value && Date.now() - cachedKey.at < KEY_CACHE_MS) return cachedKey.value;
  const out = await ssm.send(new GetParameterCommand({ Name: KEY_PARAM, WithDecryption: true }));
  cachedKey = { value: out.Parameter.Value, at: Date.now() };
  return cachedKey.value;
}

const PERIOD_KEYS = ['morning', 'night'];
const ADVICE_KEYS = ['road_conditions', 'dressing', 'accessories',
  'activities', 'feels_like', 'getting_around'];
const SYSTEM_PROMPT =
  'You give short, practical suggestions for the weather you are given: the road conditions, what to wear, '
  + 'what to carry, what the period suits doing, how it will feel, and the best way to get around. '
  + 'Use only the data provided. Never state, repeat or change a number, and do not use digits. '
  + 'When a value says not available, do not guess it and do not base advice on it. '
  + 'Write one or two plain sentences for each item, with no markdown, no links and no lists. '
  + 'Reply only with JSON in the format requested.';
const ADVICE_SCHEMA = {
  type: 'OBJECT',
  required: PERIOD_KEYS,
  properties: Object.fromEntries(PERIOD_KEYS.map((period) => [period, {
    type: 'OBJECT',
    required: ADVICE_KEYS,
    properties: Object.fromEntries(ADVICE_KEYS.map((key) => [key, { type: 'STRING' }])),
  }])),
};

// Only the weather facts and a clothing category go to the AI service. The email address, the
// zip code, the city and anything the user typed do not.
function buildPrompt(w, clothing) {
  const lines = [`Clothing category: ${clothing}`];
  for (const key of PERIOD_KEYS) {
    const period = w.periods[key];
    lines.push(`${period.name} (${period.window}):`);
    for (const item of Object.values(period.items)) lines.push(`  ${item.label}: ${item.text}`);
  }
  const keys = `{${ADVICE_KEYS.map((key) => `"${key}": "..."`).join(', ')}}`;
  lines.push(`Reply with JSON like {${PERIOD_KEYS.map((period) => `"${period}": ${keys}`).join(', ')}}.`);
  return lines.join('\n');
}

async function askGemini(prompt) {
  const key = await getApiKey();
  const res = await fetch(`${GEMINI_API_BASE}/v1beta/models/${GEMINI_MODEL}:generateContent`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-goog-api-key': key },
    body: JSON.stringify({
      systemInstruction: { parts: [{ text: SYSTEM_PROMPT }] },
      contents: [{ parts: [{ text: prompt }] }],
      generationConfig: { maxOutputTokens: 2000, responseMimeType: 'application/json', responseSchema: ADVICE_SCHEMA },
    }),
    signal: AbortSignal.timeout(15000),
  });
  if (!res.ok) {
    const err = new Error('gemini_http_error');
    err.status = res.status;
    throw err;
  }
  const data = await res.json();
  const parts = (data.candidates && data.candidates[0] && data.candidates[0].content
    && data.candidates[0].content.parts) || [];
  const text = parts.map((p) => p.text || '').join('').trim();
  if (!text) {
    const err = new Error('gemini_empty_response');
    err.status = 200;
    throw err;
  }
  return text;
}

const FORBIDDEN = /[0-9<>`{}[\]]|https?:|www\./i;

// The answer is used only if it has exactly the expected shape and short, safe text with no numbers.
function checkAdvice(text) {
  const bad = () => {
    const err = new Error('gemini_bad_answer');
    err.status = 200;
    return err;
  };
  let data;
  try {
    data = JSON.parse(text);
  } catch (err) {
    throw bad();
  }
  const sameKeys = (obj, keys) => obj && typeof obj === 'object' && !Array.isArray(obj)
    && Object.keys(obj).sort().join() === [...keys].sort().join();
  if (!sameKeys(data, PERIOD_KEYS)) throw bad();
  const advice = {};
  for (const period of PERIOD_KEYS) {
    if (!sameKeys(data[period], ADVICE_KEYS)) throw bad();
    advice[period] = {};
    for (const key of ADVICE_KEYS) {
      if (typeof data[period][key] !== 'string') throw bad();
      const sentence = data[period][key].split(/\s+/).join(' ').trim();
      if (sentence.length < 10 || sentence.length > 300 || FORBIDDEN.test(sentence)) throw bad();
      advice[period][key] = sentence;
    }
  }
  return advice;
}

// ---- Rule-based fallback ---------------------------------------------------------------
// Used when the AI service is unavailable, rate limited, capped, or its answer is unusable.

const DRESSING = {
  womenswear: [
    'a heavy coat, a sweater, thick tights or warm trousers, boots, a hat and gloves',
    'a warm coat with a sweater and long trousers or a skirt with tights',
    'a light jacket or cardigan with a blouse or dress and long trousers or leggings',
    'a light top with a skirt, dress, shorts or light trousers',
    'a light, breathable top with a dress, skirt or shorts',
  ],
  menswear: [
    'a heavy coat, a sweater, thermal layers, warm pants, boots, a hat and gloves',
    'a warm jacket over a sweater with long pants',
    'a light jacket or sweater over a shirt with long pants',
    'a t-shirt or short sleeved shirt with light pants or shorts',
    'a light, breathable shirt with shorts or light pants',
  ],
  neutral: [
    'a heavy coat, warm layers, warm pants, boots, a hat and gloves',
    'a warm jacket with layers and long pants',
    'a light jacket or sweater over a long sleeved top with long pants',
    'a t-shirt with light pants or shorts',
    'light, breathable clothes and shorts or light pants',
  ],
};
const BAND_LIMITS = [32, 50, 68, 80];

// One number from the facts, or null when that item is not available.
const factValue = (period, item, key) => {
  const entry = period.items[item];
  return entry && entry.available && entry.values[key] !== undefined ? entry.values[key] : null;
};

function roadText(period) {
  const rain = factValue(period, 'rain', 'category');
  const snow = factValue(period, 'snow', 'category');
  const low = factValue(period, 'temperature', 'low');
  const miles = factValue(period, 'visibility', 'miles');
  const wind = factValue(period, 'wind', 'sustained');
  const gust = factValue(period, 'wind', 'gust');
  const parts = [];
  if (snow === 'Heavy') parts.push('Heavy snow is likely, so roads may be slick or closed. Delay travel if you can.');
  else if (snow === 'Mild') parts.push('Some snow may leave roads slippery, so drive slowly.');
  if (rain === 'Heavy') parts.push('Heavy rain can cause standing water and poor grip, so allow extra time.');
  else if (rain === 'Mild') parts.push('Roads may be wet, so keep extra distance from other cars.');
  if ((rain === 'Mild' || rain === 'Heavy') && low !== null && low <= 34) parts.push('Rain near freezing can ice over, so watch for black ice.');
  if (miles !== null && miles < 1) parts.push('Visibility is poor, so use low beam headlights.');
  if ((wind !== null && wind >= 25) || (gust !== null && gust >= 40)) parts.push('Strong winds may push vehicles around on bridges and open roads.');
  if (parts.length) return parts.slice(0, 2).join(' ');
  if ([rain, snow, low, miles, wind].every((v) => v === null)) {
    return 'Road conditions cannot be judged because the weather data is not available.';
  }
  return 'Roads should be dry and clear, so normal driving conditions are expected.';
}

function dressingText(period, clothing) {
  const low = factValue(period, 'temperature', 'low');
  const high = factValue(period, 'temperature', 'high');
  if (low === null) return 'The temperature is not available, so check the forecast before choosing clothes.';
  const average = (low + high) / 2;
  const band = BAND_LIMITS.filter((limit) => average > limit).length;
  return `Wear ${DRESSING[clothing][band]}.`;
}

function accessoriesText(period) {
  const items = [];
  const rain = factValue(period, 'rain', 'category');
  const snow = factValue(period, 'snow', 'category');
  if (rain === 'Mild') items.push('an umbrella');
  if (rain === 'Heavy') items.push('a waterproof jacket and an umbrella');
  if (snow === 'Mild' || snow === 'Heavy') items.push('waterproof boots');
  const uv = factValue(period, 'uv', 'index');
  if (uv !== null && uv >= 3) items.push('sunscreen and sunglasses');
  const low = factValue(period, 'temperature', 'low');
  if (low !== null && low <= 40) items.push('gloves and a scarf');
  const wind = factValue(period, 'wind', 'sustained');
  if (wind !== null && wind >= 25) items.push('a windproof layer');
  return items.length ? `Bring ${items.join(', ')}.` : 'No special accessories are needed.';
}

function activitiesText(period) {
  const rain = factValue(period, 'rain', 'category');
  const snow = factValue(period, 'snow', 'category');
  const low = factValue(period, 'temperature', 'low');
  const high = factValue(period, 'temperature', 'high');
  const wind = factValue(period, 'wind', 'sustained');
  const uv = factValue(period, 'uv', 'index');
  if ([rain, snow, low, wind, uv].every((v) => v === null)) {
    return 'Activities cannot be suggested because the weather data is not available.';
  }
  if (snow === 'Heavy' || rain === 'Heavy') return 'Plan indoor activities, and keep any time outside short.';
  if (low !== null && low <= 20) return 'Keep outdoor time short, and save longer plans for a milder period.';
  if (high !== null && high >= 90) return 'Avoid hard exercise outdoors, and keep to the shade if you do go out.';
  if (rain === 'Mild' || snow === 'Mild') return 'Short outdoor trips are fine, but keep an indoor option ready.';
  if (wind !== null && wind >= 25) return 'A walk is fine, but the wind makes cycling and open ground unpleasant.';
  if (uv !== null && uv >= 6) return 'Good for being outside, though strong sun makes shade worth planning for.';
  return 'A good period for a walk, a run or other plans outdoors.';
}

function feelsLikeText(period) {
  const low = factValue(period, 'temperature', 'low');
  const high = factValue(period, 'temperature', 'high');
  const wind = factValue(period, 'wind', 'sustained');
  const humidity = factValue(period, 'humidity', 'percent');
  const rain = factValue(period, 'rain', 'category');
  if (low === null) return 'How it will feel cannot be judged because the temperature is not available.';
  if (wind !== null && wind >= 15 && low <= 50) return 'The wind will make it feel noticeably colder than the temperature suggests.';
  if ((rain === 'Mild' || rain === 'Heavy') && low <= 55) return 'Being wet will make it feel colder than the temperature suggests.';
  if (humidity !== null && humidity >= 70 && high !== null && high >= 75) {
    return 'Humid air will make it feel warmer and closer than the temperature suggests.';
  }
  if (humidity !== null && humidity <= 30) return 'The air will be dry, so it may feel sharper than the temperature suggests.';
  if (low <= 32) return 'It will feel cold, and bare skin will start to sting after a short while outside.';
  return 'It should feel close to the temperature shown, with nothing to make it worse.';
}

function gettingAroundText(period) {
  const rain = factValue(period, 'rain', 'category');
  const snow = factValue(period, 'snow', 'category');
  const miles = factValue(period, 'visibility', 'miles');
  const wind = factValue(period, 'wind', 'sustained');
  const low = factValue(period, 'temperature', 'low');
  const high = factValue(period, 'temperature', 'high');
  if ([rain, snow, miles, wind, low].every((v) => v === null)) {
    return 'The best way to travel cannot be judged because the weather data is not available.';
  }
  if (snow === 'Heavy') return 'Travel only if you must, and prefer public transport to driving yourself.';
  if (rain === 'Heavy' || (miles !== null && miles < 1)) return 'Driving beats walking here, and the journey will need extra time.';
  if (snow === 'Mild') return 'Walking is fine with proper footwear, but allow longer for any journey.';
  if (rain === 'Mild') return 'Short walks are fine with an umbrella, and cycling will be wet going.';
  if (wind !== null && wind >= 25) return 'Walking is fine, but cycling will be hard work against this wind.';
  if (high !== null && high >= 90) return 'Short walks are fine, but walking or cycling far will be tiring in this heat.';
  if (low !== null && low <= 32) return 'Walking is pleasant enough if you wrap up, and paths may be icy in places.';
  return 'Walking or cycling should both be pleasant, with nothing to slow a journey.';
}

function fallbackAdvice(w, clothing) {
  const advice = {};
  for (const key of PERIOD_KEYS) {
    const period = w.periods[key];
    advice[key] = {
      road_conditions: roadText(period), dressing: dressingText(period, clothing),
      accessories: accessoriesText(period), activities: activitiesText(period),
      feels_like: feelsLikeText(period), getting_around: gettingAroundText(period),
    };
  }
  return advice;
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
  const ssl = process.env.DB_SSL === 'off' ? false : { ca: fs.readFileSync(DB_CA_FILE, 'utf8') };
  const pool = new Pool({
    host: DB_HOST, port: DB_PORT, database: DB_NAME, user: DB_USER, password, ssl,
    max: 4, connectionTimeoutMillis: 3000, idleTimeoutMillis: 30000, statement_timeout: 3000,
  });
  pool.on('error', (err) => log('db_error', { stage: 'pool', message: err.message }));
  try {
    await pool.query(`CREATE TABLE IF NOT EXISTS reports (
      zip CHAR(5) NOT NULL,
      day DATE NOT NULL,
      clothing TEXT NOT NULL,
      payload JSONB NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      PRIMARY KEY (zip, day, clothing))`);
    await pool.query(`CREATE TABLE IF NOT EXISTS counters (
      name TEXT NOT NULL,
      bucket BIGINT NOT NULL,
      n INTEGER NOT NULL,
      expires_at TIMESTAMPTZ NOT NULL,
      PRIMARY KEY (name, bucket))`);
  } catch (err) {
    await pool.end().catch(() => {});
    throw err;
  }
  return pool;
}

function getPool() {
  if (!poolPromise) {
    poolPromise = createPool().catch((err) => {
      poolPromise = null;
      throw err;
    });
  }
  return poolPromise;
}

// Run one database call. Any failure is logged, and the database is skipped for a short time.
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

// One statement adds one to a counter only while it is under its limit. Because it is a single
// statement, two requests arriving together cannot both take the last place.
const COUNTER_SQL = `INSERT INTO counters (name, bucket, n, expires_at)
  VALUES ($1, $2, 1, now() + make_interval(secs => $3))
  ON CONFLICT (name, bucket) DO UPDATE SET n = counters.n + 1 WHERE counters.n < $4
  RETURNING n`;
const NO_LIMIT = 2147483647;

// Count one use of a name in the current window. The value is true while the count is within the limit.
const hit = (name, windowSeconds, limit) => withDb('counter', async (pool) => {
  const bucket = Math.floor(deps.now().getTime() / 1000 / windowSeconds);
  const r = await pool.query(COUNTER_SQL, [name, bucket, windowSeconds * 2, limit]);
  return r.rows.length > 0;
});

const readCounts = (names) => withDb('usage', async (pool) => {
  const bucket = Math.floor(deps.now().getTime() / 1000 / 86400);
  const r = await pool.query('SELECT name, n FROM counters WHERE bucket = $1 AND name = ANY($2)', [bucket, names]);
  return Object.fromEntries(names.map((name) => [name, (r.rows.find((row) => row.name === name) || { n: 0 }).n]));
});

// Saved reports are found by zip, day and clothing category. No email address is in the table.
const findReport = (zip, day, clothing) => withDb('read', async (pool) => {
  const r = await pool.query(
    `SELECT payload, EXTRACT(EPOCH FROM now() - created_at)::int AS age FROM reports
     WHERE zip = $1 AND day = $2 AND clothing = $3 AND created_at > now() - make_interval(secs => $4)`,
    [zip, day, clothing, REPORT_SECONDS]);
  return r.rows.length ? { payload: r.rows[0].payload, age: r.rows[0].age } : null;
});

// Keep the report for a short time, and remove every row and counter that has expired.
const saveReport = (zip, day, clothing, payload) => withDb('write', async (pool) => {
  await pool.query(
    `INSERT INTO reports (zip, day, clothing, payload) VALUES ($1, $2, $3, $4)
     ON CONFLICT (zip, day, clothing) DO UPDATE SET payload = EXCLUDED.payload, created_at = now()`,
    [zip, day, clothing, JSON.stringify(payload)]);
  await pool.query('DELETE FROM reports WHERE created_at < now() - make_interval(secs => $1)', [REPORT_SECONDS]);
  await pool.query('DELETE FROM counters WHERE expires_at < now()');
});

// ---- Email through Amazon SNS ----------------------------------------------------------
// Every address has its own subscription with a filter on a hash of the address, so a report
// published for one address is delivered to that address only.

const recipientTag = (email) => crypto.createHash('sha256').update(email).digest('hex').slice(0, 32);

// The subscription ARN of this address on the topic, 'PendingConfirmation', or null.
async function findSubscription(email) {
  let token;
  do {
    const page = await sns.send(new ListSubscriptionsByTopicCommand({ TopicArn: SNS_TOPIC_ARN, NextToken: token }));
    for (const item of page.Subscriptions || []) {
      if (item.Protocol === 'email' && String(item.Endpoint).toLowerCase() === email) return item.SubscriptionArn;
    }
    token = page.NextToken;
  } while (token);
  return null;
}

const subscribe = (email) => sns.send(new SubscribeCommand({
  TopicArn: SNS_TOPIC_ARN, Protocol: 'email', Endpoint: email, ReturnSubscriptionArn: true,
  Attributes: { FilterPolicy: JSON.stringify({ recipient: [recipientTag(email)] }) },
}));

const publish = (email, subject, message) => sns.send(new PublishCommand({
  TopicArn: SNS_TOPIC_ARN, Subject: subject, Message: message,
  MessageAttributes: { recipient: { DataType: 'String', StringValue: recipientTag(email) } },
}));

// 'sent' when SNS accepted the report, 'confirm' when the address still has to confirm.
async function sendReport(email, subject, message) {
  const arn = await deps.findSubscription(email);
  if (!arn || arn === 'PendingConfirmation') {
    await deps.subscribe(email);
    return 'confirm';
  }
  await deps.publish(email, subject, message);
  return 'sent';
}

// ---- The report ------------------------------------------------------------------------

async function fetchWeather(zip, day) {
  try {
    const res = await fetch(`${WEATHER_URL}/weather?zip=${zip}&date=${day}`, { signal: AbortSignal.timeout(20000) });
    return { status: res.status, body: await res.json() };
  } catch (err) {
    return { status: 0, body: null };
  }
}

// Everything that reaches outside this process. The tests replace these.
const deps = {
  fetchWeather, askGemini, hit, readCounts, findReport, saveReport, findSubscription, subscribe, publish,
  now: () => new Date(),
};

const WEATHER_ERRORS = {
  invalid_zip: [400, US_ONLY],
  invalid_date: [400, 'Choose a date from the calendar.'],
  date_out_of_range: [400, "Choose today or one of the next six days at the ZIP code's location."],
  zip_not_found: [404, 'That ZIP code was not found. Check the number and try again.'],
  no_nws_coverage: [404, 'The National Weather Service has no forecast for that ZIP code.'],
};
const UNAVAILABLE = 'Weather data is unavailable right now. Try again in a few minutes.';
const TOO_MANY = 'Too many requests from your address. Wait a minute and try again.';

const hasData = (w) => PERIOD_KEYS.some((key) => Object.values(w.periods[key].items).some((item) => item.available));

// The Gemini caps. If the counters cannot be reached the cap cannot be enforced, so Gemini is not called.
async function geminiAllowed() {
  const day = await deps.hit('gemini-day', 86400, GEMINI_DAILY_CAP);
  if (day.state === 'disabled') return true;
  if (day.state !== 'ok' || !day.value) return false;
  const minute = await deps.hit('gemini-minute', 60, GEMINI_PER_MINUTE);
  return minute.state === 'ok' && minute.value;
}

// Returns { advice, source, note }. Any problem with Gemini leads to the rule-based fallback.
async function getAdvice(w, clothing) {
  if (!hasData(w)) return { advice: fallbackAdvice(w, clothing), source: 'fallback', note: 'no measured values were available' };
  if (!(await geminiAllowed())) {
    log('gemini_cap_reached', {});
    return { advice: fallbackAdvice(w, clothing), source: 'fallback', note: 'the Gemini limit for today or this minute was reached' };
  }
  await deps.hit('gemini-calls', 86400, NO_LIMIT);
  try {
    return { advice: checkAdvice(await deps.askGemini(buildPrompt(w, clothing))), source: 'gemini', note: '' };
  } catch (err) {
    log('gemini_error', { status: err.status || 0, reason: err.name === 'TimeoutError' ? 'timeout' : err.message });
    await deps.hit('gemini-failures', 86400, NO_LIMIT);
    return { advice: fallbackAdvice(w, clothing), source: 'fallback', note: 'Gemini could not be used' };
  }
}

// Shared by the web page, the JSON endpoint and the email. Returns { status, body }.
async function getReport(zip, day, clothing) {
  const started = Date.now();
  log('advice_request', { zip });

  const saved = await deps.findReport(zip, day, clothing);
  if (saved.state === 'ok' && saved.value && dayInWindow(day, localDay(deps.now(), saved.value.payload.timezone))) {
    log('advice_success', { zip, source: saved.value.payload.source, saved: true, ms: Date.now() - started });
    return { status: 200, body: { ...saved.value.payload, saved: true, ageSeconds: saved.value.age } };
  }

  const weather = await deps.fetchWeather(zip, day);
  if (weather.status !== 200 || !weather.body || !weather.body.periods) {
    const known = weather.body && WEATHER_ERRORS[weather.body.error];
    if (known) {
      log('advice_rejected', { reason: weather.body.error, zip });
      return { status: known[0], body: { error: weather.body.error, message: known[1] } };
    }
    log('weather_call_failed', { zip, status: weather.status });
    return { status: 502, body: { error: 'weather_service_unavailable', message: UNAVAILABLE } };
  }

  const w = weather.body;
  const { advice, source, note } = await getAdvice(w, clothing);
  const body = {
    zip, date: day, city: w.city, state: w.state, timezone: w.timezone, periods: w.periods, credits: w.credits,
    weatherCache: w.cache, weatherAgeSeconds: w.ageSeconds || 0,
    advice, source, model: source === 'gemini' ? GEMINI_MODEL : null, note,
  };
  if (source === 'gemini' && w.cache !== 'stale') await deps.saveReport(zip, day, clothing, body);
  log('advice_success', { zip, source, saved: false, ms: Date.now() - started });
  return { status: 200, body: { ...body, saved: false, ageSeconds: 0 } };
}

const ADVICE_LABELS = {
  road_conditions: 'Suggested road conditions', dressing: 'Suggested dressing',
  accessories: 'Suggested accessories to carry', activities: 'Suggested activities',
  feels_like: 'How it will feel', getting_around: 'Getting around',
};
const SOURCE_LABELS = { gemini: 'Google Gemini', fallback: 'Rule-based fallback' };

// The report as plain text for the email. A table reads as one "Label: value" line per item.
function reportText(report) {
  const lines = [`Weather report for ${report.city}, ${report.state} ${report.zip} on ${report.date}`, ''];
  for (const key of PERIOD_KEYS) {
    const period = report.periods[key];
    lines.push(`${period.name} (${period.window})`);
    for (const item of Object.values(period.items)) lines.push(`  ${item.label}: ${item.text}`);
    for (const name of ADVICE_KEYS) lines.push(`  ${ADVICE_LABELS[name]}: ${report.advice[key][name]}`);
    lines.push('');
  }
  lines.push(`Advice written by: ${SOURCE_LABELS[report.source]}`);
  lines.push(`Weather data: ${report.credits.join('; ')}`);
  return lines.join('\n');
}

// ---- Pages -----------------------------------------------------------------------------

const MAIL_MESSAGES = {
  sent: ['ok', 'Amazon SNS accepted the report for delivery. It can take a few minutes to arrive.'],
  confirm: ['ok', 'Waiting for confirmation. Amazon SNS sent a message to this address. Open it, choose Confirm subscription, '
    + 'then press the button again. Check the spam folder. The link expires after 48 hours.'],
  limit: ['err', 'Too many email requests were made from your address. Wait a while and try again.'],
  failed: ['err', 'The email was not sent because the email service did not accept it. The report above is still correct.'],
};

function reportHtml(report, form, mail) {
  const cell = (item) => `${escapeHtml(item.text)}<br><small>${
    escapeHtml(item.available ? `source: ${item.source}` : item.reason)}</small>`;
  const items = Object.keys(report.periods.morning.items);
  const rows = items.map((name) => `<tr><th>${escapeHtml(report.periods.morning.items[name].label)}</th>
    <td>${cell(report.periods.morning.items[name])}</td><td>${cell(report.periods.night.items[name])}</td></tr>`);
  for (const name of ADVICE_KEYS) {
    rows.push(`<tr><th>${escapeHtml(ADVICE_LABELS[name])}</th>
    <td>${escapeHtml(report.advice.morning[name])}</td><td>${escapeHtml(report.advice.night[name])}</td></tr>`);
  }
  const stale = report.weatherCache === 'stale'
    ? `<p class="err">The weather services did not answer, so older saved data from ${escapeHtml(Math.round(report.weatherAgeSeconds / 60))} minutes ago is shown.</p>` : '';
  const savedNote = report.saved ? ` This is a saved report from ${escapeHtml(Math.round(report.ageSeconds / 60))} minutes ago.` : '';
  const hidden = ['date', 'zip', 'gender', 'email'].map((name) =>
    `<input type="hidden" name="${name}" value="${escapeHtml(form[name])}">`).join('');
  const mailNote = MAIL_MESSAGES[mail] ? `<p class="${MAIL_MESSAGES[mail][0]}">${escapeHtml(MAIL_MESSAGES[mail][1])}</p>` : '';
  return `<h2>${escapeHtml(report.city)}, ${escapeHtml(report.state)} ${escapeHtml(report.zip)} on ${escapeHtml(report.date)}</h2>
    ${stale}
    <table><tr><th></th><th>${escapeHtml(report.periods.morning.name)}<br><small>${escapeHtml(report.periods.morning.window)}</small></th>
    <th>${escapeHtml(report.periods.night.name)}<br><small>${escapeHtml(report.periods.night.window)}</small></th></tr>
    ${rows.join('\n')}</table>
    <p><small>Suggestion source: ${escapeHtml(SOURCE_LABELS[report.source])}${report.note ? ` (${escapeHtml(report.note)})` : ''}.${savedNote}</small></p>
    <p>Measured values come from the weather services named in each cell. ${escapeHtml(report.credits.join('; '))}</p>
    <form method="POST" action="/email">${hidden}
    <button type="submit">Email this report to ${escapeHtml(form.email)}</button></form>
    ${mailNote}`;
}

function page({ form = {}, error = '', report = null, mail = '' }) {
  const today = deps.now().toISOString().slice(0, 10);
  const value = (name) => escapeHtml(form[name] || '');
  const options = Object.keys(GENDERS).map((key) =>
    `<option value="${key}"${(form.gender || 'neutral') === key ? ' selected' : ''}>${escapeHtml(GENDER_LABELS[key])}</option>`).join('');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Weather Advisor</title>
<style>body{font-family:sans-serif;max-width:56rem;margin:1.5rem auto;padding:0 1rem}label{display:block;margin:.5rem 0}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:.3rem .5rem;text-align:left;vertical-align:top}
.err{color:#a00;font-weight:bold}.ok{color:#064;font-weight:bold}small{color:#555}</style></head><body>
<h1>Weather Advisor</h1>
<p>US locations only. Choose a day from today to six days ahead at the zip code's location.</p>
${error ? `<p class="err" role="alert">Sorry: ${escapeHtml(error)}</p>` : ''}
<form method="POST" action="/report">
  <label>Date <input type="date" name="date" value="${value('date')}" min="${addDays(today, -1)}" max="${addDays(today, 7)}" required></label>
  <label>US zip code <input name="zip" value="${value('zip')}" pattern="[0-9]{5}" maxlength="5" required></label>
  <label>Gender <select name="gender">${options}</select></label>
  <small>Used only to tailor the clothing suggestion. It is not stored with your email address.</small>
  <label>Email address <input type="email" name="email" value="${value('email')}" maxlength="254" required></label>
  <p><small>Sent to Google Gemini: the weather facts and a clothing category. Not sent: your email address, zip code, city or anything you typed.</small></p>
  <button type="submit">What should I wear?</button>
</form>
${report ? reportHtml(report, form, mail) : ''}
</body></html>`;
}

// ---- Routes ----------------------------------------------------------------------------

app.use(express.urlencoded({ extended: false, limit: '4kb' }));

// The load balancer adds the real client address at the end of X-Forwarded-For. Only a hash is kept.
function visitorId(req) {
  const forwarded = String(req.headers['x-forwarded-for'] || '');
  const address = forwarded ? forwarded.split(',').pop().trim() : (req.socket.remoteAddress || 'unknown');
  return crypto.createHash('sha256').update(address).digest('hex').slice(0, 16);
}

// A per-visitor rate limit. If the counters cannot be reached, requests are let through.
async function visitorAllowed(req) {
  const result = await deps.hit(`visitor:${visitorId(req)}`, 60, VISITOR_PER_MINUTE);
  return result.state !== 'ok' || result.value;
}

const formFields = (source) => Object.fromEntries(['date', 'zip', 'gender', 'email'].map((name) =>
  [name, typeof source[name] === 'string' ? source[name].slice(0, 254) : '']));

// An error inside an async handler goes to the error page instead of stopping the process.
const safely = (handler) => (req, res, next) => handler(req, res).catch(next);

app.get('/', (req, res) => {
  res.send(page({}));
});

app.post('/report', safely(async (req, res) => {
  const form = formFields(req.body || {});
  if (!(await visitorAllowed(req))) return res.status(429).send(page({ form, error: TOO_MANY }));
  const input = validateRequest(form, deps.now(), true);
  if (!input.ok) {
    log('advice_rejected', { reason: input.error });
    return res.status(400).send(page({ form, error: input.message }));
  }
  const { status, body } = await getReport(input.zip, input.date, input.clothing);
  if (status !== 200) return res.status(status).send(page({ form, error: body.message }));
  return res.send(page({ form, report: body }));
}));

// The report that is mailed is built again on the server from the checked fields.
app.post('/email', safely(async (req, res) => {
  const form = formFields(req.body || {});
  if (!(await visitorAllowed(req))) return res.status(429).send(page({ form, error: TOO_MANY }));
  const input = validateRequest(form, deps.now(), true);
  if (!input.ok) {
    log('advice_rejected', { reason: input.error });
    return res.status(400).send(page({ form, error: input.message }));
  }
  const { status, body } = await getReport(input.zip, input.date, input.clothing);
  if (status !== 200) return res.status(status).send(page({ form, error: body.message }));

  let mail;
  const allowed = await deps.hit(`email:${visitorId(req)}`, 3600, EMAILS_PER_VISITOR_HOUR);
  if (allowed.state === 'unavailable') mail = 'failed';
  else if (allowed.state === 'ok' && !allowed.value) mail = 'limit';
  else {
    try {
      mail = await sendReport(input.email, `Weather report for ${body.date}`, reportText(body));
      if (mail === 'sent') await deps.hit('emails-sent', 86400, NO_LIMIT);
    } catch (err) {
      log('email_error', { name: err.name });
      mail = 'failed';
    }
  }
  log('email_request', { result: mail });
  return res.send(page({ form, report: body, mail }));
}));

app.get('/advice', safely(async (req, res) => {
  if (!(await visitorAllowed(req))) {
    return res.status(429).set('Retry-After', '60').json({ error: 'rate_limited', message: TOO_MANY });
  }
  const fields = { zip: req.query.zip, date: req.query.date, gender: req.query.gender === undefined ? 'neutral' : req.query.gender };
  const input = validateRequest(fields, deps.now(), false);
  if (!input.ok) {
    log('advice_rejected', { reason: input.error });
    return res.status(400).json({ error: input.error, message: input.message });
  }
  const { status, body } = await getReport(input.zip, input.date, input.clothing);
  return res.status(status).json(body);
}));

// Counts only, no personal data. delete_weather_stack.py reads this for the cost report.
app.get('/usage', safely(async (req, res) => {
  const counts = await deps.readCounts(['gemini-calls', 'gemini-failures', 'emails-sent']);
  if (counts.state !== 'ok') return res.status(503).json({ error: `database_${counts.state}` });
  return res.json({
    date: deps.now().toISOString().slice(0, 10), gemini_calls: counts.value['gemini-calls'],
    gemini_failures: counts.value['gemini-failures'], emails_sent: counts.value['emails-sent'],
    gemini_daily_cap: GEMINI_DAILY_CAP,
  });
}));

app.use((err, req, res, next) => {
  const status = [400, 413].includes(err.status) ? err.status : 500;
  log('request_error', { status, type: err.type || err.name });
  const messages = { 400: 'The request could not be read.', 413: 'The request was too large.', 500: 'Something went wrong. Try again.' };
  res.status(status).send(page({ error: messages[status] }));
});

if (require.main === module) {
  app.listen(PORT, () => log('advisor_started', { port: PORT, model: GEMINI_MODEL }));
}

module.exports = {
  app, deps, COUNTER_SQL, GENDERS, validateZip, validateDate, validateGender, validateEmail, validateRequest,
  buildPrompt, checkAdvice, fallbackAdvice, recipientTag, sendReport, reportText, escapeHtml, page,
};