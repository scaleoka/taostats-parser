import cloudscraper
import json
import re
import csv


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
    Извлекает и возвращает JSON-объект из <script id="__NEXT_DATA__">…</script>.
    """
    pattern = r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>'
    m = re.search(pattern, html, re.S)
    if not m:
        # пробуем альтернативный вариант
        pattern2 = r'window\.__NEXT_DATA__\s*=\s*({.*?});'
        m = re.search(pattern2, html, re.S)
        if not m:
            raise RuntimeError("Не удалось найти JSON Next.js на странице")
    return json.loads(m.group(1))


def parse_subnets(data: dict) -> list:
    """
    Из JSON-данных Next.js извлекает список подсетей и возвращает его.
    """
    return (
        data
        .get("props", {})
        .get("pageProps", {})
        .get("subnets", [])
    )


def transform_subnet(s: dict) -> dict:
    """
    Приводит объект подсети к нужному набору полей.
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
    Сохраняет список словарей в CSV-файл.
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
    URL = "https://taostats.io/subnets"

    print("Загружаем страницу…")
    html = fetch_html(URL)

    print("Извлекаем JSON Next.js…")
    data = extract_next_data(html)

    print("Парсим список подсетей…")
    subs = parse_subnets(data)
    print(f"Найдено подсетей: {len(subs)}")

    print("Трансформируем и сохраняем…")
    rows = [transform_subnet(s) for s in subs]
    save_to_csv(rows, "subnets.csv")

    print("Готово.")
