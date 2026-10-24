# data/roads

Road geometry and map-feature snapshots. **Everything here is a synthetic sample.**

| File | What it is |
|---|---|
| `demo_corridor.geojson` | Two invented walking routes ("Route A" and "Route B") between the placeholder places Alpha and Beta, plus the two places. Used by the offline demo server and the browser tests. |
| `osm_snapshot_synthetic.json` | An Overpass-style `{"elements": [...]}` file with invented streetlights, help points and activity points along that corridor. Every element has a negative id (the OpenStreetMap convention for objects that do not exist) and a "synthetic sample" tag. It is **not** OpenStreetMap data. |

Load the snapshot into a running API to light up the "streetlights, help points, activity" context layers:

```bash
ADMIN_TOKEN=... python data/load_data.py baseline data/roads/osm_snapshot_synthetic.json
```

For your own area take a real snapshot instead, and keep it here (it is a copy of OpenStreetMap data, so keep its
licence and attribution: © OpenStreetMap contributors, ODbL). Either call
`POST /api/admin/baseline/import` with a bounding box, or run the query printed by
`backend/services/osm_baseline.py::build_overpass_query` and save the JSON response.

The scoring itself never uses these layers. They are shown as context only.
