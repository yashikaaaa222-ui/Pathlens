#!/usr/bin/env bash
# Runs both browser tests against the real API (outside lookups stubbed) and a built copy of the frontend.
#
#   pip install -r requirements.txt && python -m playwright install chromium
#   (cd frontend && npm install)
#   bash tests/e2e/run_all.sh
#
# Set CHROMIUM_PATH to use a browser you already have. Screenshots and downloads land in tests/e2e/out/.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$(dirname "$HERE")")"
OUT="$HERE/out"; mkdir -p "$OUT"
PY="${PYTHON:-python3}"
PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done; }
trap cleanup EXIT

wait_for() { for _ in $(seq 1 40); do curl -s -o /dev/null "$1" && return 0; sleep 0.5; done; echo "timed out waiting for $1"; return 1; }

echo "Building the frontend (open sign-in, and with a mock Supabase address)..."
( cd "$ROOT/frontend" \
  && npx vite build --outDir "$OUT/dist-open" --emptyOutDir > "$OUT/build-open.log" 2>&1 \
  && VITE_SUPABASE_URL=http://localhost:9999 VITE_SUPABASE_ANON_KEY=anon-test-key npx vite build --outDir "$OUT/dist-auth" --emptyOutDir > "$OUT/build-auth.log" 2>&1 ) || { echo "frontend build failed, see $OUT/build-*.log"; exit 1; }

rm -f "$OUT/open.db" "$OUT/auth.db"
export ADMIN_TOKEN=e2e-admin-key

DATABASE_URL="sqlite:///$OUT/open.db" REQUIRE_LOGIN=0 "$PY" "$HERE/stub_backend.py" 8000 > "$OUT/api-open.log" 2>&1 & PIDS+=($!)
DATABASE_URL="sqlite:///$OUT/auth.db" SUPABASE_JWT_SECRET="e2e-jwt-secret-which-is-long-enough-32b!" "$PY" "$HERE/stub_backend.py" 8001 > "$OUT/api-auth.log" 2>&1 & PIDS+=($!)
"$PY" "$HERE/mock_gotrue.py" 9999 > "$OUT/gotrue.log" 2>&1 & PIDS+=($!)
( cd "$ROOT/frontend" && API_PROXY_TARGET=http://localhost:8000 exec node_modules/.bin/vite preview --outDir "$OUT/dist-open" --port 4173 --strictPort > "$OUT/preview-open.log" 2>&1 ) & PIDS+=($!)
( cd "$ROOT/frontend" && API_PROXY_TARGET=http://localhost:8001 exec node_modules/.bin/vite preview --outDir "$OUT/dist-auth" --port 4174 --strictPort > "$OUT/preview-auth.log" 2>&1 ) & PIDS+=($!)

wait_for http://localhost:8000/api/health && wait_for http://localhost:8001/api/health \
  && wait_for http://localhost:4173/ && wait_for http://localhost:4174/ || exit 1

status=0
echo; echo "== Open sign-in =="; "$PY" "$HERE/browser_flow.py" || status=1
echo; echo "== Sign-in required =="; "$PY" "$HERE/auth_flow.py" || status=1
exit $status
