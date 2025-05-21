import os
import json
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Константы
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME = "taostats stats"

def parse_subnets(html):
    soup = BeautifulSoup(html, "html.parser")
    tbody = soup.find("tbody", class_="space-y-1")
    if not tbody:
        raise RuntimeError("Тело таблицы (tbody) не найдено!")
    rows = tbody.find_all("tr", class_="overflow-hidden")
    result = []
    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 14:
            continue
        netuid = tds[0].get_text(strip=True)
        subnet_cell = tds[1]
        name = subnet_cell.find("p", class_="font-normal text-foreground font-everett text-sm truncate group-hover:underline")
        name = name.get_text(strip=True) if name else ""
        id_span = subnet_cell.find("p", class_="font-normal font-everett text-sm text-[#949494]")
        subnet_id = id_span.get_text(strip=True) if id_span else ""
        emission = tds[2].get_text(strip=True)
        price = tds[3].get_text(strip=True)
        h1 = tds[4].get_text(strip=True)
        h24 = tds[5].get_text(strip=True)
        w1 = tds[6].get_text(strip=True)
        m1 = tds[7].get_text(strip=True)
        market_cap = tds[8].get_text(strip=True)
        vol_24h = tds[9].get_text(strip=True)
        liquidity = tds[10].get_text(strip=True)
        root_prop = tds[11].get_text(strip=True)
        sentiment_cell = tds[13]
        sentiment_val = ""
        sentiment_type = ""
        sentiment_p = sentiment_cell.find("p", class_="text-foreground font-everett text-xs font-medium leading-4")
        if sentiment_p:
            sentiment_val = sentiment_p.get_text(strip=True)
        sentiment_type_p = sentiment_cell.find("p", class_="font-everett text-xs mt-2 w-fit font-medium leading-[13px] text-[#B3B3B3]")
        if sentiment_type_p:
            sentiment_type = sentiment_type_p.get_text(strip=True)
        result.append([
            netuid, subnet_id, name, emission, price, h1, h24, w1, m1,
            market_cap, vol_24h, liquidity, root_prop, sentiment_val, sentiment_type
        ])
    return result

def google_sheets_write(data, service_account_json, spreadsheet_id, sheet_name):
    # Авторизация
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
    # 1. Парсим HTML
    with open("html subnets.txt", "r", encoding="utf-8") as f:
        html = f.read()
    data = parse_subnets(html)

    # 2. Пишем в Google Sheets
    # Добавим шапку
    header = [
        "netuid", "subnet_id", "name", "emission", "price", "1H", "24H", "1W", "1M",
        "market_cap", "vol_24h", "liquidity", "root_prop", "sentiment_val", "sentiment_type"
    ]
    all_data = [header] + data
    google_sheets_write(all_data, SERVICE_ACCOUNT_JSON, SPREADSHEET_ID, SHEET_NAME)
    print(f"Записано {len(data)} строк в Google Sheet '{SHEET_NAME}'.")
