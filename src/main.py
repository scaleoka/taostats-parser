#!/usr/bin/env python3
import os
import re
import json
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Конфиг из окружения
PAGE_URL      = "https://taostats.io/subnets"
SPREADSHEET_ID       = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME           = "taostats stats"

if not SPREADSHEET_ID or not SERVICE_ACCOUNT_JSON:
    raise RuntimeError("Не заданы SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON")

def fetch_build_id(html: str) -> str:
    """Ищем актуальный buildId в HTML страницы."""
    m = re.search(r'"buildId":"([^"]+)"', html)
    if not m:
        raise RuntimeError("Не удалось найти buildId в HTML")  # {{buildId}}:contentReference[oaicite:0]{index=0}:contentReference[oaicite:1]{index=1}
    return m.group(1)

def fetch_all_subnets() -> list:
    """Перебираем страницы Next.js JSON endpoint пока не получим пустой массив."""
    # 1) Получаем HTML и вытягиваем buildId
    resp = requests.get(PAGE_URL, timeout=30)
    resp.raise_for_status()
    build_id = fetch_build_id(resp.text)
    print("Найден buildId:", build_id)

    # 2) Подставляем в URL /_next/data/{buildId}/subnets.json?page={i}
    all_subs = []
    page = 1
    while True:
        url = f"https://taostats.io/_next/data/{build_id}/subnets.json?page={page}"
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            # страницы закончились или endpoint недоступен
            break
        data = r.json()
        subs = data.get("pageProps", {}).get("subnets") or []
        if not subs:
            break
        print(f"Страница {page}: получено {len(subs)} подсетей")
        all_subs.extend(subs)
        page += 1

    return all_subs

def transform(sub: dict) -> dict:
    links = sub.get("links") or {}
    return {
        "netuid":            sub.get("netuid"),
        "name":              sub.get("name"),
        "registration_date": sub.get("registration_timestamp"),
        "price":             sub.get("price"),
        "emission":          sub.get("emission"),
        "registration_cost": sub.get("registration_cost"),
        "github":            links.get("github"),
        "discord":           links.get("discord"),
        "key":               sub.get("key"),
        "vtrust":            sub.get("vTrust"),
    }

def save_to_sheets(rows: list):
    info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds).spreadsheets()
    headers = list(rows[0].keys())
    values  = [headers] + [[row[h] for h in headers] for row in rows]
    service.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1",
        valueInputOption="RAW",
        body={"values": values}
    ).execute()
    print(f"✅ Всего записано {len(rows)} строк в лист '{SHEET_NAME}'")

if __name__ == "__main__":
    print("Собираем все подсети через JSON endpoint…")
    subs = fetch_all_subnets()
    if not subs:
        raise RuntimeError("Не удалось вытянуть ни одной подсети")
    print("Всего подсетей:", len(subs))

    print("Трансформируем и сохраняем в Google Sheets…")
    rows = [transform(s) for s in subs]
    save_to_sheets(rows)
    print("Готово.")
