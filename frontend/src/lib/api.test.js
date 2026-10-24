import test from 'node:test';
import assert from 'node:assert/strict';
import { api, ApiError, describeError, setCredentials } from './api.js';

// A tiny fake server: records the last request and answers with what the test says.
function fakeFetch(status, body, { type = 'application/json', blob } = {}) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, ...init });
    return {
      ok: status >= 200 && status < 300,
      status,
      headers: { get: (k) => (k.toLowerCase() === 'content-type' ? type : null) },
      json: async () => {
        if (body === undefined) throw new Error('not json');
        return body;
      },
      blob: async () => blob ?? 'BLOB',
    };
  };
  return calls;
}

test.afterEach(() => setCredentials(() => ({})));

test('describeError turns FastAPI validation errors into plain sentences', () => {
  const body = { detail: [{ msg: 'Value error, Please confirm that you have read the privacy notice.' }, { msg: 'Field required' }] };
  assert.equal(describeError(422, body), 'Please confirm that you have read the privacy notice. Field required');
});

test('describeError reads string and object details, and falls back by status', () => {
  assert.equal(describeError(409, { detail: 'You have already disputed this report.' }), 'You have already disputed this report.');
  assert.equal(describeError(409, { detail: { message: 'A rejected report can only move to: unverified.', allowed: ['unverified'] } }), 'A rejected report can only move to: unverified.');
  assert.match(describeError(429, null), /Too many requests/);
  assert.match(describeError(401, {}), /sign in/i);
  assert.match(describeError(500, null), /\(500\)/);
});

test('reads send no credentials at all', async () => {
  setCredentials(() => ({ bearer: 'tok', adminToken: 'adm' }));
  const calls = fakeFetch(200, { status: 'ok' });
  await api.health();
  assert.deepEqual(calls[0].headers, {});
  assert.equal(calls[0].method, 'GET');
});

test('a person action sends the account token but never the admin key', async () => {
  setCredentials(() => ({ bearer: 'tok', adminToken: 'adm' }));
  const calls = fakeFetch(201, { id: 'r1' });
  await api.createReport({ lat: 1, lng: 2, consent: true });
  assert.equal(calls[0].headers.Authorization, 'Bearer tok');
  assert.equal(calls[0].headers['X-Admin-Token'], undefined);
  assert.equal(calls[0].headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(calls[0].body), { lat: 1, lng: 2, consent: true });
});

test('a moderator action sends both the account token and the admin key when present', async () => {
  setCredentials(() => ({ bearer: 'tok', adminToken: 'adm' }));
  const calls = fakeFetch(200, []);
  await api.queue();
  assert.equal(calls[0].headers.Authorization, 'Bearer tok');
  assert.equal(calls[0].headers['X-Admin-Token'], 'adm');
});

test('without credentials no auth headers are sent', async () => {
  const calls = fakeFetch(201, { disputes: 1 });
  await api.dispute('abc', 'It was repaired');
  assert.equal(calls[0].headers.Authorization, undefined);
  assert.equal(calls[0].url, '/api/reports/abc/disputes');
});

test('errors carry the status and a readable message', async () => {
  fakeFetch(422, { detail: [{ msg: 'Value error, Remove phone numbers.' }] });
  await assert.rejects(api.createReport({}), (e) => e instanceof ApiError && e.status === 422 && e.message === 'Remove phone numbers.');
});

test('a server that cannot be reached becomes status 0 with a helpful message', async () => {
  globalThis.fetch = async () => {
    throw new TypeError('fetch failed');
  };
  await assert.rejects(api.health(), (e) => e instanceof ApiError && e.status === 0 && /Could not reach/.test(e.message));
});

test('an aborted request is passed on untouched so callers can ignore it', async () => {
  globalThis.fetch = async () => {
    const e = new Error('aborted');
    e.name = 'AbortError';
    throw e;
  };
  await assert.rejects(api.classify('text'), (e) => e.name === 'AbortError');
});

test('query strings are built only from values that are set', async () => {
  let calls = fakeFetch(200, []);
  await api.listReports();
  assert.equal(calls[0].url, '/api/reports');
  calls = fakeFetch(200, []);
  await api.listReports({ includeSimulated: true });
  assert.equal(calls[0].url, '/api/reports?include_simulated=true');
  calls = fakeFetch(200, []);
  await api.adminReports('rejected');
  assert.equal(calls[0].url, '/api/admin/reports?status=rejected');
  calls = fakeFetch(200, { id: 'x' });
  await api.removePhoto('r1', 'shows a face');
  assert.equal(calls[0].url, '/api/reports/r1/photo?note=shows+a+face');
  assert.equal(calls[0].method, 'DELETE');
});

test('rejecting sends the reason, and an empty note is left out', async () => {
  let calls = fakeFetch(200, {});
  await api.setStatus('r1', 'rejected', 'Not on a public road');
  assert.deepEqual(JSON.parse(calls[0].body), { status: 'rejected', note: 'Not on a public road' });
  calls = fakeFetch(200, {});
  await api.setStatus('r1', 'verified', '');
  assert.deepEqual(JSON.parse(calls[0].body), { status: 'verified' });
});

test('files come back as blobs with their type', async () => {
  fakeFetch(200, undefined, { type: 'text/csv; charset=utf-8', blob: 'CSVDATA' });
  const out = await api.exportIssues('csv', 'all');
  assert.equal(out.blob, 'CSVDATA');
  assert.match(out.type, /text\/csv/);
});
