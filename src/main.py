import os
import requests
from bs4 import BeautifulSoup
import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Google Sheets параметры
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME = "taostats stats"

def fetch_html(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    return resp.text

def parse_table(html):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", {"id": "taostats-table"})
    if not table:
        raise RuntimeError("Не найдена таблица с id='taostats-table'")
    rows = table.find("tbody").find_all("tr")
    data = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 14:
            continue  # Пропускаем невалидные строки
        # Название и ID в одном TD
        subnet_name = subnet_id = ""
        p_tags = cols[1].find_all("p")
        if len(p_tags) >= 2:
            subnet_name = p_tags[0].get_text(strip=True)
            subnet_id = p_tags[1].get_text(strip=True)
        else:
            subnet_name = cols[1].get_text(strip=True)
        row_data = [
            cols[0].get_text(strip=True),  # порядковый номер
            subnet_id,
            subnet_name,
            cols[2].get_text(strip=True),  # emission
            cols[3].get_text(strip=True),  # price
            cols[4].get_text(strip=True),  # 1H
            cols[5].get_text(strip=True),  # 24H
            cols[6].get_text(strip=True),  # 1W
            cols[7].get_text(strip=True),  # 1M
            cols[8].get_text(strip=True),  # market cap
            cols[9].get_text(strip=True),  # vol 24h
            cols[10].get_text(strip=True), # liquidity
            cols[11].get_text(strip=True), # root prop
            cols[12].get_text(strip=True), # last 7d
            cols[13].get_text(strip=True), # sentiment
        ]
        data.append(row_data)
    return data

def google_sheets_write(data, service_account_json, spreadsheet_id, sheet_name):
    creds = Credentials.from_service_account_info(json.loads(service_account_json))
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()

    # Очищаем старые данные (кроме заголовков)
    sheet.values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A2:Z"
    ).execute()

    # Пишем новые данные
    body = {"values": data}
    sheet.values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A2",
        valueInputOption="RAW",
        body=body
    ).execute()

if __name__ == "__main__":
    url = "https://taostats.io/subnets"
    html = fetch_html(url)
    data = parse_table(html)

    header = [
        "№", "subnet_id", "subnet_name", "emission", "price", "1H", "24H", "1W", "1M",
        "market_cap", "vol_24h", "liquidity", "root_prop", "last_7d", "sentiment"
    ]
    all_data = [header] + data
    google_sheets_write(all_data, SERVICE_ACCOUNT_JSON, SPREADSHEET_ID, SHEET_NAME)
    print(f"Записано {len(data)} строк в Google Sheet '{SHEET_NAME}'.")
