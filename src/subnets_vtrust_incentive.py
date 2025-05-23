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
        all_html = ""
        page_num = 1
        while True:
            print(f"  Получаем таблицу (страница {page_num})…")
            soup = BeautifulSoup(page.content(), "html.parser")
            table = soup.find("table")
            if table:
                all_html += str(table)
            else:
                print("    [!] Не найдена <table> на странице!")
            # Проверяем, можно ли кликнуть NEXT
            next_btn = page.query_selector('nav[aria-label="pagination"] >> a[aria-label="Go to next page"]')
            if not next_btn:
                print("    [!] Кнопка Next не найдена — конец.")
                break
            next_class = next_btn.get_attribute("class") or ""
            if "disabled" in next_class or "!opacity-100" not in next_class:
                print("    [!] Кнопка Next неактивна — конец.")
                break
            next_btn.click()
            page_num += 1
            sleep(1.2)
        browser.close()
        return all_html

def parse_metrics(table_html):
    vtrust_list = []
    inc_orange = []
    inc_green = []
    soup = BeautifulSoup(table_html, "html.parser")
    rows = soup.find_all('tr')
    print(f"    Всего строк <tr>: {len(rows)}")
    for idx, row in enumerate(rows):
        tds = row.find_all('td')
        if len(tds) < 9:
            print(f"    [{idx}] Пропущена строка: ячеек {len(tds)} (нужно >=9)")
            continue
        type_td = tds[1]
        icon_svg = type_td.find('svg')
        if not icon_svg or not icon_svg.has_attr('class'):
            print(f"    [{idx}] Нет SVG-иконки во 2-м столбце.")
            continue
        classes = ' '.join(icon_svg['class']) if isinstance(icon_svg['class'], list) else icon_svg['class']
        print(f"    [{idx}] SVG классы: {classes}")
        # Щит
        if 'lucide-shield' in classes:
            val_raw = tds[5].get_text(strip=True)
            try:
                vtrust_val = float(val_raw)
                vtrust_list.append(vtrust_val)
                print(f"      [Щит] VTrust={vtrust_val}")
            except:
                print(f"      [Щит] Не удалось преобразовать '{val_raw}' в число")
        # Оранжевая кирка
        elif 'lucide-pickaxe' in classes and 'text-[#F90]' in classes:
            val_raw = tds[8].get_text(strip=True)
            try:
                incentive_val = float(val_raw)
                inc_orange.append(incentive_val)
                print(f"      [Pickaxe-Orange] Incentive={incentive_val}")
            except:
                print(f"      [Pickaxe-Orange] Не удалось преобразовать '{val_raw}' в число")
        # Зеленая кирка
        elif 'lucide-pickaxe' in classes and 'text-[#00DBBC]' in classes:
            val_raw = tds[8].get_text(strip=True)
            try:
                incentive_val = float(val_raw)
                inc_green.append(incentive_val)
                print(f"      [Pickaxe-Green] Incentive={incentive_val}")
            except:
                print(f"      [Pickaxe-Green] Не удалось преобразовать '{val_raw}' в число")
        else:
            print(f"    [{idx}] Неизвестная SVG иконка (классы: {classes})")
    vtrust_avg = round(mean(vtrust_list), 6) if vtrust_list else ""
    inc_orange_max = max(inc_orange) if inc_orange else ""
    inc_orange_min = min(inc_orange) if inc_orange else ""
    inc_green_max = max(inc_green) if inc_green else ""
    inc_green_min = min(inc_green) if inc_green else ""
    print(f"    [Итоги] VTrust avg={vtrust_avg} | Orange max={inc_orange_max} min={inc_orange_min} | Green max={inc_green_max} min={inc_green_min}")
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
            table_html = collect_table_html_with_pagination(url)
            metrics = parse_metrics(table_html)
            result_data.append(metrics)
            print(f"  subnet {netid}: {metrics}")
        except Exception as e:
            print(f"Ошибка при парсинге {url}: {e}")
            result_data.append(["ERROR"] * len(header))
    google_sheets_write_metrics(result_data, SERVICE_ACCOUNT_JSON, SPREADSHEET_ID, SHEET_NAME)
    print(f"Записано {len(result_data)-1} строк в Google Sheet '{SHEET_NAME}' начиная с J.")
