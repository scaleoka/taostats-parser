#!/usr/bin/env python3
import os
import re
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

# ——— Получение всех подсетей через RSC XHR ———
def fetch_all_subnets():
    url = "https://taostats.io/subnets"
    payload = {"page": 1, "limit": -1, "order": "market_cap_desc"}
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "Accept":       "text/x-component",
        # и те же куки/User-Agent, что в браузере, если нужно
    }

    resp = requests.post(url, data=json.dumps(payload), headers=headers, timeout=30)
    resp.raise_for_status()
    text = resp.text

    # выдергиваем подмассив subnets из RSC-стрима
    m = re.search(r'"subnets"\s*:\s*(\[\s*\{.*?\}\s*\])', text, re.S)
    if not m:
        raise RuntimeError("Не удалось найти subnets в RSC-ответе")
    return json.loads(m.group(1))

# ——— Трансформация полей под лист ———
def transform(s):
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

# ——— Запись в Google Sheets ———
def save_to_sheets(rows):
    creds_info = json.loads(GOOGLE_SERVICE_JSON)
    creds = service_account.Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds).spreadsheets()

    headers = list(rows[0].keys())
    values  = [headers] + [[r[h] for h in headers] for r in rows]

    service.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1",
        valueInputOption="RAW",
        body={"values": values}
    ).execute()
    print(f"✅ Записано {len(rows)} подсетей в лист '{SHEET_NAME}'")

# ——— Главный блок ———
if __name__ == "__main__":
    print("1) Получаем все подсети через RSC-XHR…")
    raw = fetch_all_subnets()
    print(f"→ Всего подсетей: {len(raw)}")

    print("2) Трансформируем данные…")
    rows = [transform(s) for s in raw]

    print("3) Записываем в Google Sheets…")
    save_to_sheets(rows)
    print("Готово.")
