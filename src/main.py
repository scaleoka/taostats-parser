# src/main.py
import os
import time
import json
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- Параметры из окружения ---
URL = "https://taostats.io/subnets"
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

if not SPREADSHEET_ID or not GOOGLE_SERVICE_ACCOUNT_JSON:
    raise RuntimeError("Не заданы переменные SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON")

# --- Функция сбора всех строк через Selenium + виртуальный скроллер MUI DataGrid ---
def fetch_all_subnets():
    options = uc.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    driver = uc.Chrome(options=options)
    driver.get(URL)

    wait = WebDriverWait(driver, 20)
    # ждём появления виртуального скроллера
    grid = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".MuiDataGrid-virtualScroller")))
    
    # скроллим вниз по контейнеру, пока не перестанет расти scrollHeight
    subs = []
    seen = set()
    last_height = -1

    while True:
        driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", grid)
        time.sleep(1)  # даём время подгрузиться
        new_height = driver.execute_script("return arguments[0].scrollHeight", grid)
        # собираем все строки
        rows = driver.find_elements(By.CSS_SELECTOR, "div[role='row']")
        for row in rows:
            cells = row.find_elements(By.CSS_SELECTOR, "div[role='cell']")
            # пример: [netuid, name, reg_date, price, emission, reg_cost, github, discord, active_keys, vtrust]
            data = [cell.text for cell in cells]
            key = tuple(data)
            if key not in seen:
                seen.add(key)
                subs.append(data)
        if new_height == last_height:
            break
        last_height = new_height

    driver.quit()
    return subs

# --- Функция записи в Google Sheets ---
def write_to_sheet(rows):
    creds_dict = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet("taostats stats")
    # затираем старые данные
    sheet.clear()
    # записываем заголовок + данные
    header = ["netuid", "name", "reg_date", "price", "emission", "reg_cost", "github", "discord", "active_keys", "vtrust"]
    sheet.append_row(header)
    sheet.append_rows(rows)

def main():
    print("Собираем все подсети через Selenium + виртуальный скроллер…")
    subs = fetch_all_subnets()
    print(f"Найдено всего {len(subs)} подсетей")
    print("Записываем в Google Sheets…")
    write_to_sheet(subs)
    print(f"✅ Всего записано {len(subs)} строк в лист 'taostats stats'")
    print("Готово.")

if __name__ == "__main__":
    main()
