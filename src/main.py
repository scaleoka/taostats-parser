#!/usr/bin/env python3
# src/main.py

import os
import json
from playwright.sync_api import sync_playwright
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# URL страницы с подсетями
URL = "https://taostats.io/subnets"

# Ожидаемые переменные окружения
SPREADSHEET_ID        = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON  = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME            = "taostats stats"

if not SPREADSHEET_ID or not SERVICE_ACCOUNT_JSON:
    raise RuntimeError(
        "Не заданы обязательные переменные окружения: "
        "SPREADSHEET_ID и GOOGLE_SERVICE_ACCOUNT_JSON"
    )

def fetch_subnets() -> list[dict]:
    """
    Открывает страницу Playwright, ждёт появления строк грида и парсит их.
    """
    subs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        # ждём только DOMContentLoaded, не networkidle
        page.goto(URL, timeout=60_000, wait_until="domcontentloaded")
        # затем ждём конкретно появления строк таблицы
        page.wait_for_selector(
            'div[role="row"][data-rowindex]',
            timeout=60_000
        )

        rows = page.query_selector_all('div[role="row"][data-rowindex]')
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
    return subs

def write_to_sheets(subnets: list[dict]):
    """
    Очищает диапазон A1:J и пишет данные из subnets
    в лист SHEET_NAME Google Sheets.
    """
    creds_info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds).spreadsheets()

    headers = ["netuid", "name", "registration_date", "price", "emission",
               "registration_cost", "github", "discord", "key", "vtrust"]
    values = [headers] + [[s[h] for h in headers] for s in subnets]

    # Очищаем старые данные
    service.values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1:J"
    ).execute()

    # Пишем новые
    service.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1",
        valueInputOption="RAW",
        body={"values": values}
    ).execute()

    print(f"✅ Записано {len(subnets)} подсетей в лист '{SHEET_NAME}'")

def main():
    print("1) Собираем подсети через Playwright…")
    subs = fetch_subnets()
    print(f"→ Найдено подсетей: {len(subs)}")

    print("2) Пишем в Google Sheets…")
    write_to_sheets(subs)

    print("Готово.")

if __name__ == "__main__":
    main()
