import { CONFIG } from '../config.js';
import { pct, rangeText } from '../lib/view.js';

export function Coverage({ value }) {
  return (
    <div>
      <div className="cov" role="img" aria-label={`${pct(value)} percent of the route has recent evidence`}>
        <i style={{ width: `${pct(value)}%` }} />
      </div>
      <small className="muted">{pct(value)}% of the route has recent evidence. The hatched part is unknown.</small>
    </div>
  );
}

// Everything on the card is read from the server's answer: the wording, the score and its range.
export default function RouteCard({ a, i, selected, onClick }) {
  const m = a.metrics;
  const range = rangeText(m);
  return (
    <button className={`card ${selected ? 'on' : ''}`} style={{ '--c': CONFIG.routeColors[i] }} onClick={onClick} aria-pressed={selected}>
      <span className="card-head">
        <strong>{a.name}</strong>
        <span>{(a.route.distance / 1000).toFixed(1)} km</span>
        <span>{Math.round(a.route.duration / 60)} min walk</span>
      </span>
      <span className="band">
        {m.concern_band_text}
        {m.concern_band !== 'insufficient' && <b> {m.concern_score} / 100</b>}
        {range && <small className="muted"> ({range})</small>}
      </span>
      <Coverage value={m.coverage} />
      <small className="muted">Evidence confidence: {m.confidence_label}</small>
      {m.simulated && <small className="tag simulated">Includes simulated demo reports</small>}
    </button>
  );
}
