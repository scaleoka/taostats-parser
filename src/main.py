#!/usr/bin/env python3
import os
import re
import json
import requests
from urllib.parse import urljoin
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

def fetch_via_rsc() -> list[dict]:
    """
    Попытка изъять динамический buildId из HTML и запросить
    JSON-данные подсетей напрямую у Next.js endpoint.
    """
    print("1a) Пытаемся получить subnets через JSON-API…")
    # 1. Скачиваем HTML
    html = requests.get(URL, timeout=30).text

    # 2. Ищем buildId в тегах Next.js
    m = re.search(r'/_next/data/([^/]+)/subnets\.json', html)
    if not m:
        print("→ Не удалось найти buildId в HTML")
        return []

    build_id = m.group(1)
    print(f"→ Найден buildId: {build_id}")

    # 3. Формируем URL JSON-файла
    json_url = urljoin(URL, f"/_next/data/{build_id}/subnets.json")
    params = {"page": 1, "limit": -1, "order": "market_cap_desc"}

    # 4. Запрашиваем и разбираем JSON
    resp = requests.get(json_url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # 5. Извлекаем массив подсетей (проверьте путь в структуре JSON)
    subs = (
        data
        .get("pageProps", {})
        .get("initialProps", {})
        .get("data", {})
        .get("data", [])
    )
    print(f"→ JSON-API вернуло подсетей: {len(subs)}")
    return subs

def fetch_via_playwright() -> list[dict]:
    """
    Фолбэк: если API не сработал, скрапим таблицу через Playwright.
    """
    print("1b) Фолбэк: скрапим через Playwright + CSS-селектор…")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
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
    """
    Очищает лист и записывает заголовки + данные подсетей в Google Sheets.
    """
    creds_info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds).spreadsheets()

    headers = ["netuid", "name", "registration_date", "price", "emission",
               "registration_cost", "github", "discord", "key", "vtrust"]
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
    # 1) Сначала пробуем API Next.js
    subs = fetch_via_rsc()
    # 2) Если API не вернул ничего — фолбэк на браузерный скрап
    if not subs:
        subs = fetch_via_playwright()

    print(f"→ Итого подсетей: {len(subs)}")
    print("2) Пишем в Google Sheets…")
    write_to_sheets(subs)
    print("Готово.")

if __name__ == "__main__":
    main()
