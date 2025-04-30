#!/usr/bin/env python3
import os
import sys
import json
import logging

import bittensor
from playwright.sync_api import sync_playwright
import gspread
from oauth2client.service_account import ServiceAccountCredentials


def get_env_var(name):
    val = os.environ.get(name)
    if not val:
        logging.error(f"Environment variable {name} not set")
        sys.exit(1)
    return val


def fetch_onchain_data():
    """
    Fetches on-chain subnet data via Bittensor SDK.
    Returns a dict keyed by netuid with fields: name, price, emission, reg_cost.
    """
    subtensor = bittensor.subtensor(network="mainnet")
    metas = subtensor.get_all_subnets_info()
    onchain = {}
    for di in metas:
        onchain[di.netuid] = {
            "name": di.name or f"SN{di.netuid}",
            "price": di.price,
            "emission": di.emission,
            "reg_cost": di.burn_cost
        }
    return onchain


def fetch_offchain_data():
    """
    Scrapes off-chain metadata (github, discord, key) from taostats.io via Playwright.
    Returns a dict keyed by netuid.
    """
    url = "https://taostats.io/subnets"
    offchain = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=60000)
        page.wait_for_selector("table tbody tr", timeout=60000)
        for row in page.query_selector_all("table tbody tr"):
            cols = row.query_selector_all("td")
            try:
                netuid = int(cols[0].inner_text())
            except Exception:
                continue
            # GitHub link
            gh_a = cols[5].query_selector("a")
            github = gh_a.get_attribute("href") if gh_a else ""
            # Discord link
            dc_a = cols[6].query_selector("a")
            discord = dc_a.get_attribute("href") if dc_a else ""
            # Key
            key = cols[7].inner_text().strip()
            offchain[netuid] = {"github": github, "discord": discord, "key": key}
        browser.close()
    return offchain


def write_to_sheet(rows):
    """
    Writes the provided rows to Google Sheets using credentials from env.
    """
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
    onchain = fetch_onchain_data()
    offchain = fetch_offchain_data()
    # Combine rows
    rows = [["netuid","name","price","emission","reg_cost","github","discord","key"]]
    for netuid, data in onchain.items():
        md = offchain.get(netuid, {})
        rows.append([
            netuid,
            data.get("name", ""),
            data.get("price", ""),
            data.get("emission", ""),
            data.get("reg_cost", ""),
            md.get("github", ""),
            md.get("discord", ""),
            md.get("key", "")
        ])
    write_to_sheet(rows)


if __name__ == "__main__":
    main()
