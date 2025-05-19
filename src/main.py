import os
import json
import time
import csv
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from google.oauth2 import service_account
from googleapiclient.discovery import build

# URL страницы Subnets
URL = "https://taostats.io/subnets"

# Параметры Google Sheets из секретов окружения
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
SERVICE_ACCOUNT_JSON = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
SHEET_NAME = 'taostats stats'

if not SPREADSHEET_ID or not SERVICE_ACCOUNT_JSON:
    raise RuntimeError('Не заданы переменные SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON')

def fetch_subnets_table(url: str):
    """
    Собирает все строки таблицы Subnets, переключая DataTables на «All».
    """
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = uc.Chrome(options=options)
    driver.get(url)

    wait = WebDriverWait(driver, 20)
    # Ждём, пока появится хотя бы одна строка
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))

    # Переключаем селектор «Show X entries» на All
    length_select = Select(driver.find_element(By.CSS_SELECTOR, ".dataTables_length select"))
    for opt in length_select.options:
        if 'all' in opt.text.lower():
            length_select.select_by_visible_text(opt.text)
            break
    # Ждём перерисовки таблицы
    time.sleep(2)

    # Теперь в DOM все строки
    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
    data = []
    for row in rows:
        cols = row.find_elements(By.TAG_NAME, "td")
        if len(cols) < 9:
            continue

        netuid            = cols[0].text.strip()
        name              = cols[1].text.strip()
        registration_date = cols[2].text.strip()
        price             = cols[3].text.strip()
        emission          = cols[4].text.strip()
        registration_cost = cols[5].text.strip()
        vtrust            = cols[6].text.strip()
        key               = cols[8].text.strip()

        # Ссылки GitHub / Discord
        github = discord = None
        for a in cols[7].find_elements(By.TAG_NAME, "a"):
            href = a.get_attribute("href")
            if "github.com" in href:
                github = href
            elif "discord" in href:
                discord = href

        data.append({
            "netuid":            netuid,
            "name":              name,
            "registration_date": registration_date,
            "price":             price,
            "emission":          emission,
            "registration_cost": registration_cost,
            "github":            github,
            "discord":           discord,
            "key":               key,
            "vtrust":            vtrust,
        })

    driver.quit()
    return data

def save_to_google_sheets(rows: list):
    """
    Записывает список словарей в указанный лист Google Sheets.
    """
    info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    service = build("sheets", "v4", credentials=creds)
    sheet = service.spreadsheets()

    headers = list(rows[0].keys())
    values = [headers] + [[row[h] for h in headers] for row in rows]

    body = {"values": values}
    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{SHEET_NAME}'!A1",
        valueInputOption="RAW",
        body=body
    ).execute()

    print(f"Данные записаны в лист '{SHEET_NAME}' таблицы {SPREADSHEET_ID}")

if __name__ == "__main__":
    print("Собираем все подсети через undetected-chromedriver…")
    subnets = fetch_subnets_table(URL)
    print(f"Найдено подсетей: {len(subnets)}")

    print("Записываем в Google Sheets…")
    save_to_google_sheets(subnets)
    print("Готово.")
