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

def parse_table_for_metrics(table_html):
    if not table_html:
        return [], [], []
    soup = BeautifulSoup(table_html, "html.parser")
    rows = soup.find_all("tr")[1:]  # skip header
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
        svg_class = [c.lower() for c in svg.get("class", [])]
        # VTRUST (shield)
        if "lucide-shield" in svg_class and "text-indigo-400" in svg_class:
            try:
                vtrust = float(tds[5].get_text(strip=True))
                vtrusts.append(vtrust)
            except Exception:
                continue
        # INCENTIVE ORANGE
        if "lucide-pickaxe" in svg_class:
            style = svg.get("style", "").lower()
            fill = svg.get("fill", "").lower()
            # Два способа (через класс и через style/fill)
            if "text-[#f90]" in svg_class or "#f90" in style or "#f90" in fill:
                try:
                    incentive = float(tds[8].get_text(strip=True))
                    incentive_orange.append(incentive)
                except Exception:
                    continue
            if "text-[#00dbbc]" in svg_class or "#00dbbc" in style or "#00dbbc" in fill:
                try:
                    incentive = float(tds[8].get_text(strip=True))
                    incentive_green.append(incentive)
                except Exception:
                    continue
    return vtrusts, incentive_orange, incentive_green

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

def fetch_all_table_pages(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="load", timeout=120_000)
            page.wait_for_selector("table#taostats-table", timeout=30_000)
        except PlaywrightTimeoutError:
            print(f"  [!] Не дождались таблицы на {url}")
            browser.close()
            return []
        sleep(2)
        table_htmls = []
        page_num = 1
        while True:
            table_el = page.query_selector("table#taostats-table")
            table_html = table_el.evaluate("el => el.outerHTML")
            table_htmls.append(table_html)
            print(f"    Собрана страница {page_num}")
            # Кнопка NEXT
            next_btn = page.query_selector("nav[aria-label='pagination'] >> text=Next")
            if not next_btn:
                break
            # Проверка на disabled
            disabled = next_btn.get_attribute("class")
            aria_disabled = next_btn.get_attribute("aria-disabled")
            style = next_btn.get_attribute("style") or ""
            if (
                ("disabled" in (disabled or "").lower()) or
                ("opacity-40" in (disabled or "")) or
                (aria_disabled == "true") or
                ("pointer-events: none" in style)
            ):
                break
            try:
                next_btn.click()
                page.wait_for_timeout(1300)  # Чуть больше секунды для рендера
                page.wait_for_selector("table#taostats-table", timeout=30_000)
                page_num += 1
            except Exception as e:
                print(f"    [!] Не смогли кликнуть NEXT: {e}")
                break
        browser.close()
        print(f"    Всего страниц: {len(table_htmls)}")
        return table_htmls

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
        table_pages = fetch_all_table_pages(url)
        vtrusts = []
        incentive_orange = []
        incentive_green = []
        for html in table_pages:
            v, io, ig = parse_table_for_metrics(html)
            vtrusts += v
            incentive_orange += io
            incentive_green += ig
        print(f"    Собрано строк: {len(vtrusts) + len(incentive_orange) + len(incentive_green)}")
        vtrust_avg = round(mean(vtrusts), 6) if vtrusts else ""
        orange_max = max(incentive_orange) if incentive_orange else ""
        orange_min = min(incentive_orange) if incentive_orange else ""
        green_max = max(incentive_green) if incentive_green else ""
        green_min = min(incentive_green) if incentive_green else ""
        print(f"  [OK] subnet {netid}: [VTrust avg={vtrust_avg} | Orange max={orange_max} min={orange_min} | Green max={green_max} min={green_min}]")
        result_data.append([vtrust_avg, orange_max, orange_min, green_max, green_min])
    google_sheets_write_metrics(result_data, SERVICE_ACCOUNT_JSON, SPREADSHEET_ID, SHEET_NAME)
    print(f"Записано {len(result_data)-1} строк в Google Sheet '{SHEET_NAME}' начиная с J.")
