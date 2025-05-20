import re
import json
import requests

URL = "https://taostats.io/subnets"

def fetch_via_rsc() -> list[dict]:
    print("1) GET /subnets и ищем __NEXT_DATA__…")
    resp = requests.get(URL, timeout=30)
    resp.raise_for_status()
    html = resp.text

    # 1. Извлекаем JSON из <script id="__NEXT_DATA__">
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.S
    )
    if not m:
        print("→ Не найден __NEXT_DATA__ в HTML")
        return []

    try:
        nd = json.loads(m.group(1))
    except json.JSONDecodeError:
        print("→ Не удалось распарсить JSON из __NEXT_DATA__")
        return []

    # 2. Вытаскиваем buildId
    build_id = nd.get("buildId")
    if not build_id:
        print("→ В __NEXT_DATA__ нет ключа buildId")
        return []
    print(f"→ Найден buildId = {build_id}")

    # 3. Формируем URL data-route и фетчим его
    data_url = f"https://taostats.io/_next/data/{build_id}/subnets.json"
    print(f"→ GET {data_url}")
    jr = requests.get(data_url, timeout=30)
    jr.raise_for_status()
    data = jr.json()

    # 4. Достаём список подсетей
    # Путь может чуть отличаться — подставьте нужный
    subnets = (
        data
        .get("pageProps", {})
        .get("initialData", {})      # или .get("subnets") напрямик
        .get("subnets", [])
    )
    print(f"→ RSC вернуло подсетей: {len(subnets)}")
    return subnets
