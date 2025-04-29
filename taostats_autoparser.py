#!/usr/bin/env python3
import os
import sys
import json
import logging
import requests
from oauth2client.service_account import ServiceAccountCredentials
import gspread

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# Environment variables
SERVICE_ACCOUNT_JSON = os.environ.get('SERVICE_ACCOUNT_JSON')
SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID')

if not SERVICE_ACCOUNT_JSON:
    logging.error("SERVICE_ACCOUNT_JSON not set")
    sys.exit(1)
if not SPREADSHEET_ID:
    logging.error("SPREADSHEET_ID not set")
    sys.exit(1)

# Authenticate Google Sheets
scope = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
try:
    sa_info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(sa_info, scope)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).sheet1
except Exception as e:
    logging.error("Failed to authenticate Google Sheets: %s", e)
    sys.exit(1)

VALIDATORS_URL = 'https://api.minersunion.ai/metrics/summary/'

def fetch_all_validators(timeout=10):
    """Fetch validators list from API."""
    try:
        resp = requests.get(VALIDATORS_URL, timeout=timeout)
        resp.raise_for_status()
        logging.debug("API response: %s", resp.text[:200])
    except requests.exceptions.RequestException as e:
        logging.error("Error fetching validators: %s", e)
        raise
    try:
        payload = resp.json()
    except json.JSONDecodeError as e:
        logging.error("Invalid JSON response: %s", e)
        raise
    # Extract list
    if isinstance(payload, dict):
        if 'data' in payload and isinstance(payload['data'], dict):
            return payload['data'].get('validators', [])
        return payload.get('validators', [])
    logging.warning("Unexpected payload type: %s", type(payload))
    return []

def parse_validators(validators):
    """Convert raw validators data into rows for Google Sheets."""
    headers = [
        'Subnet Name', 'Score', 'Identity', 'Hotkey',
        'Total Stake Weight', 'VTrust', 'Dividends', 'Chk Take'
    ]
    rows = [headers]
    for v in validators:
        try:
            subnet_name = v.get('subnetName') or ''
            score = float(v.get('score', 0))
            identity = v.get('identity') or ''
            hotkey = v.get('hotkey') or ''
            voting_power = int(v.get('votingPower', 0))
            vtrust = float(v.get('vtrust', 0))
            dividends = float(v.get('dividends', 0))
            chk_take = float(v.get('checkTake', 0))
        except (TypeError, ValueError) as e:
            logging.warning("Type conversion error for validator %s: %s", v, e)
            subnet_name = v.get('subnetName')
            score = v.get('score')
            identity = v.get('identity')
            hotkey = v.get('hotkey')
            voting_power = v.get('votingPower')
            vtrust = v.get('vtrust')
            dividends = v.get('dividends')
            chk_take = v.get('checkTake')
        rows.append([
            subnet_name, score, identity, hotkey,
            voting_power, vtrust, dividends, chk_take
        ])
    return rows

def write_to_sheet(rows):
    """Clear and update Google Sheet with given rows."""
    try:
        sheet.clear()
        sheet.update('A1', rows, value_input_option='RAW')
        logging.info("Written %d rows to sheet %s", len(rows)-1, SPREADSHEET_ID)
    except Exception as e:
        logging.error("Failed to write to Google Sheets: %s", e)
        raise

def main():
    try:
        validators = fetch_all_validators()
        rows = parse_validators(validators)
        write_to_sheet(rows)
    except Exception as e:
        logging.error("Script failed: %s", e)
        sys.exit(1)

if __name__ == '__main__':
    main()
