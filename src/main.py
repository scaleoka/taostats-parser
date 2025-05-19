#!/usr/bin/env python3
import os
import json
from playwright.sync_api import sync_playwright
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

URL = "https://taostats.io/subnets"
# Ожидаем две переменные окружения:
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")


def fetch_subnets():
    """
    Открываем страницу через Playwright, ждём загрузки грида и парсим все строки.
    Возвращаем список dict с полями name, netuid, price.
    """
    subs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle")
        # Ждём, пока появятся ряды таблицы
        page.wait_for_selector("div[role='row'][data-rowindex]")
        rows = page.query_selector_all("div[role='row'][data-rowindex]")
        for row in rows:
            cells = row.query_selector_all("div[role='cell']")
            if len(cells) < 4:
                continue
            name = cells[1].inner_text().strip()
            netuid = cells[2].inner_text().strip()
            price = cells[3].inner_text().strip()
            subs.append({"name": name, "netuid": netuid, "price": price})
        browser.close()
    return subs


def write_to_sheets(subs):
    """
    Очищаем диапазон A2:C на листе 'taostats stats' и пишем туда
    name / netuid / price.
    """
    if not SPREADSHEET_ID or not SERVICE_ACCOUNT_JSON:
        raise RuntimeError(
            "Missing SPREADSHEET_ID or GOOGLE_SERVICE_ACCOUNT_JSON environment variables"
        )
    creds_info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds)
    sheet = service.spreadsheets()

    # Очистка старых данных
    sheet.values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range="taostats stats!A2:C"
    ).execute()

    # Формируем новый массив значений
    values = [[s["name"], s["netuid"], s["price"]] for s in subs]
    body = {"values": values}

    # Пишем в таблицу
    sheet.values().append(
        spreadsheetId=SPREADSHEET_ID,
        range="taostats stats!A2:C",
        valueInputOption="RAW",
        body=body
    ).execute()

    print(f"✅ Written {len(subs)} rows to 'taostats stats'")


def main():
    subs = fetch_subnets()
    print(f"Found {len(subs)} subnets")
    write_to_sheets(subs)


if __name__ == "__main__":
    main()
