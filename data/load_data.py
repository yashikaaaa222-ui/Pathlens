"""Load files from data/ into a running PathLens API.

    # OpenStreetMap-style snapshot (streetlights, help points, activity). The synthetic sample is for demos only.
    python data/load_data.py baseline data/roads/osm_snapshot_synthetic.json

    # Field-audit reports from a CSV (copy data/reports/field_audit_template.csv and fill it in on the walk).
    python data/load_data.py field-audit data/reports/my_ward_audit.csv

Options: --api http://localhost:8000 (default), --token <ADMIN_TOKEN> (or set ADMIN_TOKEN), or --bearer <access token>
of a moderator account. Field-audit rows are saved as verified with the source "field_audit", so only load rows a
person really observed on the ground. A bad row is reported and skipped; the others are still loaded.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

import httpx

FIELD_AUDIT_COLUMNS = ["category", "lat", "lng", "description", "auditor"]


def auth_headers(token: str | None = None, bearer: str | None = None) -> dict[str, str]:
    if bearer:
        return {"Authorization": f"Bearer {bearer}"}
    token = token or os.getenv("ADMIN_TOKEN", "")
    return {"X-Admin-Token": token} if token else {}


def load_baseline(client: httpx.Client, path: str, headers: dict[str, str]) -> dict:
    """POST an Overpass-style JSON file to /api/admin/baseline/load. Returns the server's counts."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
        raise SystemExit(f"{path}: expected a JSON object with an 'elements' list (an Overpass response).")
    r = client.post("/api/admin/baseline/load", json={"elements": data["elements"]}, headers=headers)
    if r.status_code != 200:
        raise SystemExit(f"The API refused the snapshot ({r.status_code}): {r.text[:300]}")
    return r.json()


def load_field_audit(client: httpx.Client, path: str, headers: dict[str, str]) -> dict:
    """POST each row of a CSV to /api/reports/field-audit. Returns {"saved": n, "skipped": [(row_number, reason)]}."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in ("category", "lat", "lng") if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"{path}: missing column(s) {', '.join(missing)}. Expected: {', '.join(FIELD_AUDIT_COLUMNS)}.")
        saved, skipped = 0, []
        for n, row in enumerate(reader, start=2):  # row 1 is the header
            if not any((v or "").strip() for v in row.values()):
                continue
            try:
                body = {"category": row["category"].strip(), "lat": float(row["lat"]), "lng": float(row["lng"])}
            except (TypeError, ValueError, AttributeError):
                skipped.append((n, "lat and lng must be numbers"))
                continue
            if (row.get("description") or "").strip():
                body["description"] = row["description"].strip()
            if (row.get("auditor") or "").strip():
                body["auditor"] = row["auditor"].strip()
            r = client.post("/api/reports/field-audit", json=body, headers=headers)
            if r.status_code == 201:
                saved += 1
            elif r.status_code in (401, 403, 503):
                raise SystemExit(f"The API refused the admin credentials ({r.status_code}). Nothing more was sent.")
            else:
                reason = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
                if isinstance(reason, list):  # FastAPI validation errors
                    reason = "; ".join(str(e.get("msg", e)) for e in reason)
                skipped.append((n, str(reason)[:200]))
    return {"saved": saved, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("what", choices=["baseline", "field-audit"])
    p.add_argument("file")
    p.add_argument("--api", default=os.getenv("API_URL", "http://localhost:8000"))
    p.add_argument("--token")
    p.add_argument("--bearer")
    args = p.parse_args(argv)
    headers = auth_headers(args.token, args.bearer)
    if not headers:
        print("No credentials: pass --token, --bearer or set ADMIN_TOKEN.", file=sys.stderr)
        return 2
    with httpx.Client(base_url=args.api, timeout=60.0) as client:
        if args.what == "baseline":
            print(json.dumps(load_baseline(client, args.file, headers), indent=1))
            return 0
        result = load_field_audit(client, args.file, headers)
    print(f"Saved {result['saved']} field-audit report(s).")
    for n, reason in result["skipped"]:
        print(f"  skipped row {n}: {reason}")
    return 1 if result["skipped"] else 0


if __name__ == "__main__":
    sys.exit(main())
