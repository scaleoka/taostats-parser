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
    Пытается найти и распарсить JSON-объект из любого <script>...</script>,
    содержащего валидный JSON с ключами props → pageProps.
    Если не находит, сохраняет HTML для отладки и выбрасывает ошибку.
    """
    # находим все inline-скрипты, содержащие JSON
    candidates = re.findall(r'<script[^>]*>(\{.*?\})</script>', html, re.S)
    for raw in candidates:
        try:
            data = json.loads(raw)
            if (isinstance(data, dict) and
                "props" in data and
                isinstance(data["props"], dict) and
                "pageProps" in data["props"]):
                return data
        except json.JSONDecodeError:
            continue
    # Сохранение для отладки
    with open("error_subnets.html", "w", encoding="utf-8") as f:
        f.write(html)
    raise RuntimeError(
        "Не удалось найти JSON Next.js на странице.\n"
        "Сохранён debug-файл error_subnets.html для анализа."
    )


def parse_subnets(data: dict) -> list:
    """
    Извлекает список подсетей из структуры Next.js JSON.
    """
    return (
        data
        .get("props", {})
        .get("pageProps", {})
        .get("subnets", [])
    )


def transform_subnet(s: dict) -> dict:
    """
    Преобразует объект подсети к словарю с нужными полями.
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
