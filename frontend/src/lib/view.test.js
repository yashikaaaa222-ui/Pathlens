import test from 'node:test';
import assert from 'node:assert/strict';
import { buildCompareRequest, daysText, rangeText, gapText, basisText, movesFrom, reportGate, parseCoord, placeBody, toIso, pct, categoryLabel, exportFilename } from './view.js';

test('a pinned point is sent as coordinates and typed words as a query', () => {
  assert.deepEqual(placeBody('ignored', [51.5, -0.1]), { lat: 51.5, lng: -0.1 });
  assert.deepEqual(placeBody('  Trafalgar Square ', null), { query: 'Trafalgar Square' });
});

test('the compare request has only what the person chose', () => {
  const body = buildCompareRequest({ from: 'A street', to: 'B street', pins: { to: [51.5, -0.08] }, persona: 'night_walker', includeSimulated: true });
  assert.deepEqual(body, {
    origin: { query: 'A street' },
    destination: { lat: 51.5, lng: -0.08 },
    persona: 'night_walker',
    include_simulated: true,
    sensitivity: true,
  });
  assert.equal('at' in body, false);
  assert.equal('weights' in body, false);
});

test('a chosen time becomes an absolute ISO time, and nonsense is ignored', () => {
  assert.match(toIso('2026-12-15T22:00'), /^2026-12-1[56]T\d\d:\d\d:00\.000Z$/);
  assert.equal(toIso(''), undefined);
  assert.equal(toIso('not a date'), undefined);
  const body = buildCompareRequest({ from: 'a', to: 'b', when: '2026-12-15T22:00' });
  assert.ok(body.at);
});

test('custom weights are sent only when they add up to something', () => {
  assert.deepEqual(buildCompareRequest({ from: 'a', to: 'b', weights: { lighting: 60, hazard: 40 } }).weights, { lighting: 60, hazard: 40 });
  assert.equal('weights' in buildCompareRequest({ from: 'a', to: 'b', weights: { lighting: 0, hazard: 0 } }), false);
  assert.equal('weights' in buildCompareRequest({ from: 'a', to: 'b', weights: null }), false);
});

test('days read naturally', () => {
  assert.equal(daysText(0.2), 'today');
  assert.equal(daysText(1), '1 day ago');
  assert.equal(daysText(12.4), '12 days ago');
  assert.equal(daysText(undefined), 'today');
});

test('the range is shown only for a score that exists and actually has one', () => {
  assert.equal(rangeText({ concern_band: 'moderate', concern_range: [22, 41] }), 'range 22 to 41');
  assert.equal(rangeText({ concern_band: 'low', concern_range: [10, 10] }), '');
  assert.equal(rangeText({ concern_band: 'insufficient', concern_range: [0, 90] }), '');
  assert.equal(rangeText({ concern_band: 'low', concern_range: null }), '');
});

test('gaps and evidence tiers have plain wording', () => {
  assert.equal(gapText({ length_m: 800, from_m: 200, to_m: 1000 }), '800 m with no recent evidence, 200 to 1000 m along the route');
  assert.equal(basisText('verified'), 'verified');
  assert.match(basisText('corroborated'), /2 people/);
  assert.match(basisText('single'), /not counted/);
});

test('moderator buttons follow the server rules', () => {
  assert.deepEqual(movesFrom('unverified'), ['verified', 'rejected']);
  assert.deepEqual(movesFrom('verified'), ['resolved', 'rejected']);
  assert.deepEqual(movesFrom('rejected'), ['unverified']);
  assert.deepEqual(movesFrom('anything else'), []);
});

test('reporting is open, needs a sign-in, or is blocked because sign-in is not set up', () => {
  assert.equal(reportGate({ loginRequired: false, account: null, supabaseEnabled: false }), 'ok');
  assert.equal(reportGate({ loginRequired: true, account: { id: 'u' }, supabaseEnabled: true }), 'ok');
  assert.equal(reportGate({ loginRequired: true, account: null, supabaseEnabled: true }), 'sign-in');
  assert.equal(reportGate({ loginRequired: true, account: null, supabaseEnabled: false }), 'sign-in-unavailable');
});

test('coordinates are checked before they are sent', () => {
  assert.equal(parseCoord('51.5', -90, 90), 51.5);
  assert.equal(parseCoord(' -0.09 ', -180, 180), -0.09);
  assert.equal(parseCoord('', -90, 90), null);
  assert.equal(parseCoord('abc', -90, 90), null);
  assert.equal(parseCoord('95', -90, 90), null);
});

test('small formatters', () => {
  assert.equal(pct(0.456), 46);
  assert.equal(pct(undefined), 0);
  assert.equal(categoryLabel({ streetlight: { label: 'Broken streetlight' } }, 'streetlight'), 'Broken streetlight');
  assert.equal(categoryLabel({}, 'footpath'), 'footpath');
  assert.equal(exportFilename('csv', 'all'), 'pathlens-issues-all.csv');
});
