"""
W. Buscatti scraper — Loja Integrada platform (store ID 488685).

Material is encoded directly in the URL path:
  /masculino/pulseiras/{material}/{slug}

Strategy:
  1. Try category URLs for each material (multiple path candidates).
  2. Parse product cards with several CSS selector fallbacks (theme varies).
  3. Paginate via ?pg=N until an empty page is returned.
  4. Fallback: Loja Integrada search endpoint filtered by material keyword.
"""
import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup, Tag

from .models import Product
from .utils import parse_price, polite_delay

logger = logging.getLogger(__name__)

STORE_URL = "https://www.wbuscatti.com.br"
STORE_ID = "488685"

# Candidate URL paths per material (tried in order).
_MATERIAL_PATHS: dict[str, list[str]] = {
    "couro": [
        "/masculino/pulseiras/couro",
        "/pulseiras/couro",
        "/couro",
        "/masculino/couro",
    ],
    "metal": [
        "/masculino/pulseiras/metal",
        "/masculino/pulseiras/aco",
        "/pulseiras/metal",
        "/metal",
        "/masculino/metal",
    ],
    "corda": [
        "/masculino/pulseiras/corda",
        "/masculino/pulseiras/corda-e-tecido",
        "/pulseiras/corda",
        "/corda",
    ],
    "pedra": [
        "/masculino/pulseiras/pedra",
        "/masculino/pulseiras/pedras-naturais",
        "/pulseiras/pedra",
        "/pedra",
    ],
}

# Loja Integrada search fallback keywords per material
_SEARCH_KEYWORDS: dict[str, str] = {
    "couro": "pulseira couro",
    "metal": "pulseira metal",
    "corda": "pulseira corda",
    "pedra": "pulseira pedra",
}

# CSS selectors tried in order for product cards
_CARD_SELECTORS = [
    ".produto",
    ".produto-item",
    "li.produto",
    '[class*="produto"]',
    ".item-produto",
    ".product-item",
    ".prateleira li",
    ".shelf-item",
]

# CSS selectors tried in order for product name inside a card
_NAME_SELECTORS = [
    ".produto-nome",
    ".titulo",
    ".produto-titulo",
    "h2",
    "h3",
    "h4",
    '[class*="nome"]',
    '[class*="titulo"]',
    '[class*="name"]',
    ".item-title",
    "a",
]

# CSS selectors tried in order for price inside a card
_PRICE_SELECTORS = [
    ".produto-preco",
    ".preco-por",
    ".preco-promocional",
    ".preco",
    ".price",
    '[class*="preco"]',
    '[class*="price"]',
    ".valor",
    '[class*="valor"]',
]


def _first_text(card: Tag, selectors: list[str]) -> Optional[str]:
    for sel in selectors:
        try:
            el = card.select_one(sel)
            if el:
                text = el.get_text(" ", strip=True)
                if text:
                    return text
        except Exception:
            continue
    return None


def _parse_cards(soup: BeautifulSoup) -> list[dict]:
    cards: list[Tag] = []

    for sel in _CARD_SELECTORS:
        cards = soup.select(sel)
        if len(cards) >= 2:
            break

    # Generic heuristic fallback: any li/div containing a price string
    if not cards:
        price_re = re.compile(r"R\$\s*\d")
        for el in soup.find_all(["li", "div"]):
            if price_re.search(el.get_text()):
                children = el.find_all(["li", "div"], recursive=False)
                if not children:
                    cards.append(el)
        cards = list(dict.fromkeys(cards))  # deduplicate

    products = []
    for card in cards:
        name = _first_text(card, _NAME_SELECTORS)
        price_text = _first_text(card, _PRICE_SELECTORS)

        # If dedicated price selector failed, search entire card text
        if not price_text:
            price_text = card.get_text(" ", strip=True)

        price = parse_price(price_text)
        if name and price:
            products.append({"name": name, "price": price})

    return products


def _fetch_page(url: str, session: requests.Session) -> tuple[list[dict], bool]:
    """Fetch one listing page. Returns (products, had_results)."""
    try:
        resp = session.get(url, timeout=20)
    except requests.RequestException as exc:
        logger.warning("W. Buscatti GET %s: %s", url, exc)
        return [], False

    if resp.status_code == 404:
        return [], False
    if resp.status_code != 200:
        logger.warning("W. Buscatti %s → HTTP %d", url, resp.status_code)
        return [], False

    soup = BeautifulSoup(resp.text, "lxml")
    products = _parse_cards(soup)
    return products, True


def _scrape_category(base_url: str, session: requests.Session) -> list[dict]:
    """Paginate through a category URL and collect all products."""
    all_products: list[dict] = []
    page = 1

    while page <= 30:  # safety cap
        url = base_url if page == 1 else f"{base_url}?pg={page}"
        products, ok = _fetch_page(url, session)

        if not ok:
            break
        if not products:
            # Empty page signals end of pagination
            break

        all_products.extend(products)
        logger.debug("W. Buscatti %s page %d: %d products", base_url, page, len(products))
        page += 1
        polite_delay(1.5, 3.0)

    return all_products


def _scrape_search(keyword: str, session: requests.Session) -> list[dict]:
    """Use Loja Integrada search endpoint as a fallback."""
    url = f"{STORE_URL}/loja/busca.php"
    params = {"loja": STORE_ID, "query": keyword}
    try:
        resp = session.get(url, params=params, timeout=20)
    except requests.RequestException as exc:
        logger.warning("W. Buscatti search '%s': %s", keyword, exc)
        return []

    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    return _parse_cards(soup)


def scrape(session: requests.Session) -> list[Product]:
    results: list[Product] = []

    for material, paths in _MATERIAL_PATHS.items():
        found = False

        for path in paths:
            url = STORE_URL + path
            products = _scrape_category(url, session)

            if products:
                found = True
                for pd in products:
                    results.append(Product(
                        store="W. Buscatti",
                        name=pd["name"],
                        price=pd["price"],
                        material=material,
                        url=url,
                    ))
                logger.info("W. Buscatti [%s] via %s: %d products", material, path, len(products))
                break  # first working path wins

            polite_delay(1.0, 2.0)

        if not found:
            # Fallback: keyword search
            logger.info("W. Buscatti [%s]: no category match, trying search", material)
            kw = _SEARCH_KEYWORDS[material]
            products = _scrape_search(kw, session)
            for pd in products:
                results.append(Product(
                    store="W. Buscatti",
                    name=pd["name"],
                    price=pd["price"],
                    material=material,
                    url=STORE_URL,
                ))
            if products:
                logger.info("W. Buscatti [%s] via search '%s': %d products", material, kw, len(products))
            else:
                logger.warning(
                    "W. Buscatti [%s]: 0 products found. "
                    "The site may require browser rendering or different selectors. "
                    "Inspect https://www.wbuscatti.com.br in DevTools to verify CSS classes.",
                    material,
                )

    logger.info("W. Buscatti total: %d products", len(results))
    return results
