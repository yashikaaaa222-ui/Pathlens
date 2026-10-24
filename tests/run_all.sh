#!/usr/bin/env bash
# Runs every test suite in the repository, one after another, and says which ones failed.
#
#   bash tests/run_all.sh            # API, website helpers, data files (no browser, no network)
#   bash tests/run_all.sh --e2e      # the same, plus the real-browser tests (needs Playwright and Chromium)
#
# Setup once:  pip install -r requirements.txt   and   (cd frontend && npm install)
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-python3}"
failed=()

run() {  # run <name> <directory> <command...>
  local name="$1" dir="$2"; shift 2
  echo; echo "=== $name ==="
  ( cd "$dir" && "$@" ) || failed+=("$name")
}

run "API and scoring engine (backend/tests)" "$ROOT/backend" "$PY" -m pytest -q -p no:cacheprovider
run "Data files and loader (tests/)"          "$ROOT"         "$PY" -m pytest -q -p no:cacheprovider
run "Website helpers and API client"          "$ROOT/frontend" npm test --silent
run "Sample data is up to date"               "$ROOT"         "$PY" data/build_synthetic.py --check
if [ "${1:-}" = "--e2e" ]; then
  run "Whole system in a browser (tests/e2e)" "$ROOT"         bash tests/e2e/run_all.sh
fi

echo
if [ ${#failed[@]} -eq 0 ]; then echo "All suites passed."; else echo "FAILED: ${failed[*]}"; exit 1; fi
