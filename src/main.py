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

# --- Конфиг ---
URL         = "https://taostats.io/subnets"
SHEET_NAME  = "taostats stats"
SPREADSHEET_ID       = os.getenv("SPREADSHEET_ID")
GOOGLE_JSON         = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

if not SPREADSHEET_ID or not GOOGLE_JSON:
    raise RuntimeError("Не заданы SPREADSHEET_ID или GOOGLE_SERVICE_ACCOUNT_JSON")

# --- Google Sheets setup ---
creds_dict = json.loads(GOOGLE_JSON)
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)
sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

def try_inline():
    """Пробуем взять из <script id="__NEXT_DATA__"> (обычно только первые 10)."""
    html = cloudscraper.create_scraper().get(URL, timeout=30).text
    m = __import__('re').search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, __import__('re').S)
    if not m:
        return None
    data = json.loads(m.group(1))
    return data.get("props", {}).get("pageProps", {}).get("subnets")

def fetch_all_via_selenium():
    """
    Открываем страницу в undetected-chromedriver,
    ждём таблицу и перебираем все страницы, кликая на кнопку Next.
    """
    options = uc.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = uc.Chrome(options=options)
    driver.get(URL)

    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))

    collected = []
    page = 1
    while True:
        # ждём строки текущей страницы
        wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "table tbody tr")))
        rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
        print(f"  — Страница {page}: строк {len(rows)}")
        for r in rows:
            cols = r.find_elements(By.TAG_NAME, "td")
            if len(cols) < 9: 
                continue
            # собираем поля
            netuid = cols[0].text.strip()
            name   = cols[1].text.strip()
            regd   = cols[2].text.strip()
            price  = cols[3].text.strip()
            emis   = cols[4].text.strip()
            rcost  = cols[5].text.strip()
            vtrust = cols[6].text.strip()
            key    = cols[8].text.strip()
            gh = dc = ""
            for a in cols[7].find_elements(By.TAG_NAME, "a"):
                href = a.get_attribute("href")
                if "github.com" in href: gh = href
                if "discord"   in href: dc = href
            collected.append({
                "netuid": netuid,
                "name": name,
                "registration_date": regd,
                "price": price,
                "emission": emis,
                "registration_cost": rcost,
                "github": gh,
                "discord": dc,
                "key": key,
                "vtrust": vtrust
            })

        # пытаемся найти кнопку Next (MUI/React-пагинация)
        try:
            nxt = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Go to next page']")
        except:
            break
        # если она задизейблена — выходим
        if nxt.get_attribute("disabled") or "disabled" in nxt.get_attribute("class"):
            break
        nxt.click()
        page += 1
        time.sleep(1)

    driver.quit()
    return collected

def write_to_sheet(rows):
    """Чистим лист и заливаем заголовок + все строки."""
    headers = ["netuid","name","registration_date","price",
               "emission","registration_cost","github",
               "discord","key","vtrust"]
    sheet.clear()
    payload = [headers] + [[r[h] for h in headers] for r in rows]
    sheet.update(payload)
    print(f"✅ Всего записано {len(rows)} строк в '{SHEET_NAME}'")

if __name__ == "__main__":
    print("1) Пробуем встроенный JSON…")
    subs = try_inline()
    if subs:
        print(f" → Нашли {len(subs)} подсетей в inline, но это только первая страница.")
        # не используем inline, потому что нужно всё: переключаемся на Selenium
    print("2) Собираем через Selenium (перебор всех страниц)…")
    all_subs = fetch_all_via_selenium()
    print(f" → Всего подсетей собрано: {len(all_subs)}")

    if not all_subs:
        raise RuntimeError("Не удалось получить ни одной подсети")

    print("3) Записываем в Google Sheets…")
    write_to_sheet(all_subs)
    print("Готово.")
