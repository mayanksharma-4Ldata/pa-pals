# PALS PA API credentials & settings
# No auth token required — PALS is a public API

PALS_BASE_URL = "https://www.pals.pa.gov/api"

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.pals.pa.gov/"
}

# Output paths (relative to project root)
OUTPUT_LICENSES_DIR    = "output/licenses"
OUTPUT_DISCIPLINARY_DIR = "output/disciplinary_docs"
