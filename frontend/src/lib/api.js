// The only place that talks to the PathLens API. Pages never call fetch directly.
//
// Base address: VITE_API_URL if set (for example your Render URL), otherwise the same origin, which the
// Vite dev server proxies to http://localhost:8000 (see vite.config.js).
const BASE = ((typeof import.meta !== 'undefined' && import.meta.env && import.meta.env.VITE_API_URL) || '').replace(/\/$/, '');

export class ApiError extends Error {
  constructor(status, message, detail) {
    super(message);
    this.name = 'ApiError';
    this.status = status; // 0 means the server could not be reached at all
    this.detail = detail;
  }
}

// Credentials are supplied by the auth layer: { bearer, adminToken }. Nothing is stored in this module.
let credentials = () => ({});
export function setCredentials(fn) {
  credentials = fn;
}

// FastAPI answers 422 with a list of { msg }, other errors with { detail: "text" } or { detail: { message } }.
export function describeError(status, body) {
  const d = body && body.detail;
  if (typeof d === 'string') return d;
  if (d && typeof d === 'object' && !Array.isArray(d) && d.message) return d.message;
  if (Array.isArray(d) && d.length) {
    return d.map((x) => String(x.msg || '').replace(/^Value error,\s*/i, '')).filter(Boolean).join(' ');
  }
  if (status === 429) return 'Too many requests. Please wait a while and try again.';
  if (status === 401) return 'Please sign in.';
  if (status === 403) return 'This account is not allowed to do that.';
  if (status === 503) return 'This part of the server is switched off.';
  return `The server answered with an error (${status}).`;
}

function headersFor(auth, json) {
  const h = {};
  if (json) h['Content-Type'] = 'application/json';
  const { bearer, adminToken } = credentials();
  if (auth !== 'none' && bearer) h.Authorization = `Bearer ${bearer}`;
  if (auth === 'admin' && adminToken) h['X-Admin-Token'] = adminToken;
  return h;
}

// auth: 'none' (default, reads), 'user' (send the account token if there is one), 'admin' (token and/or admin key)
async function request(path, { method = 'GET', body, auth = 'none', as = 'json', signal } = {}) {
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers: headersFor(auth, body !== undefined),
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (err) {
    if (err && err.name === 'AbortError') throw err;
    throw new ApiError(0, 'Could not reach the PathLens server. Check that it is running and try again.');
  }
  if (!res.ok) {
    let payload = null;
    try {
      payload = await res.json();
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, describeError(res.status, payload), payload && payload.detail);
  }
  if (as === 'blob') return { blob: await res.blob(), type: res.headers.get('content-type') || '' };
  return res.status === 204 ? null : res.json();
}

const q = (params) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') s.set(k, String(v));
  const out = s.toString();
  return out ? `?${out}` : '';
};

export const api = {
  // public
  health: () => request('/api/health'),
  methodology: () => request('/api/methodology'),
  compare: (body, signal) => request('/api/routes/compare', { method: 'POST', body, signal }),
  listReports: ({ includeSimulated = false } = {}) => request(`/api/reports${q({ include_simulated: includeSimulated ? 'true' : '' })}`),
  classify: (text, signal) => request('/api/reports/classify', { method: 'POST', body: { text }, signal }),

  // people (an account is needed when the server requires sign-in)
  createReport: (payload) => request('/api/reports', { method: 'POST', body: payload, auth: 'user' }),
  confirm: (id, stillPresent) => request(`/api/reports/${id}/confirmations`, { method: 'POST', body: { still_present: stillPresent }, auth: 'user' }),
  dispute: (id, reason) => request(`/api/reports/${id}/disputes`, { method: 'POST', body: { reason }, auth: 'user' }),

  // moderators
  stats: () => request('/api/dashboard/stats', { auth: 'admin' }),
  queue: () => request('/api/dashboard/queue', { auth: 'admin' }),
  adminReports: (status) => request(`/api/admin/reports${q({ status })}`, { auth: 'admin' }),
  setStatus: (id, status, note) => request(`/api/reports/${id}/status`, { method: 'PATCH', body: { status, note: note || undefined }, auth: 'admin' }),
  events: (id) => request(`/api/reports/${id}/events`, { auth: 'admin' }),
  photo: (id) => request(`/api/reports/${id}/photo`, { auth: 'admin', as: 'blob' }),
  removePhoto: (id, note) => request(`/api/reports/${id}/photo${q({ note })}`, { method: 'DELETE', auth: 'admin' }),
  fieldAudit: (payload) => request('/api/reports/field-audit', { method: 'POST', body: payload, auth: 'admin' }),
  exportIssues: (format, status) => request(`/api/dashboard/export${q({ format, status })}`, { auth: 'admin', as: 'blob' }),
  maintenance: () => request('/api/admin/maintenance', { method: 'POST', auth: 'admin' }),
  seedDemo: (origin, destination) => request('/api/admin/demo-seed', { method: 'POST', body: { origin, destination }, auth: 'admin' }),
  clearDemo: () => request('/api/admin/demo-seed', { method: 'DELETE', auth: 'admin' }),
};
