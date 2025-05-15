import cloudscraper
import json
import re
import csv
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

URL = "https://taostats.io/subnets"


def fetch_html(url: str) -> str:
    scraper = cloudscraper.create_scraper()
    resp = scraper.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def extract_next_data(html: str) -> dict:
    # Ищем встроенный JSON Next.js
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        return json.loads(m.group(1))
    # fallback: ищем JSON в любых <script>
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
    Запускает headless Chrome через Selenium и возвращает window.__NEXT_DATA__.
    """
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)
    try:
        driver.get(url)
        time.sleep(5)
        data = driver.execute_script("return window.__NEXT_DATA__ || null")
        if data:
            return data
        raise RuntimeError("window.__NEXT_DATA__ не найден в Selenium")
    finally:
        driver.quit()


def parse_subnets(data: dict) -> list:
    return (
        data
        .get("props", {})
        .get("pageProps", {})
        .get("subnets", [])
    )


def transform_subnet(s: dict) -> dict:
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
        print("Не удалось найти JSON напрямую, запускаем Selenium…")
        data = fetch_data_selenium(URL)

    print("Парсим подсети из JSON…")
    subs = parse_subnets(data)
    print(f"Найдено подсетей: {len(subs)}")

    print("Трансформируем и сохраняем…")
    rows = [transform_subnet(s) for s in subs]
    save_to_csv(rows, "subnets.csv")
    print("Готово.")
