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

def fetch_all_table_pages(url):
    """Возвращает список table_html со всех страниц таблицы (по NEXT)."""
    table_htmls = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="load", timeout=120_000)
            page.wait_for_selector("table#taostats-table", timeout=30_000)
            sleep(1.2)
        except PlaywrightTimeoutError:
            print(f"  [!] Не дождались первой таблицы на {url}")
            browser.close()
            return []
        page_num = 1
        while True:
            try:
                table_el = page.query_selector("table#taostats-table")
                if not table_el:
                    print(f"    [!] Не нашли таблицу на странице {page_num}")
                    break
                table_html = table_el.evaluate("el => el.outerHTML")
                table_htmls.append(table_html)
                print(f"    Собрана страница {page_num}")
            except Exception as e:
                print(f"    [!] Ошибка при получении таблицы: {e}")
                break

            # Проверка NEXT (всегда есть, но не всегда активна)
            next_btn = page.query_selector("nav[aria-label='pagination'] >> text=Next")
            if not next_btn:
                print("    [!] Нет кнопки Next")
                break
            classes = next_btn.get_attribute("class") or ""
            aria_disabled = next_btn.get_attribute("aria-disabled")
            style = next_btn.get_attribute("style") or ""
            # Неактивна если явно disabled, opacity-40, aria-disabled, pointer-events: none
            if (
                "disabled" in classes.lower()
                or "opacity-40" in classes.lower()
                or aria_disabled == "true"
                or "pointer-events: none" in style
            ):
                break

            try:
                next_btn.click()
                page.wait_for_timeout(1500)
                page.wait_for_selector("table#taostats-table", timeout=30_000)
                page_num += 1
            except Exception as e:
                print(f"    [!] Не смогли кликнуть NEXT: {e}")
                break

        browser.close()
    print(f"    Всего страниц: {len(table_htmls)}")
    return table_htmls

def parse_tables_for_metrics(table_htmls):
    # Собираем все <tr> со всех страниц в один список
    all_rows = []
    for html in table_htmls:
        soup = BeautifulSoup(html, "html.parser")
        trs = soup.find_all("tr")[1:]  # пропускаем заголовок
        all_rows.extend(trs)

    vtrusts = []
    incentive_orange = []
    incentive_green = []
    for tr in all_rows:
        tds = tr.find_all("td")
        if len(tds) < 9:
            continue
        svg = tds[1].find("svg")
        if not svg or "class" not in svg.attrs:
            continue
        svg_class = [c.lower() for c in svg.get("class", [])]
        # VTRUST (щит)
        if "lucide-shield" in svg_class and "text-indigo-400" in svg_class:
            try:
                vtrust = float(tds[5].get_text(strip=True))
                vtrusts.append(vtrust)
            except Exception:
                continue
        # INCENTIVE ORANGE
        if "lucide-pickaxe" in svg_class and "text-[#f90]" in svg_class:
            try:
                incentive = float(tds[8].get_text(strip=True))
                incentive_orange.append(incentive)
            except Exception:
                continue
        # INCENTIVE GREEN
        if "lucide-pickaxe" in svg_class and "text-[#00dbbc]" in svg_class:
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
    print(f"      Строк: {len(all_rows)}, VTrust count: {len(vtrusts)}, Orange: {len(incentive_orange)}, Green: {len(incentive_green)}")
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
        table_htmls = fetch_all_table_pages(url)
        if not table_htmls:
            print(f"    [!] Нет таблиц по подсети {netid}!")
            result_data.append([""]*5)
            continue
        metrics = parse_tables_for_metrics(table_htmls)
        print(f"  [OK] subnet {netid}: {metrics}")
        result_data.append(metrics)
    google_sheets_write_metrics(result_data, SERVICE_ACCOUNT_JSON, SPREADSHEET_ID, SHEET_NAME)
    print(f"Записано {len(result_data)-1} строк в Google Sheet '{SHEET_NAME}' начиная с J.")
