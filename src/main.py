#!/usr/bin/env python3
import os
import re
import json
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- Конфиг из окружения ---
PAGE_URL            = "https://taostats.io/subnets"
SPREADSHEET_ID      = os.getenv("SPREADSHEET_ID")
GOOGLE_SERVICE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME          = "taostats stats"

if not SPREADSHEET_ID or not GOOGLE_SERVICE_JSON:
    raise RuntimeError("Не заданы SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON")

def fetch_initial_data(html: str) -> list:
    """
    Ищем массив initialData в HTML и возвращаем его как Python-список.
    """
    pattern = r'initialData"\s*:\s*(\[[\s\S]*?\])'
    m = re.search(pattern, html, re.S)
    if not m:
        raise RuntimeError("Не найден initialData в HTML")
    return json.loads(m.group(1))

def transform(sub: dict) -> dict:
    """
    Приводим поля из raw-объекта к нужному формату:
    netuid, name, registration_date, price, emission, registration_cost,
    github, discord, key, vtrust
    """
    # в raw-объекте поля могут называться по-разному
    return {
        "netuid":             sub.get("netuid"),
        "name":               sub.get("subnet_name") or sub.get("name"),
        "registration_date":  sub.get("registration_timestamp") or sub.get("timestamp"),
        "price":              sub.get("price"),
        "emission":           sub.get("emission"),
        "registration_cost":  sub.get("registration_cost") or sub.get("neuron_registration_cost"),
        "github":             sub.get("github") or sub.get("github_repo"),
        "discord":            sub.get("discord") or sub.get("discord_url"),
        "key":                sub.get("subnet_url") or sub.get("key"),
        "vtrust":             sub.get("vTrust") or sub.get("vtrust")
    }

def save_to_sheets(rows: list):
    """
    Заливаем список словарей в Google Sheets: очищаем лист и пишем заголовок + данные.
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
    print(f"✅ Записано {len(rows)} строк в лист '{SHEET_NAME}'")

if __name__ == "__main__":
    # 1) Скачиваем HTML
    resp = requests.get(PAGE_URL, timeout=30)
    resp.raise_for_status()

    # 2) Извлекаем весь массив подсетей
    raw_list = fetch_initial_data(resp.text)
    print(f"Найдено подсетей в initialData: {len(raw_list)}")  # ожидаем >100

    # 3) Трансформируем и сохраняем
    records = [transform(s) for s in raw_list]
    save_to_sheets(records)
    print("Готово.")
