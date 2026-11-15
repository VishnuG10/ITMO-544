const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

console.log = () => {};
const weather = require('./app.js');

const CHICAGO = 'America/Chicago';
const DAY = '2026-11-17';
const NOW = new Date('2026-11-16T15:00:00Z');

// ---- Builders for responses in the shape the real services send ------------------------

function layer(uom, startIso, stepHours, values) {
  const start = new Date(startIso).getTime();
  return {
    uom,
    values: values.map((value, i) => ({
      validTime: `${new Date(start + i * stepHours * 3600000).toISOString().replace('.000Z', '+00:00')}/PT${stepHours}H`,
      value,
    })),
  };
}

const repeat = (count, fn) => Array.from({ length: count }, (_, i) => fn(i));

// 2026-11-17 in Chicago is UTC-6, so the Morning hours 06 to 11 are 12:00 to 17:00 UTC.
function sampleGrid() {
  const start = '2026-11-17T06:00:00Z';
  return {
    temperature: layer('wmoUnit:degC', start, 1, repeat(24, (i) => i)),
    windSpeed: layer('wmoUnit:km_h-1', start, 1, repeat(24, (i) => 10 + i)),
    windGust: layer('wmoUnit:km_h-1', start, 1, repeat(24, (i) => 30 + i)),
    visibility: layer('wmoUnit:m', start, 1, repeat(24, (i) => 16093.44 - i * 100)),
    relativeHumidity: layer('wmoUnit:percent', start, 1, repeat(24, (i) => 50 + i)),
    probabilityOfPrecipitation: layer('wmoUnit:percent', start, 6, [10, 70, 30, 80]),
    quantitativePrecipitation: layer('wmoUnit:mm', start, 6, [0, 12.7, 0, 60.96]),
    snowfallAmount: layer('wmoUnit:mm', start, 6, [0, 0, 0, 0]),
  };
}

function sampleMeteo(overrides = {}) {
  const time = repeat(48, (i) => `2026-11-${17 + Math.floor(i / 24)}T${String(i % 24).padStart(2, '0')}:00`);
  const flat = (value) => repeat(48, () => value);
  return {
    hourly_units: {
      temperature_2m: '°C', wind_speed_10m: 'km/h', wind_gusts_10m: 'km/h', visibility: 'm',
      relative_humidity_2m: '%', precipitation_probability: '%', precipitation: 'mm', snowfall: 'cm', uv_index: '',
    },
    hourly: {
      time, temperature_2m: flat(20), wind_speed_10m: flat(16.0934), wind_gusts_10m: flat(32.1869),
      visibility: flat(8046.72), relative_humidity_2m: flat(40), precipitation_probability: flat(5),
      precipitation: flat(0), snowfall: flat(0), uv_index: repeat(48, (i) => (i % 24 === 10 ? 4.26 : 1)),
      ...overrides,
    },
  };
}

const items = (grid, meteo, period, now = NOW, day = DAY) =>
  weather.buildFacts(grid, meteo, CHICAGO, day, now).periods[period].items;

// ---- Units -----------------------------------------------------------------------------

test('units are converted to US units', () => {
  assert.strictEqual(weather.toUsUnits('temperature', 0, 'wmoUnit:degC'), 32);
  assert.strictEqual(weather.toUsUnits('temperature', 100, '°C'), 212);
  assert.strictEqual(weather.toUsUnits('temperature', 50, 'wmoUnit:degF'), 50);
  assert.ok(Math.abs(weather.toUsUnits('speed', 100, 'wmoUnit:km_h-1') - 62.1371) < 0.001);
  assert.ok(Math.abs(weather.toUsUnits('distance', 1609.344, 'wmoUnit:m') - 1) < 1e-9);
  assert.ok(Math.abs(weather.toUsUnits('depth', 25.4, 'wmoUnit:mm') - 1) < 1e-9);
  assert.ok(Math.abs(weather.toUsUnits('depth', 2.54, 'cm') - 1) < 1e-9);
  assert.strictEqual(weather.toUsUnits('percent', 55, '%'), 55);
});

test('an unknown unit is an error, not a guess', () => {
  assert.throws(() => weather.toUsUnits('speed', 10, 'furlongs'));
  assert.throws(() => weather.toUsUnits('temperature', 10, 'K'));
  assert.throws(() => weather.toUsUnits('percent', 10, 'fraction'));
});

// ---- Dates, time zones and periods -----------------------------------------------------

test('only real calendar dates written as YYYY-MM-DD are accepted', () => {
  for (const good of ['2026-11-17', '2028-02-29']) assert.strictEqual(weather.isRealDate(good), true, good);
  for (const bad of ['2026-02-30', '2026-13-01', '2027-02-29', '11/17/2026', '2026-11-17T00:00', '', 'tomorrow']) {
    assert.strictEqual(weather.isRealDate(bad), false, bad);
  }
});

test('the local hour depends on the location of the zip code', () => {
  const instant = new Date('2026-11-17T12:00:00Z');
  assert.strictEqual(weather.localHourKey(instant, 'America/Chicago'), '2026-11-17T06');
  assert.strictEqual(weather.localHourKey(instant, 'America/Anchorage'), '2026-11-17T03');
  assert.strictEqual(weather.localHourKey(instant, 'America/Puerto_Rico'), '2026-11-17T08');
  assert.strictEqual(weather.localDay(new Date('2026-11-17T03:00:00Z'), 'America/Chicago'), '2026-11-16');
});

test('daylight saving changes keep the local hours right', () => {
  assert.strictEqual(weather.localHourKey(new Date('2026-10-31T11:00:00Z'), CHICAGO), '2026-10-31T06');
  assert.strictEqual(weather.localHourKey(new Date('2026-11-01T12:00:00Z'), CHICAGO), '2026-11-01T06');
  assert.strictEqual(weather.localHourKey(new Date('2026-03-07T12:00:00Z'), CHICAGO), '2026-03-07T06');
  assert.strictEqual(weather.localHourKey(new Date('2026-03-08T11:00:00Z'), CHICAGO), '2026-03-08T06');
  const grid = { temperature: layer('wmoUnit:degC', '2026-11-01T00:00:00Z', 1, repeat(30, (i) => i)) };
  const morning = items(grid, null, 'morning', new Date('2026-10-31T15:00:00Z'), '2026-11-01');
  assert.deepStrictEqual(morning.temperature.values, { low: 54, high: 63 });
});

test('Morning is 06 to 11 and Night is 18 to 23 on the selected day', () => {
  assert.deepStrictEqual(weather.periodKeys(DAY, 'morning'),
    ['06', '07', '08', '09', '10', '11'].map((h) => `${DAY}T${h}`));
  assert.deepStrictEqual(weather.periodKeys(DAY, 'night'),
    ['18', '19', '20', '21', '22', '23'].map((h) => `${DAY}T${h}`));
});

test('the window is today through six days ahead', () => {
  assert.strictEqual(weather.dayInWindow('2026-11-16', '2026-11-16'), true);
  assert.strictEqual(weather.dayInWindow('2026-11-22', '2026-11-16'), true);
  assert.strictEqual(weather.dayInWindow('2026-11-23', '2026-11-16'), false);
  assert.strictEqual(weather.dayInWindow('2026-11-15', '2026-11-16'), false);
  assert.strictEqual(weather.dayInWindow('2026-12-02', '2026-11-28'), true);
});

test('valid times with durations are read, and amounts are shared over their hours', () => {
  assert.strictEqual(weather.parseValidTime('2026-11-17T12:00:00+00:00/PT1H').hours, 1);
  assert.strictEqual(weather.parseValidTime('2026-11-17T12:00:00+00:00/PT6H').hours, 6);
  assert.strictEqual(weather.parseValidTime('2026-11-17T12:00:00+00:00/P1D').hours, 24);
  assert.strictEqual(weather.parseValidTime('2026-11-17T12:00:00+00:00/P1DT6H').hours, 30);
  const amount = { values: [{ validTime: '2026-11-17T12:00:00+00:00/PT6H', value: 12 }] };
  const shared = weather.expandNwsLayer(amount, CHICAGO, true);
  assert.strictEqual(shared.size, 6);
  assert.strictEqual(shared.get('2026-11-17T06'), 2);
  assert.strictEqual(weather.expandNwsLayer(amount, CHICAGO, false).get('2026-11-17T11'), 12);
});

// ---- Categories ------------------------------------------------------------------------

test('rain thresholds on, just below and just above each boundary', () => {
  const cat = (chance, rate, high = 50) => weather.rainCategory(chance, rate, high).category;
  assert.strictEqual(cat(80, 0.009), 'No');
  assert.strictEqual(cat(80, 0.01), 'Mild');
  assert.strictEqual(cat(80, 0.30), 'Mild');
  assert.strictEqual(cat(80, 0.301), 'Heavy');
  assert.strictEqual(cat(19, 0.5), 'No');
  assert.strictEqual(cat(20, 0.5), 'Heavy');
  assert.strictEqual(cat(null, 0.5), 'Heavy');
});

test('rain from the chance alone is never Heavy, and nothing known gives nothing', () => {
  assert.strictEqual(weather.rainCategory(59, null, 50).category, 'No');
  assert.strictEqual(weather.rainCategory(60, null, 50).category, 'Mild');
  assert.strictEqual(weather.rainCategory(100, null, 50).category, 'Mild');
  assert.match(weather.rainCategory(60, null, 50).basis, /chance alone/);
  assert.strictEqual(weather.rainCategory(null, null, 50), null);
});

test('precipitation in a period that stays at or below freezing is not counted as rain', () => {
  assert.strictEqual(weather.rainCategory(90, 0.5, 32).category, 'No');
  assert.strictEqual(weather.rainCategory(90, 0.5, 33).category, 'Heavy');
});

test('snow thresholds on, just below and just above each boundary', () => {
  const cat = (chance, total, low = 20) => weather.snowCategory(chance, total, low).category;
  assert.strictEqual(cat(80, 0.09), 'No');
  assert.strictEqual(cat(80, 0.1), 'Mild');
  assert.strictEqual(cat(80, 1.99), 'Mild');
  assert.strictEqual(cat(80, 2.0), 'Heavy');
  assert.strictEqual(cat(19, 5), 'No');
  assert.strictEqual(cat(20, 5), 'Heavy');
  assert.strictEqual(cat(60, null, 32), 'Mild');
  assert.strictEqual(cat(60, null, 33), 'No');
  assert.strictEqual(cat(59, null, 10), 'No');
  assert.strictEqual(weather.snowCategory(null, null, 10), null);
});

test('UV levels follow the WHO scale', () => {
  assert.deepStrictEqual([0, 2.9, 3, 5.9, 6, 7.9, 8, 10.9, 11].map(weather.uvLevel),
    ['Low', 'Low', 'Moderate', 'Moderate', 'High', 'High', 'Very high', 'Very high', 'Extreme']);
});

// ---- Aggregation -----------------------------------------------------------------------

test('each item is one value for the period, with its source', () => {
  const morning = items(sampleGrid(), sampleMeteo(), 'morning');
  assert.deepStrictEqual(morning.temperature.values, { low: 43, high: 52 });
  assert.strictEqual(morning.temperature.text, '43 to 52 F');
  assert.strictEqual(morning.temperature.source, 'NWS');
  assert.deepStrictEqual(morning.wind.values, { sustained: 13, gust: 25 });
  assert.strictEqual(morning.wind.text, 'up to 13 mph, gusts to 25 mph');
  assert.strictEqual(morning.visibility.values.miles, 9.3);
  assert.strictEqual(morning.humidity.values.percent, 59);
  assert.strictEqual(morning.uv.text, '4.3 (Moderate)');
  assert.strictEqual(morning.uv.source, 'Open-Meteo');
  assert.strictEqual(morning.rain.category, 'Mild');
  assert.deepStrictEqual(morning.rain.values, { category: 'Mild', chance: 70, amount: 0.083 });
  assert.strictEqual(morning.snow.category, 'No');
});

test('the heaviest hour decides the rain category for the period', () => {
  const night = items(sampleGrid(), sampleMeteo(), 'night');
  assert.strictEqual(night.rain.category, 'Heavy');
  assert.strictEqual(night.rain.values.chance, 80);
  assert.match(night.rain.basis, /0\.30/);
});

test('snow uses the total of the period', () => {
  const grid = sampleGrid();
  grid.snowfallAmount = layer('wmoUnit:mm', '2026-11-17T06:00:00Z', 6, [0, 50.8, 0, 0]);
  grid.temperature = layer('wmoUnit:degC', '2026-11-17T06:00:00Z', 1, repeat(24, () => -5));
  const morning = items(grid, sampleMeteo(), 'morning');
  assert.strictEqual(morning.snow.category, 'Heavy');
  assert.strictEqual(morning.snow.values.amount, 2);
  assert.strictEqual(morning.rain.category, 'No');
  assert.match(morning.rain.basis, /freezing/);
});

test('UV comes only from Open-Meteo', () => {
  const morning = items(sampleGrid(), null, 'morning');
  assert.strictEqual(morning.uv.available, false);
  assert.strictEqual(morning.uv.text, 'not available');
  assert.match(morning.uv.reason, /neither/);
  assert.strictEqual(morning.temperature.available, true);
});

test('a missing layer is reported, not invented', () => {
  const grid = sampleGrid();
  delete grid.windGust;
  delete grid.visibility;
  const withSecond = items(grid, sampleMeteo(), 'morning');
  assert.strictEqual(withSecond.wind.values.gust, 20);
  assert.strictEqual(withSecond.wind.source, 'NWS and Open-Meteo');
  assert.strictEqual(withSecond.visibility.source, 'Open-Meteo');
  assert.strictEqual(withSecond.visibility.values.miles, 5);
  const alone = items(grid, null, 'morning');
  assert.strictEqual(alone.wind.values.gust, null);
  assert.strictEqual(alone.wind.text, 'up to 13 mph');
  assert.strictEqual(alone.visibility.available, false);
});

test('an empty layer and null values are gaps, not zeros', () => {
  const grid = sampleGrid();
  grid.visibility = { uom: 'wmoUnit:m', values: [] };
  const meteo = sampleMeteo({ visibility: repeat(48, () => null), uv_index: repeat(48, () => null) });
  const morning = items(grid, meteo, 'morning');
  assert.strictEqual(morning.visibility.available, false);
  assert.strictEqual(morning.uv.available, false);
  assert.strictEqual(morning.visibility.values, undefined);
});

test('with no source at all every item is not available with a reason', () => {
  const morning = items(null, null, 'morning');
  for (const item of Object.values(morning)) {
    assert.strictEqual(item.available, false);
    assert.ok(item.reason.length > 10);
  }
});

test('a series with an unknown unit is left out', () => {
  const grid = sampleGrid();
  grid.windSpeed.uom = 'wmoUnit:furlongs';
  const morning = items(grid, null, 'morning');
  assert.strictEqual(morning.wind.available, false);
});

test('a period that has already passed says so', () => {
  const lateEvening = new Date('2026-11-18T02:30:00Z');
  const facts = weather.buildFacts(sampleGrid(), sampleMeteo(), CHICAGO, DAY, lateEvening);
  assert.strictEqual(facts.periods.morning.items.temperature.reason, 'this period has already passed');
  assert.strictEqual(facts.periods.night.items.temperature.available, true);
  const duringMorning = new Date('2026-11-17T17:30:00Z');
  assert.strictEqual(items(sampleGrid(), sampleMeteo(), 'morning', duringMorning).temperature.available, true);
});

test('credits name only the sources that were used', () => {
  const both = weather.buildFacts(sampleGrid(), sampleMeteo(), CHICAGO, DAY, NOW).credits.join(' ');
  assert.match(both, /National Weather Service/);
  assert.match(both, /Open-Meteo/);
  const nwsOnly = weather.buildFacts(sampleGrid(), null, CHICAGO, DAY, NOW).credits.join(' ');
  assert.doesNotMatch(nwsOnly, /Open-Meteo/);
});

// ---- The service, with the outside calls and the database replaced ---------------------

const ZIP_BODY = { places: [{ 'place name': 'Chicago', 'state abbreviation': 'IL', latitude: '41.8474', longitude: '-87.6316' }] };
const POINT_BODY = { properties: { timeZone: CHICAGO, forecastGridData: 'https://api.weather.gov/gridpoints/LOT/76,71' } };

function fakeOutside(overrides = {}) {
  const calls = [];
  const answers = { zip_lookup: ZIP_BODY, nws_points: POINT_BODY, nws_grid: { properties: sampleGrid() }, open_meteo: sampleMeteo(), ...overrides };
  const fetchJson = async (stage) => {
    calls.push(stage);
    if (answers[stage] instanceof Error) throw answers[stage];
    return answers[stage];
  };
  return { fetchJson, calls };
}

function fakeDatabase(clock) {
  const rows = new Map();
  return {
    rows,
    findFacts: async (zip, day) => {
      const row = rows.get(`${zip}|${day}`);
      return { state: 'ok', value: row ? { payload: row.payload, age: Math.round((clock.now - row.at) / 1000) } : null };
    },
    saveFacts: async (zip, day, payload) => {
      rows.set(`${zip}|${day}`, { payload, at: clock.now });
      return { state: 'ok' };
    },
  };
}

async function withService(setup, run) {
  const original = { ...weather.deps };
  const clock = { now: NOW.getTime() };
  const database = fakeDatabase(clock);
  const outside = fakeOutside(setup.answers);
  Object.assign(weather.deps, { fetchJson: outside.fetchJson, now: () => new Date(clock.now) }, database, setup.deps || {});
  const server = weather.app.listen(0, '127.0.0.1');
  await new Promise((resolve) => server.once('listening', resolve));
  const get = async (query) => {
    const res = await fetch(`http://127.0.0.1:${server.address().port}/weather?${query}`);
    return { status: res.status, body: await res.json() };
  };
  try {
    await run({ get, clock, outside, database });
  } finally {
    await new Promise((resolve) => server.close(resolve));
    Object.assign(weather.deps, original);
  }
}

test('a bad zip or a bad date is refused before any outside call', async () => {
  await withService({}, async ({ get, outside }) => {
    for (const zip of ['abc', '6061', '606160', 'M5V 3L9', 'SW1A 1AA', '']) {
      const res = await get(`zip=${encodeURIComponent(zip)}&date=${DAY}`);
      assert.strictEqual(res.status, 400);
      assert.strictEqual(res.body.error, 'invalid_zip');
    }
    for (const date of ['2026-02-30', 'today', '']) {
      const res = await get(`zip=60616&date=${date}`);
      assert.strictEqual(res.status, 400);
      assert.strictEqual(res.body.error, 'invalid_date');
    }
    assert.deepStrictEqual(outside.calls, []);
  });
});

test('a well formed zip that does not exist is a 404 of its own', async () => {
  await withService({ answers: { zip_lookup: null } }, async ({ get, outside }) => {
    const res = await get(`zip=00000&date=${DAY}`);
    assert.strictEqual(res.status, 404);
    assert.strictEqual(res.body.error, 'zip_not_found');
    assert.deepStrictEqual(outside.calls, ['zip_lookup']);
  });
});

test('a date outside the window at the zip code location is refused', async () => {
  await withService({}, async ({ get }) => {
    assert.strictEqual((await get('zip=60616&date=2026-11-15')).body.error, 'date_out_of_range');
    assert.strictEqual((await get('zip=60616&date=2026-11-23')).body.error, 'date_out_of_range');
    assert.strictEqual((await get('zip=60616&date=2026-11-22')).status, 200);
  });
});

test('the first request is a miss and the repeat is answered from the database', async () => {
  await withService({}, async ({ get, outside }) => {
    const first = await get(`zip=60616&date=${DAY}`);
    assert.strictEqual(first.status, 200);
    assert.strictEqual(first.body.cache, 'miss');
    assert.strictEqual(first.body.city, 'Chicago');
    assert.strictEqual(first.body.timezone, CHICAGO);
    assert.deepStrictEqual(Object.keys(first.body.periods), ['morning', 'night']);
    assert.strictEqual(Object.keys(first.body.periods.morning.items).length, 7);
    const callsAfterFirst = outside.calls.length;
    const second = await get(`zip=60616&date=${DAY}`);
    assert.strictEqual(second.body.cache, 'hit');
    assert.strictEqual(outside.calls.length, callsAfterFirst);
    assert.deepStrictEqual(second.body.periods, first.body.periods);
  });
});

test('one failing source still gives an answer', async () => {
  const down = new weather.UpstreamError('nws_grid', 503);
  await withService({ answers: { nws_grid: down } }, async ({ get }) => {
    const res = await get(`zip=60616&date=${DAY}`);
    assert.strictEqual(res.status, 200);
    assert.strictEqual(res.body.periods.morning.items.temperature.source, 'Open-Meteo');
  });
});

test('when the sources are down an older copy is served with its age', async () => {
  await withService({}, async ({ get, clock, outside }) => {
    await get(`zip=60616&date=${DAY}`);
    clock.now += 2 * 3600 * 1000;
    outside.fetchJson = null;
    weather.deps.fetchJson = async (stage) => { throw new weather.UpstreamError(stage, 0); };
    const res = await get(`zip=60616&date=${DAY}`);
    assert.strictEqual(res.status, 200);
    assert.strictEqual(res.body.cache, 'stale');
    assert.strictEqual(res.body.ageSeconds, 7200);
  });
});

test('when the sources are down and nothing is saved the answer is a clear 502', async () => {
  const down = new weather.UpstreamError('zip_lookup', 0);
  await withService({ answers: { zip_lookup: down } }, async ({ get }) => {
    const res = await get(`zip=60616&date=${DAY}`);
    assert.strictEqual(res.status, 502);
    assert.strictEqual(res.body.error, 'upstream_error');
  });
  const both = { nws_grid: new weather.UpstreamError('nws_grid', 500), open_meteo: new weather.UpstreamError('open_meteo', 500) };
  await withService({ answers: both }, async ({ get }) => {
    assert.strictEqual((await get(`zip=60616&date=${DAY}`)).status, 502);
  });
});

test('the service works when the database is unavailable', async () => {
  const deps = { findFacts: async () => ({ state: 'unavailable' }), saveFacts: async () => ({ state: 'unavailable' }) };
  await withService({ deps }, async ({ get }) => {
    const res = await get(`zip=60616&date=${DAY}`);
    assert.strictEqual(res.status, 200);
    assert.strictEqual(res.body.cache, 'unavailable');
  });
});

// ---- Contract tests: responses recorded from the real services --------------------------
// python3 testing/investigate_sources.py saves them in weather-app/fixtures/real.

const REAL = path.join(__dirname, 'fixtures', 'real');
const real = (name) => JSON.parse(fs.readFileSync(path.join(REAL, name), 'utf8'));
const recorded = fs.existsSync(path.join(REAL, 'nws_grid_60616.json'));
const skip = recorded ? false : 'run python3 testing/investigate_sources.py first';

test('contract: the recorded zip lookup has the fields the code reads', { skip }, () => {
  const place = real('zippopotam_60616.json').places[0];
  assert.ok(place['place name'] && place['state abbreviation']);
  assert.ok(Number.isFinite(Number(place.latitude)) && Number.isFinite(Number(place.longitude)));
});

test('contract: the recorded NWS points response gives a time zone and a grid address', { skip }, () => {
  const props = real('nws_points_60616.json').properties;
  assert.match(props.timeZone, /^America\//);
  assert.match(props.forecastGridData, /^https:\/\/api\.weather\.gov\/gridpoints\//);
});

test('contract: the recorded NWS grid and Open-Meteo responses build a report', { skip }, () => {
  const grid = real('nws_grid_60616.json').properties;
  const meteo = real('open_meteo_60616.json');
  const tz = real('nws_points_60616.json').properties.timeZone;
  const firstDay = meteo.hourly.time[0].slice(0, 10);
  const tomorrow = new Date(`${firstDay}T00:00:00Z`);
  tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
  const day = tomorrow.toISOString().slice(0, 10);
  const facts = weather.buildFacts(grid, meteo, tz, day, new Date(`${firstDay}T12:00:00Z`));
  for (const period of Object.values(facts.periods)) {
    assert.strictEqual(period.items.temperature.available, true);
    assert.strictEqual(period.items.temperature.source, 'NWS');
    assert.strictEqual(period.items.humidity.available, true);
    assert.strictEqual(period.items.uv.source, 'Open-Meteo');
    assert.ok(['Heavy', 'Mild', 'No'].includes(period.items.rain.category));
    assert.ok(['Heavy', 'Mild', 'No'].includes(period.items.snow.category));
  }
});

test('contract: a recorded response with a field removed still builds a report', { skip }, () => {
  const grid = real('nws_grid_60616.json').properties;
  const meteo = real('open_meteo_60616.json');
  delete grid.temperature;
  delete meteo.hourly.uv_index;
  const tz = real('nws_points_60616.json').properties.timeZone;
  const day = meteo.hourly.time[30].slice(0, 10);
  const facts = weather.buildFacts(grid, meteo, tz, day, new Date(`${meteo.hourly.time[0].slice(0, 10)}T12:00:00Z`));
  assert.strictEqual(facts.periods.morning.items.temperature.source, 'Open-Meteo');
  assert.strictEqual(facts.periods.morning.items.uv.text, 'not available');
});
