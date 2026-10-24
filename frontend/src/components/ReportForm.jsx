import { useEffect, useState } from 'react';
import { api } from '../lib/api.js';
import { useAuth } from '../lib/auth.jsx';
import { useServer } from '../lib/server.jsx';
import { categoryLabel, reportGate } from '../lib/view.js';
import SignIn from './SignIn.jsx';

// Re-drawing the image onto a canvas and re-encoding it drops all metadata, including GPS in EXIF.
// The server does the same again and is the one that is trusted; this just keeps the upload small.
async function shrink(file, max = 800) {
  const bmp = await createImageBitmap(file);
  const s = Math.min(1, max / Math.max(bmp.width, bmp.height));
  const c = document.createElement('canvas');
  c.width = Math.round(bmp.width * s);
  c.height = Math.round(bmp.height * s);
  c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
  return c.toDataURL('image/jpeg', 0.7);
}

export default function ReportForm({ location, onSaved }) {
  const { categories, privacy, loginRequired } = useServer();
  const { account, supabaseEnabled } = useAuth();
  const [category, setCategory] = useState('');
  const [text, setText] = useState('');
  const [photo, setPhoto] = useState('');
  const [consent, setConsent] = useState(false);
  const [fileKey, setFileKey] = useState(0);
  const [suggestion, setSuggestion] = useState(null);
  const [state, setState] = useState({ error: '', done: null, busy: false });

  // Ask the server's classifier for a suggestion while the person types. It only ever suggests.
  useEffect(() => {
    const t = text.trim();
    if (t.length < 4) {
      setSuggestion(null);
      return undefined;
    }
    const ctl = new AbortController();
    const id = setTimeout(async () => {
      try {
        const r = await api.classify(t, ctl.signal);
        setSuggestion(r && r.category ? r : null);
      } catch (err) {
        if (err.name !== 'AbortError') setSuggestion(null);
      }
    }, 450);
    return () => {
      clearTimeout(id);
      ctl.abort();
    };
  }, [text]);

  const gate = reportGate({ loginRequired, account, supabaseEnabled });
  if (gate === 'sign-in') return <SignIn reason="Sign in to report an issue." />;
  if (gate === 'sign-in-unavailable') {
    return <p className="error" role="alert">This server asks for a sign-in before reporting, but sign-in is not set up in this build.</p>;
  }

  const fail = (error) => setState({ error, done: null, busy: false });

  async function onPhoto(e) {
    const f = e.target.files && e.target.files[0];
    if (!f) return setPhoto('');
    try {
      setPhoto(await shrink(f));
    } catch {
      fail('That image could not be read. Try a JPEG or PNG.');
    }
  }

  async function submit(e) {
    e.preventDefault();
    if (!location) return fail('Click the map to set where the issue is.');
    if (!consent) return fail('Please confirm that you have read the privacy notice.');
    setState({ error: '', done: null, busy: true });
    try {
      const res = await api.createReport({
        category: category || undefined,
        lat: location[0],
        lng: location[1],
        description: text.trim(),
        photo: photo || undefined,
        consent: true,
      });
      setCategory('');
      setText('');
      setPhoto('');
      setConsent(false);
      setSuggestion(null);
      setFileKey((k) => k + 1);
      setState({ error: '', done: res, busy: false });
      onSaved(res);
    } catch (err) {
      fail(err.message);
    }
  }

  return (
    <form className="search" onSubmit={submit}>
      <h2>Report an issue</h2>
      <p className="muted">
        {location ? `Location set: ${location[0].toFixed(5)}, ${location[1].toFixed(5)}. Click the map to move it.` : 'Click the map to set where the issue is.'}
      </p>
      <label>
        Description
        <textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={280} rows={3} placeholder="What is wrong with the street or footpath? Describe the infrastructure only." />
      </label>
      {suggestion && suggestion.category !== category && (
        <p className="muted" data-testid="suggestion">
          Suggested category: {categoryLabel(categories, suggestion.category)}
          {suggestion.confidence ? ` (${Math.round(suggestion.confidence * 100)}% sure)` : ''}.{' '}
          <button type="button" className="link" onClick={() => setCategory(suggestion.category)}>Use it</button>
        </p>
      )}
      <label>
        Category
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">Let PathLens suggest one</option>
          {Object.entries(categories).map(([k, c]) => <option key={k} value={k}>{c.label}</option>)}
        </select>
      </label>
      <label>
        Photo (optional)
        <input key={fileKey} type="file" accept="image/*" onChange={onPhoto} />
        <small className="muted">The server removes location data from it. Only moderators can see it. Faces and number plates are not blurred, so avoid people and vehicles.</small>
      </label>
      <details className="privacy">
        <summary>Privacy notice</summary>
        <ul className="plain">{privacy.map((p) => <li key={p}>{p}</li>)}</ul>
      </details>
      <label className="check">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        I have read the privacy notice and agree that my report is stored and reviewed.
      </label>
      {state.error && <p className="error" role="alert">{state.error}</p>}
      {state.done && (
        <p className="ok" role="status">
          {state.done.message}
          {state.done.photo_saved ? ' Your photo was saved for moderators.' : ''}
        </p>
      )}
      <button className="primary" disabled={state.busy}>{state.busy ? 'Sending…' : 'Submit report'}</button>
    </form>
  );
}
