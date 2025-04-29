import os
import re
import requests
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ID Google Sheets из секретов
SPREADSHEET_ID = os.environ['SPREADSHEET_ID']
# Путь к JSON кредам сервисного аккаунта
CREDS_PATH = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', 'creds.json')

# Настройка сессии HTTP
session = requests.Session()
session.headers.update({
    "User-Agent": "TAO-API-Parser/1.0"
})

API_BASE = "https://taostats.io/api"


def fetch_all_subnets():
    """Получаем список всех подсетей через JSON API"""
    resp = session.get(f"{API_BASE}/subnets?limit=1000", timeout=10)
    resp.raise_for_status()
    j = resp.json()
    return j.get('data', [])


def fetch_metagraph(netuid, order):
    """Получаем метаграф для подсети"""
    resp = session.get(
        f"{API_BASE}/subnets/{netuid}/metagraph?order={order}&limit=100", timeout=10
    )
    resp.raise_for_status()
    return resp.json().get('data', [])


def process():
    rows = []
    for sn in fetch_all_subnets():
        nid = sn.get('netuid')
        # базовые поля из списка
        row = {
            'netuid': nid,
            'name': sn.get('name'),
            'reg_date': sn.get('regTime'),
            'price': sn.get('price'),
            'emissions': sn.get('emissions'),
            'reg_cost': sn.get('regCost')
        }
        # vtrust
        mg1 = fetch_metagraph(nid, 'type:desc')
        v = [item.get('vtrustShield', 0) for item in mg1]
        row['vtrust_shield_avg'] = sum(v)/len(v) if v else None
        # incentive orange max
        mg2 = fetch_metagraph(nid, 'type:asc')
        o = [item.get('incentive', 0) for item in mg2 if item.get('type')=='orange']
        row['incentive_orange_max'] = max(o) if o else None
        # incentive green desc
        mg3 = fetch_metagraph(nid, 'incentive:desc')
        gd = [item.get('incentive', 0) for item in mg3 if item.get('type')=='green']
        row['incentive_green_desc_max'] = max(gd) if gd else None
        # incentive green asc non-zero min
        mg4 = fetch_metagraph(nid, 'incentive:asc')
        ga = [item.get('incentive', 0) for item in mg4 if item.get('type')=='green' and item.get('incentive',0)>0]
        row['incentive_green_asc_min_nz'] = min(ga) if ga else None
        rows.append(row)
    return pd.DataFrame(rows)


def save_to_gsheet(df: pd.DataFrame):
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_PATH, scopes)
    client = gspread.authorize(creds)
    sh = client.open_by_key(SPREADSHEET_ID)
    try:
        ws = sh.worksheet('taostats')
        ws.clear()
    except:
        ws = sh.add_worksheet('taostats', rows="1000", cols=str(len(df.columns)))
    data = [df.columns.tolist()] + df.values.tolist()
    ws.update(data)
    print(f"✅ Записано {len(df)} строк в Google Sheets")


def main():
    df = process()
    save_to_gsheet(df)


if __name__ == '__main__':
    main()
