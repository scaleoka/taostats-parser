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
# Optional base URL override
TAO_API_BASE_URL = os.environ.get("TAO_API_BASE_URL", "https://api.taostats.io/v1")
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

# Prepare headers (use Bearer token)
headers = {
    "Accept": "application/json",
    "Authorization": f"Bearer {TAO_API_KEY}"
}
endpoint_url = f"{TAO_API_BASE_URL}/subnets"

# Fetch subnets data
try:
    resp = requests.get(endpoint_url, headers=headers, timeout=15)
    resp.raise_for_status()
    subnets = resp.json()
except requests.HTTPError as e:
    status = getattr(resp, 'status_code', None)
    logging.error("Error fetching subnets from %s: %s", endpoint_url, e)
    if status == 401:
        logging.error("Unauthorized. Check if TAO_API_KEY is valid and format is Bearer <token>.")
    elif status == 404:
        logging.error("Not Found. Check TAO_API_BASE_URL (currently '%s').", TAO_API_BASE_URL)
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
