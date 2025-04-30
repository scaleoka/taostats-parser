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

# Load Taostats API key
TAO_API_KEY = get_env_var("TAO_API_KEY")

# Load Google Sheets credentials JSON from environment
creds_json_str = get_env_var("GSPREAD_CREDS_JSON")
creds_json = json.loads(creds_json_str)

# Spreadsheet ID
SPREADSHEET_ID = get_env_var("SPREADSHEET_ID")

# Configure gspread client
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)
gc = gspread.authorize(credentials)

# Fetch subnets from Taostats
API_URL = "https://api.taostats.io/v1/subnets"
headers = {
    "Accept": "application/json",
    "Authorization": TAO_API_KEY
}

try:
    resp = requests.get(API_URL, headers=headers, timeout=15)
    resp.raise_for_status()
    subnets = resp.json()
except Exception as e:
    logging.error(f"Error fetching subnets: {e}")
    sys.exit(1)

# Prepare rows for sheet: header + data
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

# Open the Google Sheet and clear existing sheet
sh = gc.open_by_key(SPREADSHEET_ID)
sheet = sh.sheet1
sheet.clear()

# Write all rows at once
sheet.update('A1', rows)

print(f"Successfully wrote {len(rows)-1} subnets to spreadsheet {SPREADSHEET_ID}")
