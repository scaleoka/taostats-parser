#!/usr/bin/env python3
import os
import re
import json
import requests
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# --- 1. Получаем buildId ---
HTML_URL = "https://taostats.io/subnets"
html = requests.get(HTML_URL, timeout=30).text

m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
if not m:
    raise RuntimeError("Не найден <script id=__NEXT_DATA__>")
json_data = json.loads(m.group(1))
build_id = json_data["buildId"]
print(f"buildId: {build_id}")

# --- 2. Качаем JSON c данными подсетей ---
JSON_URL = f"https://taostats.io/_next/data/{build_id}/subnets.json"
data = requests.get(JSON_URL, timeout=30).json()

try:
    subnets = data["pageProps"]["initialData"]["subnets"]
except KeyError:
    raise RuntimeError("Не удалось найти массив подсетей в JSON")

print(f"Найдено подсетей: {len(subnets)}")

# --- 3. Подготовка для Google Sheets ---
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME = "taostats stats"

if not SPREADSHEET_ID or not SERVICE_ACCOUNT_JSON:
    raise RuntimeError("SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON не заданы!")

# Выбери нужные поля (добавь/удали по необходимости)
headers = [
    "netuid", "name", "registration_date", "price", "emission",
    "registration_cost", "github", "discord", "key", "vtrust"
]

# Собираем данные (если нет поля — ставим '-')
values = [headers]
for sn in subnets:
    row = [sn.get(h, "-") for h in headers]
    values.append(row)

# --- 4. Запись в Google Sheets ---
creds_info = json.loads(SERVICE_ACCOUNT_JSON)
creds = Credentials.from_service_account_info(creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
service = build("sheets", "v4", credentials=creds).spreadsheets()

# Очищаем старые данные
service.values().clear(
    spreadsheetId=SPREADSHEET_ID,
    range=f"'{SHEET_NAME}'!A1:J"
).execute()

# Записываем новые
service.values().update(
    spreadsheetId=SPREADSHEET_ID,
    range=f"'{SHEET_NAME}'!A1",
    valueInputOption="RAW",
    body={"values": values}
).execute()

print(f"✅ Записано {len(subnets)} подсетей в лист '{SHEET_NAME}'")
