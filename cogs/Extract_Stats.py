#!/usr/bin/env python3
import argparse
import io
import os
import platform
import sys
from typing import Optional

import pandas as pd
import requests


def log(msg: str) -> None:
    print(msg, flush=True)


def robust_read_csv(url: str) -> pd.DataFrame:
    """
    Try to read a CSV robustly:
    - Handle BOM/encodings
    - Auto-detect delimiter (sep=None, engine='python')
    - Fall back to common delimiters if needed
    """
    log(f"Attempting direct CSV download from: {url}")
    r = requests.get(url, timeout=45)
    r.raise_for_status()

    # Quick content-type hint (not authoritative, but may help debug)
    ctype = r.headers.get("Content-Type", "")
    log(f"Content-Type: {ctype}")

    # Try multiple decodings
    encodings = ["utf-8-sig", "utf-8", "latin-1"]
    errors: list[str] = []
    for enc in encodings:
        try:
            text = r.content.decode(enc)
        except Exception as e:
            errors.append(f"decode({enc}): {e}")
            continue

        # First try sniffing delimiter
        for attempt in [
            {"sep": None, "engine": "python"},
            {"sep": ",", "engine": None},
            {"sep": ";", "engine": None},
            {"sep": "\t", "engine": None},
        ]:
            try:
                df = pd.read_csv(
                    io.StringIO(text),
                    sep=attempt["sep"],
                    engine=attempt["engine"] or "c",
                )
                if df.empty:
                    raise ValueError("DataFrame is empty after parsing")
                log(f"CSV parsed successfully with sep={attempt['sep'] or 'auto'} enc={enc}. Shape={df.shape}")
                return df
            except Exception as e:
                errors.append(f"read_csv(sep={attempt['sep']}, enc={enc}): {e}")

    # If we got here, we failed all attempts
    raise RuntimeError(
        "Direct CSV download and parsing failed. Attempts:\n  - " + "\n  - ".join(errors)
    )


def scrape_table_with_selenium(url: str, css_selector: Optional[str] = None, wait_seconds: int = 20) -> pd.DataFrame:
    """
    Use Selenium Manager (Selenium 4.6+) so you don't need to manage chromedriver.
    Optionally set CHROME_BINARY to point to a Chromium/Chrome binary.
    """
    log("Switching to Selenium extraction method...")
    # Deferred imports so environments without Selenium can still run CSV path
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    options = Options()
    # Headless mode that works with recent Chrome
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")

    chrome_binary = os.environ.get("CHROME_BINARY")
    if chrome_binary:
        log(f"Using Chrome binary at: {chrome_binary}")
        options.binary_location = chrome_binary

    # Let Selenium Manager resolve the correct driver automatically
    driver = webdriver.Chrome(options=options)

    try:
        driver.get(url)
        log(f"Loaded page: {url}")

        selector = css_selector or "table"
        log(f"Waiting for selector: {selector}")
        WebDriverWait(driver, wait_seconds).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, selector))
        )

        html = driver.page_source
        dfs = pd.read_html(html)
        if not dfs:
            raise RuntimeError("No HTML tables found on the page.")
        df = dfs[0]
        log(f"Selenium extracted table. Shape={df.shape}")
        return df
    finally:
        driver.quit()


def main():
    parser = argparse.ArgumentParser(description="Extract stats from CSV with Selenium fallback.")
    parser.add_argument("--url", required=True, help="CSV download URL or page URL.")
    parser.add_argument("--selector", help="CSS selector for table when using Selenium (optional).")
    parser.add_argument("--out", help="Optional path to write extracted CSV.")
    args = parser.parse_args()

    log(f"Platform: {platform.platform()} | Machine: {platform.machine()}")
    log(f"Python: {sys.version.split()[0]} | pandas: {pd.__version__}")

    df = None
    try:
        df = robust_read_csv(args.url)
    except Exception as e:
        log(f"Direct CSV download failed: {e}")
        try:
            df = scrape_table_with_selenium(args.url, css_selector=args.selector)
        except Exception as se:
            log(f"An error occurred during Selenium extraction: {se}")
            log("Failed to extract data.")
            sys.exit(1)

    if args.out:
        df.to_csv(args.out, index=False)
        log(f"Wrote output to: {args.out}")
    else:
        # Print a preview to stdout
        with pd.option_context("display.max_columns", 50, "display.width", 200):
            print(df.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
