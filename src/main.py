import cloudscraper
import json
import re
import csv
import time
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from pathlib import Path

URL = "https://taostats.io/subnets"


def fetch_html(url: str) -> str:
    """
    Загружает HTML страницы с помощью cloudscraper и возвращает текст.
    """
    scraper = cloudscraper.create_scraper()
    resp = scraper.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def extract_next_data(html: str) -> dict:
    """
    Ищет встроенный JSON Next.js в <script> и возвращает его.
    """
    # Попытка по id __NEXT_DATA__
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        return json.loads(m.group(1))
    # Альтернативный поиск любых блоков с JSON
    for raw in re.findall(r'<script[^>]*>(\{.*?\})</script>', html, re.S):
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and "props" in data:
                return data
        except json.JSONDecodeError:
            continue
    return None


def fetch_data_selenium(url: str) -> dict:
    """
    Запускает headless Chrome и возвращает window.__NEXT_DATA__.
    Требует selenium и webdriver-manager.
    """
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    try:
        driver.get(url)
        time.sleep(5)  # ждём, пока JS отработает
        data = driver.execute_script("return window.__NEXT_DATA__ || null")
        if data and isinstance(data, dict):
            return data
        raise RuntimeError("window.__NEXT_DATA__ не найден через Selenium")
    finally:
        driver.quit()


def parse_subnets(data: dict) -> list:
    """
    Извлекает список подсетей из Next.js JSON.
    """
    return (
        data
        .get("props", {})
        .get("pageProps", {})
        .get("subnets", [])
    )


def transform_subnet(s: dict) -> dict:
    """
    Преобразует объект подсети к нужному набору полей.
    """
    links = s.get("links", {}) or {}
    return {
        "netuid": s.get("netuid"),
        "name": s.get("name"),
        "registration_date": s.get("registration_timestamp"),
        "price": s.get("price"),
        "emission": s.get("emission"),
        "registration_cost": s.get("registration_cost"),
        "github": links.get("github"),
        "discord": links.get("discord"),
        "key": s.get("key"),
        "vtrust": s.get("vTrust")
    }


def save_to_csv(rows: list, filename: str):
    """
    Сохраняет список словарей в CSV.
    """
    if not rows:
        print("Нет данных для сохранения.")
        return
    fieldnames = list(rows[0].keys())
    with open(filename, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"Данные сохранены в {filename}")


if __name__ == "__main__":
    print("Загружаем HTML страницы…")
    html = fetch_html(URL)

    print("Пробуем извлечь JSON из HTML…")
    data = extract_next_data(html)

    if not data:
        print("Не найден JSON в HTML, пробуем через Selenium…")
        data = fetch_data_selenium(URL)

    print("Парсим список подсетей…")
    subs = parse_subnets(data)
    print(f"Найдено подсетей: {len(subs)}")

    print("Трансформируем подсети…")
    rows = [transform_subnet(s) for s in subs]

    print("Сохраняем в subnets.csv…")
    save_to_csv(rows, "subnets.csv")

    print("Готово.")
