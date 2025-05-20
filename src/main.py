import re
import requests
from urllib.parse import urljoin

URL = "https://taostats.io/subnets"

def fetch_via_rsc() -> list[dict]:
    print("1a) Пытаемся получить subnets через JSON-API…")

    # 1) Скачиваем HTML
    html = requests.get(URL, timeout=30).text

    # 2) Ищем buildId, который меняется при каждой сборке
    m = re.search(r'/_next/data/([^/]+)/subnets\.json', html)
    if not m:
        print("→ Не удалось найти buildId в HTML")
        return []

    build_id = m.group(1)
    print(f"→ Найден buildId: {build_id}")

    # 3) Формируем URL JSON-файла
    json_url = urljoin(URL, f"/_next/data/{build_id}/subnets.json")
    params = {"page": 1, "limit": -1, "order": "market_cap_desc"}

    # 4) Запрашиваем и разбираем JSON
    resp = requests.get(json_url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # 5) Достаем массив подсетей
    # В JSON-ответе содержится ключ data.data (тут может слегка отличаться — проверьте структуру)
    subs = data.get("pageProps", {}) \
               .get("initialProps", {}) \
               .get("data", {}) \
               .get("data", [])
    print(f"→ JSON-API вернуло подсетей: {len(subs)}")

    return subs
