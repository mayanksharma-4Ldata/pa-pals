"""
PA PALS — Snowflake-native edition.

Reads provider + PA license data directly from:
  dw_pdmpi.ui.pimaster          (NPI, first/last name, city, specialty)
  dw_pdmpi.ui.pilicensemaster   (PA license number, expiry, active flag)

For each provider:
  1. Looks up PALS by license number (if present) or by first+last name.
  2. Resolves name ambiguity using city/specialty.
  3. MERGEs updated license data back into pilicensemaster:
       - LICENSENUMBER  (filled if was missing)
       - ISSUEDATE
       - EXPIREDATE
       - ACTIVEFLAG     ('Active' if expiry >= today, else raw PALS status)
       - BOARD
       - UPDATEDATETIME
  4. Downloads disciplinary action PDFs locally as:
       {DOCS_DIR}/{NPI}_{LicenseNumber}_{OriginalFilename}
  5. Checkpoints after every batch so a restart continues from where it left off.

Usage
-----
  python3 run_pa_pals_snowflake.py                         # process all missing-expiry PA rows
  python3 run_pa_pals_snowflake.py --filter all            # process ALL PA rows
  python3 run_pa_pals_snowflake.py --filter missing_license
  python3 run_pa_pals_snowflake.py --limit 500             # cap at 500 providers
  python3 run_pa_pals_snowflake.py --no-docs               # skip doc downloads
  python3 run_pa_pals_snowflake.py --reset                 # ignore checkpoint

Parameters
----------
  --filter        {missing_expiry|missing_license|all}  default: missing_expiry
  --limit         Max providers to process              default: unlimited
  --batch-size    Snowflake MERGE batch size            default: 100
  --docs-dir      Local folder for PDFs                 default: output/disciplinary_docs
  --checkpoint    Checkpoint JSON file                   default: output/sf_checkpoint.json
  --log           Log file path                         default: output/sf_run.log
  --no-docs       Skip disciplinary document downloads
  --reset         Start fresh (ignore checkpoint)
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, date

import snowflake.connector

from pals_api import (
    search_by_license, search_by_name,
    get_license_details,
    get_disciplinary_file_path, download_disciplinary_doc,
    profession_to_board,
)
from credentials.snowflake_config import SNOWFLAKE, PIMASTER, PILICENSEMASTER
from credentials.config import OUTPUT_DISCIPLINARY_DIR


# ── helpers ───────────────────────────────────────────────────────────────────

def parse_pals_date(raw):
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


def active_flag(expiry_raw):
    d = parse_pals_date(expiry_raw)
    return "Active" if (d and d >= date.today()) else None   # None → fall back to PALS status


def normalise(s):
    return (s or "").strip().upper()


def pick_best_match(results, first, last, hint_city=None, hint_specialty=None):
    first_u = normalise(first)
    last_u  = normalise(last)

    exact = [r for r in results
             if normalise(r.get("FirstName","")).startswith(first_u[:3])
             and normalise(r.get("LastName","")) == last_u]
    pool = exact if exact else results

    active = [r for r in pool if normalise(r.get("Status","")) == "ACTIVE"]
    pool = active if active else pool

    if len(pool) == 1:
        return pool[0]

    if hint_city:
        cm = [r for r in pool if normalise(r.get("City","")) == normalise(hint_city)]
        if len(cm) == 1:
            return cm[0]
        if cm:
            pool = cm

    if hint_specialty:
        sp = normalise(hint_specialty)
        pm = [r for r in pool if sp[:6] in normalise(r.get("ProfessionType",""))]
        if len(pm) == 1:
            return pm[0]
        if pm:
            pool = pm

    return pool[0] if len(pool) == 1 else None   # None = ambiguous


# ── snowflake helpers ─────────────────────────────────────────────────────────

def sf_connect():
    return snowflake.connector.connect(**SNOWFLAKE)


def fetch_providers(cur, filter_mode, limit):
    """
    Return rows: (npi, first, middle, last, city, specialty, license_number, expiredate, activeflag)
    """
    filter_sql = {
        "missing_expiry":  "AND (l.EXPIREDATE IN ('N/A','') OR l.EXPIREDATE IS NULL)",
        "missing_license": "AND (l.LICENSENUMBER IS NULL OR l.LICENSENUMBER = '')",
        "all":             "",
    }[filter_mode]

    limit_sql = f"LIMIT {limit}" if limit else ""

    sql = f"""
        SELECT
            p.NPI,
            p.FIRSTNAME,
            p.MIDDLENAME,
            p.LASTNAME,
            p.PRACTICECITY,
            p.PRIMARYSPECIALITY,
            l.LICENSENUMBER,
            l.EXPIREDATE,
            l.ACTIVEFLAG
        FROM {PILICENSEMASTER} l
        JOIN {PIMASTER} p ON l.NPI = p.NPI
        WHERE l.STATE = 'PA'
          {filter_sql}
        ORDER BY p.NPI
        {limit_sql}
    """
    cur.execute(sql)
    cols = ["npi","first","middle","last","city","specialty",
            "license_number","expiredate","activeflag"]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def merge_to_snowflake(cur, updates, log):
    """
    Batch MERGE a list of update dicts into pilicensemaster.
    Each dict: {npi, license_number, issuedate, expiredate, activeflag, board}
    """
    if not updates:
        return

    values_sql = ",\n".join(
        f"('{u['npi']}', 'PA', "
        f"'{u['license_number'].replace(chr(39), '')}', "
        f"'{u['issuedate'].replace(chr(39), '')}', "
        f"'{u['expiredate'].replace(chr(39), '')}', "
        f"'{u['activeflag'].replace(chr(39), '')}', "
        f"'{u['board'].replace(chr(39), '')}', "
        f"'PALS')"
        for u in updates
    )

    merge_sql = f"""
        MERGE INTO {PILICENSEMASTER} target
        USING (
            SELECT column1  AS NPI,
                   column2  AS STATE,
                   column3  AS LICENSENUMBER,
                   column4  AS ISSUEDATE,
                   column5  AS EXPIREDATE,
                   column6  AS ACTIVEFLAG,
                   column7  AS BOARD,
                   column8  AS SOURCEDETAILS
            FROM VALUES {values_sql}
        ) source
        ON target.NPI = source.NPI AND target.STATE = source.STATE
        WHEN MATCHED THEN UPDATE SET
            target.LICENSENUMBER  = source.LICENSENUMBER,
            target.ISSUEDATE      = source.ISSUEDATE,
            target.EXPIREDATE     = source.EXPIREDATE,
            target.ACTIVEFLAG     = source.ACTIVEFLAG,
            target.BOARD          = source.BOARD,
            target.SOURCEDETAILS  = source.SOURCEDETAILS,
            target.UPDATEDATETIME = CURRENT_DATE()
        WHEN NOT MATCHED THEN INSERT
            (NPI, STATE, LICENSENUMBER, ISSUEDATE, EXPIREDATE, ACTIVEFLAG, BOARD, SOURCEDETAILS, CREATEDATETIME, UPDATEDATETIME)
        VALUES
            (source.NPI, 'PA', source.LICENSENUMBER, source.ISSUEDATE,
             source.EXPIREDATE, source.ACTIVEFLAG, source.BOARD, source.SOURCEDETAILS,
             CURRENT_DATE(), CURRENT_DATE())
    """
    cur.execute(merge_sql)
    log.info(f"  Snowflake MERGE: {len(updates)} rows — {cur.fetchone()}")


# ── document download ─────────────────────────────────────────────────────────

def download_docs(npi, license_number, disciplinary_list, disc_file_path, docs_dir, log):
    count = 0
    for action in disciplinary_list:
        physical_name   = action.get("PhysicalFileName", "")
        file_request_id = action.get("FileRequestID", 0)
        complaint_no    = action.get("ComplaintNumber", "")

        if not physical_name or not file_request_id:
            continue

        safe = re.sub(r'[^\w.\-]', '_', physical_name)
        out_path = os.path.join(docs_dir, f"{npi}_{license_number}_{safe}")

        if os.path.exists(out_path):
            log.debug(f"  [{npi}] Doc exists, skip: {safe}")
            count += 1
            continue

        try:
            pdf = download_disciplinary_doc(physical_name, disc_file_path)
            with open(out_path, "wb") as f:
                f.write(pdf)
            log.info(f"  [{npi}] Doc saved ({len(pdf):,}b): {npi}_{license_number}_{safe}")
            count += 1
            time.sleep(0.2)
        except Exception as e:
            log.warning(f"  [{npi}] Doc download failed ({complaint_no}): {e}")

    return count


# ── checkpoint ────────────────────────────────────────────────────────────────

def load_checkpoint(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"done": [], "stats": {}}


def save_checkpoint(path, done_npis, stats):
    with open(path, "w") as f:
        json.dump({"done": list(done_npis), "stats": stats,
                   "last_updated": datetime.now().isoformat()}, f, indent=2)


# ── logging ───────────────────────────────────────────────────────────────────

def setup_logging(log_path):
    log = logging.getLogger("pa_pals_sf")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%Y-%m-%d %H:%M:%S")
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    log.addHandler(ch)
    log.addHandler(fh)
    return log


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="PA PALS Snowflake — enrich pilicensemaster from PALS API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--filter",     default="missing_expiry",
                        choices=["missing_expiry","missing_license","all"],
                        help="Which PA rows to process (default: missing_expiry)")
    parser.add_argument("--limit",      type=int, default=None,
                        help="Cap total providers to process")
    parser.add_argument("--batch-size", type=int, default=100,
                        help="Snowflake MERGE batch size (default: 100)")
    parser.add_argument("--docs-dir",   default=OUTPUT_DISCIPLINARY_DIR,
                        help="Local folder for disciplinary PDFs")
    parser.add_argument("--checkpoint", default="output/sf_checkpoint.json")
    parser.add_argument("--log",        default="output/sf_run.log")
    parser.add_argument("--no-docs",    action="store_true")
    parser.add_argument("--reset",      action="store_true")
    args = parser.parse_args()

    os.makedirs(args.docs_dir, exist_ok=True)
    os.makedirs("output", exist_ok=True)

    log = setup_logging(args.log)

    log.info("=" * 65)
    log.info("PA PALS Snowflake run started")
    log.info(f"  Filter     : {args.filter}")
    log.info(f"  Limit      : {args.limit or 'unlimited'}")
    log.info(f"  Batch size : {args.batch_size}")
    log.info(f"  Docs dir   : {args.docs_dir}")
    log.info(f"  Docs       : {'disabled' if args.no_docs else 'enabled'}")
    log.info("=" * 65)

    # ── checkpoint ────────────────────────────────────────────────────────────
    ckpt = {} if args.reset else load_checkpoint(args.checkpoint)
    done_npis = set(ckpt.get("done", []))
    stats = ckpt.get("stats", {k: 0 for k in
                                ["total","updated","unchanged","not_found",
                                 "ambiguous","error","skipped","docs"]})
    log.info(f"Checkpoint: {len(done_npis)} NPIs already processed")

    # ── connect Snowflake ─────────────────────────────────────────────────────
    log.info("Connecting to Snowflake...")
    conn = sf_connect()
    read_cur  = conn.cursor()
    write_cur = conn.cursor()

    # ── fetch work list ───────────────────────────────────────────────────────
    log.info(f"Fetching providers from Snowflake ({args.filter})...")
    providers = fetch_providers(read_cur, args.filter, args.limit)
    log.info(f"Fetched {len(providers):,} providers to process")

    # ── disciplinary file path (once) ─────────────────────────────────────────
    disc_file_path = None
    if not args.no_docs:
        try:
            disc_file_path = get_disciplinary_file_path()
            log.info(f"Disciplinary file path: {disc_file_path}")
        except Exception as e:
            log.warning(f"Could not fetch disciplinary file path: {e} — docs will be skipped")

    # ── process ───────────────────────────────────────────────────────────────
    pending_updates = []   # accumulate before batch MERGE

    for i, p in enumerate(providers):
        npi     = p["npi"]
        first   = p["first"] or ""
        last    = p["last"]  or ""
        lic_num = p["license_number"] or ""
        city    = p["city"] or ""
        spec    = p["specialty"] or ""
        prefix  = f"[{i+1}/{len(providers)}]"

        stats["total"] += 1

        if npi in done_npis:
            log.info(f"{prefix} [{npi}] SKIPPED (checkpoint)")
            stats["skipped"] += 1
            continue

        log.info(f"{prefix} {first} {last}  NPI={npi}  LIC={lic_num or '?'}")

        # ── PALS lookup ───────────────────────────────────────────────────────
        pals_match = None
        search_mode = None

        if lic_num:
            try:
                hits = search_by_license(lic_num)
                if hits:
                    pals_match  = hits[0]
                    search_mode = "license"
            except Exception as e:
                log.warning(f"  [{npi}] License search error: {e}")

        if not pals_match:
            try:
                hits = search_by_name(last, first)
                if hits:
                    pals_match = pick_best_match(hits, first, last, city, spec)
                    if pals_match:
                        search_mode = "name"
                    else:
                        log.warning(f"  [{npi}] AMBIGUOUS — {len(hits)} matches for {first} {last}")
                        for h in hits[:4]:
                            log.warning(f"         {h.get('FirstName')} {h.get('LastName')} | "
                                        f"{h.get('LicenseNumber')} | {h.get('Status')} | {h.get('City')}")
                        stats["ambiguous"] += 1
                        done_npis.add(npi)
                        save_checkpoint(args.checkpoint, done_npis, stats)
                        continue
            except Exception as e:
                log.warning(f"  [{npi}] Name search error: {e}")

        if not pals_match:
            log.warning(f"  [{npi}] Not found on PALS")
            stats["not_found"] += 1
            done_npis.add(npi)
            save_checkpoint(args.checkpoint, done_npis, stats)
            continue

        # ── get full license details ──────────────────────────────────────────
        found_lic  = pals_match.get("LicenseNumber", lic_num) or lic_num
        person_id  = pals_match.get("PersonId")
        license_id = pals_match.get("LicenseId")

        details = {}
        try:
            details = get_license_details(person_id, license_id, found_lic) or {}
        except Exception as e:
            log.warning(f"  [{npi}] Detail fetch error: {e}")

        expiry      = (details.get("ExpiryDate")  or "").strip()
        issue       = (details.get("IssueDate")   or "").strip()
        status      = (details.get("Status")      or pals_match.get("Status") or "").strip()
        profession  = (details.get("Profession")  or pals_match.get("ProfessionType") or "").strip()
        board       = profession_to_board(profession)
        flag        = active_flag(expiry) or status   # Active if future, else raw PALS status

        log.info(f"  [{npi}] via {search_mode} | LIC={found_lic} | {flag} | exp={expiry}")

        pending_updates.append({
            "npi":            npi,
            "license_number": found_lic,
            "issuedate":      issue,
            "expiredate":     expiry or p["expiredate"] or "",
            "activeflag":     flag,
            "board":          board,
        })
        stats["updated"] += 1

        # ── disciplinary docs ─────────────────────────────────────────────────
        if not args.no_docs and disc_file_path and details:
            disc_list = details.get("DisciplinaryActionDetails") or []
            if disc_list:
                n = download_docs(npi, found_lic, disc_list, disc_file_path, args.docs_dir, log)
                stats["docs"] += n

        # ── batch MERGE into Snowflake ────────────────────────────────────────
        if len(pending_updates) >= args.batch_size:
            try:
                merge_to_snowflake(write_cur, pending_updates, log)
                conn.commit()
                pending_updates = []
            except Exception as e:
                log.error(f"Snowflake MERGE error: {e}", exc_info=True)
                stats["error"] += len(pending_updates)
                pending_updates = []

        done_npis.add(npi)
        save_checkpoint(args.checkpoint, done_npis, stats)
        time.sleep(0.3)

    # ── flush remaining updates ───────────────────────────────────────────────
    if pending_updates:
        try:
            merge_to_snowflake(write_cur, pending_updates, log)
            conn.commit()
        except Exception as e:
            log.error(f"Final Snowflake MERGE error: {e}", exc_info=True)

    read_cur.close()
    write_cur.close()
    conn.close()

    # ── summary ───────────────────────────────────────────────────────────────
    save_checkpoint(args.checkpoint, done_npis, stats)
    log.info("")
    log.info("=" * 65)
    log.info("RUN SUMMARY")
    log.info("=" * 65)
    log.info(f"  Total fetched      : {len(providers):,}")
    log.info(f"  Updated in SF      : {stats['updated']:,}")
    log.info(f"  Not found on PALS  : {stats['not_found']:,}")
    log.info(f"  Ambiguous (skipped): {stats['ambiguous']:,}")
    log.info(f"  Errors             : {stats['error']:,}")
    log.info(f"  Skipped (cached)   : {stats['skipped']:,}")
    log.info(f"  Docs downloaded    : {stats['docs']:,}")
    log.info(f"  Log                : {args.log}")
    log.info(f"  Checkpoint         : {args.checkpoint}")
    log.info("=" * 65)


if __name__ == "__main__":
    main()
