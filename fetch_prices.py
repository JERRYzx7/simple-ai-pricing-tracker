#!/usr/bin/env python3
"""Read data/products.csv and fetch the current price for each product from
its Best Buy product page using a real (non-headless) Chrome browser via
Selenium, appending one snapshot row per product to data/price_snapshots.csv.

Why Selenium + a real, visible browser instead of requests or the official API:
- Best Buy's Developer API signup does not accept a free/personal Gmail
  account, so the official API path (originally planned) turned out to be
  unusable for this assessment.
- Plain HTTP (curl/requests), even with a realistic browser User-Agent, gets
  its TLS/HTTP2 connection reset by Best Buy's anti-bot layer before any
  HTML comes back (verified directly: `curl -A "<chrome UA>" ...` fails with
  ERR_HTTP2_PROTOCOL_ERROR).
- Headless Chrome (`--headless=new`) hits the exact same reset — Best Buy
  appears to fingerprint headless Chrome specifically. Only a real, visible
  Chrome window reliably reached the actual product page in testing.
  => This means a scheduled run (run_snapshot.sh) will briefly pop open a
     Chrome window on the machine running it. That's a real limitation of
     this approach, documented in README.md rather than hidden.
- Reusing a single Chrome session to navigate to a second product page in a
  row also gets that page's connection reset — same failure signature as
  the headless case. A fresh Chrome session per product avoided this
  reliably in testing, so that's what this script does.

This script only READS data/products.csv (the human-maintained product
catalog) and only WRITES data/price_snapshots.csv (the price time series).
It never modifies products.csv.

Price is read from an inline JSON blob Best Buy's Next.js app embeds in the
page (`"customerPrice":<number>`) rather than a CSS selector — this proved
far more stable than guessing at class names, since it's the same key the
page's own React components hydrate from.
"""

import csv
import os
import re
import time
from datetime import datetime, timezone

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

PRODUCTS_PATH = "data/products.csv"
SNAPSHOTS_PATH = "data/price_snapshots.csv"
PRICE_RE = re.compile(r'"customerPrice":([0-9.]+)')
SKU_RE = re.compile(r"/sku/(\d+)|/(\d+)\.p")  # new URL format first, old as fallback
PAGE_LOAD_WAIT_SEC = 6  # let the client-rendered price hydrate
REQUEST_DELAY_SEC = 3  # be polite between page loads

SNAPSHOT_COLUMNS = ["captured_at", "date", "sku", "price", "source"]


def load_products():
    with open(PRODUCTS_PATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_driver():
    opts = Options()
    opts.add_argument("--window-size=1280,1000")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(options=opts)


def normalize_url(url):
    # Non-US IPs otherwise get redirected to an international splash page
    # instead of the product page.
    if "intl=nosplash" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}intl=nosplash"


def extract_sku(url):
    m = SKU_RE.search(url)
    if not m:
        return ""
    return m.group(1) or m.group(2)


def scrape_price(driver, url):
    driver.get(normalize_url(url))
    time.sleep(PAGE_LOAD_WAIT_SEC)
    price_match = PRICE_RE.search(driver.page_source)
    if not price_match:
        raise ValueError("customerPrice not found — the page structure may have changed, the product may be delisted, or we were redirected to a non-product page")
    return float(price_match.group(1))


def append_rows(rows):
    os.makedirs(os.path.dirname(SNAPSHOTS_PATH), exist_ok=True)
    file_exists = os.path.isfile(SNAPSHOTS_PATH)
    with open(SNAPSHOTS_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SNAPSHOT_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def main():
    products = load_products()
    now = datetime.now(timezone.utc).astimezone()
    captured_at = now.isoformat(timespec="seconds")
    today = now.date().isoformat()

    rows = []
    for product in products:
        url = product.get("url", "").strip()
        if not url:
            print(f"[skip] {product.get('display_name', '?')}: no url set, skipping")
            continue

        sku = product.get("sku", "").strip() or extract_sku(url)
        if not sku:
            print(f"[error] {url}: could not parse a sku, skipping")
            continue

        driver = build_driver()  # fresh session per product — see module docstring
        try:
            price = scrape_price(driver, url)
        except Exception as exc:
            print(f"[error] {product.get('display_name', sku)} (sku={sku}) failed to scrape: {exc}")
            continue
        finally:
            driver.quit()

        rows.append({
            "captured_at": captured_at,
            "date": today,
            "sku": sku,
            "price": price,
            "source": "scraped",
        })
        print(f"[ok] {product.get('display_name', sku)} (sku={sku}): ${price}")
        time.sleep(REQUEST_DELAY_SEC)

    if not rows:
        print("No rows were scraped successfully — check the urls in data/products.csv.")
        return

    append_rows(rows)
    print(f"Wrote {len(rows)} snapshot(s) to {SNAPSHOTS_PATH}")


if __name__ == "__main__":
    main()
