"""
PA PALS — License lookup + disciplinary document downloader.

For each provider in the input CSV this script:
  1. Looks up the PA license (by license number if provided, else by first/last name).
  2. Resolves ambiguity when name search returns multiple hits.
  3. Writes back: license_number, expiration_date, active_flag.
  4. Downloads every disciplinary action document and saves it as:
         {NPI}_{LicenseNumber}_{OriginalFilename}
  5. Checkpoints after every provider so a restart picks up where it left off.
  6. Writes a full run log and a summary at the end.

Usage
-----
  python3 run_pa_pals.py --input providers.csv --output output/pa_results.csv

Required CSV columns (case-insensitive):
  npi, first_name (or first name), last_name (or last name)

Optional CSV columns (enriched by this script):
  license_number, expiration_date, active_flag

Run options:
  --input      Path to input CSV                          [required]
  --output     Path for enriched output CSV               [default: output/licenses/pa_results_<timestamp>.csv]
  --checkpoint Path to checkpoint file                    [default: output/pa_checkpoint.json]
  --log        Path to log file                           [default: output/pa_run.log]
  --no-docs    Skip disciplinary document downloads
  --reset      Ignore existing checkpoint and start fresh
"""

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, date

from pals_api import (
    search_by_license,
    search_by_name,
    get_license_details,
    get_disciplinary_file_path,
    download_disciplinary_doc,
    profession_to_board,
)
from credentials.config import OUTPUT_LICENSES_DIR, OUTPUT_DISCIPLINARY_DIR

# ── date parsing ──────────────────────────────────────────────────────────────

def parse_date(raw):
    """Parse M/D/YYYY or MM/DD/YYYY into a date object. Returns None on failure."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def is_active(expiry_raw):
    """True if expiration date is today or in the future."""
    d = parse_date(expiry_raw)
    return d is not None and d >= date.today()


# ── name disambiguation ───────────────────────────────────────────────────────

def _normalise(s):
    return (s or "").strip().upper()


def pick_best_match(results, first, last, hint_city=None, hint_profession=None):
    """
    Given PALS search results and known provider fields, return the best
    matching record or None if it's too ambiguous to decide safely.

    Resolution order:
      1. Exact first+last name match (case-insensitive)
      2. Among those, prefer Active status
      3. Among ties, prefer city match (if hint_city given)
      4. Among ties, prefer profession match (if hint_profession given)
      5. If still >1 candidate, return None (log as ambiguous)
    """
    first_u = _normalise(first)
    last_u  = _normalise(last)

    # Step 1 — exact name match
    exact = [
        r for r in results
        if _normalise(r.get("FirstName", "")).startswith(first_u[:3])   # first 3 chars tolerate hyphens
        and _normalise(r.get("LastName", "")) == last_u
    ]
    pool = exact if exact else results

    # Step 2 — prefer Active
    active = [r for r in pool if _normalise(r.get("Status", "")) == "ACTIVE"]
    pool = active if active else pool

    if len(pool) == 1:
        return pool[0]

    # Step 3 — city hint
    if hint_city:
        city_u = _normalise(hint_city)
        city_match = [r for r in pool if _normalise(r.get("City", "")) == city_u]
        if len(city_match) == 1:
            return city_match[0]
        if city_match:
            pool = city_match

    # Step 4 — profession hint
    if hint_profession:
        prof_u = _normalise(hint_profession)
        prof_match = [r for r in pool if prof_u in _normalise(r.get("ProfessionType", ""))]
        if len(prof_match) == 1:
            return prof_match[0]
        if prof_match:
            pool = prof_match

    # Step 5 — still ambiguous
    if len(pool) > 1:
        return None   # caller logs this
    return pool[0] if pool else None


# ── checkpoint ────────────────────────────────────────────────────────────────

def load_checkpoint(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"processed": {}}   # {npi: result_row}


def save_checkpoint(path, checkpoint):
    with open(path, "w") as f:
        json.dump(checkpoint, f, indent=2)


# ── CSV helpers ───────────────────────────────────────────────────────────────

COLUMN_ALIASES = {
    "npi":            ["npi", "npi number"],
    "first_name":     ["first_name", "first name", "firstname"],
    "last_name":      ["last_name", "last name", "lastname"],
    "license_number": ["license_number", "license number", "pa_license", "pa license"],
    "expiration_date":["expiration_date", "expiration date", "license expiration", "licenseexpirationdate"],
    "active_flag":    ["active_flag", "active flag", "activeflag", "status"],
    "city":           ["city"],
    "profession":     ["profession", "profession type", "specialty"],
}


def resolve_col(headers, key):
    h_lower = {h.lower().strip(): h for h in headers}
    for alias in COLUMN_ALIASES.get(key, [key]):
        if alias in h_lower:
            return h_lower[alias]
    return None


def get_val(row, col):
    return row.get(col, "").strip() if col else ""


# ── document download ─────────────────────────────────────────────────────────

def download_docs_for_provider(npi, license_number, disciplinary_list, file_path, log):
    downloaded = []
    for action in disciplinary_list:
        physical_name  = action.get("PhysicalFileName", "")
        file_request_id= action.get("FileRequestID", 0)
        complaint_no   = action.get("ComplaintNumber", "unknown")
        disc_action    = action.get("DisciplinaryAction", "")

        if not physical_name or not file_request_id:
            log.debug(f"  [{npi}] Skipping doc — no filename or FileRequestID=0 (complaint {complaint_no})")
            continue

        # Filename: {NPI}_{LicenseNumber}_{OriginalName}
        safe_orig = re.sub(r'[^\w.\-]', '_', physical_name)
        out_name  = f"{npi}_{license_number}_{safe_orig}"
        out_path  = os.path.join(OUTPUT_DISCIPLINARY_DIR, out_name)

        if os.path.exists(out_path):
            log.info(f"  [{npi}] Doc already exists, skipping: {out_name}")
            downloaded.append(out_path)
            continue

        try:
            pdf_bytes = download_disciplinary_doc(physical_name, file_path)
            with open(out_path, "wb") as f:
                f.write(pdf_bytes)
            log.info(f"  [{npi}] Downloaded ({len(pdf_bytes):,} bytes): {out_name}")
            downloaded.append(out_path)
            time.sleep(0.2)
        except Exception as e:
            log.warning(f"  [{npi}] Failed to download {physical_name}: {e}")

    return downloaded


# ── main provider lookup ──────────────────────────────────────────────────────

def process_provider(row, col_map, file_path, download_docs, log):
    npi          = get_val(row, col_map["npi"])
    first        = get_val(row, col_map["first_name"])
    last         = get_val(row, col_map["last_name"])
    license_num  = get_val(row, col_map["license_number"])
    hint_city    = get_val(row, col_map.get("city"))
    hint_prof    = get_val(row, col_map.get("profession"))

    result = dict(row)   # copy all original columns
    result["_status"]        = "unchanged"
    result["_docs_downloaded"] = 0
    result["_notes"]         = ""

    # ── Step 1: find the PALS record ─────────────────────────────────────────
    pals_match = None
    search_mode = None

    if license_num:
        try:
            hits = search_by_license(license_num)
            if hits:
                pals_match = hits[0]
                search_mode = "license"
        except Exception as e:
            log.warning(f"[{npi}] License search error: {e}")

    if not pals_match:
        # Fallback: search by name
        try:
            hits = search_by_name(last, first)
            if hits:
                pals_match = pick_best_match(hits, first, last, hint_city, hint_prof)
                if pals_match:
                    search_mode = "name"
                else:
                    log.warning(f"[{npi}] AMBIGUOUS — {len(hits)} name matches for {first} {last}. Candidates:")
                    for h in hits[:5]:
                        log.warning(f"         {h.get('FirstName')} {h.get('LastName')} | {h.get('LicenseNumber')} | {h.get('Status')} | {h.get('City')}")
                    result["_status"] = "ambiguous"
                    result["_notes"]  = f"{len(hits)} name matches, could not resolve"
                    return result
        except Exception as e:
            log.warning(f"[{npi}] Name search error: {e}")

    if not pals_match:
        log.warning(f"[{npi}] No PALS record found for {first} {last} / license {license_num}")
        result["_status"] = "not_found"
        return result

    # ── Step 2: fetch full license details ────────────────────────────────────
    person_id      = pals_match.get("PersonId")
    license_id     = pals_match.get("LicenseId")
    found_lic_num  = pals_match.get("LicenseNumber", license_num)

    try:
        details = get_license_details(person_id, license_id, found_lic_num)
    except Exception as e:
        log.warning(f"[{npi}] Detail fetch error: {e}")
        details = {}

    expiry    = (details.get("ExpiryDate") or pals_match.get("ExpiryDate") or "").strip()
    status    = (details.get("Status")     or pals_match.get("Status")     or "").strip()
    state_name= (details.get("StateName")  or pals_match.get("State")      or "Pennsylvania").strip()

    active_flag = "Active" if is_active(expiry) else status

    # ── Step 3: write enriched columns ───────────────────────────────────────
    changed = []

    if col_map["license_number"] and not license_num and found_lic_num:
        result[col_map["license_number"]] = found_lic_num
        changed.append(f"license_number={found_lic_num}")

    if col_map["expiration_date"]:
        result[col_map["expiration_date"]] = expiry
        if expiry:
            changed.append(f"expiration={expiry}")

    if col_map["active_flag"]:
        result[col_map["active_flag"]] = active_flag
        changed.append(f"active_flag={active_flag}")

    result["_status"]    = "updated" if changed else "unchanged"
    result["_notes"]     = f"via {search_mode} | " + "; ".join(changed) if changed else f"via {search_mode} | no change"
    result["_pa_license"]= found_lic_num
    result["_expiry"]    = expiry
    result["_active"]    = active_flag

    log.info(f"[{npi}] {first} {last} | {found_lic_num} | {active_flag} | exp:{expiry} | {result['_notes']}")

    # ── Step 4: download disciplinary docs ────────────────────────────────────
    if download_docs and details:
        disciplinary = details.get("DisciplinaryActionDetails") or []
        if disciplinary:
            docs = download_docs_for_provider(npi, found_lic_num, disciplinary, file_path, log)
            result["_docs_downloaded"] = len(docs)
        else:
            log.debug(f"[{npi}] No disciplinary actions on record")

    time.sleep(0.3)
    return result


# ── entry point ───────────────────────────────────────────────────────────────

def setup_logging(log_path):
    logger = logging.getLogger("pa_pals")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%Y-%m-%d %H:%M:%S")
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


def main():
    parser = argparse.ArgumentParser(
        description="PA PALS — license enrichment + disciplinary document downloader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input",      required=True,  help="Input CSV path")
    parser.add_argument("--output",     default="",     help="Output CSV path (default: output/licenses/pa_results_<ts>.csv)")
    parser.add_argument("--checkpoint", default="output/pa_checkpoint.json", help="Checkpoint file path")
    parser.add_argument("--log",        default="output/pa_run.log",         help="Log file path")
    parser.add_argument("--no-docs",    action="store_true", help="Skip disciplinary document downloads")
    parser.add_argument("--reset",      action="store_true", help="Ignore checkpoint and start fresh")
    args = parser.parse_args()

    os.makedirs(OUTPUT_LICENSES_DIR,    exist_ok=True)
    os.makedirs(OUTPUT_DISCIPLINARY_DIR, exist_ok=True)
    os.makedirs("output", exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path  = args.output or os.path.join(OUTPUT_LICENSES_DIR, f"pa_results_{timestamp}.csv")
    log = setup_logging(args.log)

    log.info("=" * 60)
    log.info("PA PALS run started")
    log.info(f"Input:      {args.input}")
    log.info(f"Output:     {out_path}")
    log.info(f"Checkpoint: {args.checkpoint}")
    log.info(f"Docs:       {'disabled' if args.no_docs else 'enabled'}")
    log.info("=" * 60)

    # ── load input ────────────────────────────────────────────────────────────
    with open(args.input, newline="", encoding="utf-8-sig") as f:
        reader   = csv.DictReader(f)
        headers  = reader.fieldnames
        providers = list(reader)

    log.info(f"Loaded {len(providers)} providers from {args.input}")

    # ── map columns ───────────────────────────────────────────────────────────
    col_map = {key: resolve_col(headers, key) for key in COLUMN_ALIASES}
    if not col_map["npi"]:
        log.error("CSV must have an 'NPI' column. Exiting.")
        sys.exit(1)
    if not col_map["first_name"] or not col_map["last_name"]:
        log.error("CSV must have 'first_name' and 'last_name' columns. Exiting.")
        sys.exit(1)
    log.info(f"Column map: { {k: v for k, v in col_map.items() if v} }")

    # ── checkpoint ────────────────────────────────────────────────────────────
    checkpoint = {} if args.reset else load_checkpoint(args.checkpoint)
    processed  = checkpoint.get("processed", {})
    log.info(f"Checkpoint: {len(processed)} providers already done")

    # ── disciplinary file path (fetch once) ───────────────────────────────────
    disc_file_path = None
    if not args.no_docs:
        try:
            disc_file_path = get_disciplinary_file_path()
            log.info(f"Disciplinary file path: {disc_file_path}")
        except Exception as e:
            log.warning(f"Could not fetch disciplinary file path: {e}. Docs will be skipped.")

    # ── process ───────────────────────────────────────────────────────────────
    all_results = []
    stats = {"updated": 0, "unchanged": 0, "not_found": 0, "ambiguous": 0,
             "error": 0, "skipped": 0, "docs": 0}

    for i, row in enumerate(providers):
        npi = row.get(col_map["npi"], "").strip()
        first = row.get(col_map["first_name"], "").strip()
        last  = row.get(col_map["last_name"], "").strip()

        prefix = f"[{i+1}/{len(providers)}]"

        if npi in processed:
            log.info(f"{prefix} [{npi}] SKIPPED (already in checkpoint)")
            all_results.append(processed[npi])
            stats["skipped"] += 1
            continue

        log.info(f"{prefix} Processing: {first} {last}  NPI={npi}")

        try:
            result = process_provider(
                row, col_map,
                file_path=disc_file_path,
                download_docs=(not args.no_docs and disc_file_path is not None),
                log=log,
            )
            all_results.append(result)
            stats[result.get("_status", "error")] = stats.get(result.get("_status", "error"), 0) + 1
            stats["docs"] += result.get("_docs_downloaded", 0)

            processed[npi] = result
            checkpoint["processed"] = processed
            checkpoint["last_updated"] = datetime.now().isoformat()
            save_checkpoint(args.checkpoint, checkpoint)

        except Exception as e:
            log.error(f"{prefix} [{npi}] Unhandled error: {e}", exc_info=True)
            row["_status"] = "error"
            row["_notes"]  = str(e)
            all_results.append(row)
            stats["error"] += 1

    # ── write output CSV ──────────────────────────────────────────────────────
    if all_results:
        out_headers = list(all_results[0].keys())
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=out_headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_results)
        log.info(f"Output saved: {out_path}")

    # ── summary ───────────────────────────────────────────────────────────────
    log.info("")
    log.info("=" * 60)
    log.info("RUN SUMMARY")
    log.info("=" * 60)
    log.info(f"  Total providers  : {len(providers)}")
    log.info(f"  Updated          : {stats['updated']}")
    log.info(f"  Unchanged        : {stats['unchanged']}")
    log.info(f"  Not found        : {stats['not_found']}")
    log.info(f"  Ambiguous        : {stats['ambiguous']}")
    log.info(f"  Errors           : {stats['error']}")
    log.info(f"  Skipped (cached) : {stats['skipped']}")
    log.info(f"  Docs downloaded  : {stats['docs']}")
    log.info(f"  Output CSV       : {out_path}")
    log.info(f"  Log file         : {args.log}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
