import logging
import random
import re
import time

import requests

logger = logging.getLogger(__name__)

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": _CHROME_UA,
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}

# Brazilian price: "R$ 1.299,90" or "1.299,90" or "299,90"
_PRICE_RE = re.compile(r"R\$?\s*([\d]{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}|\d+)")


def parse_price(text: str) -> float | None:
    """Parse Brazilian price format to float. Returns None if unparseable."""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    raw = m.group(1)
    # "1.299,90" → "1299.90"; "299,90" → "299.90"
    normalized = raw.replace(".", "").replace(",", ".") if "," in raw else raw
    try:
        val = float(normalized)
        return val if val > 0 else None
    except ValueError:
        return None


def polite_delay(min_s: float = 1.0, max_s: float = 3.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


def make_session(use_cloudscraper: bool = False) -> requests.Session:
    """Return a configured requests session, optionally with cloudscraper."""
    if use_cloudscraper:
        try:
            import cloudscraper  # type: ignore

            s = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "desktop": True}
            )
            s.headers.update({"Accept-Language": "pt-BR,pt;q=0.9"})
            return s
        except ImportError:
            logger.warning("cloudscraper not installed; falling back to requests.")

    s = requests.Session()
    s.headers.update(HEADERS)
    return s
