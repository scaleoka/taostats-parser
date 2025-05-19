import os
import json
import time
import re
import csv
import cloudscraper
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- Конфиг ---
PAGE_URL      = "https://taostats.io/subnets"
SPREADSHEET_ID       = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME           = "taostats stats"

if not SPREADSHEET_ID or not SERVICE_ACCOUNT_JSON:
    raise RuntimeError("Не заданы SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON")

# --- 1) Попытка через __NEXT_DATA__ inline JSON ---
def try_extract_from_html():
    html = cloudscraper.create_scraper().get(PAGE_URL, timeout=30).text
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    data = json.loads(m.group(1))
    subs = data.get("props", {}).get("pageProps", {}).get("subnets")
    return subs

# --- 2) Фолбэк на Selenium + бесконечный скролл ---
def fetch_via_selenium():
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = uc.Chrome(options=options)
    driver.get(PAGE_URL)

    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))

    # бесконечный скролл вниз, пока не перестанут подгружаться новые строки
    prev_count = 0
    while True:
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        count = len(rows)
        if count == prev_count:
            break
        prev_count = count
        driver.execute_script("window.scrollBy(0, document.body.scrollHeight);")
        time.sleep(1)

    data = []
    for row in rows:
        cols = row.find_elements(By.TAG_NAME, "td")
        if len(cols) < 9:
            continue
        netuid = cols[0].text.strip()
        name   = cols[1].text.strip()
        regd   = cols[2].text.strip()
        price  = cols[3].text.strip()
        emis   = cols[4].text.strip()
        rcost  = cols[5].text.strip()
        vtrust = cols[6].text.strip()
        key    = cols[8].text.strip()
        # ссылки
        gh = dc = None
        for a in cols[7].find_elements(By.TAG_NAME, "a"):
            href = a.get_attribute("href")
            if "github.com" in href: gh = href
            if "discord"   in href: dc = href
        data.append({
            "netuid": netuid,
            "name": name,
            "registration_date": regd,
            "price": price,
            "emission": emis,
            "registration_cost": rcost,
            "github": gh,
            "discord": dc,
            "key": key,
            "vtrust": vtrust
        })

    driver.quit()
    return data

# --- 3) Сохраняем в Google Sheets ---
def save_to_sheets(rows):
    info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    svc = build("sheets", "v4", credentials=creds).spreadsheets()

    headers = list(rows[0].keys())
    values  = [headers] + [[r[h] for h in headers] for r in rows]

    svc.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1",
        valueInputOption="RAW",
        body={"values": values}
    ).execute()
    print(f"✅ Записано {len(rows)} строк в лист '{SHEET_NAME}'")

# --- MAIN ---
if __name__ == "__main__":
    print("1) Пробуем взять из inline JSON…")
    subs = try_extract_from_html()
    if subs:
        print(" → Нашли в HTML, подсетей:", len(subs))
    else:
        print(" → Inline JSON не найден, пробуем Selenium…")
        subs = fetch_via_selenium()
        print(" → Selenium собрал подсетей:", len(subs))

    if not subs:
        raise RuntimeError("Не удалось получить ни одного элемента подсетей")

    print("2) Пишем в Google Sheets…")
    save_to_sheets(subs)
    print("Готово.")
