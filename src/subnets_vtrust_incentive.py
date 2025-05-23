import os
import re
import json
from time import sleep
from statistics import mean
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME = "taostats subnets"

# Человеческий профиль браузера
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

def fetch_html_playwright(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="en-US"
        )
        page = context.new_page()
        print(f"  Открываем: {url}")
        page.goto(url, wait_until="load", timeout=120_000)
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
    """Собирает ВСЕ строки <tr> со всех страниц таблицы с поддержкой Next"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="en-US"
        )
        page = context.new_page()
        page.goto(url, wait_until="load", timeout=120_000)
        sleep(2)
        all_tr = []
        page_number = 1
        while True:
            print(f"  Получаем таблицу (страница {page_number})…")
            soup = BeautifulSoup(page.content(), "html.parser")
            # Поиск таблицы (именно <table>!)
            table = soup.find("table")
            if not table:
                print("    [!] Не найдена <table> на странице!")
                break
            trs = table.find_all("tr")
            print(f"    Всего строк <tr>: {len(trs)}")
            all_tr.extend(trs)
            # Поиск кнопки Next (она всегда внизу, aria-label="Go to next page")
            next_btn = page.query_selector('nav[aria-label="pagination"] >> a[aria-label="Go to next page"]:not(.pointer-events-none)')
            if not next_btn:
                print("    [!] Кнопка Next неактивна — конец.")
                break
            next_btn.click()
            sleep(2)
            page_number += 1
        browser.close()
        return all_tr

def parse_metrics(tr_list):
    vtrust_vals = []
    inc_orange = []
    inc_green = []
    for idx, tr in enumerate(tr_list):
        tds = tr.find_all("td")
        if len(tds) < 9:
            print(f"    [{idx}] Пропущена строка: ячеек {len(tds)} (нужно >=9)")
            continue
        # 2-ая td: иконка типа
        icon_td = tds[1]
        svg = icon_td.find("svg")
        # VTrust — если щит
        if svg and "lucide-shield" in (svg.get("class") or ""):
            try:
                val = float(tds[5].get_text(strip=True))
                vtrust_vals.append(val)
                print(f"    [{idx}] Щит VTrust = {val}")
            except Exception as e:
                print(f"    [{idx}] Щит, ошибка VTrust: {e}")
        # Оранжевая кирка
        if svg and "lucide-pickaxe" in (svg.get("class") or "") and "text-[#F90]" in (svg.get("class") or ""):
            try:
                val = float(tds[9].get_text(strip=True))
                inc_orange.append(val)
                print(f"    [{idx}] Оранжевая кирка Incentive = {val}")
            except Exception as e:
                print(f"    [{idx}] Оранжевая кирка, ошибка: {e}")
        # Зеленая кирка
        if svg and "lucide-pickaxe" in (svg.get("class") or "") and "text-[#00DBBC]" in (svg.get("class") or ""):
            try:
                val = float(tds[9].get_text(strip=True))
                inc_green.append(val)
                print(f"    [{idx}] Зеленая кирка Incentive = {val}")
            except Exception as e:
                print(f"    [{idx}] Зеленая кирка, ошибка: {e}")
    # Итоги
    vtrust_avg = round(mean(vtrust_vals), 6) if vtrust_vals else ""
    orange_max = max(inc_orange) if inc_orange else ""
    orange_min = min(inc_orange) if inc_orange else ""
    green_max = max(inc_green) if inc_green else ""
    green_min = min(inc_green) if inc_green else ""
    print(f"    [Итоги] VTrust avg={vtrust_avg} | Orange max={orange_max} min={orange_min} | Green max={green_max} min={green_min}")
    return [vtrust_avg, orange_max, orange_min, green_max, green_min]

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
            tr_list = collect_table_html_with_pagination(url)
            metrics = parse_metrics(tr_list)
            result_data.append(metrics)
            print(f"  subnet {netid}: {metrics}")
        except Exception as e:
            print(f"Ошибка при парсинге {url}: {e}")
            result_data.append(["ERROR"] * len(header))
    google_sheets_write_metrics(result_data, SERVICE_ACCOUNT_JSON, SPREADSHEET_ID, SHEET_NAME)
    print(f"Записано {len(result_data)-1} строк в Google Sheet '{SHEET_NAME}' начиная с J.")
