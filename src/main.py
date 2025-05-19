#!/usr/bin/env python3
import os
import time
import json
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- Конфиг ---
URL         = "https://taostats.io/subnets"
SHEET_NAME  = "taostats stats"
SPREADSHEET_ID       = os.getenv("SPREADSHEET_ID")
GOOGLE_JSON         = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

if not SPREADSHEET_ID or not GOOGLE_JSON:
    raise RuntimeError("Не заданы SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON")

# --- Google Sheets setup ---
creds_info = json.loads(GOOGLE_JSON)
creds = service_account.Credentials.from_service_account_info(
    creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
service = build("sheets", "v4", credentials=creds).spreadsheets()

def fetch_all_subnets():
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = uc.Chrome(options=options)
    driver.get(URL)

    wait = WebDriverWait(driver, 20)
    # ждём, пока виртуальный скроллер и хотя бы одна строка появятся
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='row'][data-rowindex]")))
    scroller = driver.find_element(By.CSS_SELECTOR, ".MuiDataGrid-virtualScroller")

    prev = 0
    while True:
        rows = driver.find_elements(By.CSS_SELECTOR, "div[role='row'][data-rowindex]")
        if len(rows) == prev:
            break
        prev = len(rows)
        # долистываем до конца контейнера
        driver.execute_script("arguments[0].scrollTo(0, arguments[0].scrollHeight);", scroller)
        time.sleep(1)  # даём виртуальному скроллеру подгрузить новые

    # парсим все найденные строки
    data = []
    for row in rows:
        cells = row.find_elements(By.CSS_SELECTOR, "div[role='cell']")
        if len(cells) < 9:
            continue
        rec = {
            "netuid":            cells[0].text.strip(),
            "name":              cells[1].text.strip(),
            "registration_date": cells[2].text.strip(),
            "price":             cells[3].text.strip(),
            "emission":          cells[4].text.strip(),
            "registration_cost": cells[5].text.strip(),
            "vtrust":            cells[6].text.strip(),
            "key":               cells[8].text.strip(),
        }
        # 7-я ячейка — иконки ссылок
        github = discord = None
        for a in cells[7].find_elements(By.TAG_NAME, "a"):
            href = a.get_attribute("href")
            if "github.com" in href:
                github = href
            elif "discord" in href:
                discord = href
        rec["github"] = github
        rec["discord"] = discord

        data.append(rec)

    driver.quit()
    return data

def save_to_sheets(rows):
    headers = list(rows[0].keys())
    values  = [headers] + [[r[h] or "" for h in headers] for r in rows]
    service.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1",
        valueInputOption="RAW",
        body={"values": values}
    ).execute()
    print(f"✅ Всего записано {len(rows)} подсетей в лист '{SHEET_NAME}'")

if __name__ == "__main__":
    print("Собираем все подсети через виртуальный скроллер DataGrid…")
    subs = fetch_all_subnets()
    print(f"→ Найдено подсетей: {len(subs)}")
    print("Записываем в Google Sheets…")
    save_to_sheets(subs)
    print("Готово.")
