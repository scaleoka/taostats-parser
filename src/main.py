import re, json, requests, csv

URL = "https://taostats.io/subnets"
HTML_ENCODING = 'utf-8'

def fetch_all_via_initial_data():
    resp = requests.get(URL, timeout=30)
    resp.raise_for_status()
    html = resp.text

    # 1) Ищем массив initialData
    m = re.search(r'initialData\":(\[.*?\])', html, re.S)
    if not m:
        raise RuntimeError("initialData не найдено в HTML")
    raw = m.group(1)

    # 2) Приводим к валидному JSON (если есть escape-символы — можно дообработать)
    data = json.loads(raw)

    # 3) Каждый элемент — это объект подсети со всеми полями
    return data

def transform(s):
    links = s.get("links") or {}
    return {
        "netuid":            s.get("netuid"),
        "name":              s.get("name"),
        "registration_date": s.get("registration_timestamp"),
        "price":             s.get("price"),
        "emission":          s.get("emission"),
        "registration_cost": s.get("registration_cost") or s.get("neuron_registration_cost"),
        "github":            links.get("github") or s.get("github"),
        "discord":           links.get("discord") or s.get("discord_url"),
        "key":               s.get("key"),
        "vtrust":            s.get("vTrust") or s.get("rank_change"),
    }

def save_to_csv(rows, path="subnets.csv"):
    if not rows:
        print("Нет данных")
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Сохранено {len(rows)} строк в {path}")

if __name__ == "__main__":
    print("Парсим initialData из HTML…")
    subs = fetch_all_via_initial_data()
    print(f"Найдено подсетей: {len(subs)}")    # должно быть >100

    print("Трансформируем и сохраняем…")
    rows = [transform(s) for s in subs]
    save_to_csv(rows)
    print("Готово.")
