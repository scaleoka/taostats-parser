#!/usr/bin/env python3

import os
import json
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# 1. Получаем HTML (либо скачиваем, либо читаем сохранённый файл)
with open("предпросмотр subnets.txt", "r", encoding="utf-8") as f:
    html = f.read()

soup = BeautifulSoup(html, "html.parser")

# 2. Ищем все строки таблицы
rows = soup.find_all("div", {"role": "row", "data-rowindex": True})

subnets = []
for row in rows:
    cells = row.find_all("div", {"role": "cell"})
    if len(cells) < 10:
        continue  # пропуск невалидных строк
    subnets.append({
        "netuid": cells[0].get_text(strip=True),
        "name": cells[1].get_text(strip=True),
        "registration_date": cells[2].get_text(strip=True),
        "price": cells[3].get_text(strip=True),
        "emission": cells[4].get_text(strip=True),
        "registration_cost": cells[5].get_text(strip=True),
        "github": cells[6].get_text(strip=True),
        "discord": cells[7].get_text(strip=True),
        "key": cells[8].get_text(strip=True),
        "vtrust": cells[9].get_text(strip=True),
    })

print(f"Найдено подсетей: {len(subnets)}")

# 3. Запись в Google Sheets
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME = "taostats stats"

if not SPREADSHEET_ID or not SERVICE_ACCOUNT_JSON:
    raise RuntimeError(
        "Не заданы переменные окружения: SPREADSHEET_ID и GOOGLE_SERVICE_ACCOUNT_JSON"
    )

creds_info = json.loads(SERVICE_ACCOUNT_JSON)
creds = Credentials.from_service_account_info(
    creds_info,
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
service = build("sheets", "v4", credentials=creds).spreadsheets()

headers = [
    "netuid", "name", "registration_date", "price", "emission",
    "registration_cost", "github", "discord", "key", "vtrust"
]
values = [headers] + [[s[h] for h in headers] for s in subnets]

# Очищаем диапазон
service.values().clear(
    spreadsheetId=SPREADSHEET_ID,
    range=f"'{SHEET_NAME}'!A1:J"
).execute()
# Пишем новые данные
service.values().update(
    spreadsheetId=SPREADSHEET_ID,
    range=f"'{SHEET_NAME}'!A1",
    valueInputOption="RAW",
    body={"values": values}
).execute()

print(f"✅ Записано {len(subnets)} подсетей в лист '{SHEET_NAME}'")
