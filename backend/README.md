# PathLens API (backend)

FastAPI service for PathLens: it compares walking routes by reported street conditions and shows the
evidence, freshness and confidence behind every result. It does not predict crime and does not say that
any route is safe.

## Run it

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # then set ADMIN_TOKEN, or the Supabase sign-in settings (admin stays off without one)
uvicorn main:app --reload       # http://localhost:8000/docs
pytest                          # 217 tests, no network needed
```

It uses a local SQLite file by default. For Supabase, set `DATABASE_URL` to the Postgres connection string
and install `psycopg[binary]` (see `requirements.txt`). Tables are created at start-up.

## Endpoints

| Method and path | Who | What it does |
|---|---|---|
| `POST /api/routes/compare` | public | Up to 3 walking routes, scored, with evidence, explanation, uncertainty range, rank stability and informational layers. Reads only. |
| `POST /api/reports` | public (sign-in when enabled), rate limited | Submit a report. `consent: true` is required. Category is optional: the classifier fills it in when confident, otherwise 422. An optional `photo` (data URL) is rebuilt on the server. |
| `POST /api/reports/classify` | public, rate limited | Suggest a category for some text. Stores nothing. |
| `GET /api/reports` | public | Map data. Never lists rejected reports; simulated ones only with `include_simulated=true`. Free text of unverified reports is withheld. |
| `POST /api/reports/{id}/confirmations` | public (sign-in when enabled), rate limited | One tap: still there / fixed. Informs moderators; never changes a score. You cannot confirm your own report. |
| `POST /api/reports/{id}/disputes` | public (sign-in when enabled), rate limited | "This is wrong or out of date", with a short reason. One per person. A disputed verified report goes back into the queue. |
| `PATCH /api/reports/{id}/status` | moderator | Verify, reject, resolve, reopen. Only allowed transitions; a rejection needs a reason; every change is audited with who made it. |
| `GET /api/reports/{id}/photo` | moderator | The cleaned photo. Never cached, never public. `DELETE` (with `?note=`) removes it and keeps the report. |
| `GET /api/reports/{id}/events` | moderator | Audit trail. |
| `POST /api/reports/field-audit` | moderator | A report from the team walking a corridor: saved verified, source `field_audit`. |
| `GET /api/dashboard/stats` | moderator | Counts, data-quality figures (including median days to resolve, disputed and photo counts) and warnings. Simulated data is kept out of every figure but its own count. |
| `GET /api/admin/reports?status=&include_simulated=` | moderator | Every report by status, including rejected ones, with free text, photo and dispute flags. |
| `GET /api/dashboard/queue` | moderator | Pending reports, most worth reviewing first, with reasons. Verified reports that are disputed or voted "fixed" come back as `flagged`. |
| `GET /api/dashboard/export?format=json\|csv&status=verified\|resolved\|all` | moderator | Open data for the city or an NGO: category, place, status and dates. No reporter, no photo. Spreadsheet formulas are neutralised in the CSV. |
| `POST /api/admin/maintenance` | moderator | Run the retention rules now (they also run at start-up). |
| `GET /api/methodology` | public | Weights, parameters, category labels, personas, trust tiers, fairness and privacy notes, account rules, classifier metrics. The website reads its labels and privacy notice from here. |
| `POST /api/admin/baseline/load` | moderator | Load a saved Overpass JSON snapshot (streetlights, help points, activity). |
| `POST /api/admin/baseline/import` | moderator | Fetch that snapshot from Overpass for a bounding box. |
| `POST /api/admin/demo-seed` / `DELETE` | moderator | Create or remove SIMULATED demo reports (always flagged). |
| `GET /api/health` | public | Liveness check. Ping it every ~10 minutes to keep a free-tier host awake. |

### Sign-in and moderators

Two ways to be a moderator, and both can be on at once:

1. **A Supabase account** whose `app_metadata.role` is `moderator` or `admin` (send `Authorization: Bearer <access token>`).
   Set that role from the Supabase dashboard or a server-side script. The API ignores `user_metadata` and the
   top-level `role` claim, because users can edit the first and the second is always `authenticated`.
2. **The shared `X-Admin-Token`** (`ADMIN_TOKEN`), kept as a fallback. Everyone using it appears as `admin` in the audit trail.

With neither configured, admin endpoints answer 503: they are switched off, not open. Never put the token in
the frontend bundle (`VITE_` variables are public); type it into the admin page at runtime.

For reporters, set `SUPABASE_JWT_SECRET` (older projects) or `SUPABASE_URL` / `SUPABASE_JWKS_URL` (projects with
asymmetric signing keys). Reports, confirmations and disputes then need a signed-in account (`REQUIRE_LOGIN=auto`;
`1` forces it, `0` disables it). Anonymous Supabase sessions do not count. Each account has its own rate limit, its
reports are tied to an account id instead of an address hash, and the audit trail shows a short pseudonym
(`user:1a2b3c4d`, `mod:5e6f7a8b`), never an email.

Example:

```bash
curl -X POST localhost:8000/api/routes/compare -H 'content-type: application/json' -d '{
  "origin": {"query": "Trafalgar Square"}, "destination": {"lat": 51.5, "lng": -0.085},
  "persona": "night_walker", "at": "2026-12-15T22:00:00Z"
}'
```

## How the known limitations are handled

| Limitation | What the backend does | Where |
|---|---|---|
| Sparse data, hollow demo | Three labelled data layers: OSM baseline, field audit, simulated. `simulated` reports are flagged, hidden by default, seedable and removable in one call. Snapshots are stored, not fetched live. | `models/report.py`, `services/demo_data.py`, `services/osm_baseline.py`, `routes/dashboard_api.py` |
| "No data" read as "good news" | Routes are cut into 100 m chunks. Chunks without recent evidence are unobserved, a route with under 20% coverage is "not enough data", and confidence is reported separately. | `services/risk_engine.py`, `services/freshness.py` |
| Reporting bias, long routes, tiny samples | Per-kilometre scoring, the longest affected stretch, a smoothing pseudo-length so one report cannot saturate a short route, and an uncertainty range. | `services/risk_engine.py` |
| Stale information | Per-category half-lives, a coverage window, unverified reports expiring after 60 days, and "still there / fixed" taps that raise a report in the queue. | `services/freshness.py`, `routes/report_api.py` |
| Neighbourhood stigmatisation | Only routes are scored. Help points, mapped lamps and surrounding activity are informational layers and never enter the score. Wording says "reported concern". | `services/risk_engine.py`, `routes/dashboard_api.py` |
| Unvalidated weights | Weights are published as provisional, can be changed per request, come as persona presets, and a sensitivity check says how often the best route changes when they move ("stable" or "close call"). | `services/risk_engine.py`, `routes/route_api.py` |
| Lighting only matters after dark | The lighting weight follows the sun at the route midpoint and the requested time. | `services/daylight.py` |
| False or duplicate reports, brigading | Rate limits; unverified reports count only when two independent reporters corroborate them, at half weight; one client cannot corroborate itself; free text hidden until reviewed; contact details refused; audit trail. | `routes/deps.py`, `routes/report_api.py`, `schemas/report.py` |
| Moderator bottleneck | Classifier suggestions, duplicate flags, and a priority queue (severity, similar reports, confirmations, age). | `ai/`, `routes/dashboard_api.py` |
| Privacy | Comparing routes writes nothing. Origin and destination live only in a short in-memory cache. No IP address is stored, only a salted one-way hash (or the account id when signed in). Consent is required to report. | `routes/route_api.py`, `services/routing.py`, `routes/deps.py`, `schemas/report.py` |
| Photos can leak location or show people | The server rebuilds every photo as a fresh JPEG (orientation applied, EXIF and GPS gone, shrunk, size-capped, anything that is not an image refused). Only moderators can see photos, they are never cached, and they are deleted on rejection or on request. Faces and plates are **not** blurred, which is why photos are not public. | `services/privacy.py`, `routes/report_api.py` |
| Personal details in free text | Phone numbers, emails, links, @handles and Indian vehicle numbers are refused in reports and disputes. Names cannot be detected, so moderators read text before it is public. | `services/privacy.py` |
| Keeping data forever | Rejected reports are deleted after 30 days, never-reviewed reports after 180, photos of resolved reports after 30. Configurable, published in `/api/methodology`, run at start-up and by `POST /api/admin/maintenance`. | `services/retention.py` |
| One shared admin password, no accountability | Per-moderator accounts through Supabase roles, named in the audit trail; rejections need a written reason. The shared token remains as a fallback. | `routes/deps.py`, `schemas/report.py` |
| A wrong verified report stays up | Anyone can dispute a report. A disputed verified report, or one voted "fixed" twice, returns to the queue as `flagged` with higher priority. Moderators decide; votes never change a score by themselves. | `routes/report_api.py`, `routes/dashboard_api.py` |
| No proof the city responds | Time-to-resolution is tracked (`resolved_at`, median days) and the verified/resolved reports can be exported without personal data. | `routes/dashboard_api.py` |
| Missing data hidden inside an average | Every route lists its **information gaps**: stretches of 200 m or more with no recent evidence, longest first, with a map position. A route with no evidence is one big gap and stays "not enough data". | `services/risk_engine.py`, `services/explanation.py` |
| Alarming wording | Labels, band text, persona text and explanation templates avoid words like "safe", "danger", "risk" and "crime". A test scans every explanation the API can produce. | `tests/test_gaps_language.py` |
| ML cold start | A hand-written synthetic seed set, word and character n-grams, a confidence threshold, a keyword fallback, and honest metrics that say they are optimistic. | `ai/` |
| Explanations that drift from the evidence | Explanations are templates filled from the analysis, with evidence ids. No free-form text. | `services/explanation.py` |
| Free-tier cold starts and rate-limited public servers | Route and geocode caching, fallback from OpenRouteService to the OSRM foot server, classifier trained at start-up, and report intake that survives a classifier failure. | `services/routing.py`, `main.py` |

## Deploy (Render or similar)

Root directory `backend`, build `pip install -r requirements.txt`, start
`uvicorn main:app --host 0.0.0.0 --port $PORT`. Set `DATABASE_URL`, `ADMIN_TOKEN`, `CORS_ORIGINS` (your
Vercel URL), `PRIVACY_SALT` (a long random string, otherwise reporter hashes reset on every restart),
`HTTP_USER_AGENT` (public OSM services require one with a contact address), and `TRUST_PROXY=1` so the
real client address is used for rate limits.

## Layout

```
backend/
  main.py  database.py
  models/    report.py (reports, audit events, confirmations, disputes)  infra.py (OSM baseline features)
  schemas/   report.py  route.py  dashboard.py
  routes/    route_api.py  report_api.py  dashboard_api.py  deps.py
  services/  routing.py  risk_engine.py  freshness.py  explanation.py  geo.py  daylight.py  osm_baseline.py  demo_data.py
             privacy.py (text screening, photo cleaning)  retention.py (deletion rules)
  ai/        classify_report.py  duplicate_detection.py  seed_reports.py
  tests/
```

Files beyond the layout you listed: `routes/deps.py` (sign-in, moderator check, rate limits), `services/geo.py`,
`services/daylight.py`, `services/osm_baseline.py`, `services/demo_data.py`, `services/privacy.py`,
`services/retention.py`, `ai/seed_reports.py`, and the tests. Do not add an `__init__.py` to `tests/`:
the tests import `conftest` directly.

Old SQLite files keep working: start-up adds any missing columns (`ensure_columns`). On Postgres, use a proper
migration tool once the schema starts changing often.

## Known gaps

- **Supabase sign-in was tested with locally made tokens.** Both the shared-secret path and the public-key path
  (with a fake key set) are covered, including expired, wrong-audience, `alg: none` and algorithm-confusion tokens. The
  real fetch of `/auth/v1/.well-known/jwks.json` was not run here, and you must set each moderator's
  `app_metadata.role` in your own project and try a login before relying on it.
- **Tested on SQLite only.** Postgres/Supabase is supported through `DATABASE_URL` but was not run here.
- **Live services.** The OSRM foot server URL was confirmed to answer with walking routes, but OpenRouteService,
  Nominatim and Overpass were not called from the build sandbox (a route comparison there answers 502, as designed).
  The Overpass query matches the Overpass QL documentation but has not run against the live server.
- **No face or number-plate blurring, and no name detection.** That is why photos are moderator-only and why a
  moderator reads text before it is public. The vehicle-number screen covers Indian formats only.
- **Rate limits and caches are per process.** With several workers they are not shared; use Redis or the host's limits then.
- **Spatial matching is in Python** (a bounding box query, then exact distance). It is fine at prototype scale;
  move it to PostGIS if reports reach the tens of thousands. Snapping the OSM baseline to individual ways is not built.
- **The website is a thin client of this API** (`../frontend`) and is covered by browser tests in `../tests/e2e` against this real
  API with only routing and place search stubbed. It has not been run against live routing services or a real Supabase project.
- **Weights, the 100 m chunk, the 30 m buffer, smoothing, half-lives and the 200 m gap length are provisional.**
  Treat the score as a comparison aid, not a validated safety model.
