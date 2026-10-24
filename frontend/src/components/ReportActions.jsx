import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../lib/api.js';
import { useAuth } from '../lib/auth.jsx';
import { useServer } from '../lib/server.jsx';
import { basisText, daysText } from '../lib/view.js';

// What anyone may do with an existing report: say it is still there, say it is fixed, or say it is wrong.
// These inform the moderators. They never change a score by themselves.
export default function ReportActions({ report, onClose }) {
  const { loginRequired } = useServer();
  const { account } = useAuth();
  const [msg, setMsg] = useState({ error: '', ok: '', busy: false });
  const [disputing, setDisputing] = useState(false);
  const [reason, setReason] = useState('');

  const needsSignIn = loginRequired && !account;

  async function run(fn, okText) {
    setMsg({ error: '', ok: '', busy: true });
    try {
      await fn();
      setMsg({ error: '', ok: okText, busy: false });
      setDisputing(false);
      setReason('');
    } catch (err) {
      setMsg({ error: err.message, ok: '', busy: false });
    }
  }

  return (
    <section className="focus" aria-label="Selected report">
      <div className="row between">
        <h2>{report.label}</h2>
        <button className="link" onClick={onClose}>Close</button>
      </div>
      <p className="muted">
        {report.basis ? basisText(report.basis) : report.status}, reported {daysText(report.age_days)}
        {report.simulated ? ', simulated demo data' : ''}.
      </p>
      {report.description && <p>{report.description}</p>}
      {report.simulated ? (
        <p className="muted fine">Simulated demo reports cannot be voted on.</p>
      ) : needsSignIn ? (
        <p className="muted">
          <Link to="/account">Sign in</Link> to say whether this is still there or fixed.
        </p>
      ) : (
        <>
          <div className="row">
            <button className="ghost" disabled={msg.busy} onClick={() => run(() => api.confirm(report.id, true), 'Thanks. Moderators will see that it is still there.')}>Still there</button>
            <button className="ghost" disabled={msg.busy} onClick={() => run(() => api.confirm(report.id, false), 'Thanks. Moderators will see that it looks fixed.')}>It looks fixed</button>
            <button className="ghost" disabled={msg.busy} onClick={() => setDisputing((d) => !d)} aria-expanded={disputing}>This is wrong</button>
          </div>
          {disputing && (
            <form
              className="search"
              onSubmit={(e) => {
                e.preventDefault();
                run(() => api.dispute(report.id, reason.trim()), 'Thanks. A moderator will look at this report again.');
              }}
            >
              <label>
                What is wrong with it?
                <textarea rows={2} maxLength={280} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: this was repaired last week." />
              </label>
              <button className="primary" disabled={msg.busy || reason.trim().length < 5}>Send to moderators</button>
            </form>
          )}
        </>
      )}
      {msg.error && <p className="error" role="alert">{msg.error}</p>}
      {msg.ok && <p className="ok" role="status">{msg.ok}</p>}
    </section>
  );
}
