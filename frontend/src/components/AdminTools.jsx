import { useState } from 'react';
import { api } from '../lib/api.js';
import { downloadBlob } from '../lib/download.js';
import { exportFilename, parseCoord } from '../lib/view.js';

function useAction() {
  const [s, setS] = useState({ busy: false, error: '', ok: '' });
  const run = async (fn) => {
    setS({ busy: true, error: '', ok: '' });
    try {
      const ok = await fn();
      setS({ busy: false, error: '', ok: ok || 'Done.' });
    } catch (e) {
      setS({ busy: false, error: e.message, ok: '' });
    }
  };
  return [s, run];
}

const Msg = ({ s }) => (
  <>
    {s.error && <p className="error" role="alert">{s.error}</p>}
    {s.ok && <p className="ok" role="status">{s.ok}</p>}
  </>
);

function Export() {
  const [format, setFormat] = useState('csv');
  const [status, setStatus] = useState('all');
  const [s, run] = useAction();
  return (
    <details className="tool">
      <summary>Export issues (open data)</summary>
      <p className="muted">Verified and resolved issues with place, status and dates. No reporter, no photo, no free text beyond the report description.</p>
      <div className="row">
        <label>Format
          <select value={format} onChange={(e) => setFormat(e.target.value)}><option value="csv">CSV</option><option value="json">JSON</option></select>
        </label>
        <label>Which
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="all">Verified and resolved</option><option value="verified">Verified only</option><option value="resolved">Resolved only</option>
          </select>
        </label>
      </div>
      <button className="ghost" disabled={s.busy} onClick={() => run(async () => {
        const { blob } = await api.exportIssues(format, status);
        downloadBlob(blob, exportFilename(format, status));
        return 'Downloaded.';
      })}>Download</button>
      <Msg s={s} />
    </details>
  );
}

function Maintenance() {
  const [s, run] = useAction();
  const [out, setOut] = useState(null);
  return (
    <details className="tool">
      <summary>Data retention</summary>
      <p className="muted">The retention rules also run when the server starts. Run them now to delete what is past its time.</p>
      <button className="ghost" disabled={s.busy} onClick={() => run(async () => {
        const r = await api.maintenance();
        setOut(r);
        return `Deleted ${r.rejected_deleted} rejected and ${r.unreviewed_deleted} never-reviewed reports, and ${r.photos_removed} photos.`;
      })}>Run retention now</button>
      <Msg s={s} />
      {out && out.policy && <ul className="plain">{out.policy.map((p) => <li key={p}>{p}</li>)}</ul>}
    </details>
  );
}

function FieldAudit({ categories, onChanged }) {
  const [f, setF] = useState({ category: 'streetlight', lat: '', lng: '', description: '', auditor: '' });
  const [s, run] = useAction();
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  return (
    <details className="tool">
      <summary>Add a field-audit report</summary>
      <p className="muted">For issues the team saw while walking a route. Saved as verified and labelled as a field audit.</p>
      <form className="search" onSubmit={(e) => {
        e.preventDefault();
        const lat = parseCoord(f.lat, -90, 90);
        const lng = parseCoord(f.lng, -180, 180);
        if (lat === null || lng === null) return run(async () => { throw new Error('Enter a latitude between -90 and 90 and a longitude between -180 and 180.'); });
        return run(async () => {
          await api.fieldAudit({ category: f.category, lat, lng, description: f.description.trim(), auditor: f.auditor.trim() || undefined });
          onChanged();
          return 'Saved as verified (field audit).';
        });
      }}>
        <label>Category
          <select value={f.category} onChange={set('category')}>{Object.entries(categories).map(([k, c]) => <option key={k} value={k}>{c.label}</option>)}</select>
        </label>
        <div className="row">
          <label>Latitude<input type="text" inputMode="decimal" value={f.lat} onChange={set('lat')} /></label>
          <label>Longitude<input type="text" inputMode="decimal" value={f.lng} onChange={set('lng')} /></label>
        </div>
        <label>Description<input type="text" maxLength={280} value={f.description} onChange={set('description')} /></label>
        <label>Auditor (a nickname)<input type="text" maxLength={40} value={f.auditor} onChange={set('auditor')} /></label>
        <button className="primary" disabled={s.busy}>Save</button>
      </form>
      <Msg s={s} />
    </details>
  );
}

function DemoData({ onChanged }) {
  const [f, setF] = useState({ olat: '', olng: '', dlat: '', dlng: '' });
  const [s, run] = useAction();
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  return (
    <details className="tool">
      <summary>Simulated demo data</summary>
      <p className="muted">Creates reports around the routes between two points. Every one is flagged as simulated and hidden unless someone asks for them.</p>
      <form className="search" onSubmit={(e) => {
        e.preventDefault();
        const v = [parseCoord(f.olat, -90, 90), parseCoord(f.olng, -180, 180), parseCoord(f.dlat, -90, 90), parseCoord(f.dlng, -180, 180)];
        if (v.includes(null)) return run(async () => { throw new Error('Enter valid coordinates for both points.'); });
        return run(async () => {
          const r = await api.seedDemo({ lat: v[0], lng: v[1] }, { lat: v[2], lng: v[3] });
          onChanged();
          return `Created ${r.created} simulated reports (${r.already_present} already there).`;
        });
      }}>
        <div className="row">
          <label>Start latitude<input type="text" inputMode="decimal" value={f.olat} onChange={set('olat')} /></label>
          <label>Start longitude<input type="text" inputMode="decimal" value={f.olng} onChange={set('olng')} /></label>
        </div>
        <div className="row">
          <label>End latitude<input type="text" inputMode="decimal" value={f.dlat} onChange={set('dlat')} /></label>
          <label>End longitude<input type="text" inputMode="decimal" value={f.dlng} onChange={set('dlng')} /></label>
        </div>
        <button className="primary" disabled={s.busy}>Create simulated reports</button>
      </form>
      <button className="ghost danger" disabled={s.busy} onClick={() => {
        if (!window.confirm('Delete every simulated report?')) return;
        run(async () => {
          const r = await api.clearDemo();
          onChanged();
          return `Deleted ${r.deleted} simulated reports.`;
        });
      }}>Delete all simulated reports</button>
      <Msg s={s} />
    </details>
  );
}

export default function AdminTools({ categories, onChanged }) {
  return (
    <section className="tools">
      <h2>Tools</h2>
      <Export />
      <FieldAudit categories={categories} onChanged={onChanged} />
      <DemoData onChanged={onChanged} />
      <Maintenance />
    </section>
  );
}
