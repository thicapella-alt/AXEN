"""
╔══════════════════════════════════════════════════════════════════════════╗
║         AXEN — Análise Competitiva de Preços · Web Scraper               ║
║  Lojas: Key Design · Beroc · W. Buscatti                                  ║
║  Materiais: Couro · Metal · Corda · Pedra                                 ║
╚══════════════════════════════════════════════════════════════════════════╝

SETUP:
    pip install -r requirements.txt

EXECUÇÃO:
    python main.py
    python main.py --cloudscraper          # cloudscraper também no Key Design
    python axen_price_scraper.py [--cloudscraper]

PROXY (ex.: IP de datacenter bloqueado — requests/cloudscraper respeitam o ambiente):
    PowerShell: $env:HTTPS_PROXY = "http://user:senha@proxy.exemplo.com:8080"
    bash:       export HTTPS_PROXY=http://user:senha@proxy.exemplo.com:8080

SAÍDA:
    - axen_competitive_prices.csv   (dados brutos)
    - axen_competitive_summary.csv  (tabela agregada por loja/material)
    - relatório impresso no terminal com a tabela final

CONFORMIDADE:
    - Respeita robots.txt (verificado em cada domínio antes de raspar)
    - Delay aleatório entre requisições (2–5s) para não sobrecarregar servidores
    - User-Agent de navegador real
    - Dados públicos de preços apenas (sem login, sem dados de clientes)
"""

import argparse
import time
import random
import re
import logging
import os
import csv
from dataclasses import dataclass, replace as dc_replace
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests
import cloudscraper          # pip install cloudscraper  (contorna Cloudflare básico)
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table
from rich import print as rprint

# ─────────────────────────────────────────────
#  CONFIGURAÇÃO GLOBAL
# ─────────────────────────────────────────────

import sys, io as _io

# Keep a module-level reference so this TextIOWrapper is never GC'd prematurely.
# If basicConfig() is a no-op (root logger already has handlers), the wrapper
# would otherwise have no owner, get collected, and its __del__ would call
# close() on sys.stdout.buffer — closing pytest's capture tmpfile mid-session.
_stdout_stream: _io.TextIOWrapper | object = (
    _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stdout, "buffer") else sys.stdout
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("scraper.log", encoding="utf-8"),
        logging.StreamHandler(stream=_stdout_stream),
    ]
)
log = logging.getLogger(__name__)
console = Console()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

DELAY_MIN = 2.0   # segundos entre requisições
DELAY_MAX = 5.0   # segundos entre requisições (aleatório)
DEBUG_HTML = os.getenv("AXEN_DEBUG_HTML", "0") == "1"
PRICE_AUDIT = False
PRICE_AUDIT_SAMPLES = 10
PRICE_AUDIT_COUNTS: dict[str, int] = {}

# ── Preços AXEN (atualizar antes de cada análise) ──────────────────────────
AXEN_PRICES: dict[str, Optional[float]] = {
    "corda": 125.0,
    "metal": 215.0,
    "couro": 215.0,
    "pedra": None,    # AXEN não tem pedra no lote atual
}


# ─────────────────────────────────────────────
#  MODELOS DE DADOS
# ─────────────────────────────────────────────

@dataclass
class Product:
    store: str
    name: str
    price: float
    material: str           # couro | metal | corda | pedra | desconhecido
    url: str = ""
    category_url: str = ""
    context: str = ""
    delivery_info: str = ""  # ex.: "Frete Grátis", "Entrega em 3 dias", ""


# ─────────────────────────────────────────────
#  UTILITÁRIOS
# ─────────────────────────────────────────────

def polite_sleep():
    """Espera aleatória para não sobrecarregar o servidor."""
    t = random.uniform(DELAY_MIN, DELAY_MAX)
    log.debug(f"Sleeping {t:.1f}s")
    time.sleep(t)


def _get_with_retry(
    session,
    url: str,
    retries: int = 3,
    backoff: float = 5.0,
    **kwargs,
) -> requests.Response:
    """GET com retry exponencial. Lança a última exceção se todas as tentativas falharem."""
    for attempt in range(retries):
        try:
            return session.get(url, **kwargs)
        except requests.RequestException as exc:
            if attempt == retries - 1:
                raise
            wait = backoff * (attempt + 1)
            log.warning("Tentativa %d/%d falhou para %s: %s — aguardando %.0fs", attempt + 1, retries, url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def dump_debug_html(store: str, material: str, url: str, html: str) -> None:
    """Salva HTML para diagnóstico quando não há itens detectados."""
    if not DEBUG_HTML:
        return
    safe_store = re.sub(r"[^a-z0-9]+", "_", store.lower()).strip("_")
    safe_material = re.sub(r"[^a-z0-9]+", "_", material.lower()).strip("_")
    ts = int(time.time())
    out_dir = Path("debug_pages")
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / f"{safe_store}_{safe_material}_{ts}.html"
    out_file.write_text(f"<!-- URL: {url} -->\n{html}", encoding="utf-8")
    log.info(f"[{store}] HTML de diagnóstico salvo em: {out_file}")


def audit_price(store: str, raw_price, parsed_price: Optional[float], source: str = "") -> None:
    """Registra amostras de normalização de preço por loja."""
    if not PRICE_AUDIT:
        return
    count = PRICE_AUDIT_COUNTS.get(store, 0)
    if count >= PRICE_AUDIT_SAMPLES:
        return
    PRICE_AUDIT_COUNTS[store] = count + 1
    suffix = f" | src={source}" if source else ""
    log.info(f"[PRICE_AUDIT][{store}] raw={raw_price!r} -> parsed={parsed_price!r}{suffix}")


_robots_cache: dict[str, Optional[RobotFileParser]] = {}


def check_robots(base_url: str, path: str = "/") -> bool:
    """
    Verifica se o robots.txt permite raspar o caminho indicado.
    Retorna True se permitido, False se bloqueado.
    O resultado é cacheado por domínio para evitar requests repetidos.
    """
    if base_url not in _robots_cache:
        rp = RobotFileParser()
        rp.set_url(urljoin(base_url, "/robots.txt"))
        try:
            rp.read()
            _robots_cache[base_url] = rp
        except Exception as e:
            log.warning("Não foi possível ler robots.txt de %s: %s. Continuando.", base_url, e)
            _robots_cache[base_url] = None

    rp = _robots_cache[base_url]
    if rp is None:
        return True

    allowed = rp.can_fetch(HEADERS["User-Agent"], urljoin(base_url, path))
    if not allowed:
        log.warning("robots.txt BLOQUEIA %s — pulando.", urljoin(base_url, path))
    return allowed


def parse_price(text: str) -> Optional[float]:
    """Converte preços em formato BR/US para float."""
    if text is None:
        return None

    if isinstance(text, (int, float)):
        return float(text)

    raw = str(text).strip()
    if not raw:
        return None

    def _parse_numeric_token(token: str) -> Optional[float]:
        cleaned = re.sub(r"[^\d,.\-]", "", token)
        if not cleaned:
            return None

        has_comma = "," in cleaned
        has_dot = "." in cleaned

        if has_comma and has_dot:
            # Usa o último separador como decimal (ex.: 1.234,56 ou 1,234.56)
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
        elif has_comma:
            # Só vírgula: trata como decimal
            cleaned = cleaned.replace(".", "").replace(",", ".")
        elif has_dot:
            # Só ponto: se vários, assume último como decimal
            if cleaned.count(".") > 1:
                parts = cleaned.split(".")
                cleaned = "".join(parts[:-1]) + "." + parts[-1]

        try:
            return float(cleaned)
        except ValueError:
            return None

    # Captura todos os preços monetários explícitos e ignora parcelas "Nx de R$ ..."
    monetary_values = []
    had_monetary_match = False  # tracks whether any R$ token was found at all
    for match in re.finditer(r"R\$\s*([\d\.,]+)", raw, flags=re.IGNORECASE):
        had_monetary_match = True
        prefix = raw[max(0, match.start() - 14):match.start()].lower()
        if re.search(r"\d+\s*x\s*de\s*$", prefix):
            continue  # skip installment tokens like "3x de R$ 41,67"
        value = _parse_numeric_token(match.group(1))
        if value is not None and value > 0:
            monetary_values.append(value)

    # Se houver "de/por" (preço antigo e promocional), usa o menor.
    if monetary_values:
        return min(monetary_values)

    # R$ appeared but every token was an installment price — do not fall through
    # to raw extraction (which would incorrectly merge "3" + "41,67" → 341.67).
    if had_monetary_match:
        return None

    cleaned = re.sub(r"[^\d,.\-]", "", raw)
    if not cleaned:
        return None
    return _parse_numeric_token(cleaned)


def classify_material(text: str) -> str:
    """
    Classifica material a partir do título/categoria/atributos.
    Ordem de prioridade: mais específico primeiro.
    """
    t = text.lower()
    
    # Pedra (antes de corda pois alguns nomes misturam)
    if any(w in t for w in [
        "pedra", "stone", "hematita", "ônix", "onix", "jade",
        "turquesa", "quartzo", "obsidiana", "bead", "miçanga", "natural"
    ]):
        return "pedra"
    
    # Couro
    if any(w in t for w in [
        "couro", "leather", "pele", "suede", "camurça"
    ]):
        return "couro"
    
    # Metal / Aço
    if any(w in t for w in [
        "metal", "aço", "steel", "inox", "prata", "ouro", "gold",
        "silver", "titanio", "titânio", "corrente", "chain", "elo",
        "grumet", "aro", "rígida", "rigida", "bangle"
    ]):
        return "metal"
    
    # Corda / Náutica / Nylon
    if any(w in t for w in [
        "corda", "rope", "nylon", "náutica", "nautica", "fio",
        "tecido", "borracha", "paracord", "silicone", "wave",
        "âncora", "ancora", "anchor", "knot", "nó"
    ]):
        return "corda"
    
    return "desconhecido"


def is_masculine_product(product: Product) -> bool:
    """Heurística para manter itens masculinos (com fallback para neutros)."""
    text = f"{product.name} {product.url} {product.category_url} {product.context}".lower()
    norm = (
        text.replace("ã", "a")
        .replace("á", "a")
        .replace("â", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("õ", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )

    feminine_terms = [
        "feminino", "feminina", "para ela", "woman", "women",
        "femin", "lady", "girls", "menina", "delicada",
    ]
    if any(term in norm for term in feminine_terms):
        return False

    masculine_terms = [
        "masculino", "masculina", "masculin", "para ele", "homem",
        "male", "men", "joias-masculinas", "pulseiras-masculinas",
    ]
    if any(term in norm for term in masculine_terms):
        return True

    # Fallback: se não há sinal feminino explícito e o item é pulseira,
    # tratamos como masculino/neutro para não perder catálogos válidos (ex.: Beroc).
    return "pulseira" in norm


def get_session(use_cloudscraper: bool = False):
    """Retorna sessão requests ou cloudscraper conforme necessidade."""
    if use_cloudscraper:
        s = cloudscraper.create_scraper()
        s.headers.update(HEADERS)
        return s
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def extract_delivery_info(tag) -> str:
    """
    Tenta extrair informação de prazo/frete visível num card de produto.
    Funciona tanto com Tag do BeautifulSoup quanto com texto bruto.
    Retorna string normalizada ou "" se não encontrar nada relevante.
    """
    if tag is None:
        return ""
    text = tag.get_text(" ", strip=True) if hasattr(tag, "get_text") else str(tag)
    text_lower = text.lower()

    # Frete grátis
    if any(w in text_lower for w in ["frete grátis", "frete gratis", "entrega grátis", "entrega gratis"]):
        return "Frete Grátis"

    # Prazo em dias — captura padrões como "em 3 dias úteis", "em 5 dias", "3 dias úteis"
    import re as _re
    m = _re.search(r"(\d+)\s*dias?\s*(úteis?|uteis?)?", text_lower)
    if m:
        dias = m.group(1)
        tipo = " úteis" if m.group(2) else ""
        return f"Entrega em {dias} dia{'s' if int(dias) > 1 else ''}{tipo}"

    # VTEX / Shopify: "Receba até…" / "Chegará até…" / "Chegará entre X e Y"
    if any(w in text_lower for w in [
        "receba até", "receba ate",
        "chegará até", "chegara ate",
        "chegará entre", "chegara entre",
        "entrega até", "entrega ate",
    ]):
        # Tenta extrair número de dias se estiver explícito
        m2 = _re.search(r"(\d+)\s*dias?\s*(úteis?|uteis?)?", text_lower)
        if m2:
            dias = m2.group(1)
            tipo = " úteis" if m2.group(2) else ""
            return f"Entrega em {dias} dia{'s' if int(dias) > 1 else ''}{tipo}"
        # Tenta extrair datas e calcular dias a partir de hoje
        import datetime as _dt
        _MESES = {
            "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
            "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
        }
        m3 = _re.search(r"(\d{1,2})\s+de\s+([a-z]{3})", text_lower)
        if m3:
            try:
                dia_num = int(m3.group(1))
                mes_num = _MESES.get(m3.group(2), 0)
                if mes_num:
                    hoje = _dt.date.today()
                    ano  = hoje.year if mes_num >= hoje.month else hoje.year + 1
                    data = _dt.date(ano, mes_num, dia_num)
                    diff = (data - hoje).days
                    if 0 < diff <= 60:
                        return f"Entrega em {diff} dia{'s' if diff > 1 else ''}"
            except (ValueError, OverflowError):
                pass
        return "Entrega Agendada"

    # Termos genéricos
    if "entrega rápida" in text_lower or "entrega rapida" in text_lower:
        return "Entrega Rápida"
    if "envio imediato" in text_lower:
        return "Envio Imediato"
    if any(w in text_lower for w in ["frete calculado", "calcular frete", "calculado no checkout", "finalização de compra", "finalizacao de compra"]):
        return "Frete calculado no checkout"

    return ""


# ─────────────────────────────────────────────
#  SCRAPER — BEROC (beroc.com.br)
#  Plataforma: Shopify
#  Paginação: ?page=N  ou link rel="next"
# ─────────────────────────────────────────────

class BerocScraper:
    BASE = "https://beroc.com.br"
    
    # URLs das categorias — ajuste se o site mudar
    CATEGORIES = {
        "corda":  "/collections/pulseiras-de-corda",
        "couro":  "/collections/pulseiras-de-couro",
        "metal":  "/collections/pulseira-de-metal",
        "pedra":  "/collections/pulseira-de-pedra",
    }
    
    def __init__(self, use_cloudscraper: bool = True):
        self.session = get_session(use_cloudscraper=use_cloudscraper)
    
    def scrape(self) -> list[Product]:
        products = []
        for material, cat_path in self.CATEGORIES.items():
            if not check_robots(self.BASE, cat_path):
                continue
            log.info(f"[Beroc] Raspando categoria: {material}")
            products.extend(self._scrape_category(cat_path, material))
        return products
    
    def _scrape_category(self, cat_path: str, material: str) -> list[Product]:
        products = []
        page = 1
        
        while True:
            url = f"{self.BASE}{cat_path}/products.json?limit=250&page={page}"
            log.info(f"[Beroc] Página {page}: {url}")
            
            try:
                resp = _get_with_retry(self.session, url, timeout=15)
                resp.raise_for_status()
                payload = resp.json()
            except Exception as e:
                log.error(f"[Beroc] Erro ao buscar {url}: {e}")
                break
            
            items = payload.get("products", [])
            if not items:
                log.info(f"[Beroc] Sem produtos na página {page} — fim da paginação.")
                break
            
            for item in items:
                product = self._parse_product_json(item, material, url)
                if product:
                    products.append(product)
            
            if len(items) < 250:
                break
            
            page += 1
            polite_sleep()
        
        log.info(f"[Beroc] {material}: {len(products)} produtos coletados.")
        return products
    
    def _parse_product_json(self, item: dict, material: str, cat_url: str) -> Optional[Product]:
        try:
            name = (item.get("title") or "").strip()
            if not name:
                return None
            
            variants = item.get("variants", [])
            if not variants:
                return None
            
            price = None
            for variant in variants:
                raw = variant.get("price")
                candidate = parse_price(str(raw))
                audit_price("Beroc", raw, candidate, source=cat_url)
                if candidate and candidate > 0:
                    price = candidate if price is None else min(price, candidate)
            if price is None or price <= 0:
                return None
            
            handle = item.get("handle", "")
            prod_url = urljoin(self.BASE, f"/products/{handle}") if handle else ""
            
            mat = classify_material(name)
            if mat == "desconhecido":
                mat = material

            return Product(
                store="Beroc",
                name=name,
                price=price,
                material=mat,
                url=prod_url,
                category_url=cat_url,
                context=f"{cat_url} {handle}",
            )
        except Exception as e:
            log.debug(f"[Beroc] Erro ao parsear produto: {e}")
            return None


# ─────────────────────────────────────────────
#  SCRAPER — KEY DESIGN (keydesign.com.br)
#  Plataforma: VTEX
#  Paginação: _from=0&to=47 (incrementos de 48)
# ─────────────────────────────────────────────

class KeyDesignScraper:
    BASE = "https://www.keydesign.com.br"
    PAGE_SIZE = 50
    # Termos de busca focados em pulseiras
    SEARCH_TERMS = ["pulseira"]

    def __init__(self, use_cloudscraper: bool = False):
        self.session = get_session(use_cloudscraper=use_cloudscraper)

    def scrape(self) -> list[Product]:
        products = []
        log.info("[Key Design] Coletando via API VTEX...")

        seen_ids: set[str] = set()
        for term in self.SEARCH_TERMS:
            for p in self._scrape_term(term):
                if p.url not in seen_ids:
                    seen_ids.add(p.url)
                    products.append(p)
        return products

    def _scrape_term(self, term: str) -> list[Product]:
        products = []
        offset = 0

        while True:
            url = (
                f"{self.BASE}/api/catalog_system/pub/products/search/{term}"
                f"?_from={offset}&_to={offset + self.PAGE_SIZE - 1}"
            )
            log.info(f"[Key Design] '{term}' offset {offset}: {url}")

            try:
                resp = _get_with_retry(self.session, url, timeout=15)
                resp.raise_for_status()
                items = resp.json()
            except Exception as e:
                log.error(f"[Key Design] Erro: {e}")
                break

            if not items:
                log.info(f"[Key Design] '{term}' sem produtos no offset {offset}.")
                break

            for item in items:
                product = self._parse_product_api(item, url)
                if product:
                    products.append(product)

            if len(items) < self.PAGE_SIZE:
                break

            offset += self.PAGE_SIZE
            polite_sleep()

        log.info(f"[Key Design] '{term}': {len(products)} produtos coletados.")
        return products

    def _parse_product_api(self, item: dict, cat_url: str) -> Optional[Product]:
        try:
            name = (item.get("productName") or "").strip()
            if not name:
                return None
            
            best_price = None
            for sku in item.get("items", []):
                for seller in sku.get("sellers", []):
                    offer = seller.get("commertialOffer", {})
                    candidate = offer.get("Price")
                    if candidate is None:
                        continue
                    candidate = float(candidate)
                    audit_price("Key Design", offer.get("Price"), candidate, source=cat_url)
                    if candidate <= 0:
                        continue
                    best_price = candidate if best_price is None else min(best_price, candidate)

            if best_price is None:
                return None
            
            prod_url = item.get("link", "")
            mat = self._infer_material(item, name)
            # Guarda o primeiro SKU ID para uso na simulação de frete
            first_sku = (item.get("items") or [{}])[0].get("itemId", "")
            cats_str  = " ".join(item.get("categories", []))

            return Product(
                store="Key Design",
                name=name,
                price=best_price,
                material=mat,
                url=prod_url,
                category_url=cat_url,
                context=f"sku:{first_sku} {cats_str}".strip(),
            )
        except Exception as e:
            log.debug(f"[Key Design] Erro ao parsear produto: {e}")
            return None

    def _infer_material(self, item: dict, name: str) -> str:
        categories = " ".join(item.get("categories", [])).lower()
        if "corrente" in categories:
            return "metal"
        return classify_material(f"{categories} {name}")


# ─────────────────────────────────────────────
#  SCRAPER — W. BUSCATTI (wbuscatti.com.br)
#  Plataforma: Loja integrada / WooCommerce
#  Paginação: /page/N/
# ─────────────────────────────────────────────

class WBuscattiScraper:
    BASE = "https://www.wbuscatti.com.br"
    MAX_PAGES_PER_CATEGORY = 40
    
    # Ajuste os slugs conforme a estrutura real do site
    CATEGORIES = {
        "couro":  "/categoria-produto/pulseiras/couro/",
        "metal":  "/categoria-produto/pulseiras/metal/",
        "corda":  "/categoria-produto/pulseiras/corda/",
        "pedra":  "/categoria-produto/pulseiras/pedra/",
    }
    
    # Fallback: busca por keyword se categoria não existir
    SEARCH_FALLBACK = {
        "couro": "/busca?q=pulseira+couro",
        "metal": "/busca?q=pulseira+metal",
        "corda": "/busca?q=pulseira+corda",
        "pedra": "/busca?q=pulseira+pedra",
    }
    
    def __init__(self, use_cloudscraper: bool = True):
        self.session = get_session(use_cloudscraper=use_cloudscraper)
    
    def scrape(self) -> list[Product]:
        products = []
        for material, cat_path in self.CATEGORIES.items():
            if not check_robots(self.BASE, cat_path):
                continue
            log.info(f"[W.Buscatti] Raspando categoria: {material}")
            result = self._scrape_category(cat_path, material)
            
            # Se não encontrou produtos, tenta fallback de busca
            if not result and material in self.SEARCH_FALLBACK:
                log.info(f"[W.Buscatti] Tentando fallback de busca para {material}")
                result = self._scrape_category(
                    self.SEARCH_FALLBACK[material], material
                )
            
            products.extend(result)
        return products
    
    def _scrape_category(self, cat_path: str, material: str) -> list[Product]:
        products = []
        page = 1
        # Dedup por (nome, preço) — mais robusto que URL (que pode ser vazia)
        seen_keys: set[tuple] = set()
        stale_pages = 0

        while True:
            if page > self.MAX_PAGES_PER_CATEGORY:
                log.warning(
                    f"[W.Buscatti] Limite de segurança atingido ({self.MAX_PAGES_PER_CATEGORY} páginas) em {material}."
                )
                break

            # WooCommerce usa /page/N/ ou ?paged=N
            if "?" in cat_path:
                url = f"{self.BASE}{cat_path}&paged={page}"
            else:
                url = f"{self.BASE}{cat_path}page/{page}/" if page > 1 else f"{self.BASE}{cat_path}"

            log.info(f"[W.Buscatti] Página {page}: {url}")

            try:
                resp = _get_with_retry(self.session, url, timeout=15)
                if resp.status_code == 404:
                    log.info(f"[W.Buscatti] 404 na página {page} — fim.")
                    break
                resp.raise_for_status()
            except Exception as e:
                log.error(f"[W.Buscatti] Erro: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")

            # Loja Integrada: layout atual da vitrine
            items = soup.select(
                "article.product-card[data-id], article.product-card, "
                "li.product, article.product, .product-item"
            )

            if not items:
                log.info(f"[W.Buscatti] Sem produtos na página {page}.")
                dump_debug_html("WBuscatti", material, url, resp.text)
                break

            new_this_page = 0
            for item in items:
                product = self._parse_product(item, material, url)
                if product:
                    key = (product.name.lower().strip(), round(product.price, 2))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        products.append(product)
                        new_this_page += 1

            # Detecta página repetida (WooCommerce às vezes responde com a pág 1 para N > total)
            if new_this_page == 0:
                stale_pages += 1
                log.info(
                    f"[W.Buscatti] Página {page} sem novos produtos ({stale_pages}ª vez consecutiva)."
                )
                if stale_pages >= 2:
                    log.info("[W.Buscatti] Conteúdo repetido — encerrando paginação.")
                    break
            else:
                stale_pages = 0

            # Verifica próxima página
            next_link = soup.select_one(
                "a.next, a[aria-label='Next'], "
                ".woocommerce-pagination a.next, "
                "nav.woocommerce-pagination a:last-child, "
                "a[rel='next'], .pagination a.next"
            )
            next_href = (next_link.get("href", "").strip() if next_link else "")
            if not next_link or not next_href or next_href == "#" or "javascript:" in next_href.lower():
                break

            page += 1
            polite_sleep()

        log.info(f"[W.Buscatti] {material}: {len(products)} produtos coletados.")
        return products
    
    def _parse_product(self, item, material: str, cat_url: str) -> Optional[Product]:
        try:
            name_el = item.select_one(
                ".card-product-name, "
                "[itemprop='name'], "
                "h2.woocommerce-loop-product__title, "
                "h3.product-title, "
                ".product-name, [class*='product-title']"
            )
            name = name_el.get_text(strip=True) if name_el else ""
            if not name:
                return None
            
            price_el = (
                item.select_one(".product-card-price-new") or
                item.select_one(".product-card-main-price") or
                item.select_one("ins .woocommerce-Price-amount") or
                item.select_one(".woocommerce-Price-amount") or
                item.select_one("[class*='price'] bdi") or
                item.select_one("[class*='price']")
            )
            
            if not price_el:
                return None
            
            price = parse_price(price_el.get_text())
            audit_price("W. Buscatti", price_el.get_text(" ", strip=True), price, source=cat_url)
            if price is None or price <= 0:
                return None
            
            link_el = item.select_one("a.card_img_wrapper[href], a[href]")
            prod_url = (link_el["href"] if link_el else "").strip()
            if prod_url and not prod_url.startswith("http"):
                prod_url = urljoin(self.BASE, prod_url)
            
            mat = classify_material(name)
            if mat == "desconhecido":
                mat = material

            return Product(
                store="W. Buscatti",
                name=name,
                price=price,
                material=mat,
                url=prod_url,
                category_url=cat_url,
                context=item.get_text(" ", strip=True)[:400],
            )
        except Exception as e:
            log.debug(f"[W.Buscatti] Erro ao parsear produto: {e}")
            return None


# ─────────────────────────────────────────────
#  SCRAPER — LEÃO DE NEMÉIA (leaodenemeia.com.br)
#  Plataforma: VTEX
#  Abordagem: API Catalog (igual Key Design)
# ─────────────────────────────────────────────

class LeaoDeNemeiaScraper:
    BASE      = "https://www.leaodenemeia.com.br"
    PAGE_SIZE = 50
    SEARCH_TERMS = ["pulseira"]

    def __init__(self, use_cloudscraper: bool = False):
        self.session = get_session(use_cloudscraper=use_cloudscraper)

    def scrape(self) -> list[Product]:
        products: list[Product] = []
        seen_urls: set[str] = set()
        log.info("[Leão de Neméia] Coletando via API VTEX...")
        for term in self.SEARCH_TERMS:
            for p in self._scrape_term(term):
                if p.url not in seen_urls:
                    seen_urls.add(p.url)
                    products.append(p)
        log.info("[Leão de Neméia] Total: %d produtos.", len(products))
        return products

    def _scrape_term(self, term: str) -> list[Product]:
        products: list[Product] = []
        offset = 0
        while True:
            url = (
                f"{self.BASE}/api/catalog_system/pub/products/search/{term}"
                f"?_from={offset}&_to={offset + self.PAGE_SIZE - 1}"
            )
            log.info("[Leão de Neméia] '%s' offset %d", term, offset)
            try:
                resp = _get_with_retry(self.session, url, timeout=15)
                resp.raise_for_status()
                items = resp.json()
            except Exception as e:
                log.error("[Leão de Neméia] Erro: %s", e)
                break
            if not items:
                break
            for item in items:
                p = self._parse_item(item, url)
                if p:
                    products.append(p)
            if len(items) < self.PAGE_SIZE:
                break
            offset += self.PAGE_SIZE
            polite_sleep()
        log.info("[Leão de Neméia] '%s': %d produtos.", term, len(products))
        return products

    def _parse_item(self, item: dict, cat_url: str) -> Optional[Product]:
        try:
            name = (item.get("productName") or "").strip()
            if not name:
                return None
            best_price = None
            for sku in item.get("items", []):
                for seller in sku.get("sellers", []):
                    offer = seller.get("commertialOffer", {})
                    candidate = offer.get("Price")
                    if candidate and float(candidate) > 0:
                        best_price = float(candidate) if best_price is None else min(best_price, float(candidate))
            if not best_price:
                return None
            prod_url = item.get("link", "")
            categories = " ".join(item.get("categories", [])).lower()
            mat = classify_material(f"{categories} {name}")
            # Guarda o primeiro SKU ID para uso na simulação de frete
            first_sku = (item.get("items") or [{}])[0].get("itemId", "")
            cats_str  = " ".join(item.get("categories", []))
            return Product(
                store="Leão de Neméia",
                name=name,
                price=best_price,
                material=mat,
                url=prod_url,
                category_url=cat_url,
                context=f"sku:{first_sku} {cats_str}".strip(),
            )
        except Exception as e:
            log.debug("[Leão de Neméia] Erro ao parsear: %s", e)
            return None


# ─────────────────────────────────────────────
#  SCRAPER — PAPACHULLI (papachulli.com.br)
#  Plataforma: Tray
#  Abordagem: HTML scraping com paginação ?pg=N
# ─────────────────────────────────────────────

class PapachulliScraper:
    BASE     = "https://www.papachulli.com.br"
    MAX_PAGES = 20

    CATEGORIES: dict[str, str] = {
        "couro": "/categoria/pulseiras/couro",
        "pedra": "/categoria/pulseiras/pedras",
        "corda": "/categoria/pulseiras/corda",
        "metal": "/categoria/pulseiras/correntes",
    }
    FALLBACK = "/categoria/pulseiras"

    def __init__(self, use_cloudscraper: bool = True):
        self.session = get_session(use_cloudscraper=use_cloudscraper)

    def scrape(self) -> list[Product]:
        products: list[Product] = []
        for material, path in self.CATEGORIES.items():
            if not check_robots(self.BASE, path):
                continue
            result = self._scrape_category(path, material)
            if not result:
                log.info("[Papachulli] [%s] sem resultado em categoria, tentando fallback.", material)
                result = [p for p in self._scrape_category(self.FALLBACK, material)
                          if classify_material(p.name) == material or p.material == material]
            products.extend(result)
            log.info("[Papachulli] [%s]: %d produtos.", material, len(result))
        log.info("[Papachulli] Total: %d produtos.", len(products))
        return products

    def _scrape_category(self, path: str, material: str) -> list[Product]:
        products: list[Product] = []
        seen: set[str] = set()
        for page in range(1, self.MAX_PAGES + 1):
            url = f"{self.BASE}{path}" + (f"?pg={page}" if page > 1 else "")
            log.info("[Papachulli] %s pág %d", path, page)
            try:
                resp = _get_with_retry(self.session, url, timeout=15)
                if resp.status_code == 404:
                    break
                resp.raise_for_status()
            except Exception as e:
                log.error("[Papachulli] Erro: %s", e)
                break
            soup = BeautifulSoup(resp.text, "lxml")
            cards = soup.select("div.product, li.product-item, .showcase-item")
            if not cards:
                dump_debug_html("Papachulli", material, url, resp.text)
                break
            page_new = 0
            for card in cards:
                p = self._parse_card(card, material, url)
                if p and p.url not in seen:
                    seen.add(p.url)
                    products.append(p)
                    page_new += 1
            if page_new == 0:
                break  # página repetida ou sem novidades
            # Verifica se há próxima página
            if not soup.select_one("a[rel='next'], .pagination a.next, li.next a"):
                break
            polite_sleep()
        return products

    def _parse_card(self, card, material: str, cat_url: str) -> Optional[Product]:
        try:
            name_el = card.select_one(".product-name, h2.product-name, h3.product-name, .title")
            if not name_el:
                return None
            name = name_el.get_text(strip=True)
            if not name:
                return None

            # Tray: preço à vista tem prioridade; fallback para preço cheio
            price_el = (
                card.select_one(".preco-avista, .sale-price, .current-price, [class*='price']")
            )
            if not price_el:
                return None
            price = parse_price(price_el.get_text())
            if not price or price <= 0:
                return None

            link_el  = card.select_one("a[href]")
            prod_url = link_el["href"] if link_el else ""
            if prod_url and not prod_url.startswith("http"):
                prod_url = urljoin(self.BASE, prod_url)

            delivery = extract_delivery_info(card)

            mat = classify_material(name)
            if mat == "desconhecido":
                mat = material

            return Product(
                store="Papachulli",
                name=name,
                price=price,
                material=mat,
                url=prod_url,
                category_url=cat_url,
                delivery_info=delivery,
            )
        except Exception as e:
            log.debug("[Papachulli] Erro ao parsear card: %s", e)
            return None


# ─────────────────────────────────────────────
#  SCRAPER — 4MEN (4men.com.br)
#  Plataforma: WordPress + WooCommerce
#  Abordagem: WooCommerce Store API (pública, sem auth)
#  Preço em centavos: dividir por 10^currency_minor_unit
# ─────────────────────────────────────────────

class QuatroMenScraper:
    BASE     = "https://4men.com.br"
    API_BASE = "https://4men.com.br/wp-json/wc/store/v1/products"
    PER_PAGE = 50

    # Slugs de categoria mapeados por material (descobertos via API)
    CATEGORY_SLUGS: dict[str, list[str]] = {
        "couro": ["pulseira-de-couro-masculina"],
        "pedra": ["pulseira-de-pedra-masculina"],
        "corda": ["pulseira-de-corda", "pulseira-de-tecido"],
        "metal": ["pulseira-de-aco"],
    }
    FALLBACK_SLUG = "pulseira-masculina"

    def __init__(self, use_cloudscraper: bool = True):
        self.session = get_session(use_cloudscraper=use_cloudscraper)

    def scrape(self) -> list[Product]:
        products: list[Product] = []

        for material, slugs in self.CATEGORY_SLUGS.items():
            mat_products: list[Product] = []
            mat_seen_urls: set[str] = set()   # dedup dentro do mesmo material (múltiplos slugs)
            for slug in slugs:
                for p in self._fetch_category(slug, material):
                    if p.url and p.url not in mat_seen_urls:
                        mat_seen_urls.add(p.url)
                        mat_products.append(p)
            # Se nenhuma subcategoria retornou, usa fallback geral e filtra por material
            if not mat_products:
                log.info("[4Men] [%s] subcategorias vazias, tentando fallback.", material)
                for p in self._fetch_category(self.FALLBACK_SLUG, material):
                    inferred = classify_material(p.name)
                    if inferred == material or inferred == "desconhecido":
                        mat_products.append(p)
            # Dedup por URL
            for p in mat_products:
                if p.url not in {x.url for x in products}:
                    products.append(p)
            log.info("[4Men] [%s]: %d produtos.", material, len(mat_products))

        log.info("[4Men] Total: %d produtos.", len(products))
        return products

    def _fetch_category(self, slug: str, material: str) -> list[Product]:
        products: list[Product] = []
        page = 1
        while True:
            params = {"category": slug, "per_page": self.PER_PAGE, "page": page, "status": "publish"}
            log.info("[4Men] categoria='%s' pág %d", slug, page)
            try:
                resp = _get_with_retry(self.session, self.API_BASE, params=params, timeout=15)
                if resp.status_code == 400:
                    break  # slug inválido
                resp.raise_for_status()
                items = resp.json()
            except Exception as e:
                log.error("[4Men] Erro: %s", e)
                break
            if not items:
                break
            for item in items:
                p = self._parse_item(item, material)
                if p:
                    products.append(p)
            if len(items) < self.PER_PAGE:
                break
            page += 1
            polite_sleep()
        return products

    def _parse_item(self, item: dict, material: str) -> Optional[Product]:
        try:
            name = (item.get("name") or "").strip()
            if not name:
                return None

            prices     = item.get("prices", {})
            raw_price  = prices.get("sale_price") or prices.get("price") or "0"
            minor_unit = int(prices.get("currency_minor_unit", 2))
            price      = int(raw_price) / (10 ** minor_unit) if str(raw_price).isdigit() else 0.0
            if price <= 0:
                return None

            prod_url = item.get("permalink", "")
            cats     = [c.get("slug", "") for c in item.get("categories", [])]

            mat = classify_material(name)
            if mat == "desconhecido":
                # Tenta inferir pelo slug de categoria
                cat_text = " ".join(cats)
                mat = classify_material(cat_text)
            if mat == "desconhecido":
                mat = material

            return Product(
                store="4Men",
                name=name,
                price=price,
                material=mat,
                url=prod_url,
                category_url=self.API_BASE,
                context=" ".join(cats),
            )
        except Exception as e:
            log.debug("[4Men] Erro ao parsear item: %s", e)
            return None


# ─────────────────────────────────────────────
#  SCRAPER — MERCADO LIVRE (mercadolivre.com.br)
#  Abordagem: API oficial ML (Search API v2)
#  Token OAuth lido de ml_tokens.json (gerado por auth_ml.py)
#  Fallback: Playwright headless se token ausente/expirado
# ─────────────────────────────────────────────

class MercadoLivreScraper:
    API_BASE  = "https://api.mercadolibre.com"
    SITE_ID   = "MLB"
    MIN_PRICE = 30.0    # filtra acessórios de baixíssimo valor
    LIMIT     = 50      # itens por página (máx ML: 50)
    MAX_PAGES = 5       # até 250 itens por query

    # Termos de busca por material
    SEARCH_QUERIES: dict[str, list[str]] = {
        "corda":  ["pulseira masculina corda", "pulseira masculina paracord"],
        "couro":  ["pulseira masculina couro"],
        "metal":  ["pulseira masculina aco inox", "pulseira masculina metal"],
        "pedra":  ["pulseira masculina pedra natural", "pulseira masculina hematita"],
    }

    # Slugs para fallback Playwright (caso API falhe)
    _PLAYWRIGHT_SLUGS: dict[str, list[str]] = {
        "corda":  ["pulseira-masculina-corda", "pulseira-masculina-paracord"],
        "couro":  ["pulseira-masculina-couro"],
        "metal":  ["pulseira-masculina-aco-inox", "pulseira-masculina-metal"],
        "pedra":  ["pulseira-masculina-pedra-natural", "pulseira-masculina-hematita"],
    }

    def __init__(self):
        self._token: Optional[str] = self._load_token()
        self._token_expired: bool = False   # True quando API retornou 401
        self._session = get_session()

    # ── Token OAuth ─────────────────────────────────────────────────────────

    def _load_token(self) -> Optional[str]:
        import json

        # Prioridade 1: user token via Authorization Code flow (permite search API)
        try:
            from api.routers.auth_ml import refresh_token_if_needed
            token = refresh_token_if_needed()
            if token:
                log.info("[ML] Token de usuário carregado (Authorization Code).")
                return token
        except Exception as e:
            log.debug("[ML] Sem user token disponível: %s", e)

        # Prioridade 2: ml_tokens.json legado
        token_file = Path("ml_tokens.json")
        if token_file.exists():
            try:
                data = json.loads(token_file.read_text(encoding="utf-8"))
                token = data.get("access_token")
                if token:
                    log.info("[ML] Token carregado de ml_tokens.json.")
                    return token
            except Exception as e:
                log.warning("[ML] Erro ao ler ml_tokens.json: %s", e)

        # Prioridade 3: client_credentials (não funciona para search, mas tenta)
        import os
        client_id     = os.getenv("ML_CLIENT_ID")
        client_secret = os.getenv("ML_CLIENT_SECRET")
        if client_id and client_secret:
            try:
                resp = requests.post(
                    f"{self.API_BASE}/oauth/token",
                    data={
                        "grant_type":    "client_credentials",
                        "client_id":     client_id,
                        "client_secret": client_secret,
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                token = resp.json().get("access_token")
                if token:
                    log.info("[ML] Token client_credentials obtido (search pode falhar com 403).")
                    return token
            except Exception as e:
                log.warning("[ML] Falha ao obter token via .env: %s", e)

        return None

    def _auth_headers(self) -> dict:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    # ── Ponto de entrada ─────────────────────────────────────────────────────

    def scrape(self) -> list[Product]:
        # A Search API é pública — funciona sem token (token apenas aumenta rate limit)
        if self._token:
            log.info("[Mercado Livre] Usando API oficial com token OAuth.")
        else:
            log.info("[Mercado Livre] Token ausente — tentando API pública (sem auth).")
        products = self._scrape_via_api()
        if not products:
            log.warning("[Mercado Livre] API retornou 0 produtos — ativando fallback Playwright (headless).")
            products = self._scrape_via_playwright()
        log.info("[Mercado Livre] Total: %d produtos coletados.", len(products))
        return products

    # ════════════════════════════════════════════════════════
    #  CAMADA 1 — API OFICIAL
    # ════════════════════════════════════════════════════════

    def _scrape_via_api(self) -> list[Product]:
        products: list[Product] = []
        seen_ids: set[str] = set()

        for material, queries in self.SEARCH_QUERIES.items():
            for query in queries:
                found = self._api_search(query, material, seen_ids)
                products.extend(found)
                log.info("[ML API] '%s': %d produtos.", query, len(found))
                if self._token_expired:
                    log.warning("[ML API] Interrompendo busca — token expirado.")
                    return products   # sai cedo; scrape() vai ativar fallback
                polite_sleep()

        return products

    def _api_search(self, query: str, material: str, seen_ids: set) -> list[Product]:
        products: list[Product] = []

        for pg in range(self.MAX_PAGES):
            offset = pg * self.LIMIT
            url = (
                f"{self.API_BASE}/sites/{self.SITE_ID}/search"
                f"?q={requests.utils.quote(query)}"
                f"&limit={self.LIMIT}&offset={offset}"
                f"&condition=new"
            )
            log.info("[ML API] '%s' offset %d", query, offset)

            try:
                resp = _get_with_retry(
                    self._session, url,
                    headers=self._auth_headers(),
                    timeout=15,
                )
                if resp.status_code == 401:
                    log.warning("[ML API] Token expirado (401) — fallback Playwright sera ativado.")
                    self._token_expired = True
                    return products
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                log.error("[ML API] Erro: %s", e)
                break

            items = data.get("results", [])
            if not items:
                break

            for idx, item in enumerate(items):
                p = self._parse_api_item(item, material)
                if p and p.url not in seen_ids:
                    seen_ids.add(p.url)
                    # Injeta posição absoluta (1-based entre todas as páginas) e query no context
                    position = offset + idx + 1
                    query_slug = query.replace(" ", "_")
                    new_ctx = f"{p.context} position:{position} query:{query_slug}".strip()
                    products.append(dc_replace(p, context=new_ctx))

            total = data.get("paging", {}).get("total", 0)
            if offset + self.LIMIT >= total:
                break

            polite_sleep()

        return products

    def _parse_api_item(self, item: dict, material: str) -> Optional[Product]:
        try:
            name = (item.get("title") or "").strip()
            if not name:
                return None

            price = float(item.get("price") or 0)
            if price < self.MIN_PRICE:
                return None

            # Preço original (antes de desconto) — útil para detectar promoções
            original_price = item.get("original_price")

            prod_url = item.get("permalink", "").split("?")[0]
            shipping = item.get("shipping", {})
            delivery = "Frete Grátis" if shipping.get("free_shipping") else ""

            mat = classify_material(name)
            if mat == "desconhecido":
                mat = material

            context_parts = [f"id:{item.get('id', '')}"]
            if original_price and original_price > price:
                pct = round((1 - price / original_price) * 100)
                context_parts.append(f"desconto:{pct}%")
            context = " ".join(context_parts)

            return Product(
                store="Mercado Livre",
                name=name,
                price=price,
                material=mat,
                url=prod_url,
                category_url=self.API_BASE,
                delivery_info=delivery,
                context=context,
            )
        except Exception as e:
            log.debug("[ML API] Erro ao parsear item: %s", e)
            return None

    # ════════════════════════════════════════════════════════
    #  CAMADA 2 — FALLBACK PLAYWRIGHT (sem token)
    # ════════════════════════════════════════════════════════

    def _scrape_via_playwright(self) -> list[Product]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning(
                "[ML] Playwright não instalado. "
                "Execute: pip install playwright && python -m playwright install chromium"
            )
            return []

        products: list[Product] = []
        seen_urls: set[str] = set()

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="pt-BR",
                viewport={"width": 1280, "height": 800},
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = ctx.new_page()

            for material, slugs in self._PLAYWRIGHT_SLUGS.items():
                for slug in slugs:
                    found = self._pw_scrape_slug(page, slug, material, seen_urls)
                    products.extend(found)
                    log.info("[ML Playwright] '%s': %d produtos.", slug, len(found))
                    polite_sleep()

            browser.close()

        return products

    def _pw_scrape_slug(self, page, slug: str, material: str, seen_urls: set) -> list[Product]:
        BASE_URL = "https://lista.mercadolivre.com.br"
        products: list[Product] = []

        for pg in range(1, self.MAX_PAGES + 1):
            offset = (pg - 1) * 50 + 1
            url = (
                f"{BASE_URL}/{slug}_Desde_{offset}_ITEM*CONDITION_2230284"
                if pg > 1
                else f"{BASE_URL}/{slug}_ITEM*CONDITION_2230284"
            )
            log.info("[ML Playwright] %s pág %d", slug, pg)

            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
            except Exception as e:
                log.warning("[ML Playwright] Erro ao carregar %s: %s", url, e)
                try:
                    page.screenshot(path=f"/var/www/axen/debug_ml_pw_error_{slug}_pg{pg}.png")
                    log.info("[ML Playwright] Screenshot de erro salvo para %s pg%d", slug, pg)
                except Exception:
                    pass
                break

            log.info("[ML Playwright] URL após goto: %s", page.url)
            if pg == 1:
                page.screenshot(path=f"/var/www/axen/debug_ml_pw_{slug}.png")
                log.info("[ML Playwright] Screenshot salvo para %s", slug)

            cards = (
                page.query_selector_all("li.ui-search-layout__item")
                or page.query_selector_all(".poly-card--grid")
                or page.query_selector_all("[class*='ui-search-layout__item']")
                or page.query_selector_all(".andes-card")
            )
            if not cards:
                if pg == 1:
                    page.screenshot(path=f"/var/www/axen/debug_ml_search_{slug}.png")
                    log.warning("[ML Playwright] 0 cards em %s — screenshot salvo.", slug)
                break

            page_products = []
            for card in cards:
                p = self._pw_parse_card(card, material, url)
                if p and p.url not in seen_urls:
                    seen_urls.add(p.url)
                    page_products.append(p)

            products.extend(page_products)

            next_btn = page.query_selector(
                "a.andes-pagination__link[title='Siguiente'], "
                "li.andes-pagination__button--next a"
            )
            if not next_btn or not page_products:
                break

            polite_sleep()

        return products

    def _pw_parse_card(self, card, material: str, page_url: str) -> Optional[Product]:
        try:
            title_el = card.query_selector(".poly-component__title, h2.ui-search-item__title")
            if not title_el:
                return None
            title = title_el.inner_text().strip()
            if not title:
                return None

            fraction = card.query_selector(".andes-money-amount__fraction")
            cents    = card.query_selector(".andes-money-amount__cents")
            if not fraction:
                return None
            price_str = fraction.inner_text().replace(".", "").replace(",", "")
            if cents:
                price_str += "." + cents.inner_text().replace(",", ".")
            price = parse_price(price_str)
            if price is None or price < self.MIN_PRICE:
                return None

            link_el  = card.query_selector("a.poly-component__title, a[href*='mercadolivre']")
            prod_url = link_el.get_attribute("href") if link_el else ""
            prod_url = prod_url.split("?")[0] if prod_url else ""

            shipping_el = card.query_selector(
                ".poly-component__shipping, .ui-search-item__shipping, "
                "[class*='shipping'], [class*='frete']"
            )
            delivery = extract_delivery_info(shipping_el.inner_text() if shipping_el else "")

            mat = classify_material(title)
            if mat == "desconhecido":
                mat = material

            return Product(
                store="Mercado Livre",
                name=title,
                price=price,
                material=mat,
                url=prod_url,
                category_url=page_url,
                delivery_info=delivery,
            )
        except Exception as e:
            log.debug("[ML Playwright] Erro ao parsear card: %s", e)
            return None


# ─────────────────────────────────────────────
#  DELIVERY INFO COLLECTOR
#  Estratégia multi-camada (sem formulários de CEP):
#
#  1. VTEX Checkout Simulation API  → Key Design, Leão de Neméia
#  2. Policy page scraping (HTTP)   → Papachulli, W. Buscatti, 4Men
#                                      + fallback Key Design
#  3. Playwright (Beroc apenas)     → Shopify exibe prazo automático
#  4. Manual override               → delivery_manual.json
#
#  Mercado Livre: prazo capturado diretamente nos cards de listagem.
# ─────────────────────────────────────────────

class CepDeliveryChecker:
    """
    Coleta prazos de entrega em 4 camadas:
      1. VTEX Checkout Simulation API  (Key Design, Leão de Neméia)
      2. Scraping de página de política de entrega — HTTP puro, sem browser
      3. Playwright apenas para Beroc  (Shopify exibe prazo automaticamente)
      4. Fallback manual via delivery_manual.json
    """

    CEP_FULL     = "88065185"
    HTTP_TIMEOUT = 12
    PW_TIMEOUT   = 30_000
    SAMPLE       = 3   # SKUs amostrados para VTEX API e CEP playwright

    VTEX_STORES: dict[str, str] = {
        "Key Design":     "https://www.keydesign.com.br",
        "Leão de Neméia": "https://www.leaodenemeia.com.br",
    }

    # Lojas que suportam cálculo de frete real por CEP na página do produto
    # (requerem seleção de tamanho antes de calcular)
    CEP_PW_STORES = {"Papachulli", "W. Buscatti", "4Men"}

    # Prioridade de tamanho: G > M > P
    SIZE_PRIORITY = ["G", "M", "P"]

    # URLs candidatas de política de entrega por loja (fallback se Playwright falhar)
    POLICY_URLS: dict[str, list[str]] = {
        "Key Design": [
            "https://www.keydesign.com.br/institucional/entrega",
            "https://www.keydesign.com.br/institucional/frete",
            "https://www.keydesign.com.br/politica-de-entrega",
            "https://www.keydesign.com.br/frete-e-entrega",
        ],
        "Papachulli": [
            "https://www.papachulli.com.br/i/politica-de-entrega",
            "https://www.papachulli.com.br/politica-de-entrega",
            "https://www.papachulli.com.br/i/entrega",
            "https://www.papachulli.com.br/entrega",
        ],
        "W. Buscatti": [
            "https://www.wbuscatti.com.br/politica-de-entrega",
            "https://www.wbuscatti.com.br/politica-de-envio",
            "https://www.wbuscatti.com.br/frete-e-entrega",
            "https://www.wbuscatti.com.br/entrega",
        ],
        "4Men": [
            "https://4men.com.br/politica-de-entrega",
            "https://4men.com.br/envio",
            "https://4men.com.br/entrega",
            "https://4men.com.br/frete",
        ],
        "Beroc": [
            "https://beroc.com.br/policies/shipping-policy",
        ],
    }

    SKIP_STORES = {"Mercado Livre"}  # prazo já capturado nos cards de listagem

    # ══════════════════════════════════════════════════════════
    #  PONTO DE ENTRADA
    # ══════════════════════════════════════════════════════════

    def enrich(self, products: list[Product]) -> list[Product]:
        # 1 — VTEX API
        for store, base_url in self.VTEX_STORES.items():
            products = self._enrich_vtex_api(products, store, base_url)

        # 2 — Policy page (para quem ainda não tem prazo e não usa CEP playwright)
        stores_sem_prazo = {
            p.store for p in products
            if not p.delivery_info
            and p.store not in self.SKIP_STORES
            and p.store not in self.CEP_PW_STORES   # CEP_PW_STORES usa camada 2.5
            and p.store in self.POLICY_URLS
        }
        for store in stores_sem_prazo:
            result = self._scrape_policy_page(store)
            if result:
                for i, p in enumerate(products):
                    if p.store == store and not p.delivery_info:
                        products[i] = dc_replace(p, delivery_info=result)

        # 2.5 — Playwright CEP em páginas de produto (Papachulli, W. Buscatti, 4Men)
        #         Seleciona tamanho G > M > P, digita CEP, clica calcular e lê prazo.
        stores_cep_pw = [
            store for store in self.CEP_PW_STORES
            if any(p.store == store and not p.delivery_info and p.url for p in products)
        ]
        if stores_cep_pw:
            products = self._enrich_cep_playwright(products, stores_cep_pw)

        # 3 — Playwright: Beroc (Shopify mostra prazo sem CEP)
        beroc_todo = [
            i for i, p in enumerate(products)
            if p.store == "Beroc" and not p.delivery_info and p.url
        ]
        if beroc_todo:
            products = self._enrich_beroc_playwright(products, beroc_todo)

        # 4 — Overrides manuais
        products = self._apply_manual_overrides(products)

        return products

    # ══════════════════════════════════════════════════════════
    #  CAMADA 1 — VTEX CHECKOUT SIMULATION API
    # ══════════════════════════════════════════════════════════

    def _enrich_vtex_api(self, products: list[Product], store: str, base_url: str) -> list[Product]:
        session  = get_session()
        all_prods = [(i, p) for i, p in enumerate(products) if p.store == store and p.url]
        if not all_prods:
            return products

        # Tenta obter SKU do context (prefixo "sku:<id>")
        sku_id: Optional[str] = None
        for _, p in random.sample(all_prods, min(self.SAMPLE, len(all_prods))):
            m = re.match(r"sku:(\d+)", p.context or "")
            if m:
                sku_id = m.group(1)
                break

        if not sku_id:
            log.warning("[Delivery] [%s] SKU não encontrado — pulando API VTEX.", store)
            return products

        log.info("[Delivery] [%s] VTEX simulation SKU=%s CEP=%s", store, sku_id, self.CEP_FULL)
        result = self._vtex_sim_request(session, base_url, sku_id, store)

        if result:
            log.info("[Delivery] [%s] -> %s (%d produtos)", store, result, len(all_prods))
            for i, p in all_prods:
                if not p.delivery_info:
                    products[i] = dc_replace(p, delivery_info=result)
        else:
            log.warning("[Delivery] [%s] VTEX sim sem resultado — tentando policy page.", store)
            fallback = self._scrape_policy_page(store)
            if fallback:
                for i, p in all_prods:
                    if not p.delivery_info:
                        products[i] = dc_replace(p, delivery_info=fallback)

        return products

    def _vtex_sim_request(self, session, base_url: str, sku_id: str, store: str = "") -> str:
        url     = f"{base_url}/api/checkout/pub/orderForms/simulation"
        payload = {
            "items":      [{"id": str(sku_id), "quantity": 1, "seller": "1"}],
            "postalCode": self.CEP_FULL,
            "country":    "BRA",
        }
        try:
            resp = session.post(url, json=payload, timeout=self.HTTP_TIMEOUT,
                                headers={"Content-Type": "application/json"})
            if resp.status_code != 200:
                log.debug("[Delivery] [%s] VTEX HTTP %d", store, resp.status_code)
                return ""
            data = resp.json()
        except Exception as e:
            log.debug("[Delivery] [%s] VTEX request error: %s", store, e)
            return ""

        slas: list[tuple[str, int]] = []
        for li in data.get("logisticsInfo", []):
            for sla in li.get("slas", []):
                est   = sla.get("shippingEstimate", "")
                price = sla.get("price", -1)
                if est:
                    slas.append((est, int(price)))

        if not slas:
            log.debug("[Delivery] [%s] VTEX sim slas vazio. Resp: %s", store, str(data)[:300])
            return ""

        free = [(e, p) for e, p in slas if p == 0]
        best_est, best_price = min(free if free else slas, key=lambda x: self._est_days(x[0]))
        days, uteis = self._parse_est(best_est)
        if days is None:
            return "Entrega Agendada"
        tipo  = " úteis" if uteis else ""
        prazo = f"Entrega em {days} dia{'s' if days > 1 else ''}{tipo}"
        return f"Frete Grátis ({prazo})" if best_price == 0 else prazo

    @staticmethod
    def _est_days(est: str) -> int:
        m = re.match(r"(\d+)(bd|d)", est)
        return int(m.group(1)) * (2 if m.group(2) == "bd" else 1) if m else 999

    @staticmethod
    def _parse_est(est: str) -> tuple:
        m = re.match(r"(\d+)(bd|d)", est)
        return (int(m.group(1)), m.group(2) == "bd") if m else (None, False)

    # ══════════════════════════════════════════════════════════
    #  CAMADA 2 — POLICY PAGE SCRAPING (HTTP puro)
    # ══════════════════════════════════════════════════════════

    def _scrape_policy_page(self, store: str) -> str:
        """
        Faz GET em páginas de política de entrega (ex.: /politica-de-entrega)
        e extrai o prazo declarado em texto como '5 dias úteis'.
        Muito mais confiável que formulários AJAX porque é HTML estático.
        """
        session = get_session(use_cloudscraper=True)
        for url in self.POLICY_URLS.get(store, []):
            try:
                resp = _get_with_retry(session, url, timeout=self.HTTP_TIMEOUT)
                if resp.status_code != 200:
                    log.debug("[Delivery] [%s] policy %s -> HTTP %d", store, url, resp.status_code)
                    continue
                soup = BeautifulSoup(resp.text, "lxml")
                for tag in soup(["script", "style", "nav", "header", "footer"]):
                    tag.decompose()
                text   = soup.get_text(" ", strip=True)
                result = self._parse_policy_text(text)
                if result:
                    log.info("[Delivery] [%s] policy %s -> %s", store, url, result)
                    return result
                log.debug("[Delivery] [%s] policy %s — sem prazo detectado.", store, url)
            except Exception as e:
                log.debug("[Delivery] [%s] policy %s error: %s", store, url, e)
        return ""

    def _parse_policy_text(self, text: str) -> str:
        """
        Extrai prazo de entrega de texto de página de política.
        Ignora contextos de troca/devolução/pagamento.
        Aceita faixas como '5 a 10 dias úteis' (retorna o mínimo).
        """
        SKIP = {"troca", "devolu", "retorno", "cancelamento", "reembolso",
                "garantia", "parcela", "pagamento", "juros"}
        DELIVERY_WORDS = {"entrega", "envio", "prazo", "frete",
                          "receber", "chegar", "despacho", "postagem"}

        for sentence in re.split(r"[.!?\n]+", text):
            s = sentence.lower().strip()
            if not s or len(s) < 8:
                continue
            if any(w in s for w in SKIP):
                continue
            if not any(w in s for w in DELIVERY_WORDS):
                continue

            # Captura padrão "N dias úteis" ou "N a M dias úteis"
            m = re.search(
                r"(\d+)(?:\s*(?:a|à|até|e)\s*\d+)?\s*dias?\s*(úteis?|uteis?)?",
                s,
            )
            if m:
                dias = int(m.group(1))
                if 1 <= dias <= 45:          # faixa realista de entrega
                    tipo = " úteis" if m.group(2) else ""
                    return f"Entrega em {dias} dia{'s' if dias > 1 else ''}{tipo}"
        return ""

    # ══════════════════════════════════════════════════════════
    #  CAMADA 2.5 — PLAYWRIGHT CEP (Papachulli, W. Buscatti, 4Men)
    #  Lógica: para cada loja, amostra 3 produtos, navega na página,
    #  seleciona tamanho G > M > P, digita CEP, clica calcular,
    #  lê prazo e propaga para todos os produtos da loja.
    # ══════════════════════════════════════════════════════════

    def _enrich_cep_playwright(self, products: list[Product], stores: list[str]) -> list[Product]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("[Delivery CEP] Playwright não disponível — usando fallback policy page.")
            for store in stores:
                result = self._scrape_policy_page(store)
                if result:
                    for i, p in enumerate(products):
                        if p.store == store and not p.delivery_info:
                            products[i] = dc_replace(p, delivery_info=result)
            return products

        store_results: dict[str, str] = {}
        MAX_ATTEMPTS = 20  # máx. de produtos tentados por loja (pula os fora de estoque)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale="pt-BR",
                viewport={"width": 1280, "height": 900},
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = ctx.new_page()

            for store in stores:
                candidates = [
                    p for p in products
                    if p.store == store and p.url and not p.delivery_info
                ]
                if not candidates:
                    continue

                # Embaralha e tenta até MAX_ATTEMPTS produtos, parando
                # ao coletar SAMPLE resultados válidos (produtos em estoque)
                random.shuffle(candidates)
                pool = candidates[:MAX_ATTEMPTS]
                collected: list[str] = []

                for prod in pool:
                    if len(collected) >= self.SAMPLE:
                        break
                    result = self._pw_cep_on_product(page, store, prod.url)
                    if result:
                        collected.append(result)
                        log.info("[Delivery CEP] [%s] %s -> %s", store, prod.url, result)
                    else:
                        log.debug(
                            "[Delivery CEP] [%s] %s sem resultado (fora de estoque ou sem prazo) — próximo.",
                            store, prod.url,
                        )
                    polite_sleep()

                if collected:
                    from collections import Counter
                    best = Counter(collected).most_common(1)[0][0]
                    store_results[store] = best
                    log.info(
                        "[Delivery CEP] [%s] prazo confirmado: %s (%d/%d amostras válidas)",
                        store, best, len(collected), len(pool),
                    )
                else:
                    # Fallback: tenta policy page
                    log.warning(
                        "[Delivery CEP] [%s] Todos os %d produtos tentados falharam "
                        "(fora de estoque?) — tentando policy page.",
                        store, len(pool),
                    )
                    fallback = self._scrape_policy_page(store)
                    if fallback:
                        store_results[store] = fallback

            browser.close()

        # Propaga resultado para todos os produtos sem prazo de cada loja
        for store, delivery in store_results.items():
            for i, p in enumerate(products):
                if p.store == store and not p.delivery_info:
                    products[i] = dc_replace(p, delivery_info=delivery)

        return products

    def _pw_cep_on_product(self, page, store: str, url: str) -> str:
        """
        Navega até a página do produto, verifica se está em estoque,
        seleciona qualquer tamanho disponível, digita o CEP e clica calcular.
        Retorna o prazo mais curto encontrado ou "" em caso de falha / fora de estoque.
        """
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.PW_TIMEOUT)
            page.wait_for_timeout(2000)

            # 0 — Verifica estoque (pula produto indisponível)
            if self._pw_is_out_of_stock(page):
                log.debug("[Delivery CEP] [%s] fora de estoque — pulando: %s", store, url)
                return ""

            # 1 — Seleciona qualquer tamanho disponível
            self._pw_select_size(page)
            page.wait_for_timeout(1500)   # aguarda AJAX Tray/WooCommerce processar a variação

            # 2 — Preenche CEP
            if not self._pw_fill_cep(page):
                log.debug("[Delivery CEP] [%s] campo CEP não encontrado.", store)
                return ""

            # 3 — Clica calcular e aguarda AJAX
            self._pw_click_calc(page)
            page.wait_for_timeout(3500)

            # 4 — Lê resultado
            return self._pw_read_result(page)

        except Exception as e:
            log.warning("[Delivery CEP] [%s] %s: %s", store, url, e)
            return ""

    def _pw_is_out_of_stock(self, page) -> bool:
        """
        Retorna True se a página indicar produto fora de estoque / indisponível.

        Estratégia em 3 camadas — da mais precisa para a mais genérica:

        Camada 1 — Seletores CSS específicos do formulário do produto (WooCommerce /
                   Tray / etc.). Não sofre falsos negativos por produtos relacionados.
        Camada 2 — Texto do elemento de estoque DENTRO do form de produto.
        Camada 3 — Texto geral do body, mas SÓ marca OOS se NÃO houver botão de
                   compra ATIVO no form principal (não nos relacionados).
        """
        # ── Camada 1: seletores CSS do PRODUTO PRINCIPAL ─────────────────────
        # WooCommerce
        try:
            # .stock.out-of-stock  →  elemento "Fora de estoque" dentro do produto
            if page.locator(".stock.out-of-stock").first.is_visible(timeout=400):
                return True
        except Exception:
            pass

        try:
            # Botão de compra com classe .disabled (WooCommerce marca assim quando OOS)
            # Mas verifica SOMENTE dentro do form de produto principal
            cart_form = page.locator("form.cart, form#product-form, .product-form")
            if cart_form.first.is_visible(timeout=400):
                # Botão desabilitado no form de produto
                disabled = cart_form.first.locator(
                    "button.disabled, button[disabled], "
                    ".single_add_to_cart_button.disabled, "
                    ".disabled.add_to_cart_button"
                )
                if disabled.first.is_visible(timeout=300):
                    return True
                # Sem botão de compra no form → OOS ou produto configurável sem seleção
                active_btn = cart_form.first.locator(
                    "button[name='add-to-cart']:not([disabled]):not(.disabled), "
                    "button[type='submit']:not([disabled]):not(.disabled)"
                )
                if active_btn.count() == 0:
                    # Pode ser produto variável ainda não configurado — não marcar OOS
                    pass
        except Exception:
            pass

        # ── Camada 2: texto do elemento de estoque do produto (p.stock) ──────
        try:
            stock_el = page.locator("p.stock, span.stock, .woocommerce-product-details__short-description + .stock")
            if stock_el.first.is_visible(timeout=300):
                stock_text = stock_el.first.inner_text().lower()
                OOS_WORDS = ["esgotado", "esgotada", "fora de estoque", "out of stock",
                             "indisponível", "indisponivel", "sem estoque"]
                if any(w in stock_text for w in OOS_WORDS):
                    return True
        except Exception:
            pass

        # ── Camada 3: texto geral — apenas se botão ativo AUSENTE no form ────
        try:
            text = page.inner_text("body").lower()
        except Exception:
            return False

        OOS_SIGNALS = [
            "fora de estoque", "out of stock", "esgotado", "esgotada",
            "indisponível", "indisponivel", "sem estoque", "produto indisponível",
            "produto esgotado", "não disponível", "nao disponivel",
        ]
        has_oos = any(s in text for s in OOS_SIGNALS)
        if not has_oos:
            return False  # sem sinal OOS algum → em estoque

        # Há sinal OOS no body. Verifica se o botão de compra ATIVO existe no
        # formulário principal (evita falso positivo por produtos relacionados).
        try:
            active_cart_btn = page.locator(
                "form.cart button:not([disabled]):not(.disabled), "
                "form#product-form button[type='submit']:not([disabled]):not(.disabled), "
                ".single_add_to_cart_button:not(.disabled):not([disabled])"
            ).first
            if active_cart_btn.is_visible(timeout=400):
                return False  # botão ativo encontrado → em estoque
        except Exception:
            pass

        return True  # sinal OOS + sem botão ativo no form → fora de estoque

    def _pw_select_size(self, page) -> None:
        """
        Garante que algum tamanho esteja selecionado antes de calcular o frete.

        Estratégia em 4 camadas:
          1. Tenta G → M → P por texto (botão/link/label/data-value)
          2. Tenta G → M → P em <select> de variação (value ou label)
          3. Seleciona qualquer <select> de variação → primeira opção não-vazia
          4. Clica em qualquer botão/link de opção de produto visível
        A ideia é que qualquer tamanho selecionado é suficiente para
        habilitar o campo de CEP — não importa qual.
        """
        # ── Seletores de <select> de variação (plataformas Tray / WooCommerce / LI) ──
        SELECT_VARIATION = [
            "select[name*='tamanho']",
            "select[name*='size']",
            "select[id*='tamanho']",
            "select[id*='size']",
            "select.variation-select",
            ".variations select",
            "select[data-attribute_name]",
            "select[id*='attribute']",
            "select[name*='attribute']",
            # Tray usa nomes como "atributo_1", "opcao_0" etc.
            "select[name^='atributo']",
            "select[name^='opcao']",
            "select[id^='atributo']",
            # Papachulli (Tray) — id exato do select de variação
            "#variation_first_select",
            "select[id*='variation']",
            # Loja Integrada
            "select.product-form__select",
            "select[data-option-index]",
            # Genérico — qualquer select dentro do form de produto
            "form.product-form select",
            "form.cart select",
            "#product-form select",
            ".product-single__form select",
        ]

        # ── Seletores de botões/links de tamanho ────────────────────────────────
        BUTTON_CONTAINERS = [
            # Tray / Loja Integrada
            ".product-options-list",
            ".product-option-list",
            ".attribute-options",
            ".variant-options",
            # WooCommerce Variation Swatches
            ".variable-items-wrapper",
            ".wc-pao-addon-field",
            # Genérico
            ".product-variants",
            ".size-selector",
            "[class*='size-option']",
            "[class*='variacao']",
            "[class*='variation']",
        ]

        # ──────────────────────────────────────────────────────────────────────
        # CAMADA 1 — Tenta G/M/P por texto exato (botão, link, label, span)
        # ──────────────────────────────────────────────────────────────────────
        for size in self.SIZE_PRIORITY:
            for locator_str in [
                f"button:text-is('{size}')",
                f"a:text-is('{size}')",
                f"label:text-is('{size}')",
                f"span:text-is('{size}')",
                f"li:text-is('{size}')",
                f"[data-value='{size}']",
                f"[data-attribute-value='{size}']",
                f".variable-item[data-value='{size}']",
                f"input[value='{size}'] + label",
            ]:
                try:
                    el = page.locator(locator_str).first
                    if el.is_visible(timeout=400):
                        el.click()
                        log.debug("[size] '%s' via '%s'", size, locator_str)
                        return
                except Exception:
                    pass

        # ──────────────────────────────────────────────────────────────────────
        # CAMADA 2 — Tenta G/M/P em <select> de variação
        # Usa timeout=500 para não travar 30s quando a opção não existe
        # ──────────────────────────────────────────────────────────────────────
        for sel_str in SELECT_VARIATION:
            try:
                sel_el = page.locator(sel_str).first
                if not sel_el.is_visible(timeout=400):
                    continue
                for size in self.SIZE_PRIORITY:
                    for method, val in [("value", size), ("value", size.lower()),
                                        ("label", size), ("label", size.lower())]:
                        try:
                            if method == "value":
                                sel_el.select_option(value=val, timeout=500)
                            else:
                                sel_el.select_option(label=val, timeout=500)
                            log.debug("[size] '%s' via select %s (%s)", size, sel_str, method)
                            return
                        except Exception:
                            pass
            except Exception:
                pass

        # ──────────────────────────────────────────────────────────────────────
        # CAMADA 3 — Seleciona PRIMEIRA opção não-vazia em qualquer <select>
        #            de variação (qualquer tamanho serve)
        # ──────────────────────────────────────────────────────────────────────
        for sel_str in SELECT_VARIATION:
            try:
                sel_el = page.locator(sel_str).first
                if not sel_el.is_visible(timeout=400):
                    continue
                # Lê todas as options via JS e pega a primeira com valor
                opts = page.evaluate(
                    """(sel) => {
                        const el = document.querySelector(sel);
                        if (!el) return [];
                        return [...el.options].map(o => ({value: o.value, text: o.text.trim()}));
                    }""",
                    sel_str,
                )
                for opt in opts:
                    v = opt.get("value", "").strip()
                    if v and v not in ("", "0", "escolha", "selecione", "choose", "select"):
                        try:
                            sel_el.select_option(value=v, timeout=500)
                            log.debug("[size] primeira opcao '%s' via select %s", v, sel_str)
                            return
                        except Exception:
                            pass
                    t = opt.get("text", "").strip()
                    if t and t.lower() not in ("", "escolha", "selecione", "choose", "select"):
                        try:
                            sel_el.select_option(label=t, timeout=500)
                            log.debug("[size] primeira opcao label '%s' via select %s", t, sel_str)
                            return
                        except Exception:
                            pass
            except Exception:
                pass

        # ──────────────────────────────────────────────────────────────────────
        # CAMADA 4 — Clica em qualquer primeiro botão/link de opção de produto
        #            dentro de containers de variação conhecidos
        # ──────────────────────────────────────────────────────────────────────
        for container in BUTTON_CONTAINERS:
            try:
                # Pega primeiro filho clicável dentro do container
                el = page.locator(
                    f"{container} button, {container} a, "
                    f"{container} label, {container} li, {container} span"
                ).first
                if el.is_visible(timeout=400):
                    el.click()
                    log.debug("[size] primeiro elemento em container '%s'", container)
                    return
            except Exception:
                pass

        log.debug("[size] nenhum seletor de tamanho encontrado — prosseguindo sem selecionar.")

    def _pw_fill_cep(self, page) -> bool:
        """
        Preenche o campo de CEP com CEP_FULL.
        Retorna True se o campo foi encontrado e preenchido com sucesso.

        Estratégia em 3 tentativas por campo (inputs Tray/React podem rejeitar fill()):
          1. fill() nativo do Playwright
          2. press_sequentially() — simula teclas reais (funciona em inputs controlados)
          3. JavaScript direto com eventos React/Vue sintéticos
        """
        CEP_SELECTORS = [
            "input[id='cep']",
            "input[name='cep']",
            "input[id*='cep']",
            "input[name*='cep']",
            "input[name='zip']",
            "input[id='zip']",
            "input[placeholder*='CEP']",
            "input[placeholder*='cep']",
            "input[autocomplete='postal-code']",
            "input[autocomplete='shipping postal-code']",
            "input.input-cep",
            "input[id*='frete']",
            "#calcular-frete input",
            ".frete-calcular input",
        ]
        cep_digits = re.sub(r"\D", "", self.CEP_FULL)   # somente dígitos

        for sel in CEP_SELECTORS:
            try:
                field = page.locator(sel).first
                if not field.is_visible(timeout=700):
                    continue

                field.scroll_into_view_if_needed(timeout=1000)
                field.click()
                field.triple_click()    # seleciona texto existente

                # Tentativa 1 — fill() nativo
                filled = False
                try:
                    field.fill(self.CEP_FULL)
                    val = field.input_value(timeout=500)
                    if re.sub(r"\D", "", val) == cep_digits:
                        filled = True
                except Exception:
                    pass

                # Tentativa 2 — press_sequentially (simula teclado real)
                if not filled:
                    try:
                        field.triple_click()
                        field.press("Control+a")
                        field.press("Delete")
                        field.press_sequentially(cep_digits, delay=40)
                        val = field.input_value(timeout=500)
                        if re.sub(r"\D", "", val) == cep_digits:
                            filled = True
                    except Exception:
                        pass

                # Tentativa 3 — JavaScript com eventos sintéticos React/Vue
                if not filled:
                    try:
                        js_sel = sel.replace("'", "\\'")
                        page.evaluate(f"""() => {{
                            const el = document.querySelector('{js_sel}');
                            if (!el) return;
                            const setter = Object.getOwnPropertyDescriptor(
                                window.HTMLInputElement.prototype, 'value').set;
                            setter.call(el, '{cep_digits}');
                            el.dispatchEvent(new Event('input',  {{bubbles:true}}));
                            el.dispatchEvent(new Event('change', {{bubbles:true}}));
                        }}""")
                        val = field.input_value(timeout=500)
                        if re.sub(r"\D", "", val) == cep_digits:
                            filled = True
                    except Exception:
                        pass

                if filled:
                    log.debug("[Delivery CEP] CEP preenchido via '%s'.", sel)
                    return True

            except Exception:
                pass

        return False

    def _pw_click_calc(self, page) -> None:
        """
        Clica no botão de calcular frete.
        Fallback: pressiona Enter no campo CEP.
        """
        CALC_SELECTORS = [
            "button:has-text('Calcular')",
            "button:has-text('CALCULAR')",
            "input[value='Calcular']",
            "input[value='CALCULAR']",
            "button:has-text('IR')",
            "input[value='IR']",
            "a:has-text('Calcular')",
            "a:has-text('CALCULAR')",
            "button[id*='calcular']",
            "button[class*='calcular']",
            "button[class*='frete-btn']",
            "[data-action='calcular-frete']",
            ".btn-calcular-frete",
            "#btn-calcular",
        ]
        for sel in CALC_SELECTORS:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=700):
                    btn.click()
                    log.debug("[Delivery CEP] Botão calcular via '%s'.", sel)
                    return
            except Exception:
                pass

        # Fallback: Enter no campo CEP
        for cep_sel in ["input[id*='cep']", "input[name*='cep']"]:
            try:
                field = page.locator(cep_sel).first
                if field.is_visible(timeout=400):
                    field.press("Enter")
                    log.debug("[Delivery CEP] Calcular via Enter no campo CEP.")
                    return
            except Exception:
                pass

        log.debug("[Delivery CEP] Botão calcular não encontrado.")

    def _pw_read_result(self, page) -> str:
        """
        Lê o prazo de entrega dos resultados de frete calculado.
        Retorna a opção com menor número de dias (excluindo opções > 30 dias).
        """
        RESULT_SELECTORS = [
            # Tray
            ".freight-simulations",
            ".shipping-options",
            ".frete-opcoes",
            ".freight-list",
            # WooCommerce
            ".woocommerce-shipping-calculator",
            "table.shop_table.woocommerce-shipping-calculator",
            ".shipping_calculator",
            # Loja Integrada
            "#shipping-calculator",
            ".frete-resultado",
            "[id*='shipping-result']",
            "[class*='shipping-result']",
            "[class*='frete-result']",
            # Genérico — tabela de frete
            "table.frete",
            ".frete-table",
        ]

        # Coleta texto de todos os seletores conhecidos
        all_texts: list[str] = []
        for sel in RESULT_SELECTORS:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=1500):
                    all_texts.append(el.inner_text())
            except Exception:
                pass

        # Fallback: busca elementos com padrão de dias via JS
        if not all_texts:
            try:
                js_texts = page.evaluate("""() => {
                    const found = [];
                    document.querySelectorAll('*').forEach(el => {
                        const t = (el.innerText || '').trim();
                        const low = t.toLowerCase();
                        if ((low.match(/\\d+\\s*dias?/) ||
                             low.includes('sedex') || low.includes('pac') ||
                             low.includes('loggi') || low.includes('j&t')) &&
                             t.length < 400 && el.children.length <= 8) {
                            found.push(t);
                        }
                    });
                    // ordena do menor para o maior (mais específico primeiro)
                    found.sort((a, b) => a.length - b.length);
                    return found.slice(0, 12);
                }""")
                all_texts.extend(js_texts or [])
            except Exception:
                pass

        # Extrai e rankeia todos os prazos encontrados no texto coletado
        found_days: list[int] = []
        for text in all_texts:
            for m in re.finditer(r"(\d+)\s*dias?\s*(úteis?|uteis?)?", text.lower()):
                days = int(m.group(1))
                if 1 <= days <= 30:     # range realista de entrega
                    found_days.append(days)

        if not found_days:
            # Tenta extract_delivery_info em cada bloco de texto
            for text in all_texts:
                d = extract_delivery_info(text)
                if d and d not in ("Frete Grátis", "Frete calculado no checkout", ""):
                    return d
            # Verifica frete grátis
            for text in all_texts:
                if any(w in text.lower() for w in ["frete grátis", "frete gratis", "grátis"]):
                    return "Frete Grátis"
            return ""

        # Retorna o prazo mais curto encontrado
        min_days = min(found_days)
        return f"Entrega em {min_days} dia{'s' if min_days > 1 else ''} úteis"

    # ══════════════════════════════════════════════════════════
    #  CAMADA 3 — PLAYWRIGHT (Beroc / Shopify apenas)
    # ══════════════════════════════════════════════════════════

    def _enrich_beroc_playwright(self, products: list[Product], beroc_todo: list[int]) -> list[Product]:
        """
        Shopify exibe o prazo na página do produto automaticamente.
        Amostra 3 páginas, propaga o resultado mais comum.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("[Delivery] Playwright não disponível — Beroc sem prazo.")
            return products

        sample  = random.sample(beroc_todo, min(self.SAMPLE, len(beroc_todo)))
        results: list[str] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="pt-BR",
                viewport={"width": 1280, "height": 800},
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = ctx.new_page()

            for i in sample:
                url    = products[i].url
                result = self._pw_beroc(page, url)
                if result:
                    results.append(result)
                    products[i] = dc_replace(products[i], delivery_info=result)
                polite_sleep()

            browser.close()

        if results:
            from collections import Counter
            best = Counter(results).most_common(1)[0][0]
            log.info("[Delivery] [Beroc] propagando '%s' para %d produtos.", best, len(beroc_todo))
            for i in beroc_todo:
                if not products[i].delivery_info:
                    products[i] = dc_replace(products[i], delivery_info=best)

        return products

    def _pw_beroc(self, page, url: str) -> str:
        """Lê 'Chegará entre X e Y' que o Shopify exibe sem precisar de CEP."""
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.PW_TIMEOUT)
            page.wait_for_timeout(3000)

            # Busca elementos com 'chegará' (menor elemento = mais específico)
            texts = page.evaluate("""() => {
                const found = [];
                document.querySelectorAll('*').forEach(el => {
                    const t = (el.innerText || '').trim();
                    const low = t.toLowerCase();
                    if ((low.includes('chegará') || low.includes('chegara')) &&
                        t.length > 8 && t.length < 250 && el.children.length <= 5) {
                        found.push(t);
                    }
                });
                found.sort((a, b) => a.length - b.length);
                return found.slice(0, 8);
            }""")

            for t in (texts or []):
                d = extract_delivery_info(t)
                if d and d not in ("Frete Grátis", "Frete calculado no checkout"):
                    log.info("[Delivery] [Beroc] %s -> %s", url, d)
                    return d

            # Seletores Shopify padrão
            for sel in [
                "[data-delivery-estimate]", ".product__pickup-availabilities",
                "[class*='delivery-estimate']", "[class*='shipping-estimate']",
                ".product-meta__shipping",
            ]:
                el = page.query_selector(sel)
                if not el:
                    continue
                d = extract_delivery_info(el.inner_text().strip())
                if d and d not in ("Frete Grátis", "Frete calculado no checkout"):
                    log.info("[Delivery] [Beroc] seletor -> %s", d)
                    return d

        except Exception as e:
            log.warning("[Delivery] [Beroc] %s: %s", url, e)
        return ""

    # ══════════════════════════════════════════════════════════
    #  CAMADA 4 — MANUAL OVERRIDES (delivery_manual.json)
    # ══════════════════════════════════════════════════════════

    def _apply_manual_overrides(self, products: list[Product]) -> list[Product]:
        """
        Lê delivery_manual.json e aplica overrides para lojas sem prazo.
        Formato: {"Key Design": "Entrega em 7 dias úteis", "Papachulli": "..."}
        """
        override_file = Path("delivery_manual.json")
        if not override_file.exists():
            return products
        try:
            import json
            overrides: dict = json.loads(override_file.read_text(encoding="utf-8"))
            applied = 0
            for i, p in enumerate(products):
                val = overrides.get(p.store, "")
                if val and not p.delivery_info:
                    products[i] = dc_replace(p, delivery_info=val)
                    applied += 1
            if applied:
                log.info("[Delivery] Manual overrides aplicados: %d produtos.", applied)
        except Exception as e:
            log.warning("[Delivery] delivery_manual.json erro: %s", e)
        return products


# ─────────────────────────────────────────────
#  PROCESSAMENTO E RELATÓRIO
# ─────────────────────────────────────────────

def _extract_discount(context: str) -> str:
    """Extrai 'desconto:X%' do campo context (preenchido pela API do ML)."""
    if not context:
        return ""
    m = re.search(r"desconto:(\d+%)", context)
    return m.group(1) if m else ""


def _extract_position(context: str) -> str:
    """Extrai 'position:N' do campo context. Retorna string vazia se ausente ou zero."""
    if not context:
        return ""
    m = re.search(r"\bposition:(\d+)", context)
    if not m:
        return ""
    pos = int(m.group(1))
    return str(pos) if pos > 0 else ""


def build_rows(products: list[Product], *, male_only: bool = False) -> list[dict]:
    """Converte lista de produtos em linhas e remove duplicatas."""
    seen = set()
    rows = []
    for p in products:
        if male_only and not is_masculine_product(p):
            continue
        key = (p.store, p.name, round(p.price, 2))
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "Loja": p.store,
                "Material": p.material,
                "Produto": p.name,
                "Preço": float(p.price),
                "Desconto": _extract_discount(p.context),
                "Posição": _extract_position(p.context),   # posição na busca ML; vazio para outras lojas
                "Entrega": p.delivery_info or "-",
                "URL": p.url,
            }
        )
    return rows


def build_promotions(rows: list[dict]) -> list[dict]:
    """
    Filtra produtos com desconto declarado (campo 'Desconto' não vazio).
    Ordena por percentual de desconto decrescente.
    """
    promo_rows = [r for r in rows if r.get("Desconto")]
    promo_rows.sort(
        key=lambda r: int(r["Desconto"].replace("%", "") or "0"),
        reverse=True,
    )
    return promo_rows


def build_delivery_detail(rows: list[dict]) -> list[dict]:
    """
    Agrega por (Material, Concorrente) para comparação granular de prazo e frete.

    Para cada combinação de material × loja, calcula:
      - Preço mínimo e médio de todos os produtos dessa loja nesse material
      - Prazo mais comum (moda dos valores de delivery_info)
      - Percentual de produtos com frete grátis

    Returns:
        Lista de dicts ordenados por (Material, Concorrente), com campos:
        Material, Concorrente, Preço Mín, Preço Médio, Prazo, Frete Grátis %
    """
    from collections import Counter

    groups: dict[tuple, list] = {}   # (material, store) → list of row dicts
    for row in rows:
        key = (row["Material"], row["Loja"])
        groups.setdefault(key, []).append(row)

    detail = []
    for (material, store), group in groups.items():
        prices = [float(r["Preço"]) for r in group]
        deliveries = [
            r["Entrega"] for r in group
            if r.get("Entrega") and r["Entrega"] not in ("-", "—", "")
        ]
        prazo = Counter(deliveries).most_common(1)[0][0] if deliveries else "-"
        free_count = sum(
            1 for d in deliveries
            if "grát" in d.lower() or "gratis" in d.lower()
        )
        free_pct = round(free_count / len(group) * 100)

        detail.append({
            "Material": material,
            "Concorrente": store,
            "Preço Mín": round(min(prices), 2),
            "Preço Médio": round(sum(prices) / len(prices), 2),
            "Prazo": prazo,
            "Frete Grátis %": f"{free_pct}%",
        })

    return sorted(detail, key=lambda r: (r["Material"], r["Concorrente"]))


def build_summary(rows: list[dict]) -> list[dict]:
    """Agrega por Loja + Material: min, média, max e contagem."""
    grouped: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        key = (row["Loja"], row["Material"])
        grouped.setdefault(key, []).append(float(row["Preço"]))

    summary = []
    for (store, material), prices in grouped.items():
        summary.append(
            {
                "Loja": store,
                "Material": material,
                "Mínimo": min(prices),
                "Médio": round(sum(prices) / len(prices), 2),
                "Máximo": max(prices),
                "Produtos": len(prices),
            }
        )

    return sorted(summary, key=lambda r: (r["Material"], r["Loja"]))


def build_delivery_summary(rows: list[dict]) -> list[dict]:
    """
    Agrega informações de entrega por loja:
    - % de produtos com frete grátis
    - % de produtos com prazo informado
    - Prazo mais comum (moda)
    """
    from collections import Counter

    store_data: dict[str, dict] = {}
    for row in rows:
        store = row["Loja"]
        info  = row.get("Entrega", "—").strip()
        if store not in store_data:
            store_data[store] = {"total": 0, "frete_gratis": 0, "com_prazo": 0, "termos": []}
        store_data[store]["total"] += 1
        # Exclui placeholders vazios ("-", "—", "") — só conta info real
        if info and info not in ("-", "—"):
            store_data[store]["com_prazo"] += 1
            store_data[store]["termos"].append(info)
            if "grátis" in info.lower() or "gratis" in info.lower():
                store_data[store]["frete_gratis"] += 1

    summary = []
    for store, d in sorted(store_data.items()):
        total = d["total"] or 1
        moda  = Counter(d["termos"]).most_common(1)
        summary.append({
            "Loja":           store,
            "Produtos":       d["total"],
            "Frete Grátis":  f"{d['frete_gratis'] / total * 100:.0f}%",
            "Com Prazo":     f"{d['com_prazo'] / total * 100:.0f}%",
            "Prazo Mais Comum": moda[0][0] if moda else "-",
        })
    return summary


def print_delivery_table(delivery_summary: list[dict]) -> None:
    """Imprime tabela de comparativo de prazos de entrega no terminal."""
    table = Table(
        title="Comparativo de Prazos de Entrega -- Concorrentes",
        show_header=True,
        header_style="bold white on dark_green",
        border_style="green",
        show_lines=True,
    )
    table.add_column("Loja",              style="bold cyan",   min_width=16)
    table.add_column("Produtos",          justify="center",    min_width=9)
    table.add_column("Frete Grátis",     justify="center",    min_width=13, style="green")
    table.add_column("Com Prazo Info",   justify="center",    min_width=14)
    table.add_column("Prazo Mais Comum", style="yellow",      min_width=22)

    for row in delivery_summary:
        # Destaca frete grátis alto em verde, baixo em vermelho
        fg = row["Frete Grátis"]
        fg_pct = int(fg.replace("%", ""))
        fg_styled = f"[green]{fg}[/]" if fg_pct >= 50 else (f"[yellow]{fg}[/]" if fg_pct > 0 else f"[dim]{fg}[/]")

        table.add_row(
            row["Loja"],
            str(row["Produtos"]),
            fg_styled,
            row["Com Prazo"],
            row["Prazo Mais Comum"],
        )
    console.print(table)


def print_delivery_detail_table(detail: list[dict]) -> None:
    """
    Imprime tabela detalhada de prazos e preços por material × concorrente no terminal.
    Inclui comparação direta com o preço AXEN via badge colorido.
    """
    if not detail:
        return

    # Agrupa por material para exibir uma tabela por bloco
    materials: list[str] = []
    for row in detail:
        if row["Material"] not in materials:
            materials.append(row["Material"])

    for material in materials:
        axen_price = AXEN_PRICES.get(material)
        title_suffix = f"  •  AXEN: R$ {axen_price:.0f}" if axen_price else ""

        table = Table(
            title=f"📦 Entrega & Preços por Concorrente — {material.upper()}{title_suffix}",
            show_header=True,
            header_style="bold white on dark_blue",
            border_style="blue",
            show_lines=True,
        )
        table.add_column("Concorrente",   style="bold cyan",  min_width=16)
        table.add_column("Preço Mín",     justify="right",    min_width=10)
        table.add_column("Preço Médio",   justify="right",    min_width=11, style="bold")
        table.add_column("Prazo",         style="yellow",     min_width=22)
        table.add_column("Frete Grátis",  justify="center",   min_width=12)
        table.add_column("vs. AXEN",      justify="center",   min_width=12)

        for row in detail:
            if row["Material"] != material:
                continue

            avg = row["Preço Médio"]
            fg = row["Frete Grátis %"]
            fg_pct = int(fg.replace("%", ""))
            fg_styled = (
                f"[green]{fg}[/]" if fg_pct >= 50
                else f"[yellow]{fg}[/]" if fg_pct > 0
                else f"[dim]{fg}[/]"
            )

            if axen_price is not None:
                diff = (axen_price - avg) / avg * 100
                if diff > 10:
                    vs_axen = f"[green]+{diff:.0f}%[/]"   # AXEN mais cara → concorrente é opção
                elif diff > -10:
                    vs_axen = f"[yellow]≈{diff:+.0f}%[/]"  # equivalente
                else:
                    vs_axen = f"[red]{diff:.0f}%[/]"       # AXEN mais barata que este concorrente
            else:
                vs_axen = "—"

            table.add_row(
                row["Concorrente"],
                f"R$ {row['Preço Mín']:.2f}",
                f"R$ {avg:.2f}",
                row["Prazo"],
                fg_styled,
                vs_axen,
            )

        console.print(table)


def write_csv(path: str, rows: list[dict], fieldnames: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_rich_table(summary: list[dict]):
    """Imprime tabela colorida no terminal."""
    table = Table(
        title="📊 Análise Competitiva de Preços — AXEN vs. Concorrentes",
        show_header=True,
        header_style="bold white on dark_blue",
        border_style="blue",
        show_lines=True,
    )
    
    table.add_column("Loja",      style="bold cyan",  min_width=14)
    table.add_column("Material",  style="yellow",     min_width=10)
    table.add_column("Mín (R$)",  justify="right",    min_width=10)
    table.add_column("Médio (R$)", justify="right",   min_width=11, style="bold green")
    table.add_column("Máx (R$)",  justify="right",    min_width=10)
    table.add_column("Qtd",       justify="center",   min_width=6)
    
    for row in summary:
        table.add_row(
            row["Loja"],
            row["Material"].capitalize(),
            f"R$ {row['Mínimo']:.2f}",
            f"R$ {row['Médio']:.2f}",
            f"R$ {row['Máximo']:.2f}",
            str(int(row["Produtos"])),
        )
    
    console.print(table)


def print_promotions_table(promotions: list[dict]) -> None:
    """Imprime tabela de promoções detectadas no terminal."""
    if not promotions:
        return
    table = Table(
        title=f"🔥 Promoções Detectadas nos Concorrentes ({len(promotions)} produtos)",
        show_header=True,
        header_style="bold white on dark_red",
        border_style="red",
        show_lines=True,
    )
    table.add_column("Loja",      style="bold cyan", min_width=14)
    table.add_column("Material",  style="yellow",    min_width=8)
    table.add_column("Produto",                      min_width=30)
    table.add_column("Preço",     justify="right",   min_width=10, style="bold green")
    table.add_column("Desconto",  justify="center",  min_width=9,  style="bold red")
    table.add_column("Frete",                        min_width=12)

    for row in promotions[:30]:   # exibe no máx. 30 no terminal
        table.add_row(
            row["Loja"],
            row["Material"].capitalize(),
            row["Produto"][:50] + ("…" if len(row["Produto"]) > 50 else ""),
            f"R$ {float(row['Preço']):.2f}",
            row["Desconto"],
            row.get("Entrega", "-"),
        )
    console.print(table)
    if len(promotions) > 30:
        rprint(f"[dim]… e mais {len(promotions) - 30} promoções em axen_competitive_promotions.csv[/]")


def print_axen_insights(summary: list[dict], axen_prices: dict):
    """
    Imprime resumo executivo com posicionamento da AXEN
    em relação aos concorrentes.
    
    axen_prices = {
        "corda": 125,
        "metal": 215,
        "couro": 215,
        "pedra": None,   # sem pedra no lote atual
    }
    """
    rprint("\n[bold white on dark_orange3]  📋 RESUMO EXECUTIVO — POSICIONAMENTO AXEN  [/]")
    
    for material, axen_price in axen_prices.items():
        if axen_price is None:
            continue
        
        mat_data = [row for row in summary if row["Material"] == material]
        if not mat_data:
            rprint(f"\n[yellow]{material.upper()}:[/] sem dados dos concorrentes.")
            continue
        
        rprint(f"\n[bold yellow]▶ {material.upper()}[/]  (AXEN: [bold green]R$ {axen_price:.0f}[/])")
        
        for row in mat_data:
            store = row["Loja"]
            avg   = row["Médio"]
            mn    = row["Mínimo"]
            mx    = row["Máximo"]
            
            diff_avg = ((axen_price - avg) / avg) * 100
            
            if diff_avg < -15:
                position = f"[green]{diff_avg:+.1f}% vs média[/] ← AXEN mais barata"
            elif diff_avg < 0:
                position = f"[green]{diff_avg:+.1f}% vs média[/] ← AXEN ligeiramente mais barata"
            elif diff_avg < 10:
                position = f"[yellow]{diff_avg:+.1f}% vs média[/] ← AXEN equivalente"
            else:
                position = f"[red]{diff_avg:+.1f}% vs média[/] ← AXEN mais cara"
            
            rprint(
                f"  {store:15s} | "
                f"Faixa: R${mn:.0f}–{mx:.0f} | "
                f"Média: R${avg:.0f} | "
                f"{position}"
            )


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main(
    *,
    key_design_cloudscraper: bool = False,
    male_only: bool = False,
    price_audit: bool = False,
    price_audit_samples: int = 10,
    persist_db: bool = True,
) -> list[Product]:
    global PRICE_AUDIT, PRICE_AUDIT_SAMPLES, PRICE_AUDIT_COUNTS
    PRICE_AUDIT = price_audit
    PRICE_AUDIT_SAMPLES = max(1, int(price_audit_samples))
    PRICE_AUDIT_COUNTS = {}
    rprint("\n[bold white on blue]  AXEN Price Intelligence Scraper · Iniciando  [/]\n")
    
    all_products: list[Product] = []
    
    # ── Beroc ──
    rprint("[bold cyan]→ Raspando Beroc...[/]")
    try:
        beroc = BerocScraper(use_cloudscraper=True)
        beroc_products = beroc.scrape()
        all_products.extend(beroc_products)
        rprint(f"  [green]✓ Beroc: {len(beroc_products)} produtos[/]")
    except Exception as e:
        rprint(f"  [red]✗ Beroc falhou: {e}[/]")
    
    polite_sleep()
    
    # ── Key Design ──
    rprint("[bold cyan]→ Raspando Key Design...[/]")
    try:
        kd = KeyDesignScraper(use_cloudscraper=key_design_cloudscraper)
        kd_products = kd.scrape()
        all_products.extend(kd_products)
        rprint(f"  [green]✓ Key Design: {len(kd_products)} produtos[/]")
    except Exception as e:
        rprint(f"  [red]✗ Key Design falhou: {e}[/]")
    
    polite_sleep()
    
    # ── W. Buscatti ──
    rprint("[bold cyan]→ Raspando W. Buscatti...[/]")
    try:
        wb = WBuscattiScraper(use_cloudscraper=True)
        wb_products = wb.scrape()
        all_products.extend(wb_products)
        rprint(f"  [green]✓ W. Buscatti: {len(wb_products)} produtos[/]")
    except Exception as e:
        rprint(f"  [red]✗ W. Buscatti falhou: {e}[/]")

    polite_sleep()

    # ── Leão de Neméia ──
    rprint("[bold cyan]→ Raspando Leão de Neméia...[/]")
    try:
        lnm = LeaoDeNemeiaScraper()
        lnm_products = lnm.scrape()
        all_products.extend(lnm_products)
        rprint(f"  [green]✓ Leão de Neméia: {len(lnm_products)} produtos[/]")
    except Exception as e:
        rprint(f"  [red]✗ Leão de Neméia falhou: {e}[/]")

    polite_sleep()

    # ── Papachulli ──
    rprint("[bold cyan]→ Raspando Papachulli...[/]")
    try:
        ppc = PapachulliScraper()
        ppc_products = ppc.scrape()
        all_products.extend(ppc_products)
        rprint(f"  [green]✓ Papachulli: {len(ppc_products)} produtos[/]")
    except Exception as e:
        rprint(f"  [red]✗ Papachulli falhou: {e}[/]")

    polite_sleep()

    # ── 4Men ──
    rprint("[bold cyan]→ Raspando 4Men...[/]")
    try:
        qm = QuatroMenScraper()
        qm_products = qm.scrape()
        all_products.extend(qm_products)
        rprint(f"  [green]✓ 4Men: {len(qm_products)} produtos[/]")
    except Exception as e:
        rprint(f"  [red]✗ 4Men falhou: {e}[/]")

    polite_sleep()

    # ── Mercado Livre ──
    rprint("[bold cyan]→ Raspando Mercado Livre (API + Playwright fallback)...[/]")
    try:
        ml = MercadoLivreScraper()
        ml_products = ml.scrape()
        all_products.extend(ml_products)
        rprint(f"  [green]✓ Mercado Livre: {len(ml_products)} produtos[/]")
    except Exception as e:
        rprint(f"  [red]✗ Mercado Livre falhou: {e}[/]")

    if not all_products:
        rprint("[red]Nenhum produto coletado. Verifique a conectividade e os seletores.[/]")
        return []

    # ── CEP Delivery Checker ──
    rprint("[bold cyan]→ Simulando frete com CEP 88065-185 (Playwright)...[/]")
    try:
        checker = CepDeliveryChecker()
        all_products = checker.enrich(all_products)
        rprint("  [green]✓ Verificação de prazos concluída[/]")
    except Exception as e:
        rprint(f"  [yellow]⚠ Verificação de frete falhou (não crítico): {e}[/]")

    # ── Processamento ──
    rprint(f"\n[bold]Total coletado: {len(all_products)} produtos[/]")

    rows = build_rows(all_products, male_only=male_only)
    if male_only:
        rprint(f"[bold]Após filtro masculino: {len(rows)} produtos[/]")
    if not rows:
        rprint("[red]Nenhum produto masculino encontrado com os critérios atuais.[/]")
        return all_products
    summary = build_summary(rows)

    # ── Promoções detectadas (via API ML) ──
    promotions = build_promotions(rows)

    # ── Persistência no banco de dados histórico ──
    # Skipped when persist_db=False (the scheduler owns this step with better
    # error handling, fail_run tracking, and proper run_id management).
    if persist_db:
        rprint("[bold cyan]→ Persistindo no banco de dados...[/]")
        try:
            from axen_database import get_connection, migrate, ingest_scrape_run
            _db_conn = get_connection()
            migrate(_db_conn)
            _run_id = ingest_scrape_run(_db_conn, all_products)
            _db_conn.close()
            rprint(f"  [green]✓ Banco atualizado (run_id={_run_id})[/]")
        except Exception as _db_err:
            rprint(f"  [yellow]⚠ Banco indisponível (não crítico): {_db_err}[/]")

    # ── Saída: CSVs ──
    delivery_summary = build_delivery_summary(rows)
    delivery_detail = build_delivery_detail(rows)
    write_csv(
        "axen_competitive_prices.csv",
        rows,
        ["Loja", "Material", "Produto", "Preço", "Desconto", "Posição", "Entrega", "URL"],
    )
    write_csv(
        "axen_competitive_summary.csv",
        summary,
        ["Loja", "Material", "Mínimo", "Médio", "Máximo", "Produtos"],
    )
    write_csv(
        "axen_competitive_delivery.csv",
        delivery_summary,
        ["Loja", "Produtos", "Frete Grátis", "Com Prazo", "Prazo Mais Comum"],
    )
    write_csv(
        "axen_delivery_detail.csv",
        delivery_detail,
        ["Material", "Concorrente", "Preço Mín", "Preço Médio", "Prazo", "Frete Grátis %"],
    )
    if promotions:
        write_csv(
            "axen_competitive_promotions.csv",
            promotions,
            ["Loja", "Material", "Produto", "Preço", "Desconto", "Posição", "Entrega", "URL"],
        )
        rprint(
            "\n[green]✓ Arquivos salvos:[/] "
            "axen_competitive_prices.csv · "
            "axen_competitive_summary.csv · "
            "axen_competitive_delivery.csv · "
            "axen_delivery_detail.csv · "
            "[yellow]axen_competitive_promotions.csv[/]"
        )
    else:
        rprint(
            "\n[green]✓ Arquivos salvos:[/] "
            "axen_competitive_prices.csv · "
            "axen_competitive_summary.csv · "
            "axen_competitive_delivery.csv · "
            "axen_delivery_detail.csv"
        )

    # ── Saída: tabelas no terminal ──
    print_rich_table(summary)
    print_delivery_table(delivery_summary)
    print_delivery_detail_table(delivery_detail)
    if promotions:
        print_promotions_table(promotions)

    # ── Resumo executivo AXEN ──
    print_axen_insights(summary, AXEN_PRICES)

    rprint("\n[bold green]✓ Análise concluída.[/]")
    return all_products


def cli_main() -> None:
    parser = argparse.ArgumentParser(
        description="AXEN — análise competitiva de preços (Key Design, Beroc, W. Buscatti)."
    )
    parser.add_argument(
        "--cloudscraper",
        action="store_true",
        help="Usa cloudscraper também no Key Design (útil se a VTEX/Cloudflare bloquear requests simples).",
    )
    parser.add_argument(
        "--male-only",
        action="store_true",
        help="Mantém apenas produtos masculinos (exclui itens femininos e neutros sem sinal masculino).",
    )
    parser.add_argument(
        "--price-audit",
        action="store_true",
        help="Loga amostras de normalização de preço por loja (raw -> parsed).",
    )
    parser.add_argument(
        "--price-audit-samples",
        type=int,
        default=10,
        help="Qtd. máxima de amostras de preço por loja no modo --price-audit (padrão: 10).",
    )
    args = parser.parse_args()
    main(
        key_design_cloudscraper=args.cloudscraper,
        male_only=args.male_only,
        price_audit=args.price_audit,
        price_audit_samples=args.price_audit_samples,
    )


if __name__ == "__main__":
    cli_main()
