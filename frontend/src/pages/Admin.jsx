import { useCallback, useEffect, useState } from 'react';
import AdminReport from '../components/AdminReport.jsx';
import AdminTools from '../components/AdminTools.jsx';
import ServerNotice from '../components/ServerNotice.jsx';
import SignIn from '../components/SignIn.jsx';
import { api } from '../lib/api.js';
import { useAuth } from '../lib/auth.jsx';
import { useServer } from '../lib/server.jsx';

const TABS = [
  ['queue', 'Review queue'],
  ['verified', 'Verified'],
  ['resolved', 'Resolved'],
  ['rejected', 'Rejected'],
];

const GATE_TEXT = {
  checking: 'Checking your access…',
  no: 'Those credentials were not accepted. A moderator account needs its role set on the server (app_metadata.role), or use the shared admin key.',
  off: 'Admin tools are switched off on this server: no ADMIN_TOKEN is set and sign-in is not configured.',
  unreachable: 'Could not reach the server to check your access.',
};

// Moderators prove who they are to the server, which decides. The browser never guesses.
function Gate() {
  const { moderator, setAdminToken, supabaseEnabled, account } = useAuth();
  const [key, setKey] = useState('');
  return (
    <div className="admin">
      <h1>Report moderation</h1>
      <p className="muted">Only moderators can review reports. The admin key stays in this tab&apos;s memory and is forgotten on refresh. Never put it in the site&apos;s settings.</p>
      {GATE_TEXT[moderator] && <p className={moderator === 'checking' ? 'muted' : 'error'} role="alert">{GATE_TEXT[moderator]}</p>}
      <form className="search" onSubmit={(e) => { e.preventDefault(); setAdminToken(key.trim()); }}>
        <label>
          Admin key
          <input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
        </label>
        <button className="primary" disabled={!key.trim()}>Use admin key</button>
      </form>
      {supabaseEnabled && !account && <SignIn reason="Or sign in with a moderator account." />}
      {account && <p className="muted">Signed in as {account.email}. This account is not a moderator.</p>}
    </div>
  );
}

export default function Admin() {
  const { moderator, setAdminToken, account } = useAuth();
  const { status: serverStatus, categories } = useServer();
  const [tab, setTab] = useState('queue');
  const [stats, setStats] = useState(null);
  const [items, setItems] = useState([]);
  const [state, setState] = useState({ busy: false, error: '' });

  const load = useCallback(async () => {
    setState({ busy: true, error: '' });
    try {
      const [s, list] = await Promise.all([api.stats(), tab === 'queue' ? api.queue() : api.adminReports(tab)]);
      setStats(s);
      setItems(tab === 'queue' ? list : list.map((report) => ({ report, reasons: [], kind: 'pending' })));
      setState({ busy: false, error: '' });
    } catch (err) {
      setState({ busy: false, error: err.message });
    }
  }, [tab]);

  useEffect(() => {
    if (moderator === 'yes') load();
  }, [moderator, load]);

  if (serverStatus !== 'ready') return <div className="admin"><ServerNotice /></div>;
  if (moderator !== 'yes') return <Gate />;

  const count = (k) => (!stats ? null : k === 'queue' ? stats.pending : (stats.by_status && stats.by_status[k]) || 0);

  return (
    <div className="admin">
      <h1>Report moderation</h1>
      <p className="muted">
        You are {account ? `signed in as ${account.email}` : 'using the shared admin key'}. Every change is written to the audit trail.
        {!account && ' Everyone using the shared key appears as "admin"; moderator accounts are named individually.'}{' '}
        <button className="link" onClick={() => setAdminToken('')}>Forget the admin key</button>
      </p>

      {stats && (
        <section aria-label="Overview">
          <ul className="tiles">
            <li><b>{stats.total}</b> reports</li>
            <li><b>{stats.pending}</b> pending</li>
            <li><b>{stats.pending_over_7_days}</b> waiting over 7 days</li>
            <li><b>{stats.disputed}</b> disputed</li>
            <li><b>{stats.with_photo}</b> with a photo</li>
            <li><b>{stats.median_days_to_resolve === null || stats.median_days_to_resolve === undefined ? 'n/a' : stats.median_days_to_resolve}</b> median days to resolve</li>
          </ul>
          {stats.warnings.length > 0 && (
            <ul className="notes">{stats.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          )}
        </section>
      )}

      <div className="stats" role="group" aria-label="Filter by status">
        {TABS.map(([k, label]) => (
          <button key={k} aria-pressed={tab === k} onClick={() => setTab(k)}>
            <b>{count(k) ?? '·'}</b> {label}
          </button>
        ))}
      </div>

      {state.error && <p className="error" role="alert">{state.error}</p>}
      {!state.busy && !items.length && !state.error && (
        <p className="muted">{tab === 'queue' ? 'Nothing waiting for review.' : `No ${tab} reports.`}</p>
      )}

      <ul className="reps">
        {items.map((it) => <AdminReport key={it.report.id} item={it} categories={categories} onChanged={load} />)}
      </ul>

      <AdminTools categories={categories} onChanged={load} />
    </div>
  );
}
