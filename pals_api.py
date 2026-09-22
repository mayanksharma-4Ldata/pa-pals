"""
Core PALS API client.
All network calls go through here — search, detail fetch, document download.
"""

import json
import ssl
import urllib.request
import urllib.parse

from credentials.config import PALS_BASE_URL, HEADERS

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def _post(endpoint, payload):
    url = f"{PALS_BASE_URL}/{endpoint}"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        return json.loads(r.read().decode())


def _get_raw(endpoint, params=None):
    url = f"{PALS_BASE_URL}/{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    get_headers = {k: v for k, v in HEADERS.items() if k != "Content-Type"}
    req = urllib.request.Request(url, headers=get_headers, method="GET")
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        return r.read(), r.headers.get("Content-Type", "")


def search_by_license(license_number):
    return _post("Search/SearchForPersonOrFacilty", {
        "OptPersonFacility": "Person",
        "ProfessionID": "", "LicenseTypeId": "",
        "LastName": "", "FirstName": "", "MiddleName": "",
        "LicenseNumber": license_number,
        "City": "", "State": "PA", "zipcode": "",
        "Country": "United States", "County": None,
        "IsFacility": 0, "PersonId": None, "PageNo": 1, "RecaptchaResponse": ""
    })


def search_by_name(last_name, first_name):
    return _post("Search/SearchForPersonOrFacilty", {
        "OptPersonFacility": "Person",
        "ProfessionID": "", "LicenseTypeId": "",
        "LastName": last_name, "FirstName": first_name, "MiddleName": "",
        "LicenseNumber": "",
        "City": "", "State": "PA", "zipcode": "",
        "Country": "United States", "County": None,
        "IsFacility": 0, "PersonId": None, "PageNo": 1, "RecaptchaResponse": ""
    })


def get_license_details(person_id, license_id, license_number):
    return _post("Search/GetPersonOrFacilityDetails", {
        "PersonId": person_id,
        "LicenseNumber": license_number,
        "IsFacility": 0,
        "LicenseId": license_id
    })


# ── profession → PA board name mapping ───────────────────────────────────────

_BOARD_MAP = {
    "medicine":                        "Pennsylvania State Board of Medicine",
    "osteopathic medicine":            "Pennsylvania State Board of Osteopathic Medicine",
    "physical therapy":                "Pennsylvania State Board of Physical Therapy",
    "occupational therapy":            "Pennsylvania State Board of Occupational Therapy",
    "chiropractic":                    "Pennsylvania State Board of Chiropractic",
    "nursing":                         "Pennsylvania State Board of Nursing",
    "dentistry":                       "Pennsylvania State Board of Dentistry",
    "podiatry":                        "Pennsylvania State Board of Podiatric Medicine",
    "pharmacy":                        "Pennsylvania State Board of Pharmacy",
    "optometry":                       "Pennsylvania State Board of Optometry",
    "psychology":                      "Pennsylvania State Board of Psychology",
    "social work":                     "Pennsylvania State Board of Social Workers, Marriage and Family Therapists and Professional Counselors",
    "speech":                          "Pennsylvania State Board of Examiners in Speech-Language Pathology and Audiology",
    "speech-language pathology":       "Pennsylvania State Board of Examiners in Speech-Language Pathology and Audiology",
    "audiology":                       "Pennsylvania State Board of Examiners in Speech-Language Pathology and Audiology",
    "radiology personnel":             "Pennsylvania State Board of Medicine",
    "athletic trainer":                "Pennsylvania State Board of Medicine",
    "veterinary medicine":             "Pennsylvania State Board of Veterinary Medicine",
    "cosmetology":                     "Pennsylvania State Board of Cosmetology",
    "funeral director":                "Pennsylvania State Board of Funeral Directors",
    "engineering":                     "Pennsylvania State Registration Board for Professional Engineers, Land Surveyors and Geologists",
    "real estate":                     "Pennsylvania State Real Estate Commission",
    "nursing home administrator":      "Pennsylvania State Board of Examiners of Nursing Home Administrators",
    "landscape architecture":          "Pennsylvania State Board of Landscape Architects",
    "architecture":                    "Pennsylvania State Architects Licensure Board",
    "auctioneer":                      "Pennsylvania State Board of Auctioneer Examiners",
    "barber":                          "Pennsylvania State Board of Barber Examiners",
    "vehicle":                         "Pennsylvania State Board of Vehicle Manufacturers, Dealers and Salespersons",
    "crane operator":                  "Pennsylvania State Board of Crane Operators",
}


def profession_to_board(profession_type):
    """Map PALS ProfessionType to the full Pennsylvania State Board name."""
    if not profession_type:
        return ""
    key = profession_type.strip().lower()
    # exact match first
    if key in _BOARD_MAP:
        return _BOARD_MAP[key]
    # partial match — find the first key that appears in the profession string
    for k, v in _BOARD_MAP.items():
        if k in key:
            return v
    # fallback: prefix with standard format
    return f"Pennsylvania State Board of {profession_type.strip().title()}"


def get_disciplinary_file_path():
    data, _ = _get_raw("BaseApi/GetSettings", {"settingCode": "DISCIPLINARYACTIONFILEPATH"})
    return json.loads(data.decode())


def download_disciplinary_doc(physical_filename, file_path):
    url = (
        f"{PALS_BASE_URL}/Upload/SearchDownloadFile"
        f"?FilePath={urllib.parse.quote(file_path)}"
        f"&FileName={urllib.parse.quote(physical_filename)}"
    )
    get_headers = {k: v for k, v in HEADERS.items() if k != "Content-Type"}
    req = urllib.request.Request(url, headers=get_headers, method="GET")
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        return r.read()
