"""
Fetch PA licenses from PALS for a list of providers (NPI, first name, last name).

Usage:
    python fetch_pa_licenses.py --input providers.csv
    python fetch_pa_licenses.py --license MD031820E
"""

import csv
import time
import json
import argparse
import os
from datetime import datetime

from pals_api import search_by_name, search_by_license, get_license_details
from credentials.config import OUTPUT_LICENSES_DIR

FIELDNAMES = [
    "npi", "first_name", "middle_name", "last_name",
    "license_number", "license_state", "license_status",
    "profession", "license_type",
    "issue_date", "effective_date", "expiration_date", "last_renewal_date",
]


def lookup_provider(npi, first, last):
    row = {f: "" for f in FIELDNAMES}
    row["npi"] = npi
    row["first_name"] = first
    row["last_name"] = last

    results = search_by_name(last, first)
    if not results:
        print(f"  No results")
        return row

    active = [r for r in results if r.get("Status", "").lower() == "active"]
    best = active[0] if active else results[0]

    row["middle_name"]    = best.get("MiddleName", "") or ""
    row["license_number"] = best.get("LicenseNumber", "") or ""
    row["license_state"]  = best.get("State", "") or ""
    row["license_status"] = best.get("Status", "") or ""
    row["profession"]     = best.get("ProfessionType", "") or ""
    row["license_type"]   = best.get("LicenceType", "") or ""

    person_id  = best.get("PersonId")
    license_id = best.get("LicenseId")

    if person_id and license_id:
        details = get_license_details(person_id, license_id, row["license_number"])
        if details:
            row["issue_date"]       = details.get("IssueDate", "") or ""
            row["effective_date"]   = details.get("StatusEffectivedate", "") or ""
            row["expiration_date"]  = details.get("ExpiryDate", "") or ""
            row["last_renewal_date"]= details.get("LastRenewalDate", "") or ""
            row["license_state"]    = details.get("StateName", row["license_state"]) or row["license_state"]
            row["license_status"]   = details.get("Status", row["license_status"]) or ""
            print(f"  {row['license_number']} | {row['license_status']} | Expires: {row['expiration_date']}")
    return row


def lookup_by_license(license_number):
    results = search_by_license(license_number)
    if not results:
        print(f"  No results for {license_number}")
        return None

    best = results[0]
    person_id  = best.get("PersonId")
    license_id = best.get("LicenseId")
    details = get_license_details(person_id, license_id, license_number) if person_id and license_id else {}

    row = {
        "npi":              "",
        "first_name":       best.get("FirstName", "") or "",
        "middle_name":      best.get("MiddleName", "") or "",
        "last_name":        best.get("LastName", "") or "",
        "license_number":   license_number,
        "license_state":    details.get("StateName", best.get("State", "")) or "",
        "license_status":   details.get("Status", best.get("Status", "")) or "",
        "profession":       best.get("ProfessionType", "") or "",
        "license_type":     best.get("LicenceType", "") or "",
        "issue_date":       details.get("IssueDate", "") or "",
        "effective_date":   details.get("StatusEffectivedate", "") or "",
        "expiration_date":  details.get("ExpiryDate", "") or "",
        "last_renewal_date":details.get("LastRenewalDate", "") or "",
    }
    return row


def main():
    parser = argparse.ArgumentParser(description="Fetch PA PALS license data")
    parser.add_argument("--input",   help="CSV file with columns: NPI, First Name, Last Name")
    parser.add_argument("--license", help="Single license number lookup (e.g. MD031820E)")
    args = parser.parse_args()

    os.makedirs(OUTPUT_LICENSES_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUTPUT_LICENSES_DIR, f"pa_licenses_{timestamp}.csv")

    rows = []

    if args.license:
        print(f"Looking up license: {args.license}")
        row = lookup_by_license(args.license)
        if row:
            rows.append(row)
            print(json.dumps(row, indent=2))

    elif args.input:
        with open(args.input, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            providers = list(reader)

        for i, p in enumerate(providers):
            npi   = p.get("NPI", "").strip()
            first = p.get("First Name", "").strip()
            last  = p.get("Last Name", "").strip()
            print(f"[{i+1}/{len(providers)}] {first} {last} (NPI: {npi})")
            rows.append(lookup_provider(npi, first, last))
            time.sleep(0.3)

    else:
        parser.print_help()
        return

    if rows:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nSaved {len(rows)} record(s) -> {out_path}")


if __name__ == "__main__":
    main()
