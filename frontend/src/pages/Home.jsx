import { Link } from 'react-router-dom';
import { CONFIG } from '../config.js';

// The same visual language as the map: solid line = recent evidence, dashed line = unknown.
function Sketch() {
  const [a, b] = CONFIG.routeColors;
  return (
    <figure className="sketch">
      <svg viewBox="0 0 520 250" role="img" aria-labelledby="sk-t sk-d">
        <title id="sk-t">Two routes between the same start and end</title>
        <desc id="sk-d">Route A is solid along its whole length. Route B is solid at first and dashed after the middle.</desc>
        <path d="M40 200 C 150 215, 220 150, 290 135 S 420 70, 480 55" fill="none" stroke={a} strokeWidth="7" strokeLinecap="round" />
        <path d="M40 200 C 100 130, 160 70, 250 75" fill="none" stroke={b} strokeWidth="7" strokeLinecap="round" />
        <path d="M250 75 C 330 80, 400 40, 480 55" fill="none" stroke={b} strokeWidth="3" strokeLinecap="round" strokeDasharray="3 9" />
        {[[110, 207], [205, 170], [330, 118], [95, 140], [175, 88]].map(([x, y]) => (
          <circle key={`${x}-${y}`} cx={x} cy={y} r="7" fill="#1B2733" stroke="#fff" strokeWidth="2" />
        ))}
        <circle cx="40" cy="200" r="9" fill="#fff" stroke="#1B2733" strokeWidth="3" />
        <circle cx="480" cy="55" r="9" fill="#fff" stroke="#1B2733" strokeWidth="3" />
      </svg>
      <figcaption>
        Solid stretches have recent reports along them. Dashed stretches have none, so what is there is unknown, not clear.
      </figcaption>
    </figure>
  );
}

export default function Home() {
  return (
    <div className="home">
      <section className="hero">
        <div>
          <h1>Compare walking routes by what has been reported along them</h1>
          <p className="lede">
            PathLens shows reported street conditions such as broken streetlights, obstructions and damaged footpaths, and how much evidence sits behind each route.
          </p>
          <div className="row">
            <Link to="/explore" className="primary">Compare routes</Link>
            <Link to="/explore?mode=report" className="ghost">Report an issue</Link>
          </div>
        </div>
        <Sketch />
      </section>

      <section className="home-grid">
        <div>
          <h2>How to read a result</h2>
          <dl>
            <dt>Reported concern</dt>
            <dd>A score from 0 to 100 built only from counted reports, per kilometre, so a longer route is not penalised for being longer.</dd>
            <dt>Evidence confidence</dt>
            <dd>Shown separately. It says how much of the route has recent evidence. A route with little evidence is marked as not enough data, never as low concern.</dd>
            <dt>Counted or shown</dt>
            <dd>Verified reports count in full. Two unverified reports of the same issue nearby count at half weight. A single unverified report is shown on the map but not counted.</dd>
          </dl>
        </div>
        <div>
          <h2>What PathLens does not do</h2>
          <ul className="plain">
            <li>It does not predict crime or say that a route is safe.</li>
            <li>It does not treat missing reports as proof that nothing is wrong.</li>
            <li>It does not score neighbourhoods, only the routes you ask about.</li>
            <li>It labels simulated demo data wherever it appears.</li>
          </ul>
        </div>
      </section>
    </div>
  );
}
