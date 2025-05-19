import os
import re
import json
import time
import requests
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import gspread
from oauth2client.service_account import ServiceAccountCredentials

URL = "https://taostats.io/subnets"
SHEET_NAME = "taostats stats"

def get_service_account_credentials():
    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        raise RuntimeError("Не заданы переменные SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON")
    return json.loads(creds_json)

def write_to_sheets(subnets):
    SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
    if not SPREADSHEET_ID:
        raise RuntimeError("Не заданы переменные SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON")
    creds = get_service_account_credentials()
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    gc = gspread.service_account_from_dict(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_NAME, rows="1000", cols="8")
    ws.clear()
    headers = ["netuid", "subnet_name", "github_repo", "subnet_contact", "subnet_url", "discord", "description", "additional"]
    ws.append_row(headers)
    for s in subnets:
        row = [
            s.get("netuid"),
            s.get("subnet_name"),
            s.get("github_repo"),
            s.get("subnet_contact"),
            s.get("subnet_url"),
            s.get("discord"),
            s.get("description"),
            s.get("additional")
        ]
        ws.append_row(row)
    print(f"✅ Всего записано {len(subnets)} подсетей в лист '{SHEET_NAME}'")

def fetch_all_subnets_via_regex():
    resp = requests.get(URL)
    html = resp.text
    # Ищем первый JSON-массив объектов {...},{...},...
    m = re.search(r'(\[\s*\{.+?\}\s*\])', html, re.DOTALL)
    if not m:
        raise RuntimeError("Не удалось найти JSON-массив подсетей в HTML")
    return json.loads(m.group(1))

def fetch_all_subnets_via_selenium():
    driver = uc.Chrome()
    driver.get(URL)
    wait = WebDriverWait(driver, 15)
    # Ждём, пока появится виртуальный скроллер таблицы
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".MuiDataGrid-virtualScroller")))
    prev_count = 0
    # Скроллим вниз, пока не перестанут появляться новые строки
    while True:
        driver.execute_script("window.scrollBy(0, window.innerHeight);")
        time.sleep(1)
        rows = driver.find_elements(By.CSS_SELECTOR, "div[role='row'][data-rowindex]")
        if len(rows) == prev_count:
            break
        prev_count = len(rows)
    page_src = driver.page_source
    driver.quit()
    m = re.search(r'(\[\s*\{.+?\}\s*\])', page_src, re.DOTALL)
    if not m:
        raise RuntimeError("Не удалось найти JSON-массив подсетей через Selenium")
    return json.loads(m.group(1))

def fetch_all_subnets():
    try:
        return fetch_all_subnets_via_regex()
    except Exception as e:
        print("1) Regex failed:", e)
    try:
        return fetch_all_subnets_via_selenium()
    except Exception as e:
        print("2) Selenium failed:", e)
    raise RuntimeError("Не удалось спарсить подсети любым из методов")

def main():
    subs = fetch_all_subnets()
    write_to_sheets(subs)
    print("Готово.")

if __name__ == "__main__":
    main()
