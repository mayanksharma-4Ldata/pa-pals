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
