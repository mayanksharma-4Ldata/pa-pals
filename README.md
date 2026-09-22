# PA PALS — Pennsylvania License Lookup & Document Downloader

Fetches PA license data from the [PALS public API](https://www.pals.pa.gov/), enriches provider records with
expiration dates / active flags, and downloads disciplinary action PDFs — in two modes:

| Mode | Script | Source |
|---|---|---|
| **CSV mode** | `run_pa_pals.py` | Input CSV → enriched output CSV |
| **Snowflake mode** | `run_pa_pals_snowflake.py` | Reads `pimaster` + `pilicensemaster`, writes back to `pilicensemaster` |

---

## Setup

```bash
git clone https://github.com/mayanksharma-4Ldata/pa-pals
cd pa-pals

pip install -r requirements.txt
playwright install chromium          # only needed for Playwright-based scripts
```

**Snowflake credentials** (Snowflake mode only):
```bash
cp credentials/snowflake_config.example.py credentials/snowflake_config.py
# Edit snowflake_config.py and fill in your account / user / password
```

> `credentials/snowflake_config.py` is git-ignored and will never be committed.

---

## Mode 1 — CSV input (`run_pa_pals.py`)

### Input CSV columns

| Column | Required | Notes |
|---|---|---|
| `NPI` | Yes | Provider NPI |
| `first_name` | Yes | Also accepts `First Name`, `firstname` |
| `last_name` | Yes | Also accepts `Last Name`, `lastname` |
| `license_number` | No | If blank, searches PALS by name |
| `expiration_date` | No | Filled/updated by the script |
| `active_flag` | No | Set to `Active` if expiry ≥ today |
| `city` | No | Helps resolve ambiguous name matches |
| `profession` | No | Helps resolve ambiguous name matches |

### Run

```bash
# Basic
python3 run_pa_pals.py --input providers.csv

# With explicit output
python3 run_pa_pals.py --input providers.csv --output output/pa_enriched.csv

# License data only, no docs
python3 run_pa_pals.py --input providers.csv --no-docs

# Start fresh (ignore checkpoint)
python3 run_pa_pals.py --input providers.csv --reset
```

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `--input` | *(required)* | Input CSV path |
| `--output` | `output/licenses/pa_results_<ts>.csv` | Output CSV |
| `--checkpoint` | `output/pa_checkpoint.json` | Resume checkpoint |
| `--log` | `output/pa_run.log` | Log file |
| `--no-docs` | off | Skip disciplinary document downloads |
| `--reset` | off | Ignore checkpoint, start fresh |

---

## Mode 2 — Snowflake (`run_pa_pals_snowflake.py`)

Reads directly from:
- `dw_pdmpi.ui.pimaster` — NPI, first/last name, city, specialty
- `dw_pdmpi.ui.pilicensemaster` — PA license number, expiry, active flag

For each provider, looks up PALS and MERGEs back into `pilicensemaster`:

| Column updated | Logic |
|---|---|
| `LICENSENUMBER` | Filled from PALS if was NULL/blank |
| `ISSUEDATE` | From PALS |
| `EXPIREDATE` | From PALS |
| `ACTIVEFLAG` | `Active` if expiry ≥ today, else raw PALS status (e.g. `Expired`, `Null and Void`) |
| `BOARD` | Profession/board from PALS |
| `UPDATEDATETIME` | Set to `CURRENT_DATE()` |

### Run

```bash
# Process all PA rows with missing expiry (default)
python3 run_pa_pals_snowflake.py

# Process all PA rows
python3 run_pa_pals_snowflake.py --filter all

# Only rows with missing license number
python3 run_pa_pals_snowflake.py --filter missing_license

# Test run — 50 providers, no docs
python3 run_pa_pals_snowflake.py --limit 50 --no-docs

# Resume after interruption (same command, checkpoint is auto-loaded)
python3 run_pa_pals_snowflake.py

# Start fresh
python3 run_pa_pals_snowflake.py --reset
```

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `--filter` | `missing_expiry` | `missing_expiry` · `missing_license` · `all` |
| `--limit` | unlimited | Cap total providers to process |
| `--batch-size` | `100` | How many rows per Snowflake MERGE |
| `--docs-dir` | `output/disciplinary_docs` | Local folder for PDFs |
| `--checkpoint` | `output/sf_checkpoint.json` | Resume checkpoint |
| `--log` | `output/sf_run.log` | Log file |
| `--no-docs` | off | Skip disciplinary document downloads |
| `--reset` | off | Ignore checkpoint, start fresh |

---

## How license lookup works

1. **By license number** — if the row has a `LICENSENUMBER`, PALS is queried directly (exact match).
2. **By name fallback** — if no license number, searches PALS by first + last name in PA:
   - Prefers records with `Active` status.
   - Breaks ties using `city` and `specialty/profession`.
   - If still ambiguous → logs all candidates and **skips** (never guesses).
3. Active flag logic:  
   `ACTIVEFLAG = 'Active'` if `EXPIREDATE ≥ today`, otherwise the raw PALS status string.

---

## Downloaded document naming

```
{NPI}_{LicenseNumber}_{OriginalFilename}
```

Example:
```
1174503361_MD031820E_Squire_Karen_Marie_15-49-08957_CA_OK.pdf
```

Saved to `output/disciplinary_docs/` (or `--docs-dir`). Already-downloaded files are skipped.

---

## Checkpoint / resume

A checkpoint file is saved after every provider. If the run is interrupted:

```bash
# Just re-run — it picks up from where it stopped
python3 run_pa_pals_snowflake.py

# To start over
python3 run_pa_pals_snowflake.py --reset
```

---

## Run summary (printed to console and log)

```
=================================================================
RUN SUMMARY
=================================================================
  Total fetched      : 294,382
  Updated in SF      : 281,004
  Not found on PALS  : 8,211
  Ambiguous (skipped): 3,102
  Errors             : 65
  Skipped (cached)   : 2,000
  Docs downloaded    : 1,847
  Log                : output/sf_run.log
  Checkpoint         : output/sf_checkpoint.json
=================================================================
```

---

## Quick single lookups

```bash
# Lookup one license, print JSON
python3 fetch_pa_licenses.py --license MD031820E

# Download disciplinary docs for one NPI + license
python3 download_disciplinary_docs.py --license MD031820E --npi 1174503361
```

---

## Snowflake tables

| Table | Role |
|---|---|
| `dw_pdmpi.ui.pimaster` | Provider master — NPI, name, city, specialty |
| `dw_pdmpi.ui.pilicensemaster` | License master — updated by this script |

Key `pilicensemaster` columns written by this script:
`LICENSENUMBER`, `ISSUEDATE`, `EXPIREDATE`, `ACTIVEFLAG`, `BOARD`, `SOURCEDETAILS='PALS'`, `UPDATEDATETIME`

---

## Notes

- PALS is a public API — no authentication required.
- A 300 ms delay is added between PALS calls to be polite to the server.
- Disciplinary documents are on a PA government file share served via `SearchDownloadFile`.
  If a doc returns HTTP 500 it is unavailable externally — contact `RA-PROTHONOTARY@PA.GOV` / `717-772-2686`.
- `credentials/snowflake_config.py` is git-ignored — never commit real credentials.
