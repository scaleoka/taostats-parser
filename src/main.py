import os
import requests
from bs4 import BeautifulSoup
import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Секреты из ENV
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
SHEET_NAME = "taostats stats"
URL = 'https://taostats.io/subnets'

# Получаем данные с сайта
resp = requests.get(URL)
resp.raise_for_status()
soup = BeautifulSoup(resp.text, 'html.parser')

# Найти таблицу - если класс другой, поправь на свой!
table = soup.find('table')
if not table:
    raise RuntimeError('Не найдена таблица!')

# Извлечь заголовки
headers = [th.get_text(strip=True) for th in table.find('tr').find_all('th')]
# Подстрой названия под реальные значения!
header_map = {h.lower(): i for i, h in enumerate(headers)}
needed = [
    'netuid', 'name', 'registration date', 'price', 'emission',
    'registration cost', 'github', 'discord', 'key', 'vtrust'
]
needed_map = [header_map[n] for n in needed]

# Собрать данные
data = []
for row in table.find_all('tr')[1:]:
    cols = [td.get_text(strip=True) for td in row.find_all('td')]
    if len(cols) < len(headers):
        continue
    data.append([cols[i] for i in needed_map])

# Подготовить тело для Google Sheets
values = [needed] + data

# Подключиться к Google Sheets
creds = Credentials.from_service_account_info(
    json.loads(SERVICE_ACCOUNT_JSON),
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
service = build('sheets', 'v4', credentials=creds)
sheet = service.spreadsheets()

# Очистить и записать в лист
sheet.values().clear(
    spreadsheetId=SPREADSHEET_ID,
    range=f"'{SHEET_NAME}'!A1:J"
).execute()

sheet.values().update(
    spreadsheetId=SPREADSHEET_ID,
    range=f"'{SHEET_NAME}'!A1",
    valueInputOption="RAW",
    body={'values': values}
).execute()

print(f"✅ Данные успешно записаны в Google Sheet '{SHEET_NAME}' ({len(data)} строк)")
