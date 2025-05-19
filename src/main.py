import os
import re
import json
import time
import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Конфиг
PAGE_URL      = "https://taostats.io/subnets"
SPREADSHEET_ID       = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME           = "taostats stats"

if not SPREADSHEET_ID or not SERVICE_ACCOUNT_JSON:
    raise RuntimeError("Не заданы SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON")

def fetch_build_id(html: str) -> str:
    m = re.search(r'"buildId":"([^"]+)"', html)
    if not m:
        raise RuntimeError("Не найден buildId в HTML")
    return m.group(1)

def try_json_endpoint() -> list:
    html     = requests.get(PAGE_URL, timeout=30).text
    build_id = fetch_build_id(html)
    url      = f"https://taostats.io/_next/data/{build_id}/subnets.json"
    resp     = requests.get(url, timeout=30)
    resp.raise_for_status()
    data     = resp.json()
    return data["pageProps"]["subnets"]

def fetch_with_selenium() -> list:
    opt = uc.ChromeOptions()
    opt.add_argument("--headless=new")
    opt.add_argument("--no-sandbox")
    opt.add_argument("--disable-dev-shm-usage")

    driver = uc.Chrome(options=opt)
    driver.get(PAGE_URL)
    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))

    # переключаем Show X → All
    select = Select(driver.find_element(By.CSS_SELECTOR, ".dataTables_length select"))
    for o in select.options:
        if "all" in o.text.lower():
            select.select_by_visible_text(o.text)
            break
    time.sleep(2)

    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
    out = []
    for r in rows:
        cols = r.find_elements(By.TAG_NAME, "td")
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
        gh = dc = None
        for a in cols[7].find_elements(By.TAG_NAME, "a"):
            href = a.get_attribute("href")
            if "github.com" in href: gh = href
            if "discord"   in href: dc = href
        out.append({
            "netuid": netuid, "name": name, "registration_date": regd,
            "price": price, "emission": emis, "registration_cost": rcost,
            "github": gh, "discord": dc, "key": key, "vtrust": vtrust
        })
    driver.quit()
    return out

def save_to_sheets(rows: list):
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

if __name__ == "__main__":
    try:
        print("Попытка через _next/data JSON…")
        subs = try_json_endpoint()
        print(" → JSON endpoint сработал, подсетей:", len(subs))
    except Exception as e:
        print("JSON endpoint упал:", e)
        print("Переключаемся на Selenium…")
        subs = fetch_with_selenium()
        print(" → Selenium собрал подсетей:", len(subs))

    save_to_sheets(subs)
    print("Готово.")
