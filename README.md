# PA PALS — Pennsylvania License Lookup & Document Downloader

Fetches PA license data from the [PALS public API](https://www.pals.pa.gov/) for a list of providers,
enriches your CSV with license number / expiration date / active flag, and downloads any disciplinary
action documents on record — all resumable if interrupted.

---

## Prerequisites

```bash
pip install playwright
playwright install chromium      # only needed if you run the Playwright-based scripts
```

Python 3.9+ required. No other third-party dependencies — everything else uses the standard library.

---

## Directory structure

```
PA_PALS/
├── credentials/
│   └── config.py               ← API base URL and headers (edit if PALS changes)
├── output/
│   ├── licenses/               ← enriched CSVs written here
│   └── disciplinary_docs/      ← PDFs written here as {NPI}_{LicenseNumber}_{OriginalName}.pdf
├── pals_api.py                 ← all PALS API calls (search, detail, download)
├── run_pa_pals.py              ← main entry point — run this
├── fetch_pa_licenses.py        ← lightweight single-license / single-provider lookup
├── download_disciplinary_docs.py ← standalone doc downloader
└── requirements.txt
```

---

## Input CSV format

The input CSV must have at minimum:

| Column | Required | Notes |
|---|---|---|
| `NPI` | Yes | Provider NPI number |
| `first_name` | Yes | Also accepts `First Name` or `firstname` |
| `last_name` | Yes | Also accepts `Last Name` or `lastname` |
| `license_number` | No | PA license number — if blank, script searches by name |
| `expiration_date` | No | Will be filled/updated by the script |
| `active_flag` | No | Will be set to `Active` if expiry is in the future, else the raw PALS status |
| `city` | No | Helps resolve ambiguous name matches |
| `profession` | No | Helps resolve ambiguous name matches |

Column names are **case-insensitive** and common variants are recognised automatically.

---

## Running the main script

```bash
cd PA_PALS

# Basic run
python3 run_pa_pals.py --input providers.csv

# With explicit output path
python3 run_pa_pals.py --input providers.csv --output output/pa_enriched.csv

# Skip document downloads (license data only)
python3 run_pa_pals.py --input providers.csv --no-docs

# Restart fresh (ignore checkpoint)
python3 run_pa_pals.py --input providers.csv --reset
```

### All parameters

| Parameter | Default | Description |
|---|---|---|
| `--input` | *(required)* | Path to input CSV |
| `--output` | `output/licenses/pa_results_<timestamp>.csv` | Path for enriched output CSV |
| `--checkpoint` | `output/pa_checkpoint.json` | Checkpoint file — tracks completed NPIs |
| `--log` | `output/pa_run.log` | Log file path |
| `--no-docs` | off | Pass this flag to skip document downloads |
| `--reset` | off | Pass this flag to ignore the checkpoint and start fresh |

---

## Resume after interruption

The script saves a checkpoint after every provider. If it crashes or you Ctrl-C it:

```bash
# Just re-run the same command — it skips providers already in the checkpoint
python3 run_pa_pals.py --input providers.csv
```

The checkpoint is stored at `output/pa_checkpoint.json`.  
To start over from scratch: `--reset` flag or delete the checkpoint file.

---

## How license lookup works

1. **If `license_number` is provided** — searches PALS by license number (exact match).
2. **If no license number** — searches by first + last name in PA.
   - Prefers Active status matches.
   - Uses `city` and `profession` columns (if present) to break ties.
   - If still ambiguous (multiple equal candidates), logs all candidates and marks the row as `ambiguous` — **does not guess**.
3. Fetches full license details (issue date, expiry, renewal, disciplinary actions).
4. Sets `active_flag = Active` if expiry date is today or in the future; otherwise uses the raw PALS status.

---

## Downloaded document naming

```
{NPI}_{LicenseNumber}_{OriginalFilename}
```

Example:
```
1174503361_MD031820E_Squire_Karen_Marie_15-49-08957_CA_OK.pdf
```

Documents are saved to `output/disciplinary_docs/`. If a file already exists it is skipped (not re-downloaded).

---

## Output CSV columns

All original columns are preserved. The script adds:

| Column | Description |
|---|---|
| `_status` | `updated`, `unchanged`, `not_found`, `ambiguous`, or `error` |
| `_pa_license` | PA license number found |
| `_expiry` | Expiration date from PALS |
| `_active` | Active flag value written |
| `_docs_downloaded` | Number of documents downloaded for this provider |
| `_notes` | How the match was found + what changed |

---

## Run summary (printed and logged)

```
============================================================
RUN SUMMARY
============================================================
  Total providers  : 500
  Updated          : 423
  Unchanged        : 31
  Not found        : 18
  Ambiguous        : 12
  Errors           : 2
  Skipped (cached) : 14
  Docs downloaded  : 67
  Output CSV       : output/licenses/pa_results_20260921_214803.csv
  Log file         : output/pa_run.log
============================================================
```

---

## Single-license / quick lookups

```bash
# Fetch one license and print JSON
python3 fetch_pa_licenses.py --license MD031820E

# Download disciplinary docs for one license+NPI
python3 download_disciplinary_docs.py --license MD031820E --npi 1174503361
```

---

## Notes

- PALS is a public API — no authentication required.
- A 0.3 s delay is added between providers to be polite to the server.
- Disciplinary documents are stored on a PA government network share; the `SearchDownloadFile`
  endpoint serves them publicly. If a document returns HTTP 500, it is unavailable externally —
  contact `RA-PROTHONOTARY@PA.GOV` or `717-772-2686`.
- The script uses only Python stdlib + `playwright` (needed only for the Playwright scripts,
  not for `run_pa_pals.py`).
