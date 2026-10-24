import { useEffect, useState } from 'react';
import { Check, History, RotateCcw, X } from 'lucide-react';
import { api } from '../lib/api.js';
import { categoryLabel, daysText, movesFrom } from '../lib/view.js';

const ACTION_TEXT = { verified: 'Verify', resolved: 'Mark resolved', unverified: 'Reopen', rejected: 'Reject' };
const ACTION_ICON = { verified: Check, unverified: RotateCcw, rejected: X };

// One report in the moderator's list: what it says, why it is worth a look, and what can be done with it.
export default function AdminReport({ item, categories, onChanged }) {
  const r = item.report;
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState('');
  const [photoUrl, setPhotoUrl] = useState('');
  const [removing, setRemoving] = useState(false);
  const [removeNote, setRemoveNote] = useState('');
  const [events, setEvents] = useState(null);

  // A photo is fetched only when a moderator asks to see it, and released from memory afterwards.
  useEffect(() => () => photoUrl && URL.revokeObjectURL(photoUrl), [photoUrl]);

  async function guard(fn) {
    setBusy(true);
    setErr('');
    try {
      await fn();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  }

  const move = (status, why) =>
    guard(async () => {
      await api.setStatus(r.id, status, why);
      setRejecting(false);
      setNote('');
      onChanged();
    });

  const showPhoto = () =>
    guard(async () => {
      const { blob } = await api.photo(r.id);
      setPhotoUrl(URL.createObjectURL(blob));
    });

  const dropPhoto = () =>
    guard(async () => {
      await api.removePhoto(r.id, removeNote.trim() || undefined);
      setPhotoUrl('');
      setRemoving(false);
      setRemoveNote('');
      onChanged();
    });

  const toggleHistory = () =>
    events
      ? setEvents(null)
      : guard(async () => {
          setEvents(await api.events(r.id));
        });

  return (
    <li className="rep" data-testid="admin-report">
      <div className="rep-main">
        <strong>
          {categoryLabel(categories, r.category)} <span className={`tag ${r.status}`}>{r.status}</span>
          {item.kind === 'flagged' && <span className="tag flagged"> flagged</span>}
          {r.simulated && <span className="tag dashed"> simulated</span>}
        </strong>
        <span className="muted">
          {daysText(r.age_days)} · {r.lat.toFixed(5)}, {r.lng.toFixed(5)} · {r.source.replace('_', ' ')}
        </span>
        {r.description && <p>{r.description}</p>}
        <span className="muted fine">
          {r.confirmations.still_present} say still there · {r.confirmations.fixed} say fixed · {r.disputes} dispute{r.disputes === 1 ? '' : 's'}
        </span>
        {r.classifier_category && r.classifier_category !== r.category && (
          <span className="muted fine">
            The classifier suggested {categoryLabel(categories, r.classifier_category)}
            {r.classifier_confidence ? ` (${Math.round(r.classifier_confidence * 100)}%)` : ''}.
          </span>
        )}
        {r.possible_duplicate_of && <p className="dupe">Possible duplicate of report {r.possible_duplicate_of.slice(0, 8)}. Consider verifying one and rejecting the rest.</p>}
        {item.reasons && item.reasons.length > 0 && (
          <ul className="why">
            {item.reasons.map((x) => <li key={x}>{x}</li>)}
          </ul>
        )}
      </div>

      {photoUrl && <img src={photoUrl} alt="Photo submitted with this report" className="thumb big" />}

      <div className="row">
        {movesFrom(r.status).map((to) => {
          const Icon = ACTION_ICON[to];
          return (
            <button
              key={to}
              className={to === 'verified' ? 'primary' : 'ghost'}
              disabled={busy}
              onClick={() => (to === 'rejected' ? setRejecting((v) => !v) : move(to))}
            >
              {Icon && <Icon size={16} aria-hidden />} {ACTION_TEXT[to]}
            </button>
          );
        })}
        {r.has_photo && !photoUrl && <button className="ghost" disabled={busy} onClick={showPhoto}>View photo</button>}
        {r.has_photo && <button className="ghost" disabled={busy} onClick={() => setRemoving((v) => !v)}>Remove photo</button>}
        <button className="ghost" disabled={busy} onClick={toggleHistory} aria-expanded={Boolean(events)}><History size={16} aria-hidden /> History</button>
      </div>

      {rejecting && (
        <form
          className="search wide"
          onSubmit={(e) => {
            e.preventDefault();
            move('rejected', note.trim());
          }}
        >
          <label>
            Why is it rejected? (recorded in the audit trail)
            <input type="text" value={note} maxLength={280} onChange={(e) => setNote(e.target.value)} />
          </label>
          <button className="primary" disabled={busy || !note.trim()}>Reject report</button>
        </form>
      )}
      {removing && (
        <form
          className="search wide"
          onSubmit={(e) => {
            e.preventDefault();
            dropPhoto();
          }}
        >
          <label>
            Why is the photo removed? (optional)
            <input type="text" value={removeNote} maxLength={280} onChange={(e) => setRemoveNote(e.target.value)} placeholder="for example: shows a face" />
          </label>
          <button className="primary" disabled={busy}>Remove photo, keep report</button>
        </form>
      )}
      {events && (
        <ul className="rows wide events">
          {events.map((e, k) => (
            <li key={k}>
              <span>{e.action}</span>
              <span className="muted">{e.actor}</span>
              <span className="muted">{new Date(e.at).toLocaleString()}</span>
              {e.note && <span className="span3">{e.note}</span>}
            </li>
          ))}
        </ul>
      )}
      {err && <p className="error wide" role="alert">{err}</p>}
    </li>
  );
}
