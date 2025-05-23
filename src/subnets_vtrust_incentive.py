import os
import re
import json
from time import sleep
from statistics import mean
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME = "taostats subnets"

def fetch_html_playwright(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="load", timeout=120000)
        sleep(2)
        html = page.content()
        browser.close()
        return html

def get_max_subnets(html):
    soup = BeautifulSoup(html, "html.parser")
    info_p = soup.find("p", string=re.compile(r"Showing.*of \d+ entries"))
    if not info_p:
        raise RuntimeError("Не найден элемент с числом подсетей!")
    match = re.search(r"of\s+(\d+)\s+entries", info_p.text)
    if not match:
        raise RuntimeError("Не удалось извлечь число подсетей!")
    return int(match.group(1))

def collect_table_html_with_pagination(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="load", timeout=120000)
        sleep(2)
        all_rows = []
        while True:
            # Сбор данных до клика!
            soup = BeautifulSoup(page.content(), "html.parser")
            table = soup.find("div", {"class": re.compile(r"overflow-x-auto")})
            if table:
                rows = table.find_all("div", {"class": re.compile(r"table-row")})
                all_rows.extend(rows)
            # Проверяем, можно ли кликнуть NEXT
            next_btn = page.query_selector('nav[aria-label="pagination"] >> a[aria-label="Go to next page"]')
            if not next_btn:
                break
            next_class = next_btn.get_attribute("class") or ""
            # Если есть disabled или нет !opacity-100 — считаем кнопку неактивной
            if "disabled" in next_class or "!opacity-100" not in next_class:
                break
            next_btn.click()
            sleep(1.2)
        browser.close()
        rows_html = [str(row) for row in all_rows]
        return rows_html

def parse_metrics(rows_html):
    vtrust_list = []
    inc_orange = []
    inc_green = []
    for row_html in rows_html:
        soup = BeautifulSoup(row_html, "html.parser")
        cells = soup.find_all("div", {"class": re.compile(r"table-cell")})
        # Поиск по иконке
        icon_div = soup.find("svg", {"class": re.compile(r"lucide-shield")})
        if icon_div:
            idx = [i for i, c in enumerate(cells) if c.find("svg", {"class": re.compile(r"lucide-shield")})]
            if idx:
                v_idx = idx[0] + 4
                if v_idx < len(cells):
                    try:
                        val = float(cells[v_idx].get_text(strip=True))
                        vtrust_list.append(val)
                    except: pass
        # Оранжевая кирка
        icon_pickaxe_orange = soup.find("svg", {"class": re.compile(r"lucide-pickaxe.*text-\\#F90")})
        if icon_pickaxe_orange:
            idx = [i for i, c in enumerate(cells) if c.find("svg", {"class": re.compile(r"lucide-pickaxe.*text-\\#F90")})]
            if idx:
                inc_idx = idx[0] + 6
                if inc_idx < len(cells):
                    try:
                        val = float(cells[inc_idx].get_text(strip=True))
                        inc_orange.append(val)
                    except: pass
        # Зелёная кирка
        icon_pickaxe_green = soup.find("svg", {"class": re.compile(r"lucide-pickaxe.*text-\\#00DBBC")})
        if icon_pickaxe_green:
            idx = [i for i, c in enumerate(cells) if c.find("svg", {"class": re.compile(r"lucide-pickaxe.*text-\\#00DBBC")})]
            if idx:
                inc_idx = idx[0] + 6
                if inc_idx < len(cells):
                    try:
                        val = float(cells[inc_idx].get_text(strip=True))
                        inc_green.append(val)
                    except: pass
    vtrust_avg = round(mean(vtrust_list), 6) if vtrust_list else ""
    inc_orange_max = max(inc_orange) if inc_orange else ""
    inc_orange_min = min(inc_orange) if inc_orange else ""
    inc_green_max = max(inc_green) if inc_green else ""
    inc_green_min = min(inc_green) if inc_green else ""
    return [vtrust_avg, inc_orange_max, inc_orange_min, inc_green_max, inc_green_min]

def google_sheets_write_metrics(data, service_account_json, spreadsheet_id, sheet_name):
    creds = Credentials.from_service_account_info(json.loads(service_account_json))
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()
    # Начинаем с J2
    sheet.values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!J2",
        valueInputOption="RAW",
        body={"values": data}
    ).execute()

if __name__ == "__main__":
    base_url = "https://taostats.io/subnets"
    html = fetch_html_playwright(base_url)
    max_subnets = get_max_subnets(html)
    print(f"Всего подсетей: {max_subnets}")
    result_data = []
    header = ["vtrust_avg", "inc_orange_max", "inc_orange_min", "inc_green_max", "inc_green_min"]
    result_data.append(header)
    for netid in range(max_subnets):
        url = f"https://taostats.io/subnets/{netid}/metagraph?order=stake%3Adesc&limit=100"
        print(f"Парсим {url} ...")
        try:
            rows_html = collect_table_html_with_pagination(url)
            metrics = parse_metrics(rows_html)
            result_data.append(metrics)
            print(f"  subnet {netid}: {metrics}")
        except Exception as e:
            print(f"Ошибка при парсинге {url}: {e}")
            result_data.append(["ERROR"] * len(header))
    google_sheets_write_metrics(result_data, SERVICE_ACCOUNT_JSON, SPREADSHEET_ID, SHEET_NAME)
    print(f"Записано {len(result_data)-1} строк в Google Sheet '{SHEET_NAME}' начиная с J.")
