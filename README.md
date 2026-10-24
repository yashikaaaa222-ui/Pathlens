# PathLens

Compare walking routes by reported street conditions (lighting, hazards), with the evidence, freshness and
confidence shown next to every score. It does not predict crime and does not say a route is safe.

PathLens was called ShadowSafe AI while it was being planned. It was renamed because "safe" in a product name reads as
a promise this project explicitly does not make.

## What is in here

```
backend/       FastAPI: routing, scoring engine, reports, moderation, sign-in, photos, retention, classifier
frontend/      React + Vite + Leaflet: a thin client of the API (no scoring in the browser)
data/          roads/ (invented corridor, OSM-style sample), reports/ (your field-audit sheets), synthetic/ (seed set, simulated reports)
notebooks/     data_exploration.ipynb: classifier, freshness, scoring behaviour, weight sensitivity (executed, with charts)
tests/         Checks that cross folders: sample data, loader scripts, the notebook; tests/e2e drives a real browser
requirements.txt   One Python environment for the API, the notebook and the browser tests
render.yaml    Deploy blueprint for the API (untested)
```

Everything under `data/roads` and `data/synthetic` is invented and labelled so. Real reports never go into version
control: `data/reports/.gitignore` ignores everything except the template.

The browser holds no copy of the scoring rules. Weights, category labels, personas and the privacy notice all
come from `GET /api/methodology`, so what the interface says is always what the engine does.

## Run it (two terminals)

```bash
# 1. API  ->  http://localhost:8000/docs
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # set ADMIN_TOKEN (a long random string)
uvicorn main:app --reload

# 2. Website  ->  http://localhost:5173   (forwards /api to localhost:8000)
cd frontend
npm install
npm run dev
```

Set the prototype city in `frontend/src/config.js` (`center`, `zoom`). Then:

1. **Explore**: type two places, compare routes. With no reports yet every route says "Not enough data": that is
   correct, not an error.
2. **Admin** (`/admin`): enter your `ADMIN_TOKEN`. Add real evidence with *Add a field-audit report*, or load
   clearly labelled **simulated** demo reports with *Simulated demo data* (they stay hidden until someone ticks
   "Include simulated demo reports").
3. **Report an issue** on Explore: click the map, describe the problem, tick the privacy consent.

## Sign-in with Supabase (optional)

Without it, anyone can report and moderators use the shared `ADMIN_TOKEN`. With it, reporters have accounts
(one person cannot corroborate their own report), and moderators are named individually in the audit trail.

1. Create a Supabase project. Authentication: email sign-in on. Anonymous sign-ins are not accepted by the API.
2. Backend `.env`: either `SUPABASE_URL` (projects with asymmetric signing keys) or `SUPABASE_JWT_SECRET`
   (older projects). The API then requires a sign-in for reports, confirmations and disputes.
3. Frontend `.env`: `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` (both public by design). **Never** put the
   JWT secret or a service-role key in a `VITE_` variable.
4. Make a moderator: in the Supabase SQL editor run
   `update auth.users set raw_app_meta_data = coalesce(raw_app_meta_data, '{}'::jsonb) || '{"role":"moderator"}'::jsonb where email = 'you@example.com';`
   then sign in again so the new token carries the role. Only `app_metadata.role` counts; users can edit
   `user_metadata` themselves, so the API ignores it.

This path is tested against a mock of Supabase Auth, not against a real project. Try one real login before relying on it.

## Deploy

- **API** (Render or similar): root `backend`, build `pip install -r requirements.txt`, start
  `uvicorn main:app --host 0.0.0.0 --port $PORT`. Set `DATABASE_URL`, `ADMIN_TOKEN`, `CORS_ORIGINS` (your Vercel URL),
  `PRIVACY_SALT`, `HTTP_USER_AGENT` (with a contact address) and `TRUST_PROXY=1`. See `backend/README.md`.
  `render.yaml` at the repository root is a ready blueprint for this (untested). The free Render service loses its
  disk on restart, so point `DATABASE_URL` at Postgres rather than SQLite, and uncomment `psycopg` in
  `backend/requirements.txt`. Postgres has not been run here.
- **Website** (Vercel): root `frontend`, build `npm run build`, output `dist`. Set `VITE_API_URL` to the API address
  (and the two `VITE_SUPABASE_*` values if you use sign-in). `vercel.json` already routes every path to the app.

## Tests

| What | How | Count |
|---|---|---|
| API and engine | `cd backend && pytest` (no network needed) | 217 |
| Interface helpers and API client | `cd frontend && npm test` | 23 |
| Sample data, loader scripts, the notebook | `pytest` from the repository root | 22 |
| Whole system in a real browser | `bash tests/e2e/run_all.sh` (see the header of that file for setup) | 25 steps |

All but the browser tests in one go: `bash tests/run_all.sh` (add `--e2e` for the browser tests too).

The browser tests run the real API with only the outside lookups (walking routes, place search) replaced by fixed
answers, so they pass without internet. They cover comparing, the "not enough data" and gap displays, reporting
with consent and a photo, voting, disputes, moderation with the audit trail, export, retention, and both
sign-in setups.

## Data and notebook

- `python data/load_data.py baseline data/roads/osm_snapshot_synthetic.json` loads the sample map features into a
  running API (it needs `ADMIN_TOKEN`); `python data/load_data.py field-audit <sheet.csv>` loads a field-audit sheet
  as verified reports. See the README in each `data/` folder.
- `jupyter lab notebooks/` opens the exploration notebook. The charts are already saved in the file. It documents one
  open design question about unverified reports and coverage (see Known gaps).

## What the product does and does not do

- Scores only routes you ask about, never neighbourhoods. Only lighting and road hazards are scored; help points,
  mapped lamps and surrounding activity are shown as context.
- Unknown is not clear: stretches with no recent evidence are dashed on the map and listed, and a route with too
  little evidence is "Not enough data", never "low".
- Every score comes with a range, a separate confidence, and a "stable" or "close call" ranking check.
- Unverified reports are labelled. A single unverified report is shown but not counted.
- Photos are rebuilt on the server (location data removed), seen by moderators only, and deleted when a report is
  rejected. Retention rules are published and enforced.
- Simulated data is always labelled and hidden by default.

## Known gaps

- **Open design question (decide before real use):** a single unverified report is not counted, but it still
  counts as *coverage*. A route whose only evidence is such reports can read "Low reported concern". The notebook
  (section 5) shows it with numbers and lists three ways to resolve it. The engine is unchanged.
- Live OpenRouteService, Nominatim and Overpass calls, Postgres/Supabase, and a real Supabase login were not run
  from the build sandbox. Run each once on your machine.
- No face or number-plate blurring and no automatic name detection, which is why photos are moderator-only.
- Rate limits and caches are per server process.
- Weights, the 100 m chunk, the 30 m buffer, the 200 m gap length and the half-lives are provisional. The score is a
  comparison aid, not a validated safety model. Pilot it on one ward and check that people read "Not enough data"
  and "close call" the way you intend.
