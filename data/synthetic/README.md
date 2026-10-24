# data/synthetic

Invented data for development, tests and demos. None of it describes a real place or a real person.

| File | What it is |
|---|---|
| `classifier_seed.csv` | The 90 hand-written training sentences of the report classifier (18 per category), exported from `backend/ai/seed_reports.py`. One author wrote all of them, so any accuracy figure from this set is optimistic. Replace and grow it with verified, moderator-labelled reports. |
| `simulated_reports.csv` | Simulated reports along the two invented routes in `data/roads`, made by the same generator as the "Simulated demo data" button, with a fixed date. Every row says `source=simulated` and `simulated=true`. |

Both files are generated, not edited by hand:

```bash
python data/build_synthetic.py            # rewrite the generated files in data/roads and data/synthetic
python data/build_synthetic.py --check    # fail if a committed file is out of date
```

To change the classifier's training set, edit `backend/ai/seed_reports.py` and rebuild.
