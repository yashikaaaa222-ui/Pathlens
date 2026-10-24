import { FACTOR_LABELS, basisText, categoryLabel, daysText, gapText } from '../lib/view.js';
import { useServer } from '../lib/server.jsx';

// File name kept to match the repo layout. The interface says "concern" and "reported", never "risk" or "safe".
export default function Breakdown({ a }) {
  const m = a.metrics;
  const { categories } = useServer();
  const kinds = Object.entries(m.counts || {});
  return (
    <section>
      <h2>What the score is made of</h2>
      <ul className="rows">
        {Object.keys(FACTOR_LABELS).map((f) => {
          const x = m.factors[f];
          return (
            <li key={f}>
              <span>{FACTOR_LABELS[f]}</span>
              {x && x.available ? (
                <>
                  <span className="muted">counts for {m.weight_share[f]}%</span>
                  <b>{x.score}</b>
                </>
              ) : (
                <span className="muted span2">Left out of the score, not scored as zero.</span>
              )}
            </li>
          );
        })}
      </ul>
      <p className="muted">
        {m.counted} counted report{m.counted === 1 ? '' : 's'}
        {kinds.length > 0 && <> ({kinds.map(([k, n]) => `${categoryLabel(categories, k)} ${n}`).join(', ')})</>}.
        {m.last_update_days !== null && m.last_update_days !== undefined && ` Latest evidence is ${m.last_update_days} day${m.last_update_days === 1 ? '' : 's'} old.`}
        {m.longest_stretch_m > 0 && ` Longest affected stretch: ${m.longest_stretch_m} m.`}
        {m.expired_unverified > 0 && ` ${m.expired_unverified} unverified report${m.expired_unverified === 1 ? ' is' : 's are'} too old to count.`}{' '}
        Weights are a provisional default, not a validated model.
      </p>
    </section>
  );
}

export function Explanation({ notes, stability }) {
  return (
    <section>
      <h2>Why these routes compare this way</h2>
      {stability && (
        <p className={`stab ${stability.stable ? 'stable' : 'close'}`}>
          Ranking: <b>{stability.label}</b>. {stability.stable ? 'The best route stays the same when the weights are nudged.' : 'The best route changes when the weights are nudged. Treat these routes as roughly equal.'}
        </p>
      )}
      <ul className="notes">{notes.map((n, k) => <li key={k}>{n.text}</li>)}</ul>
    </section>
  );
}

// Stretches where nothing is known: shown so that missing information is never read as good news.
export function Gaps({ a }) {
  if (!a.gaps || !a.gaps.length) return null;
  return (
    <section>
      <h2>Where nothing is known</h2>
      <ul className="notes">
        {a.gaps.map((g) => <li key={g.from_m}>{gapText(g)}</li>)}
      </ul>
      <p className="muted fine">Unknown is not the same as clear. These stretches are drawn dashed and ringed on the map.</p>
    </section>
  );
}

const LAYER_LABELS = { help: 'Help points', lamps: 'Mapped streetlights', activity: 'Surrounding activity' };

// OpenStreetMap layers. They are shown for context and never change the score.
export function Informational({ a }) {
  const layers = Object.entries(a.metrics.informational || {});
  if (!layers.length) return null;
  return (
    <section>
      <h2>Context from OpenStreetMap (not scored)</h2>
      <ul className="rows ctx">
        {layers.map(([k, layer]) => (
          <li key={k}>
            <span>{LAYER_LABELS[k] || k}</span>
            <span className="tag dashed">{layer.available ? 'available' : 'unknown'}</span>
            <span className="muted">{layer.note}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function Evidence({ a }) {
  const list = [...a.evidence].sort((x, y) => x.chunk - y.chunk).slice(0, 30);
  return (
    <section>
      <h2>Reports along {a.name}</h2>
      {!list.length && <p className="muted">No reports found along this route.</p>}
      <ul className="rows ev">
        {list.map((e) => (
          <li key={e.id}>
            <span>{e.label}</span>
            <span className={`tag ${e.basis}`}>{basisText(e.basis)}</span>
            <span className="muted">{daysText(e.age_days)}{e.simulated ? ', simulated' : ''}</span>
            <span className="muted">{e.counted ? 'Counted' : 'Shown, not counted'}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
