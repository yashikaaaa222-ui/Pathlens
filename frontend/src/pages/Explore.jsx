import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { RotateCcw, Search } from 'lucide-react';
import MapView from '../components/MapView.jsx';
import RouteCard from '../components/RouteCard.jsx';
import Breakdown, { Evidence, Explanation, Gaps, Informational } from '../components/RiskBreakdown.jsx';
import ReportForm from '../components/ReportForm.jsx';
import ReportActions from '../components/ReportActions.jsx';
import ServerNotice from '../components/ServerNotice.jsx';
import { api } from '../lib/api.js';
import { useServer } from '../lib/server.jsx';
import { buildCompareRequest } from '../lib/view.js';

const PERIOD_TEXT = {
  day: 'Daylight: lighting reports count for less.',
  twilight: 'Twilight: lighting reports count for part of their weight.',
  night: 'After dark: lighting reports count in full.',
};

export default function Explore() {
  const [params] = useSearchParams();
  const { status: serverStatus, personas } = useServer();
  const [mode, setMode] = useState(params.get('mode') === 'report' ? 'report' : 'compare');
  const [q, setQ] = useState({ from: '', to: '' });
  const [pins, setPins] = useState({ from: null, to: null });
  const [persona, setPersona] = useState('default');
  const [when, setWhen] = useState('');
  const [own, setOwn] = useState(false);
  const [weights, setWeights] = useState({ lighting: 35, hazard: 30 });
  const [includeSim, setIncludeSim] = useState(false);
  const [result, setResult] = useState(null);
  const [sel, setSel] = useState(0);
  const [reports, setReports] = useState([]);
  const [focusId, setFocusId] = useState(null);
  const [draft, setDraft] = useState(null);
  const [status, setStatus] = useState({ busy: false, error: '' });
  const abort = useRef(null);

  const routes = result ? result.routes : [];
  const current = routes[sel];

  const loadReports = useCallback(async () => {
    try {
      setReports(await api.listReports({ includeSimulated: includeSim }));
    } catch {
      setReports([]);
    }
  }, [includeSim]);

  useEffect(() => {
    if (serverStatus === 'ready') loadReports();
  }, [serverStatus, loadReports]);

  const run = useCallback(
    async (e) => {
      if (e) e.preventDefault();
      if (abort.current) abort.current.abort();
      const ctl = new AbortController();
      abort.current = ctl;
      setStatus({ busy: true, error: '' });
      try {
        const body = buildCompareRequest({ from: q.from, to: q.to, pins, persona, when, includeSimulated: includeSim, weights: own ? weights : null });
        const res = await api.compare(body, ctl.signal);
        setResult(res);
        setSel(0);
        setFocusId(null);
        // The routes carry the resolved start and end, so a re-run with other options does not look the names up again.
        const line = res.routes[0].route.coords;
        setPins({ from: line[0], to: line[line.length - 1] });
        setStatus({ busy: false, error: '' });
      } catch (err) {
        if (err.name === 'AbortError') return;
        setStatus({ busy: false, error: err.message });
      }
    },
    [q, pins, persona, when, includeSim, own, weights],
  );

  // Changing who is walking, when, or the weights re-compares the same two places straight away.
  const hasResult = useRef(false);
  hasResult.current = Boolean(result);
  const optionsKey = JSON.stringify([persona, when, includeSim, own, own ? weights : null]);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (hasResult.current) run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [optionsKey]);

  function edit(k, v) {
    setQ((s) => ({ ...s, [k]: v }));
    setPins((p) => ({ ...p, [k]: null }));
  }

  function pick({ lat, lng }) {
    if (mode === 'report') return setDraft([lat, lng]);
    const k = !pins.from ? 'from' : !pins.to ? 'to' : null;
    if (!k) return;
    setPins((p) => ({ ...p, [k]: [lat, lng] }));
    setQ((s) => ({ ...s, [k]: `Pinned ${lat.toFixed(4)}, ${lng.toFixed(4)}` }));
  }

  function reset() {
    if (abort.current) abort.current.abort();
    setQ({ from: '', to: '' });
    setPins({ from: null, to: null });
    setResult(null);
    setFocusId(null);
    setStatus({ busy: false, error: '' });
    loadReports();
  }

  async function saved() {
    setDraft(null);
    await loadReports();
    if (result) run();
  }

  const focus = useMemo(() => {
    if (!focusId) return null;
    const e = current && current.evidence.find((x) => x.id === focusId);
    if (e) return e;
    return reports.find((r) => r.id === focusId) || null;
  }, [focusId, current, reports]);

  const persona0 = personas[persona];

  return (
    <div className="explore">
      <aside className="panel">
        <div className="seg" role="group" aria-label="Mode">
          <button type="button" aria-pressed={mode === 'compare'} onClick={() => setMode('compare')}>Compare routes</button>
          <button type="button" aria-pressed={mode === 'report'} onClick={() => setMode('report')}>Report an issue</button>
        </div>

        <ServerNotice />

        {serverStatus === 'ready' && mode === 'report' && <ReportForm location={draft} onSaved={saved} />}

        {serverStatus === 'ready' && mode === 'compare' && (
          <>
            <form onSubmit={run} className="search">
              <label>
                From
                <input value={q.from} onChange={(e) => edit('from', e.target.value)} placeholder="Address, place, or click the map" />
              </label>
              <label>
                To
                <input value={q.to} onChange={(e) => edit('to', e.target.value)} placeholder="Address, place, or click the map" />
              </label>

              <label>
                Who is walking
                <select value={persona} onChange={(e) => setPersona(e.target.value)}>
                  {Object.entries(personas).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
                </select>
                {persona0 && <small className="muted">{persona0.description}</small>}
              </label>
              <label>
                When (optional, defaults to now)
                <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
              </label>
              <details className="privacy">
                <summary>Adjust the weights yourself</summary>
                <label className="check">
                  <input type="checkbox" checked={own} onChange={(e) => setOwn(e.target.checked)} />
                  Use my own weights instead of the preset
                </label>
                {own && (
                  <>
                    {['lighting', 'hazard'].map((f) => (
                      <label key={f}>
                        {f === 'lighting' ? 'Lighting' : 'Road hazards'}: {weights[f]}
                        <input type="range" min="0" max="100" value={weights[f]} onChange={(e) => setWeights((w) => ({ ...w, [f]: Number(e.target.value) }))} />
                      </label>
                    ))}
                    <small className="muted">Weights are provisional. The ranking check below says whether they matter for these routes.</small>
                  </>
                )}
              </details>

              <div className="row">
                <button className="primary" disabled={status.busy || !q.from || !q.to}>
                  <Search size={16} aria-hidden /> {status.busy ? 'Finding routes…' : 'Compare routes'}
                </button>
                <button type="button" className="ghost" onClick={reset}>
                  <RotateCcw size={16} aria-hidden /> Clear
                </button>
              </div>
            </form>
            {status.error && <p className="error" role="alert">{status.error}</p>}

            {!result ? (
              <p className="muted">
                Type two places, or click the map to drop a start pin and an end pin. PathLens shows what has been reported along each route. It does not say whether a route is safe.
              </p>
            ) : (
              <>
                <label className="check">
                  <input type="checkbox" checked={includeSim} onChange={(e) => setIncludeSim(e.target.checked)} />
                  Include simulated demo reports
                </label>
                <p className="muted fine" data-testid="daylight">
                  {PERIOD_TEXT[result.daylight.period] || ''}
                </p>
                <div className="cards">
                  {routes.map((a, i) => <RouteCard key={a.name} a={a} i={i} selected={i === sel} onClick={() => setSel(i)} />)}
                </div>
                <Explanation notes={result.notes} stability={result.stability} />
                {current && <Gaps a={current} />}
                {current && <Breakdown a={current} />}
                {current && <Informational a={current} />}
                {current && <Evidence a={current} />}
                <p className="muted fine">
                  {result.disclaimer} {result.params.weights_note} Routes: {result.params.route_provider}.
                </p>
              </>
            )}
          </>
        )}

        {focus && <ReportActions key={focus.id} report={focus} onClose={() => setFocusId(null)} />}
      </aside>

      <div className="mapwrap">
        <MapView
          analyses={routes}
          selected={sel}
          onSelect={setSel}
          pins={pins}
          draft={mode === 'report' ? draft : null}
          onPick={pick}
          loose={routes.length ? [] : reports}
          focusId={focusId}
          onFocus={setFocusId}
        />
      </div>
    </div>
  );
}
