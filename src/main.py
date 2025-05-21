#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
from playwright.sync_api import sync_playwright
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

URL = "https://taostats.io/subnets"
SHEET_NAME = "taostats stats"
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

if not SPREADSHEET_ID or not SERVICE_ACCOUNT_JSON:
    raise RuntimeError("Не заданы обязательные переменные окружения: SPREADSHEET_ID и GOOGLE_SERVICE_ACCOUNT_JSON")

def fetch_subnets():
    print("Запускаем headless-браузер и ждём таблицу…")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=90000)
        page.wait_for_selector('div[role="row"][data-rowindex]', timeout=60000)
        rows = page.query_selector_all('div[role="row"][data-rowindex]')
        subnets = []
        for row in rows:
            cells = row.query_selector_all('div[role="cell"]')
            # Смотрим — все ли нужные столбцы на месте
            if len(cells) < 10:
                continue
            subnets.append({
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
        print(f"Найдено подсетей: {len(subnets)}")
        return subnets

def write_to_sheets(subnets):
    print("Записываем в Google Sheets…")
    creds_info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds).spreadsheets()

    headers = ["netuid", "name", "registration_date", "price", "emission",
               "registration_cost", "github", "discord", "key", "vtrust"]
    values = [headers] + [[subnet.get(h, "-") for h in headers] for subnet in subnets]

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
    subnets = fetch_subnets()
    write_to_sheets(subnets)
    print("Готово.")

if __name__ == "__main__":
    main()
