#!/usr/bin/env python3
import csv
import sys
import time
from playwright.sync_api import sync_playwright

URL = "https://taostats.io/subnets"

def fetch_all_subnets() -> list[dict]:
    """Launch a headless Chromium, navigate to the subnets page,
    scroll the grid to load all rows, then scrape headers + cell text."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, timeout=60_000)

        # wait for the data grid to appear
        page.wait_for_selector('div[role="row"][data-rowindex]', timeout=30_000)

        # scroll down until no new rows appear
        prev_count = -1
        while True:
            rows = page.query_selector_all('div[role="row"][data-rowindex]')
            if len(rows) == prev_count:
                break
            prev_count = len(rows)
            # scroll to bottom
            page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            time.sleep(1)  # give the grid time to fetch/render

        # grab column headers
        header_elems = page.query_selector_all('div[role="columnheader"]')
        headers = [h.inner_text().strip() for h in header_elems]

        # collect every row
        data = []
        for row in rows:
            cells = row.query_selector_all('div[role="cell"]')
            texts = [c.inner_text().strip() for c in cells]
            # zip into a dict, skip rows that don't match header count
            if len(texts) == len(headers):
                data.append(dict(zip(headers, texts)))

        browser.close()
        return data

def main():
    try:
        subs = fetch_all_subnets()
    except Exception as e:
        print("❌ Failed to fetch subnets:", e, file=sys.stderr)
        sys.exit(1)

    if not subs:
        print("⚠️ No subnets found.", file=sys.stderr)
        sys.exit(1)

    # write out a CSV; replace this with your Google Sheets code as needed
    out_file = "subnets.csv"
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=subs[0].keys())
        writer.writeheader()
        writer.writerows(subs)

    print(f"✅ Wrote {len(subs)} rows to {out_file}")

if __name__ == "__main__":
    main()
