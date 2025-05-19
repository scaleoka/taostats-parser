#!/usr/bin/env python3
import os
import re
import json
import cloudscraper
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ——— Конфиг через ENV ———
URL                = "https://taostats.io/subnets"
SPREADSHEET_ID     = os.getenv("SPREADSHEET_ID")
GOOGLE_SERVICE_JSON= os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME         = "taostats stats"

if not SPREADSHEET_ID or not GOOGLE_SERVICE_JSON:
    raise RuntimeError("Не заданы SECRETS SPREADSHEET_ID и GOOGLE_SERVICE_ACCOUNT_JSON")

def fetch_subnets():
    """
    Скачивает HTML и через regexp вытягивает массив подсетей 
    из вызова self.__next_f.push([... , jsonArray ])
    """
    scraper = cloudscraper.create_scraper()
    html = scraper.get(URL, timeout=30).text

    # Ищем вызов self.__next_f.push и захватываем третий параметр — JSON-массив
    m = re.search(
        r'self\.__next_f\.push\(\[1,"[^"]+",\s*(\[\{.+?\}\])\)',
        html,
        re.S
    )
    if not m:
        raise RuntimeError("Не удалось найти JSON-массив подсетей в HTML")
    return json.loads(m.group(1))

def transform(sub):
    """
    Приводим поля объекта к вашим колонкам:
    netuid, name, registration_date, price, emission,
    registration_cost, github, discord, key, vtrust
    """
    return {
        "netuid":            sub.get("netuid"),
        "name":              sub.get("name") or sub.get("subnet_name"),
        "registration_date": sub.get("registration_timestamp") or sub.get("timestamp"),
        "price":             sub.get("price"),
        "emission":          sub.get("emission"),
        "registration_cost": sub.get("registration_cost") or sub.get("neuron_registration_cost"),
        "github":            sub.get("github") or sub.get("github_repo"),
        "discord":           sub.get("discord") or sub.get("discord_url"),
        "key":               sub.get("subnet_url") or sub.get("key"),
        "vtrust":            sub.get("vTrust") or sub.get("vtrust"),
    }

def save_to_sheets(rows):
    """
    Заливаем список словарей rows в Google Sheets:
    очищаем лист и пишем заголовок + данные.
    """
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

if __name__ == "__main__":
    print("1) Парсим HTML и извлекаем все подсети…")
    subs = fetch_subnets()
    print(f"→ Найдено подсетей: {len(subs)}")

    print("2) Трансформируем данные…")
    rows = [transform(s) for s in subs]

    print("3) Сохраняем в Google Sheets…")
    save_to_sheets(rows)
    print("Готово.")
