import os
import json
import re
from time import sleep
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright

SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_NAME = "taostats subnets"

def fetch_html_playwright(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="load", timeout=120000)
        sleep(2)
        html = page.content()
        browser.close()
        return html

def get_max_subnets(html):
    soup = BeautifulSoup(html, "html.parser")
    info_p = soup.find("p", string=re.compile(r"Showing.*of \d+ entries"))
    if not info_p:
        raise RuntimeError("Не найден элемент с числом подсетей!")
    match = re.search(r"of\s+(\d+)\s+entries", info_p.text)
    if not match:
        raise RuntimeError("Не удалось извлечь число подсетей!")
    return int(match.group(1))

def clean_concat_texts(elements):
    return ''.join([el.get_text(strip=True) for el in elements])

def clean_bittensor(text):
    return text.replace("Bittensor", "").strip()

def clean_emission(text):
    return text.replace("%", "", 1).strip()

def parse_metagraph(html):
    soup = BeautifulSoup(html, "html.parser")
    netuid = ""
    netuid_block = soup.find("p", string="Netuid:")
    if netuid_block:
        netuid_val = netuid_block.find_next_sibling("p")
        netuid = netuid_val.get_text(strip=True) if netuid_val else ""
    subnet_name = ""
    title = soup.find("p", class_=re.compile(r"font-bold.*text-2xl"))
    if title:
        subnet_name = title.get_text(strip=True)
    reg_date = ""
    reg_p = soup.find("p", string="Reg:")
    if reg_p:
        reg_val = reg_p.find_next_sibling("p")
        reg_date = reg_val.get_text(strip=True) if reg_val else ""
    net_key = ""
    netkey_block = soup.find("a", href=re.compile(r"^/account/"))
    if netkey_block:
        netkey_span = netkey_block.find("span")
        net_key = netkey_span.get_text(strip=True) if netkey_span else ""
    emission = ""
    emissions_label = soup.find("p", string=re.compile(r"Emissions"))
    if emissions_label:
        em_div = emissions_label.find_parent("div", class_=re.compile("flex"))
        if em_div:
            vals = em_div.find_next_sibling("div")
            if vals:
                all_ps = vals.find_all("p")
                emission = clean_concat_texts(all_ps)
                emission = clean_emission(emission)
    # --- Только правильный price! ---
    price = ""
    price_section = soup.find("p", string="Price")
    if price_section:
        price_outer = price_section.find_parent("div", class_=re.compile(r"flex-col gap-4"))
        if price_outer:
            flexrow = price_outer.find("div", class_=re.compile(r"flex-row items-end"))
            if flexrow:
                ps = flexrow.find_all("p", recursive=False)
                price = ''.join([p.get_text(strip=True) for p in ps])
                price = clean_bittensor(price)
    reg_cost = ""
    reg_cost_label = soup.find("p", string=re.compile(r"Reg Cost"))
    if reg_cost_label:
        cost_div = reg_cost_label.find_parent("div", class_=re.compile("flex"))
        if cost_div:
            vals = cost_div.find_next_sibling("div")
            if vals:
                all_ps = vals.find_all("p")
                reg_cost = clean_concat_texts(all_ps)
                reg_cost = clean_bittensor(reg_cost)
    discord = ""
    discord_img = soup.find("img", src="/images/logo/discord.svg")
    if discord_img and discord_img.parent.name == "a":
        discord = discord_img.parent.get("href", "")
    github = ""
    github_img = soup.find("img", src="/images/logo/github.svg")
    if github_img and github_img.parent.name == "a":
        github = github_img.parent.get("href", "")
    return [netuid, subnet_name, reg_date, net_key, discord, github, emission, price, reg_cost]

def google_sheets_write(data, service_account_json, spreadsheet_id, sheet_name):
    creds = Credentials.from_service_account_info(json.loads(service_account_json))
    service = build('sheets', 'v4', credentials=creds)
    sheet = service.spreadsheets()
    sheet.values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A2:Z"
    ).execute()
    body = {"values": data}
    sheet.values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A2",
        valueInputOption="RAW",
        body=body
    ).execute()

if __name__ == "__main__":
    base_url = "https://taostats.io/subnets"
    html = fetch_html_playwright(base_url)
    max_subnets = get_max_subnets(html)
    print(f"Всего подсетей: {max_subnets}")
    all_data = []
    header = [
        "subnet_id", "subnet_name", "reg_date", "net_key",
        "discord", "github", "emission", "price", "reg_cost"
    ]
    all_data.append(header)
    for netid in range(max_subnets):
        url = f"https://taostats.io/subnets/{netid}/metagraph?order=stake%3Adesc&limit=100"
        try:
            print(f"Парсим {url} ...")
            html_subnet = fetch_html_playwright(url)
            row = parse_metagraph(html_subnet)
            all_data.append(row)
            print(f"  {row[0]} {row[1]}")
        except Exception as e:
            print(f"Ошибка при парсинге {url}: {e}")
            all_data.append([str(netid), "ERROR"] + [""] * (len(header) - 2))
    google_sheets_write(all_data, SERVICE_ACCOUNT_JSON, SPREADSHEET_ID, SHEET_NAME)
    print(f"Записано {len(all_data)-1} строк в Google Sheet '{SHEET_NAME}'.")
