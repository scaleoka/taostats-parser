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

def fetch_all_table_htmls_with_pagination(url):
    # Возвращает список HTML-таблиц с каждой страницы пагинации
    tables_html = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="load", timeout=120000)
        page_num = 1
        while True:
            print(f"  Получаем таблицу (страница {page_num})…")
            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            table_div = soup.find("div", class_="w-full overflow-x-auto")
            if table_div:
                tables_html.append(str(table_div))
            else:
                print("    [!] Не найден <div class='w-full overflow-x-auto'>!")
                break
            # Ищем Next — если disabled, pointer-events-none или opacity-50 — выходим
            next_btn = page.query_selector('nav[aria-label="pagination"] >> text=Next')
            if not next_btn:
                print("    [!] Нет кнопки Next — конец.")
                break
            next_btn_classes = next_btn.get_attribute("class") or ""
            if any(d in next_btn_classes for d in ["disabled", "pointer-events-none", "opacity-50"]):
                print("    [!] Кнопка Next неактивна — конец.")
                break
            try:
                next_btn.click()
                sleep(1.2)
                page_num += 1
            except Exception as ex:
                print(f"    [!] Не удалось кликнуть Next: {ex}")
                break
        browser.close()
    return tables_html

def parse_metrics_from_tables(table_html_list):
    vtrust_vals = []
    inc_orange_vals = []
    inc_green_vals = []

    for table_html in table_html_list:
        soup = BeautifulSoup(table_html, "html.parser")
        rows = soup.find_all("tr")
        for i, row in enumerate(rows):
            tds = row.find_all("td")
            if len(tds) < 10:
                continue
            # 2-ой столбец: Type (ищем иконку)
            type_td = tds[1]
            # VTrust (щит)
            shield = type_td.find("svg", class_="lucide lucide-shield text-indigo-400")
            if shield:
                try:
                    vtrust_val = float(tds[5].get_text(strip=True))
                    vtrust_vals.append(vtrust_val)
                except Exception: pass
            # Orange Pickaxe
            pickaxe_orange = type_td.find("svg", class_="lucide lucide-pickaxe text-[#F90]")
            if pickaxe_orange:
                try:
                    incentive_val = float(tds[8].get_text(strip=True))
                    inc_orange_vals.append(incentive_val)
                except Exception: pass
            # Green Pickaxe
            pickaxe_green = type_td.find("svg", class_="lucide lucide-pickaxe text-[#00DBBC]")
            if pickaxe_green:
                try:
                    incentive_val = float(tds[8].get_text(strip=True))
                    inc_green_vals.append(incentive_val)
                except Exception: pass
    # Метрики
    vtrust_avg = round(mean(vtrust_vals), 6) if vtrust_vals else ""
    inc_orange_max = max(inc_orange_vals) if inc_orange_vals else ""
    inc_orange_min = min(inc_orange_vals) if inc_orange_vals else ""
    inc_green_max = max(inc_green_vals) if inc_green_vals else ""
    inc_green_min = min(inc_green_vals) if inc_green_vals else ""
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

def get_max_subnets(html):
    soup = BeautifulSoup(html, "html.parser")
    info_p = soup.find("p", string=re.compile(r"Showing.*of \d+ entries"))
    if not info_p:
        raise RuntimeError("Не найден элемент с числом подсетей!")
    match = re.search(r"of\s+(\d+)\s+entries", info_p.text)
    if not match:
        raise RuntimeError("Не удалось извлечь число подсетей!")
    return int(match.group(1))

def fetch_html_playwright(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="load", timeout=120000)
        sleep(2)
        html = page.content()
        browser.close()
        return html

if __name__ == "__main__":
    base_url = "https://taostats.io/subnets"
    print(f"Открываем: {base_url}")
    html = fetch_html_playwright(base_url)
    max_subnets = get_max_subnets(html)
    print(f"Всего подсетей: {max_subnets}")
    result_data = []
    header = ["vtrust_avg", "inc_orange_max", "inc_orange_min", "inc_green_max", "inc_green_min"]
    result_data.append(header)
    for netid in range(max_subnets):
        url = f"https://taostats.io/subnets/{netid}/metagraph?order=type%3Adesc&limit=100"
        print(f"Парсим {url} ...")
        try:
            tables_html = fetch_all_table_htmls_with_pagination(url)
            metrics = parse_metrics_from_tables(tables_html)
            print(f"  [OK] subnet {netid}: {metrics}")
            result_data.append(metrics)
        except Exception as e:
            print(f"Ошибка при парсинге {url}: {e}")
            result_data.append(["ERROR"] * len(header))
    google_sheets_write_metrics(result_data, SERVICE_ACCOUNT_JSON, SPREADSHEET_ID, SHEET_NAME)
    print(f"Записано {len(result_data)-1} строк в Google Sheet '{SHEET_NAME}' начиная с J.")
