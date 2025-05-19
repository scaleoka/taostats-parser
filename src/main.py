#!/usr/bin/env python3
# src/main.py

import os
import re
import json
import requests
from playwright.sync_api import sync_playwright
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

def fetch_via_rsc() -> list[dict]:
    print("1a) Пытаемся получить subnets через RSC-endpoint…")
    body = {"page": 1, "limit": -1, "order": "market_cap_desc"}
    headers = {"Accept": "text/x-component"}
    resp = requests.post(URL, json=body, headers=headers, timeout=30)
    resp.raise_for_status()
    text = resp.text

    # Найти самый большой JSON-массив в ответе
    match = re.search(r"(\[\s*\{[\s\S]*\}\s*\])", text)
    if not match:
        print("→ Не удалось найти JSON-массив в RSC-ответе")
        return []

    try:
        subs = json.loads(match.group(1))
        print(f"→ RSC вернуло подсетей: {len(subs)}")
        return subs
    except json.JSONDecodeError:
        print("→ Ошибка декодирования JSON из RSC-ответа")
        return []

def fetch_via_playwright() -> list[dict]:
    print("1b) Фолбэк: скрапим через Playwright + CSS-селектор…")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        # подождём появления строк DataGrid
        page.wait_for_selector('div[role="row"][data-rowindex]', timeout=60_000)
        rows = page.query_selector_all('div[role="row"][data-rowindex]')
        subs = []
        for row in rows:
            cells = row.query_selector_all('div[role="cell"]')
            if len(cells) < 10:
                continue
            subs.append({
                "netuid":            cells[0].inner_text().strip(),
                "name":              cells[1].inner_text().strip(),
                "registration_date": cells[2].inner_text().strip(),
                "price":             cells[3].inner_text().strip(),
                "emission":          cells[4].inner_text().strip(),
                "registration_cost": cells[5].inner_text().strip(),
                "github":            cells[6].inner_text().strip(),
                "discord":           cells[7].inner_text().strip(),
                "key":               cells[8].inner_text().strip(),
                "vtrust":            cells[9].inner_text().strip(),
            })
        browser.close()
        print(f"→ Playwright скрапнул подсетей: {len(subs)}")
        return subs

def write_to_sheets(subnets: list[dict]):
    creds_info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds).spreadsheets()

    headers = ["netuid", "name", "registration_date", "price", "emission",
               "registration_cost", "github", "discord", "key", "vtrust"]
    values = [headers] + [[s[h] for h in headers] for s in subnets]

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
    # 1) Попытка через RSC
    subs = fetch_via_rsc()
    # 2) Если не вышло — фолбэк на Playwright
    if not subs:
        subs = fetch_via_playwright()

    print(f"→ Итого подсетей: {len(subs)}")
    print("2) Пишем в Google Sheets…")
    write_to_sheets(subs)
    print("Готово.")

if __name__ == "__main__":
    main()
