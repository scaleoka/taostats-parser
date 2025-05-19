#!/usr/bin/env python3
import os
import time
import json
import cloudscraper
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- Настройка через ENV ---
URL         = "https://taostats.io/subnets"
SHEET_NAME  = "taostats stats"
SPREADSHEET_ID       = os.getenv("SPREADSHEET_ID")
GOOGLE_JSON         = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

if not SPREADSHEET_ID or not GOOGLE_JSON:
    raise RuntimeError("Не заданы SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON")

# --- Инициализация Google Sheets ---
creds_dict = json.loads(GOOGLE_JSON)
scope = ["https://www.googleapis.com/auth/spreadsheets",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

def fetch_all_subnets():
    """
    Открывает страницу в headless-хроме, ждёт отрисовки таблицы
    и кликает на кнопку 'Next page' до тех пор, пока она не задизейблена.
    Возвращает список всех строк (каждая — список текстов td или href).
    """
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = uc.Chrome(options=options)
    driver.get(URL)

    wait = WebDriverWait(driver, 20)
    # Ждём появления первой строки
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))

    all_rows = []
    page = 1
    while True:
        # Считываем все <tr> текущей страницы
        wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "table tbody tr")))
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        print(f" — Страница {page}: строк {len(rows)}")
        for row in rows:
            cols = row.find_elements(By.TAG_NAME, "td")
            if len(cols) < 9:
                continue
            # собираем текст/ссылки из ячеек
            rec = []
            for idx, col in enumerate(cols):
                # ссылки в 7-й ячейке
                if idx == 7:
                    hrefs = [a.get_attribute("href") for a in col.find_elements(By.TAG_NAME, "a")]
                    # GitHub и/или Discord
                    rec.append(" ".join(hrefs))
                else:
                    rec.append(col.text.strip())
            # нам нужны только первые 10 полей: netuid,name,reg_date,price,emission,reg_cost,github+discord,key,vtrust
            all_rows.append(rec[:10])
        # Находим кнопку Next (MUI aria-label)
        btns = driver.find_elements(By.CSS_SELECTOR, "button[aria-label*='next']")
        if not btns:
            break
        nxt = btns[-1]
        # Проверяем атрибут aria-disabled
        if nxt.get_attribute("aria-disabled") == "true":
            break
        # Кликаем и ждём подгрузки
        nxt.click()
        page += 1
        time.sleep(1)

    driver.quit()
    return all_rows

def write_to_sheet(rows):
    """
    Очищает лист и заливает в него заголовок + все собранные строки.
    """
    headers = ["netuid","name","registration_date","price",
               "emission","registration_cost","github_discord",
               "key","vtrust"]
    # чистим лист
    sheet.clear()
    payload = [headers] + rows
    sheet.update(payload)
    print(f"✅ Всего записано {len(rows)} подсетей в лист '{SHEET_NAME}'")

if __name__ == "__main__":
    print("Собираем все подсети через Selenium…")
    data = fetch_all_subnets()
    print(f"Найдено всего {len(data)} подсетей")
    print("Записываем в Google Sheets…")
    write_to_sheet(data)
    print("Готово.")
