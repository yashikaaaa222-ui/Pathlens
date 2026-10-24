// Pure helpers for turning API answers into screen text, and screen state into API requests.
// No React and no browser APIs, so Node can test them. Nothing here scores anything: all numbers come from the server.

export const FACTOR_LABELS = { lighting: 'Lighting', hazard: 'Road hazards' };

export const pct = (x) => Math.round((Number(x) || 0) * 100);

export const categoryLabel = (categories, key) => (categories && categories[key] && categories[key].label) || key;

// A moderator can only move a report along these paths. The server enforces the same rule (and answers 409);
// this list only decides which buttons to show.
const MOVES = { unverified: ['verified', 'rejected'], verified: ['resolved', 'rejected'], resolved: ['unverified'], rejected: ['unverified'] };
export const movesFrom = (status) => MOVES[status] || [];

// "2026-12-15T22:00" from a datetime-local input is the person's local time; the server wants an absolute time.
export function toIso(localValue) {
  if (!localValue) return undefined;
  const d = new Date(localValue);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

// Either a pinned point on the map or the words the person typed (looked up on the server, never stored).
export function placeBody(text, pin) {
  if (pin) return { lat: pin[0], lng: pin[1] };
  return { query: String(text || '').trim() };
}

export function buildCompareRequest({ from, to, pins = {}, persona = 'default', when = '', includeSimulated = false, weights = null }) {
  const body = {
    origin: placeBody(from, pins.from),
    destination: placeBody(to, pins.to),
    persona,
    include_simulated: Boolean(includeSimulated),
    sensitivity: true,
  };
  const at = toIso(when);
  if (at) body.at = at;
  if (weights && Number(weights.lighting) + Number(weights.hazard) > 0) {
    body.weights = { lighting: Number(weights.lighting), hazard: Number(weights.hazard) };
  }
  return body;
}

export function daysText(days) {
  const n = Math.round(Number(days) || 0);
  if (n <= 0) return 'today';
  return n === 1 ? '1 day ago' : `${n} days ago`;
}

// "score 34 (range 22 to 41)". The range is how the score moves if the unobserved part looked like the observed part.
export function rangeText(m) {
  if (!m || m.concern_band === 'insufficient' || !m.concern_range) return '';
  const [lo, hi] = m.concern_range;
  return lo === hi ? '' : `range ${lo} to ${hi}`;
}

export function gapText(g) {
  return `${g.length_m} m with no recent evidence, ${g.from_m} to ${g.to_m} m along the route`;
}

export function basisText(basis) {
  return { verified: 'verified', corroborated: 'corroborated by 2 people', single: 'single report, not counted' }[basis] || basis;
}

// What the person should do before they can report, given what the server and the browser know.
export function reportGate({ loginRequired, account, supabaseEnabled }) {
  if (!loginRequired || account) return 'ok';
  return supabaseEnabled ? 'sign-in' : 'sign-in-unavailable';
}

export function parseCoord(value, min, max) {
  const n = Number(String(value).trim());
  if (String(value).trim() === '' || !Number.isFinite(n) || n < min || n > max) return null;
  return n;
}

export const exportFilename = (format, status) => `pathlens-issues-${status || 'all'}.${format}`;
