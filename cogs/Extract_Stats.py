import csv
import io
import sys
from typing import List, Dict, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

DEFAULT_PAGE_URL = "http://178.18.248.164:7012/games/5558"
USER_AGENT = "CSV-FirstTwoRows/1.0 (+https://github.com)"


def fetch_text(url: str, timeout: int = 20, max_size_bytes: int = 12 *
