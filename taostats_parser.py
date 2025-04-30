import os
import sys
import json
import logging
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials


def get_env_var(name):
    val = os.environ.get(name)
    if not val:
        logging.error(f"Environment variable {name} not set")
        sys.exit(1)
    return val

# Load environment variables
TAO_API_KEY = get_env_var("TAO_API_KEY")
creds_json_str = get_env_var("GSPREAD_CREDS_JSON")
creds_json = json.loads(creds_json_str)
SPREADSHEET_ID = get_env_var("SPREADSHEET_ID")

# Configure gspread client
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)
gc = gspread.authorize(credentials)

# Define API endpoints
API_URL = "https://api.taostats.io/v1/subnets"
FALLBACK_URL = "https://taostats.io/data.json"
headers = {
    "Accept": "application/json",
    "Authorization": TAO_API_KEY
}

# Fetch subnets data
try:
    resp = requests.get(API_URL, headers=headers, timeout=15)
    resp.raise_for_status()
    subnets = resp.json()
except requests.HTTPError as e:
    status = getattr(resp, 'status_code', None)
    if status == 404:
        logging.warning("Endpoint %s returned 404, falling back to %s", API_URL, FALLBACK_URL)
        try:
            resp2 = requests.get(FALLBACK_URL, timeout=15)
            resp2.raise_for_status()
            data = resp2.json()
            subnets = data.get("subnets", [])
        except Exception as e2:
            logging.error("Error fetching fallback data: %s", e2)
            sys.exit(1)
    else:
        logging.error("Error fetching subnets from %s: %s", API_URL, e)
        sys.exit(1)
except Exception as e:
    logging.error("Unexpected error: %s", e)
    sys.exit(1)

# Prepare rows for Google Sheets
fields = ["netuid", "name", "price", "emission", "reg_cost", "github", "discord", "key"]
rows = [fields]
for sb in subnets:
    rows.append([
        sb.get("netuid", ""),
        sb.get("name", ""),
        sb.get("price", ""),
        sb.get("emission", ""),
        sb.get("reg_cost", ""),
        sb.get("github", ""),
        sb.get("discord", ""),
        sb.get("key", "")
    ])

# Write to Google Sheet
sh = gspread.authorize(credentials).open_by_key(SPREADSHEET_ID)
sheet = sh.sheet1
sheet.clear()
sheet.update('A1', rows)

print(f"Successfully wrote {len(rows)-1} subnets to spreadsheet {SPREADSHEET_ID}")
