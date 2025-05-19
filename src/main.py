#!/usr/bin/env python3
import os
import json
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ——— Конфиг из окружения ———
SPREADSHEET_ID       = os.getenv("SPREADSHEET_ID")
GOOGLE_SERVICE_JSON  = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME           = "taostats stats"

if not SPREADSHEET_ID or not GOOGLE_SERVICE_JSON:
    raise RuntimeError("Не заданы SECRETS SPREADSHEET_ID и GOOGLE_SERVICE_ACCOUNT_JSON")

# ——— Забираем все подсети чистым JSON-запросом ———
def fetch_all_subnets():
    url = "https://taostats.io/subnets"
    payload = {"page": 1, "limit": -1, "order": "market_cap_desc"}
    headers = {
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()

    # попробуем сразу распарсить JSON
    try:
        data = resp.json()
    except ValueError:
        # для диагностики — посмотрите, что вернулось:
        with open("debug_subnets_response.txt", "w", encoding="utf-8") as f:
            f.write(resp.text)
        raise RuntimeError(
            "Не удалось распарсить JSON из /subnets — "
            "сохранён debug_subnets_response.txt для разбору."
        )

    subnets = data.get("subnets")
    if subnets is None:
        raise RuntimeError(f"В JSON нет ключа 'subnets'. Полный ответ сохранён в debug_subnets_response.txt.")
    return subnets

# ——— Трансформируем под колонки Google Sheets ———
def transform(s: dict) -> dict:
    return {
        "netuid":            s.get("netuid"),
        "name":              s.get("name") or s.get("subnet_name"),
        "registration_date": s.get("registration_timestamp") or s.get("timestamp"),
        "price":             s.get("price"),
        "emission":          s.get("emission"),
        "registration_cost": s.get("registration_cost") or s.get("neuron_registration_cost"),
        "github":            s.get("github") or s.get("github_repo"),
        "discord":           s.get("discord") or s.get("discord_url"),
        "key":               s.get("key") or s.get("subnet_url"),
        "vtrust":            s.get("vTrust") or s.get("vtrust"),
    }

# ——— Заливаем результат в Google Sheets ———
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

# ——— Точка входа ———
if __name__ == "__main__":
    print("1) Получаем все подсети через JSON-API…")
    all_subnets = fetch_all_subnets()
    print(f"→ Найдено подсетей: {len(all_subnets)}")

    print("2) Трансформируем данные…")
    rows = [transform(s) for s in all_subnets]

    print("3) Записываем в Google Sheets…")
    save_to_sheets(rows)
    print("Готово.")
