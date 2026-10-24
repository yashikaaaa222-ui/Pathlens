# data/reports

Where real report files live **on your own machine**. Nothing real is committed: the `.gitignore` here ignores
every file except this README and the empty template.

| File | What it is |
|---|---|
| `field_audit_template.csv` | Header only. Copy it, fill one row per problem you see while walking a corridor, then load it. |
| your own files (ignored by git) | Field-audit sheets, and CSV exports from the admin page (*Export issues*). |

## Field-audit sheet

| Column | Rule |
|---|---|
| `category` | One of `streetlight`, `poor_lighting`, `obstruction`, `footpath`, `visibility` |
| `lat`, `lng` | Where the problem is, in decimal degrees (copy them from a map app) |
| `description` | Optional, up to 280 characters. Describe the infrastructure only: no phone numbers, links, handles, vehicle numbers or names |
| `auditor` | Optional, up to 40 characters. Use a code or initials, not a full name |

Load it into a running API (rows are saved as **verified**, source `field_audit`, so include only what a person
really saw):

```bash
ADMIN_TOKEN=... python data/load_data.py field-audit data/reports/my_ward_audit.csv
```

A bad row is reported and skipped; the rest are still loaded. Rows are not checked for duplicates, so do not
load the same sheet twice.

For invented sample rows to try the product with, see `data/synthetic/` instead.
