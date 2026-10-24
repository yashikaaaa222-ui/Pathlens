# tests

| Where | What it checks | How to run |
|---|---|---|
| `backend/tests/` | The API and the scoring engine: reports, moderation, sign-in, photos, retention, classifier, gaps, wording | `cd backend && pytest` |
| `frontend/src/lib/*.test.js` | The website's API client and display helpers | `cd frontend && npm test` |
| `tests/test_data_files.py` | The sample files in `data/` stay synthetic and reproducible, and the loader script works against the real API | `pytest` from the repository root |
| `tests/e2e/` | The built website in a real browser against the real API (outside lookups replaced by fixed answers) | `bash tests/e2e/run_all.sh` |

Everything at once: `bash tests/run_all.sh` (add `--e2e` to include the browser tests).

The API's own tests stay in `backend/tests` because they share its fixtures and configuration; this folder holds
the checks that cross the boundaries between the parts.

`tests/e2e/stub_backend.py` is also handy for an offline demo: it is the real API with only the walking-route and
place-search lookups replaced by the two invented routes in `data/roads` (places "Alpha" and "Beta").
