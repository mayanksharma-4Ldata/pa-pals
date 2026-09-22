"""
Download disciplinary action documents from PALS for a given license number or NPI.

Usage:
    python download_disciplinary_docs.py --license MD031820E
    python download_disciplinary_docs.py --input providers.csv
"""

import os
import argparse
import time
import csv
from datetime import datetime

from pals_api import (
    search_by_license, search_by_name,
    get_license_details, get_disciplinary_file_path,
    download_disciplinary_doc
)
from credentials.config import OUTPUT_DISCIPLINARY_DIR


def process_license(license_number, npi="", file_path=None):
    print(f"\nLooking up: {license_number}")
    results = search_by_license(license_number)
    if not results:
        print(f"  No PALS results for {license_number}")
        return []

    best = results[0]
    person_id  = best.get("PersonId")
    license_id = best.get("LicenseId")
    name = f"{best.get('FirstName','')} {best.get('LastName','')}".strip()
    print(f"  Found: {name} | Status: {best.get('Status')}")

    details = get_license_details(person_id, license_id, license_number)
    disciplinary = details.get("DisciplinaryActionDetails", []) if details else []

    if not disciplinary:
        print(f"  No disciplinary actions on record")
        return []

    print(f"  Disciplinary actions: {len(disciplinary)}")
    downloaded = []

    for action in disciplinary:
        complaint_no   = action.get("ComplaintNumber", "")
        physical_name  = action.get("PhysicalFileName", "")
        file_name      = action.get("FileName", physical_name)
        file_request_id= action.get("FileRequestID", 0)
        disc_action    = action.get("DisciplinaryAction", "")

        print(f"  -> Complaint: {complaint_no} | Action: {disc_action} | File: {physical_name}")

        if not physical_name:
            print(f"     Skipping — no filename")
            continue

        if file_request_id == 0:
            print(f"     Skipping — FileRequestID=0, document not on file")
            continue

        try:
            pdf_bytes = download_disciplinary_doc(physical_name, file_path=file_path)

            safe_name = physical_name.replace(" ", "_").replace("/", "-")
            npi_tag = f"{npi}_" if npi else ""
            out_file = os.path.join(OUTPUT_DISCIPLINARY_DIR, f"{npi_tag}{license_number}_{safe_name}")

            with open(out_file, "wb") as f:
                f.write(pdf_bytes)

            print(f"     Saved ({len(pdf_bytes):,} bytes) -> {out_file}")
            downloaded.append(out_file)
        except Exception as e:
            print(f"     Error downloading: {e}")

    return downloaded


def main():
    parser = argparse.ArgumentParser(description="Download PALS disciplinary action documents")
    parser.add_argument("--license", help="Single license number (e.g. MD031820E)")
    parser.add_argument("--npi",     help="NPI to tag in the output filename")
    parser.add_argument("--input",   help="CSV with columns: NPI, License Number")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DISCIPLINARY_DIR, exist_ok=True)

    # Fetch base file path once — reused for all downloads
    print("Fetching disciplinary file path from PALS settings...")
    file_path = get_disciplinary_file_path()
    print(f"  File path: {file_path}")

    if args.license:
        process_license(args.license, npi=args.npi or "", file_path=file_path)

    elif args.input:
        with open(args.input, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        for i, row in enumerate(rows):
            npi     = row.get("NPI", "").strip()
            lic_num = row.get("License Number", "").strip()
            if not lic_num:
                print(f"[{i+1}] NPI {npi} — no license number, skipping")
                continue
            print(f"\n[{i+1}/{len(rows)}] NPI: {npi} | License: {lic_num}")
            process_license(lic_num, npi=npi, file_path=file_path)
            time.sleep(0.3)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
