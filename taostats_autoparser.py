import os
import requests
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials

API_BASE = "https://taostats.io/api"
SS_ID   = os.environ['SPREADSHEET_ID']
CREDS   = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', 'creds.json')

session = requests.Session()
session.headers.update({"User-Agent":"TAO-API-Parser/1.0"})

def fetch_all_subnets():
    # сразу запрашиваем limit=1000, чтобы получить все
    resp = session.get(f"{API_BASE}/subnets?limit=1000", timeout=10)
    resp.raise_for_status()
    j = resp.json()
    return j['data']  # список dict с netuid, name, reg, price, emissions, regCost, key и т.д.

def fetch_metagraph(netuid, order):
    resp = session.get(f"{API_BASE}/subnets/{netuid}/metagraph?order={order}&limit=100", timeout=10)
    resp.raise_for_status()
    return resp.json()['data']  # список узлов с полями stake, incentive, type и т.д.

def process():
    rows = []
    for sn in fetch_all_subnets():
        nid = sn['netuid']
        # усредняем vtrust
        mg = fetch_metagraph(nid, "type:desc")
        vtrust = [node['vtrustShield'] for node in mg if 'vtrustShield' in node]
        row = {
            'netuid': nid,
            'name': sn.get('name'),
            'reg_date': sn.get('regTime'),
            'price': sn.get('price'),
            'emissions': sn.get('emissions'),
            'reg_cost': sn.get('regCost'),
            'vtrust_shield_avg': sum(vtrust)/len(vtrust) if vtrust else None,
            # аналогично для мотыг:
            **{/* incentive_orange_max, incentive_green_desc_max, incentive_green_asc_min_nz */}
        }
        rows.append(row)
    return pd.DataFrame(rows)

def save_to_gsheet(df):
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS, scopes)
    client = gspread.authorize(creds)
    sh = client.open_by_key(SS_ID)
    try:
        ws = sh.worksheet('taostats')
        ws.clear()
    except:
        ws = sh.add_worksheet('taostats', rows="1000", cols=str(len(df.columns)))
    data = [df.columns.tolist()] + df.values.tolist()
    ws.update(data)

def main():
    df = process()
    save_to_gsheet(df)
    print("Готово —", len(df), "записей.")

if __name__=='__main__':
    main()
