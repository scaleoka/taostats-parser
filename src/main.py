#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# --- Настройки ---
URL = "https://taostats.io/subnets"
SHEET_NAME = "taostats stats"
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")

if not SPREADSHEET_ID or not SERVICE_ACCOUNT_JSON:
    raise RuntimeError("Не заданы обязательные переменные окружения: SPREADSHEET_ID и GOOGLE_SERVICE_ACCOUNT_JSON")

def fetch_html():
    print("Скачиваем страницу…")
    resp = requests.get(URL)
    resp.raise_for_status()
    return resp.text

def parse_subnets(html):
    print("Парсим данные из HTML…")
    soup = BeautifulSoup(html, "html.parser")

    # Поиск таблицы по CSS-селектору
    table = soup.find("table")
    if not table:
        raise RuntimeError("Не найдена таблица с подсетями!")

    # Заголовки столбцов
    headers = []
    for th in table.find_all("th"):
        headers.append(th.text.strip().lower().replace(" ", "_"))

    # Получаем строки
    subnets = []
    for tr in table.find_all("tr")[1:]:  # пропускаем header
        tds = tr.find_all("td")
        if len(tds) < 10:
            continue
        # Сбор данных по нужным столбцам
        subnet = {
            "netuid":            tds[0].text.strip(),
            "name":              tds[1].text.strip(),
            "registration_date": tds[2].text.strip(),
            "price":             tds[3].text.strip(),
            "emission":          tds[4].text.strip(),
            "registration_cost": tds[5].text.strip(),
            "github":            tds[6].text.strip(),
            "discord":           tds[7].text.strip(),
            "key":               tds[8].text.strip(),
            "vtrust":            tds[9].text.strip(),
        }
        subnets.append(subnet)
    print(f"Найдено подсетей: {len(subnets)}")
    return subnets

def write_to_sheets(subnets):
    print("Записываем в Google Sheets…")
    creds_info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds).spreadsheets()

    headers = ["netuid", "name", "registration_date", "price", "emission",
               "registration_cost", "github", "discord", "key", "vtrust"]
    values = [headers] + [[subnet.get(h, "-") for h in headers] for subnet in subnets]

    # Очищаем старое содержимое
    service.values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1:J"
    ).execute()

    # Записываем новые данные
    service.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1",
        valueInputOption="RAW",
        body={"values": values}
    ).execute()
    print(f"✅ Записано {len(subnets)} подсетей в лист '{SHEET_NAME}'")

def main():
    html = fetch_html()
    subnets = parse_subnets(html)
    write_to_sheets(subnets)
    print("Готово.")

if __name__ == "__main__":
    main()
