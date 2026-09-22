# PA PALS — Pennsylvania License Lookup & Document Downloader

Fetches PA license data from the [PALS public API](https://www.pals.pa.gov/), enriches provider records with
expiration dates / active flags / board names, and downloads disciplinary action PDFs.

**Primary mode: Snowflake** — reads directly from `dw_pdmpi.ui.pimaster` and `dw_pdmpi.ui.pilicensemaster`,
enriches records, and MERGEs results back into `pilicensemaster`.  
CSV mode is also available for ad-hoc lookups outside Snowflake.

---

## Setup

```bash
git clone https://github.com/mayanksharma-4Ldata/pa-pals
cd pa-pals

pip install -r requirements.txt
```

**Snowflake credentials** — copy the example and fill in your details:
```bash
cp credentials/snowflake_config.example.py credentials/snowflake_config.py
# Edit snowflake_config.py — account, user, password, warehouse, database, schema, role
```

> `credentials/snowflake_config.py` is git-ignored and will **never** be committed.

---

## Mode 1 — Snowflake (primary) — `run_pa_pals_snowflake.py`

### What it does

1. Reads providers from `dw_pdmpi.ui.pimaster` (NPI, name, city, specialty)
2. Joins `dw_pdmpi.ui.pilicensemaster` for PA license rows
3. Looks up each provider on PALS:
   - **By license number** if one exists in Snowflake
   - **By first + last name** as fallback; resolves ambiguity using city and specialty
   - Skips (never guesses) if still ambiguous after all hints
4. MERGEs enriched data back into `pilicensemaster`
5. Downloads disciplinary action PDFs locally as `{NPI}_{LicenseNumber}_{filename}`
6. Saves a checkpoint after every provider — **safe to interrupt and resume**

### Columns updated in `pilicensemaster`

| Column | Logic |
|---|---|
| `LICENSENUMBER` | Filled from PALS if was NULL/blank |
| `ISSUEDATE` | From PALS |
| `EXPIREDATE` | From PALS |
| `ACTIVEFLAG` | `Active` if `EXPIREDATE ≥ today`, else raw PALS status (e.g. `Expired`, `Null and Void`) |
| `BOARD` | Full Pennsylvania State Board name (e.g. `Pennsylvania State Board of Medicine`) |
| `SOURCEDETAILS` | Set to `PALS` |
| `UPDATEDATETIME` | Set to `CURRENT_DATE()` |

### Run

```bash
# Process all PA rows with missing expiry date (default — recommended first run)
python3 run_pa_pals_snowflake.py

# Process ALL PA rows regardless of current values
python3 run_pa_pals_snowflake.py --filter all

# Only rows where license number is missing
python3 run_pa_pals_snowflake.py --filter missing_license

# Test run — 100 providers, skip doc downloads
python3 run_pa_pals_snowflake.py --limit 100 --no-docs

# Resume after interruption (checkpoint is auto-loaded — just re-run)
python3 run_pa_pals_snowflake.py

# Start fresh (discard checkpoint)
python3 run_pa_pals_snowflake.py --reset
```

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `--filter` | `missing_expiry` | `missing_expiry` · `missing_license` · `all` |
| `--limit` | unlimited | Cap total providers to process |
| `--batch-size` | `100` | Rows per Snowflake MERGE commit |
| `--docs-dir` | `output/disciplinary_docs` | Local folder for downloaded PDFs |
| `--checkpoint` | `output/sf_checkpoint.json` | Checkpoint file for resume |
| `--log` | `output/sf_run.log` | Log file path |
| `--no-docs` | off | Skip disciplinary document downloads |
| `--reset` | off | Ignore checkpoint, start fresh |

### Run summary (printed to console and log)

```
=================================================================
PA PALS Snowflake run started
  Filter     : missing_expiry
  Limit      : unlimited
  Batch size : 100
  Docs       : enabled
=================================================================
...
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

## Mode 2 — CSV input — `run_pa_pals.py`

For ad-hoc lookups when you don't need Snowflake. Reads a CSV, enriches it, writes an output CSV.

### Input CSV columns

| Column | Required | Notes |
|---|---|---|
| `NPI` | Yes | Provider NPI |
| `first_name` | Yes | Also accepts `First Name`, `firstname` |
| `last_name` | Yes | Also accepts `Last Name`, `lastname` |
| `license_number` | No | If blank, searches PALS by name |
| `city` | No | Helps resolve ambiguous name matches |
| `profession` | No | Helps resolve ambiguous name matches |

### Run

```bash
python3 run_pa_pals.py --input providers.csv

# With explicit output file
python3 run_pa_pals.py --input providers.csv --output output/pa_enriched.csv

# License data only, no docs
python3 run_pa_pals.py --input providers.csv --no-docs

# Start fresh (ignore checkpoint)
python3 run_pa_pals.py --input providers.csv --reset
```

---

## How license lookup and ambiguity resolution works

1. If a `LICENSENUMBER` exists → query PALS directly by license (exact match).
2. If no license number → search PALS by first + last name, then:
   - Prefer `Active` status records.
   - Break ties by `city` match.
   - Break ties by `specialty/profession` match.
   - If still ambiguous → **log all candidates and skip** (never guesses).
3. Active flag: `Active` if `EXPIREDATE ≥ today`, otherwise the raw PALS status string.

---

## Board name mapping

PALS returns a raw `ProfessionType` (e.g. `Medicine`, `Physical Therapy`).  
This is mapped to the full Pennsylvania State Board name:

| ProfessionType | Board |
|---|---|
| Medicine | Pennsylvania State Board of Medicine |
| Osteopathic Medicine | Pennsylvania State Board of Osteopathic Medicine |
| Physical Therapy | Pennsylvania State Board of Physical Therapy |
| Occupational Therapy | Pennsylvania State Board of Occupational Therapy |
| Chiropractic | Pennsylvania State Board of Chiropractic |
| Nursing | Pennsylvania State Board of Nursing |
| Dentistry | Pennsylvania State Board of Dentistry |
| Podiatry | Pennsylvania State Board of Podiatric Medicine |
| Pharmacy | Pennsylvania State Board of Pharmacy |
| Optometry | Pennsylvania State Board of Optometry |
| Psychology | Pennsylvania State Board of Psychology |
| Social Work | Pennsylvania State Board of Social Workers, Marriage and Family Therapists and Professional Counselors |
| Speech / Speech-Language Pathology | Pennsylvania State Board of Examiners in Speech-Language Pathology and Audiology |
| Radiology Personnel | Pennsylvania State Board of Medicine |
| Athletic Trainer | Pennsylvania State Board of Medicine |

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

A checkpoint file is saved after every provider. If the run is interrupted, just re-run the same command — it picks up exactly where it stopped:

```bash
# Interrupted run — just re-run, checkpoint is auto-loaded
python3 run_pa_pals_snowflake.py

# Start over from scratch
python3 run_pa_pals_snowflake.py --reset
```

---

## Quick single-provider lookups

```bash
# Lookup one license number, print JSON
python3 fetch_pa_licenses.py --license MD031820E

# Download disciplinary docs for one NPI + license
python3 download_disciplinary_docs.py --license MD031820E --npi 1174503361
```

---

## Snowflake tables

| Table | Role |
|---|---|
| `dw_pdmpi.ui.pimaster` | Provider master — NPI, name, city, specialty (read-only) |
| `dw_pdmpi.ui.pilicensemaster` | License master — read + updated by this script |

---

## Notes

- PALS is a public API — no authentication required.
- A 300 ms delay is added between PALS calls to avoid hammering the server.
- Disciplinary documents are on a PA government file share served via the `SearchDownloadFile` API.
  If a doc returns HTTP 500 it is unavailable externally — contact `RA-PROTHONOTARY@PA.GOV` / `717-772-2686`.
- `credentials/snowflake_config.py` is git-ignored — never commit real credentials.
