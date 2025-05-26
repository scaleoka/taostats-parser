import os
import re
import json
from time import sleep
from statistics import mean
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME = "taostats subnets"

def fetch_html_playwright(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="load", timeout=120_000)
            page.wait_for_selector("table#taostats-table", timeout=30_000)
            sleep(2)  # На всякий случай для рендера содержимого
            table_el = page.query_selector("table#taostats-table")
            table_html = table_el.evaluate("el => el.outerHTML")
        except PlaywrightTimeoutError:
            print(f"  [!] Не дождались таблицы на {url}")
            table_html = ""
        browser.close()
        return table_html

def parse_table_for_metrics(table_html):
    if not table_html:
        return ["", "", "", "", ""]
    soup = BeautifulSoup(table_html, "html.parser")
    rows = soup.find_all("tr")[1:]  # пропускаем заголовок
    vtrusts = []
    incentive_orange = []
    incentive_green = []
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 9:
            continue
        svg = tds[1].find("svg")
        if not svg or "class" not in svg.attrs:
            continue
        svg_class = " ".join(svg.get("class"))
        # VTRUST (щит)
        if "lucide-shield" in svg_class and "text-indigo-400" in svg_class:
            try:
                vtrust = float(tds[5].get_text(strip=True))
                vtrusts.append(vtrust)
            except Exception:
                continue
        # INCENTIVE ORANGE
        if "lucide-pickaxe" in svg_class and "text-[#F90]" in svg_class:
            try:
                incentive = float(tds[8].get_text(strip=True))
                incentive_orange.append(incentive)
            except Exception:
                continue
        # INCENTIVE GREEN
        if "lucide-pickaxe" in svg_class and "text-[#00DBBC]" in svg_class:
            try:
                incentive = float(tds[8].get_text(strip=True))
                incentive_green.append(incentive)
            except Exception:
                continue
    vtrust_avg = round(mean(vtrusts), 6) if vtrusts else ""
    orange_max = max(incentive_orange) if incentive_orange else ""
    orange_min = min(incentive_orange) if incentive_orange else ""
    green_max = max(incentive_green) if incentive_green else ""
    green_min = min(incentive_green) if incentive_green else ""
    return [vtrust_avg, orange_max, orange_min, green_max, green_min]

def google_sheets_write_metrics(data, service_account_json, spreadsheet_id, sheet_name):
    creds = Credentials.from_service_account_info(json.loads(service_account_json))
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()
    sheet.values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!J2",
        valueInputOption="RAW",
        body={"values": data}
    ).execute()

def get_max_subnets(html):
    soup = BeautifulSoup(html, "html.parser")
    info_p = soup.find("p", string=re.compile(r"Showing.*of \d+ entries"))
    if not info_p:
        raise RuntimeError("Не найден элемент с числом подсетей!")
    match = re.search(r"of\s+(\d+)\s+entries", info_p.text)
    if not match:
        raise RuntimeError("Не удалось извлечь число подсетей!")
    return int(match.group(1))

if __name__ == "__main__":
    base_url = "https://taostats.io/subnets"
    print(f"Открываем: {base_url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(base_url, wait_until="load", timeout=120_000)
        sleep(2)
        html = page.content()
        browser.close()
    max_subnets = get_max_subnets(html)
    print(f"Всего подсетей: {max_subnets}")
    result_data = []
    header = ["vtrust_avg", "inc_orange_max", "inc_orange_min", "inc_green_max", "inc_green_min"]
    result_data.append(header)
    for netid in range(max_subnets):
        url = f"https://taostats.io/subnets/{netid}/metagraph?order=type%3Adesc&limit=100"
        print(f"Парсим {url} ...")
        table_html = fetch_html_playwright(url)
        if not table_html:
            print(f"    [!] Не найден <table id='taostats-table'>!")
            result_data.append([""]*5)
            continue
        metrics = parse_table_for_metrics(table_html)
        print(f"  [OK] subnet {netid}: {metrics}")
        result_data.append(metrics)
    google_sheets_write_metrics(result_data, SERVICE_ACCOUNT_JSON, SPREADSHEET_ID, SHEET_NAME)
    print(f"Записано {len(result_data)-1} строк в Google Sheet '{SHEET_NAME}' начиная с J.")
