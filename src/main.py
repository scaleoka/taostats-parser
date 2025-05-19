#!/usr/bin/env python3
import os
import re
import json
import cloudscraper
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ——— Конфиг из окружения ———
SPREADSHEET_ID      = os.getenv("SPREADSHEET_ID")
GOOGLE_SERVICE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME          = "taostats stats"
SUBNETS_URL         = "https://taostats.io/subnets"

if not SPREADSHEET_ID or not GOOGLE_SERVICE_JSON:
    raise RuntimeError("Не заданы SECRETS SPREADSHEET_ID и GOOGLE_SERVICE_ACCOUNT_JSON")

def fetch_all_subnets():
    scraper = cloudscraper.create_scraper()
    payload = {"page": 1, "limit": -1, "order": "market_cap_desc"}
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Accept":       "text/x-component",
        "Origin":       "https://taostats.io",
        "Referer":      "https://taostats.io/subnets",
    }

    resp = scraper.post(SUBNETS_URL, data=json.dumps(payload), headers=headers, timeout=30)
    resp.raise_for_status()
    text = resp.text

    # для отладки, если вдруг регэксп сломается
    with open("debug_rsc_response.txt", "w", encoding="utf-8") as f:
        f.write(text[:200000])  # первые 200к символов

    # выдираем массив subnets из RSC-текста
    m = re.search(r'"subnets"\s*:\s*(\[\s*\{.+?\}\s*\])', text, re.S)
    if not m:
        raise RuntimeError("Не удалось найти массив subnets в RSC-ответе (см. debug_rsc_response.txt)")
    return json.loads(m.group(1))

def transform(s: dict) -> dict:
    # нормализуем имена полей
    return {
        "netuid":           s.get("netuid"),
        "name":             s.get("name") or s.get("subnet_name"),
        "registration_date":s.get("registration_timestamp") or s.get("timestamp"),
        "price":            s.get("price"),
        "emission":         s.get("emission"),
        "registration_cost":s.get("registration_cost") or s.get("neuron_registration_cost"),
        "github":           s.get("github") or s.get("github_repo"),
        "discord":          s.get("discord") or s.get("discord_url"),
        "key":              s.get("key") or s.get("subnet_url"),
        "vtrust":           s.get("vTrust") or s.get("vtrust"),
    }

def save_to_sheets(rows):
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
    print(f"✅ Записано {len(rows)} подсетей в лист '{SHEET_NAME}'")

if __name__ == "__main__":
    print("1) Забираем RSC-стрим…")
    subs = fetch_all_subnets()
    print(f"→ Найдено подсетей: {len(subs)}")

    print("2) Трансформируем…")
    rows = [transform(s) for s in subs]

    print("3) Пишем в Google Sheets…")
    save_to_sheets(rows)
    print("Готово.")
