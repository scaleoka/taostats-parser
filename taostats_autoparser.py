#!/usr/bin/env python3
# taostats_http_parser.py

import os
import re
import time
import requests
import pandas as pd
import gspread
from bs4 import BeautifulSoup
from oauth2client.service_account import ServiceAccountCredentials

# --- ПАРАМЕТРЫ И АУТЕНТИФИКАЦИЯ ---

# ID Google Sheet (берём из secrets)
SPREADSHEET_ID = os.environ['SPREADSHEET_ID']
# Путь к JSON-сервисному аккаунту
CREDS_PATH = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', 'creds.json')

# Общие настройки HTTP
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; TAO-Parser/1.0)"
})

# --- ФУНКЦИИ ПАРСИНГА ---

def get_total_netids() -> int:
    """Скачиваем /subnets и вытаскиваем число total entries."""
    url = "https://taostats.io/subnets"
    resp = SESSION.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    p = soup.find("p", string=re.compile(r"of\s+\d+\s+entries"))
    if not p:
        raise RuntimeError("Не удалось найти общий счётчик подсетей на /subnets")
    m = re.search(r"of\s+(\d+)\s+entries", p.text)
    return int(m.group(1))

def parse_main_page_http(netid: str) -> dict:
    """Парсим основные поля (name, price, emissions и т.д.) с metagraph?order=type:asc."""
    url = f"https://taostats.io/subnets/{netid}/metagraph?order=type%3Aasc"
    resp = SESSION.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Helpers
    def find_text(selector, **kwargs):
        el = soup.select_one(selector, **kwargs)
        return el.get_text(strip=True) if el else None

    def extract_label(label):
        # находим <p> с текстом label, поднимаемся к общему блоку, собираем все <p> внутри
        lbl = soup.find("p", string=label)
        if not lbl: 
            return None
        parent = lbl.find_parent("div", class_=re.compile(r"bg\-\#272727"))
        ps = parent.find_all("p") if parent else []
        txt = " ".join([p.get_text(strip=True) for p in ps])
        m = re.search(r"(\d+\.\d+|\d+)", txt)
        return m.group(1) if m else None

    data = {
        "netuid": find_text("div.flex.flex-row.items-center.gap-1 p.font-fira"),
        "name":   find_text("p.font-bold"),
        "reg_date": find_text("p:contains('Reg:') + p", ),
        "price":      extract_label("Price"),
        "emission":   extract_label("Emissions"),
        "reg_cost":   extract_label("Reg Cost"),
        "github":     None,
        "discord":    None,
        "key":        find_text("span.flex-shrink-0.truncate"),
    }
    # github / discord ссылки
    gh = soup.select_one("a[href*='github.com']")
    dc = soup.select_one("a[href*='discord.com']")
    data["github"]  = gh["href"] if gh else None
    data["discord"] = dc["href"] if dc else None

    return data

def parse_table_stats_http(netid: str, order: str, icon_class: str, col_index: int) -> list[float]:
    """Парсим таблицу id=taostats-table на странице metagraph?order=...&limit=100."""
    url = f"https://taostats.io/subnets/{netid}/metagraph?order={order}&limit=100"
    resp = SESSION.get(url, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    table = soup.find("table", id="taostats-table")
    vals = []
    if not table:
        return vals
    for row in table.find_all("tr"):
        if not row.find("svg", class_=re.compile(re.escape(icon_class))):
            continue
        cols = row.find_all("td")
        if len(cols) > col_index:
            txt = cols[col_index].get_text(strip=True)
            try:
                vals.append(float(txt))
            except ValueError:
                pass
    return vals

def process_single_netid_http(netid: str) -> dict:
    """Собираем все поля для одного netid."""
    base = parse_main_page_http(netid)
    # vtrust
    v = parse_table_stats_http(netid, "type%3Adesc", "lucide-shield text-indigo-400", 4)
    base["vtrust_shield_avg"] = sum(v)/len(v) if v else None
    # incentive orange/max
    o = parse_table_stats_http(netid, "type%3Aasc", r"lucide-pickaxe text-\[\#F90\]", 7)
    base["incentive_orange_max"] = max(o) if o else None
    # incentive green desc/max
    gd = parse_table_stats_http(netid, "incentive%3Adesc", r"lucide-pickaxe text-\[\#00DBBC\]", 7)
    base["incentive_green_desc_max"] = max(gd) if gd else None
    # incentive green asc/min non-zero
    ga = parse_table_stats_http(netid, "incentive%3Aasc", r"lucide-pickaxe text-\[\#00DBBC\]", 7)
    base["incentive_green_asc_min_nz"] = min(ga) if ga else None
    return base

# --- СОХРАНЕНИЕ В GOOGLE SHEETS ---

def save_to_gsheet(df: pd.DataFrame):
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_PATH, scopes)
    client = gspread.authorize(creds)
    sh = client.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet("taostats")
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet("taostats", rows="1000", cols="20")
    data = [df.columns.tolist()] + df.values.tolist()
    ws.update(data)
    print("✅ Данные записаны в Google Sheets")

# --- ENTRYPOINT ---

def main():
    # определяем список netid'ов
    rng = os.environ.get("NETID_RANGE")
    if rng:
        start, end = map(int, rng.split("-"))
        netids = [str(i) for i in range(start, end+1)]
    else:
        total = get_total_netids()
        print(f"Найдено подсетей: {total}")
        netids = [str(i) for i in range(1, total+1)]

    all_data = []
    for nid in netids:
        try:
            print(f"→ Обработка NetUID {nid}")
            rec = process_single_netid_http(nid)
            all_data.append(rec)
        except Exception as e:
            print(f"[X] Ошибка {nid}: {e}")

    if all_data:
        df = pd.DataFrame(all_data)
        save_to_gsheet(df)
    else:
        print("Ничего не собрано.")

if __name__ == "__main__":
    main()
