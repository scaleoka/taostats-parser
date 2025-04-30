#!/usr/bin/env python3
import os
import sys
import json
import logging
import requests
from playwright.sync_api import sync_playwright
import gspread
from oauth2client.service_account import ServiceAccountCredentials

def get_env_var(name):
    val = os.environ.get(name)
    if not val:
        logging.error(f"Environment variable {name} not set")
        sys.exit(1)
    return val


def fetch_api_json():
    """
    Launches a headless browser to intercept the first JSON XHR containing 'subnets' in its URL.
    Returns the parsed JSON data (a list of subnets).
    """
    url = "https://taostats.io/subnets"
    api_data = None
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        def handle_response(response):
            nonlocal api_data
            try:
                if "subnets" in response.url and response.request.resource_type == "xhr" and \
                   response.headers.get("content-type", "").startswith("application/json"):
                    api_data = response.json()
            except Exception:
                pass
        page.on("response", handle_response)
        page.goto(url, timeout=60000)
        page.wait_for_timeout(5000)
        browser.close()
    if not api_data:
        logging.error("Failed to detect JSON API endpoint on %s", url)
        sys.exit(1)
    return api_data


def write_to_sheet(rows):
    creds_json_str = get_env_var("GSPREAD_CREDS_JSON")
    creds = json.loads(creds_json_str)
    SPREADSHEET_ID = get_env_var("SPREADSHEET_ID")
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds, scope)
    gc = gspread.authorize(credentials)
    sh = gc.open_by_key(SPREADSHEET_ID)
    sheet = sh.sheet1
    sheet.clear()
    sheet.update('A1', rows)
    print(f"Successfully wrote {len(rows)-1} subnets to spreadsheet {SPREADSHEET_ID}")


def main():
    data = fetch_api_json()
    # Expecting a list of objects with desired keys
    header = ["netuid","name","price","emission","reg_cost","github","discord","key"]
    rows = [header]
    for sb in data:
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
    write_to_sheet(rows)

if __name__ == "__main__":
    main()
