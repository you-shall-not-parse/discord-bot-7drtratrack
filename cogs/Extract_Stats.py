import csv
import io
import sys
from typing import List, Dict, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

DEFAULT_PAGE_URL = "http://178.18.248.164:7012/games/5558"
USER_AGENT = "CSV-FirstTwoRows/1.0 (+https://github.com)"


def fetch_text(url: str, timeout: int = 20, max_size_bytes: int = 12 * 1024 * 1024) -> str:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    cl = resp.headers.get("Content-Length")
    if cl and cl.isdigit() and int(cl) > max_size_bytes:
        raise ValueError(f"Remote content too large: {int(cl)} bytes")
    data = resp.content
    if len(data) > max_size_bytes:
        raise ValueError(f"Remote content too large after download: {len(data)} bytes")
    # Use server-provided encoding or fallback to utf-8
    encoding = resp.encoding or "utf-8"
    try:
        return data.decode(encoding, errors="replace")
    except Exception:
        return data.decode("utf-8", errors="replace")


def is_probably_csv_url(url: str) -> bool:
    try:
        return urlparse(url).path.lower().endswith(".csv")
    except Exception:
        return False


def find_csv_links_from_html(page_html: str, base_url: str) -> List[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    links: List[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        label = (a.get_text() or "").strip().lower()
        if ".csv" in href.lower() or "csv" in label:
            links.append(urljoin(base_url, href))
    # Deduplicate, preserve order
    seen = set()
    deduped = []
    for u in links:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    # Prefer links that end with .csv
    deduped.sort(key=lambda u: (0 if is_probably_csv_url(u) else 1, u))
    return deduped


def sniff_dialect(sample: str) -> csv.Dialect:
    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(sample, delimiters=[",", ";", "\t", "|"])
        dialect.skipinitialspace = True
        return dialect
    except Exception:
        class Simple(csv.Dialect):
            delimiter = ","
            quotechar = '"'
            doublequote = True
            skipinitialspace = True
            lineterminator = "\n"
            quoting = csv.QUOTE_MINIMAL
        return Simple()


def read_first_two_rows(csv_text: str) -> Optional[List[Dict[str, str]]]:
    sample = csv_text[:4096]
    dialect = sniff_dialect(sample)
    f = io.StringIO(csv_text)
    # Try DictReader (header expected)
    reader = csv.DictReader(f, dialect=dialect)
    if reader.fieldnames and all(h is not None for h in reader.fieldnames):
        rows: List[Dict[str, str]] = []
        try:
            for _ in range(2):
                r = next(reader)
                rows.append(r)
        except StopIteration:
            pass
        return rows if rows else None
    # Fallback: no header -> use csv.reader and synthesize headers
    f.seek(0)
    reader2 = csv.reader(f, dialect=dialect)
    rows_l: List[List[str]] = []
    try:
        for _ in range(2):
            rows_l.append(next(reader2))
    except StopIteration:
        pass
    if not rows_l:
        return None
    max_len = max(len(r) for r in rows_l)
    headers = [f"col{i+1}" for i in range(max_len)]
    dict_rows = []
    for r in rows_l:
        padded = r + [""] * (max_len - len(r))
        dict_rows.append(dict(zip(headers, padded)))
    return dict_rows


def resolve_csv_url(page_or_csv_url: str) -> str:
    if is_probably_csv_url(page_or_csv_url):
        return page_or_csv_url
    html = fetch_text(page_or_csv_url)
    links = find_csv_links_from_html(html, page_or_csv_url)
    if not links:
        raise RuntimeError(f"No CSV link found on page: {page_or_csv_url}")
    return links[0]


def main():
    page_or_csv = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PAGE_URL
    try:
        csv_url = resolve_csv_url(page_or_csv)
        print(f"Using CSV: {csv_url}")
        csv_text = fetch_text(csv_url)
        first_two = read_first_two_rows(csv_text)
        if not first_two:
            print("No rows found or could not parse CSV.", file=sys.stderr)
            sys.exit(2)
        for i, row in enumerate(first_two, start=1):
            print(f"Row {i}: {row}")
    except requests.HTTPError as e:
        print(f"HTTP error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
