import os
import re
import time
import pandas as pd
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException
import chromedriver_autoinstaller

# Google Sheets
import gspread
from oauth2client.service_account import ServiceAccountCredentials

chromedriver_autoinstaller.install()

def create_driver():
    options = uc.ChromeOptions()
    options.headless = True
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    return uc.Chrome(options=options)

def get_total_netids():
    driver = create_driver()
    driver.get("https://taostats.io/subnets")
    # ждём появления текста "of N entries"
    p = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.XPATH, "//p[contains(text(),'entries')]"))
    )
    text = p.text  # e.g. "Showing 1 to 10 of 97 entries"
    driver.quit()
    m = re.search(r"of\s+(\d+)\s+entries", text)
    return int(m.group(1)) if m else 0

def wait_if_captcha_present(driver, timeout=5):
    try:
        WebDriverWait(driver, timeout).until(
            EC.text_to_be_present_in_element(
                (By.CSS_SELECTOR, "div.flex.flex-row.items-center.gap-1 p.font-fira"),
                "NetUID"
            )
        )
        print("⚠️ Капча обнаружена! Решите её вручную.")
        WebDriverWait(driver, 300).until_not(
            EC.text_to_be_present_in_element(
                (By.CSS_SELECTOR, "div.flex.flex-row.items-center.gap-1 p.font-fira"),
                "NetUID"
            )
        )
        print("✅ Капча ушла.")
    except TimeoutException:
        pass

def safe_find_text(driver, by, selector):
    try:
        return driver.find_element(by, selector).text.strip()
    except NoSuchElementException:
        return None

def extract_price_parts_by_label(driver, label_text):
    try:
        parent = driver.find_element(
            By.XPATH,
            f"//p[text()='{label_text}']/ancestor::div[contains(@class, 'bg-[#272727]')]"
        )
        texts = [p.text for p in parent.find_elements(By.TAG_NAME, 'p') if p.text.strip()]
        full = " ".join(texts)
        match = re.search(r"(\d+\.\d+|\d+)(?:\s*\d+)?", full)
        return match.group(0).replace(" ", "") if match else None
    except Exception:
        return None

def parse_main_page(driver, netid):
    url = f"https://taostats.io/subnets/{netid}/metagraph?order=type%3Aasc"
    driver.get(url)
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//p[text()='Price']")))
    data = {
        'netuid': safe_find_text(driver, By.CSS_SELECTOR, "div.flex.flex-row.items-center.gap-1 p.font-fira"),
        'name': safe_find_text(driver, By.CSS_SELECTOR, "p.font-bold"),
        'reg_date': safe_find_text(driver, By.XPATH, "//p[text()='Reg:']/following-sibling::p"),
        'price': extract_price_parts_by_label(driver, "Price"),
        'emission': extract_price_parts_by_label(driver, "Emissions"),
        'reg_cost': extract_price_parts_by_label(driver, "Reg Cost"),
        'github': driver.find_element(By.CSS_SELECTOR, "a[href*='github.com']").get_attribute('href') if driver.find_elements(By.CSS_SELECTOR, "a[href*='github.com']") else None,
        'discord': driver.find_element(By.CSS_SELECTOR, "a[href*='discord.com']").get_attribute('href') if driver.find_elements(By.CSS_SELECTOR, "a[href*='discord.com']") else None,
        'key': safe_find_text(driver, By.CSS_SELECTOR, "span.flex-shrink-0.truncate")
    }
    return data

def parse_table_stats(driver, netid, order, icon_css, col_index):
    url = f"https://taostats.io/subnets/{netid}/metagraph?order={order}&limit=100"
    driver.get(url)
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.ID, 'taostats-table')))
    table = driver.find_element(By.ID, 'taostats-table')
    vals = []
    for row in table.find_elements(By.TAG_NAME, 'tr'):
        if not row.find_elements(By.CSS_SELECTOR, icon_css):
            continue
        try:
            vals.append(float(row.find_elements(By.TAG_NAME, 'td')[col_index].text))
        except:
            pass
    return vals

def process_single_netid(nid):
    driver = create_driver()
    try:
        print(f"→ Обработка {nid}")
        driver.get(f"https://taostats.io/subnets/{nid}/metagraph?order=stake%3Adesc")
        wait_if_captcha_present(driver)
        data = parse_main_page(driver, nid)
        v = parse_table_stats(driver, nid, 'type%3Adesc', 'svg.lucide-shield.text-indigo-400', 4)
        data['vtrust_shield_avg'] = sum(v)/len(v) if v else None
        o = parse_table_stats(driver, nid, 'type%3Aasc', r"svg.lucide-pickaxe.text-\[\#F90\]", 7)
        data['incentive_orange_max'] = max(o) if o else None
        gd = parse_table_stats(driver, nid, 'incentive%3Adesc', r"svg.lucide-pickaxe.text-\[\#00DBBC\]", 7)
        data['incentive_green_desc_max'] = max(gd) if gd else None
        ga = parse_table_stats(driver, nid, 'incentive%3Aasc', r"svg.lucide-pickaxe.text-\[\#00DBBC\]", 7)
        data['incentive_green_asc_min_nz'] = min(ga) if ga else None
        return data
    finally:
        driver.quit()

def save_to_gsheet(df: pd.DataFrame):
    # считываем переменные
    spreadsheet_id = os.environ['SPREADSHEET_ID']
    creds_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', 'creds.json')
    scopes = ['https://www.googleapis.com/auth/spreadsheets']
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scopes)
    client = gspread.authorize(creds)
    sh = client.open_by_key(spreadsheet_id)
    try:
        ws = sh.worksheet('taostats')
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet('taostats', rows="1000", cols="20")
    data = [df.columns.tolist()] + df.values.tolist()
    ws.update(data)
    print("✅ Данные записаны в Google Sheets")

def main():
    total = get_total_netids()
    print(f"Найдено подсетей: {total}")
    netids = [str(i) for i in range(1, total+1)]
    all_data = []
    for nid in netids:
        try:
            all_data.append(process_single_netid(nid))
        except Exception as e:
            print(f"[X] Ошибка {nid}: {e}")
    if all_data:
        df = pd.DataFrame(all_data)
        save_to_gsheet(df)
    else:
        print("Ничего не собрано.")

if __name__ == '__main__':
    main()
