import os
import json
import csv
import time
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from google.oauth2 import service_account
from googleapiclient.discovery import build

# URL страницы с таблицей подсетей
URL = "https://taostats.io/subnets"

# Читаем параметры для Google Sheets из переменных окружения
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID')
SERVICE_ACCOUNT_JSON = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')

if not SPREADSHEET_ID or not SERVICE_ACCOUNT_JSON:
    raise RuntimeError('Не заданы переменные SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON')

# Ищем и собираем данные из таблицы подсетей через undetected-chromedriver

def fetch_subnets_table(url: str):
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = uc.Chrome(options=options)
    driver.get(url)

    # Ждём загрузки таблицы
    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))

    # Скроллим страницу до конца, чтобы загрузить все строки
    last_h = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)
        new_h = driver.execute_script("return document.body.scrollHeight")
        if new_h == last_h:
            break
        last_h = new_h

    rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
    data = []
    for row in rows:
        cols = row.find_elements(By.TAG_NAME, "td")
        if len(cols) < 9:
            continue
        netuid = cols[0].text.strip()
        name = cols[1].text.strip()
        registration_date = cols[2].text.strip()
        price = cols[3].text.strip()
        emission = cols[4].text.strip()
        registration_cost = cols[5].text.strip()
        vtrust = cols[6].text.strip()
        key = cols[8].text.strip()

        # Ссылки GitHub/Discord в колонке с иконками
        github = discord = None
        for a in cols[7].find_elements(By.TAG_NAME, "a"):
            href = a.get_attribute("href")
            if 'github.com' in href:
                github = href
            elif 'discord' in href:
                discord = href

        data.append({
            "netuid": netuid,
            "name": name,
            "registration_date": registration_date,
            "price": price,
            "emission": emission,
            "registration_cost": registration_cost,
            "github": github,
            "discord": discord,
            "key": key,
            "vtrust": vtrust
        })

    driver.quit()
    return data

# Функция для записи данных в Google Sheets

def save_to_google_sheets(rows):
    # Загружаем учётные данные из JSON
    info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info,
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()

    # Подготавливаем значения (заголовок + строки)
    headers = list(rows[0].keys())
    values = [headers] + [[row[h] for h in headers] for row in rows]

    body = {'values': values}
    sheet.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range='A1',
        valueInputOption='RAW',
        body=body
    ).execute()
    print(f"Данные записаны в таблицу {SPREADSHEET_ID}")

if __name__ == "__main__":
    print("Собираем подсети через Selenium...")
    subnets = fetch_subnets_table(URL)
    print(f"Найдено строк: {len(subnets)}")

    print("Сохраняем в Google Sheets...")
    save_to_google_sheets(subnets)
    print("Готово.")
