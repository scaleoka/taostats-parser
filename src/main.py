#!/usr/bin/env python3
# src/main.py

import os
import re
import json
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

URL = "https://taostats.io/subnets"
SPREADSHEET_ID       = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME           = "taostats stats"

if not SPREADSHEET_ID or not SERVICE_ACCOUNT_JSON:
    raise RuntimeError(
        "Не заданы обязательные переменные окружения: "
        "SPREADSHEET_ID и GOOGLE_SERVICE_ACCOUNT_JSON"
    )

def parse_initial_data(html: str) -> list[dict]:
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>',
        html, re.DOTALL
    )
    if not m:
        raise RuntimeError("Не найден __NEXT_DATA__ в HTML")
    data = json.loads(m.group(1))
    subs = data.get("props", {}).get("pageProps", {}).get("subnets")
    if not isinstance(subs, list):
        raise RuntimeError("Не найден массив subnets в __NEXT_DATA__")
    return subs

def fetch_subnets() -> list[dict]:
    # Попробовать встроенный JSON
    resp = requests.get(URL, timeout=30)
    try:
        subs = parse_initial_data(resp.text)
        print(f"→ Inline JSON: {len(subs)} подсетей")
        return subs
    except Exception as e:
        print(f"→ Inline JSON failed ({e}), falling back to Playwright…")

    # Playwright fallback
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        # Ждём только DOMContentLoaded, не networkidle
        page.goto(URL, timeout=60_000, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(
                'div[role="row"][data-rowindex]',
                timeout=60_000
            )
        except PlaywrightTimeoutError:
            # Дополнительная пауза, если нужно
            page.wait_for_timeout(5_000)

        subs = page.evaluate(
            """() => {
                return Array.from(
                  document.querySelectorAll('div[role="row"][data-rowindex]')
                ).map(row => {
                  const cells = Array.from(row.querySelectorAll('div[role="cell"]'));
                  return {
                    netuid:            cells[0]?.innerText.trim() || "",
                    name:              cells[1]?.innerText.trim() || "",
                    registration_date: cells[2]?.innerText.trim() || "",
                    price:             cells[3]?.innerText.trim() || "",
                    emission:          cells[4]?.innerText.trim() || "",
                    registration_cost: cells[5]?.innerText.trim() || "",
                    github:            cells[6]?.innerText.trim() || "",
                    discord:           cells[7]?.innerText.trim() || "",
                    key:               cells[8]?.innerText.trim() || "",
                    vtrust:            cells[9]?.innerText.trim() || "",
                  };
                });
            }"""
        )
        browser.close()
        print(f"→ Playwright scraped: {len(subs)} подсетей")
        return subs

def write_to_sheets(subnets: list[dict]):
    creds_info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds).spreadsheets()

    headers = [
        "netuid","name","registration_date","price","emission",
        "registration_cost","github","discord","key","vtrust"
    ]
    values = [headers] + [[s.get(h, "") for h in headers] for s in subnets]

    service.values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1:J"
    ).execute()

    service.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1",
        valueInputOption="RAW",
        body={"values": values}
    ).execute()

    print(f"✅ Записано {len(subnets)} подсетей в лист '{SHEET_NAME}'")

def main():
    print("1) Собираем подсети…")
    subs = fetch_subnets()
    print("2) Пишем в Google Sheets…")
    write_to_sheets(subs)
    print("Готово.")

if __name__ == "__main__":
    main()
