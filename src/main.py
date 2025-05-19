#!/usr/bin/env python3
import os
import re
import json
import cloudscraper
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ——— Конфиг из окружения ———
URL                  = "https://taostats.io/subnets"
SHEET_NAME           = "taostats stats"
SPREADSHEET_ID       = os.getenv("SPREADSHEET_ID")
GOOGLE_SERVICE_JSON  = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

if not SPREADSHEET_ID or not GOOGLE_SERVICE_JSON:
    raise RuntimeError("Не заданы переменные SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON")

def fetch_page_html() -> str:
    """Скачиваем HTML страницы /subnets."""
    scraper = cloudscraper.create_scraper()
    resp = scraper.get(URL, timeout=30)
    resp.raise_for_status()
    return resp.text

def parse_initial_data(html: str) -> list[dict]:
    """
    Ищем в HTML массив initialData:
      "initialData": [ {…}, {…}, … ]
    и возвращаем его как list[dict].
    """
    m = re.search(
        r'"initialData"\s*:\s*(\[\s*\{.*?\}\s*\])',
        html,
        re.DOTALL
    )
    if not m:
        # для отладки:
        with open("debug_subnets.html", "w", encoding="utf-8") as f:
            f.write(html)
        raise RuntimeError("Не удалось найти initialData в HTML (смотрите debug_subnets.html)")
    return json.loads(m.group(1))

def transform(sub: dict) -> dict:
    """Приводим поля к нужным колонкам."""
    return {
        "netuid":            sub.get("netuid"),
        "name":              sub.get("subnet_name") or sub.get("name"),
        "registration_date": sub.get("registration_timestamp") or sub.get("timestamp"),
        "price":             sub.get("price"),
        "emission":          sub.get("emission"),
        "registration_cost": sub.get("neuron_registration_cost") or sub.get("registration_cost"),
        "github":            sub.get("github_repo") or sub.get("github"),
        "discord":           sub.get("discord_url") or sub.get("discord"),
        "key":               sub.get("subnet_url") or sub.get("key"),
        "vtrust":            sub.get("vTrust") or sub.get("vtrust"),
    }

def save_to_sheets(rows: list[dict]):
    """Заливаем готовые строки в Google Sheets."""
    creds_info = json.loads(GOOGLE_SERVICE_JSON)
    creds = service_account.Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds).spreadsheets()

    headers = list(rows[0].keys())
    values  = [headers] + [[r.get(h, "") for h in headers] for r in rows]

    service.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1",
        valueInputOption="RAW",
        body={"values": values}
    ).execute()
    print(f"✅ Всего записано {len(rows)} подсетей в лист '{SHEET_NAME}'")

def main():
    print("1) Скачиваем HTML…")
    html = fetch_page_html()

    print("2) Извлекаем initialData…")
    subs = parse_initial_data(html)
    print(f"→ Найдено подсетей: {len(subs)}")

    print("3) Трансформируем записи…")
    rows = [transform(s) for s in subs]

    print("4) Записываем в Google Sheets…")
    save_to_sheets(rows)

    print("Готово.")

if __name__ == "__main__":
    main()
