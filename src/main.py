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

# ——— Конфиг ———
URL         = "https://taostats.io/subnets"
SHEET_NAME  = "taostats stats"
SPREADSHEET_ID       = os.getenv("SPREADSHEET_ID")
GOOGLE_JSON         = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

if not SPREADSHEET_ID or not GOOGLE_JSON:
    raise RuntimeError("Не заданы SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON")

# ——— Инициализация Google Sheets ———
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
    # ждём, пока появится хотя бы одна строка (role="row" с aria-rowindex)
    wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "div[role='row'][aria-rowindex]")))

    # сам контейнер виртуального скроллера
    scroller = driver.find_element(By.CSS_SELECTOR, ".MuiDataGrid-virtualScroller")

    prev_count = -1
    while True:
        # все отрендеренные в данный момент строки
        rows = driver.find_elements(By.CSS_SELECTOR, "div[role='row'][aria-rowindex]")
        if len(rows) == prev_count:
            break
        prev_count = len(rows)

        # доскролливаем вниз
        driver.execute_script(
            "arguments[0].scrollTo(0, arguments[0].scrollHeight);", scroller
        )
        time.sleep(1)

    # парсим каждую строку
    data = []
    for row in rows:
        # пропускаем шапку таблицы: у неё aria-rowindex="0"
        idx = row.get_attribute("aria-rowindex")
        if idx == "0":
            continue

        cells = row.find_elements(By.CSS_SELECTOR, "div[role='cell']")
        # должно быть минимум 9 ячеек
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
        # 7-я ячейка — ссылки
        gh = dc = ""
        for a in cells[7].find_elements(By.TAG_NAME, "a"):
            href = a.get_attribute("href")
            if "github.com" in href:
                gh = href
            elif "discord" in href:
                dc = href
        rec["github"]  = gh
        rec["discord"] = dc

        data.append(rec)

    driver.quit()
    return data

def save_to_sheets(rows):
    # собираем payload: заголовок + данные
    headers = list(rows[0].keys())
    values  = [headers] + [[r.get(h, "") for h in headers] for r in rows]

    service.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1",
        valueInputOption="RAW",
        body={"values": values}
    ).execute()
    print(f"✅ Всего записано {len(rows)} подсетей в лист '{SHEET_NAME}'")

if __name__ == "__main__":
    print("Собираем все подсети через виртуальный скроллер DataGrid…")
    all_subs = fetch_all_subnets()
    print(f"→ Найдено подсетей: {len(all_subs)}")
    print("Записываем в Google Sheets…")
    save_to_sheets(all_subs)
    print("Готово.")
