#!/usr/bin/env python3
# main.py — TaoStats Subnets Parser

import os
import json
import time

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import gspread
from google.oauth2.service_account import Credentials

# --- Selenium-парсер всех подсетей с виртуальным скроллом ---
def fetch_all_subnets(url="https://taostats.io/subnets", timeout=60, pause=1):
    driver = uc.Chrome()
    driver.get(url)
    wait = WebDriverWait(driver, 20)
    # ждём пока появится хотя бы одна строка
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='row'][data-rowindex]")))
    start = time.time()
    prev_count = 0

    # скроллим вниз, пока не перестанут подтягиваться новые строки
    while time.time() - start < timeout:
        rows = driver.find_elements(By.CSS_SELECTOR, "div[role='row'][data-rowindex]")
        count = len(rows)
        driver.execute_script("window.scrollBy(0, document.body.scrollHeight);")
        time.sleep(pause)
        if count == prev_count:
            break
        prev_count = count

    # парсим каждую строку
    subs = []
    rows = driver.find_elements(By.CSS_SELECTOR, "div[role='row'][data-rowindex]")
    for row in rows:
        cells = row.find_elements(By.CSS_SELECTOR, "div[role='cell']")
        # здесь предполагается, что столбцы идут в порядке:
        # netuid, name, registration_date, price, emission,
        # registration_cost, github, discord, key, vtrust
        subs.append({
            "netuid": cells[0].text,
            "name": cells[1].text,
            "registration_date": cells[2].text,
            "price": cells[3].text,
            "emission": cells[4].text,
            "registration_cost": cells[5].text,
            "github": cells[6].text,
            "discord": cells[7].text,
            "key": cells[8].text,
            "vtrust": cells[9].text,
        })

    driver.quit()
    return subs

# --- Запись в Google Sheets ---
def write_to_sheet(subnets, spreadsheet_id, credentials_json, sheet_name="taostats stats"):
    creds = Credentials.from_service_account_info(
        json.loads(credentials_json),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    gs = gspread.Client(auth=creds)
    sh = gs.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet(sheet_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(sheet_name, rows="1000", cols="20")

    # Заголовки
    headers = ["netuid","name","registration_date","price","emission",
               "registration_cost","github","discord","key","vtrust"]
    ws.append_row(headers)

    # Данные
    for s in subnets:
        ws.append_row([s[h] for h in headers])

def main():
    # переменные окружения
    SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
    GOOGLE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    if not SPREADSHEET_ID or not GOOGLE_JSON:
        raise RuntimeError("Не заданы переменные SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON")

    print("Собираем все подсети через Selenium + виртуальный скролл…")
    subs = fetch_all_subnets()
    print(f"Найдено подсетей: {len(subs)}")

    print(f"Пишем в Google Sheets (‘taostats stats’)…")
    write_to_sheet(subs, SPREADSHEET_ID, GOOGLE_JSON)
    print("✅ Готово.")

if __name__ == "__main__":
    main()
