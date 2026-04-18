#!/usr/bin/env python3
"""
AXEN — Competitive Accessory Price Scraper
==========================================
Scrapes Key Design, W. Buscatti, and Beroc for bracelet prices
segmented by material: couro, metal, corda, pedra.

Usage examples:
  python main.py                          # scrape all stores
  python main.py --stores beroc wbuscatti # scrape specific stores
  python main.py --cloudscraper           # use cloudscraper for anti-bot bypass
  python main.py --no-playwright          # skip Playwright fallback
  python main.py --output-csv prices.csv --output-products produtos.csv
"""
import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import requests
from rich.console import Console
from rich.table import Table

from scraper import beroc, keydesign, wbuscatti
from scraper.analysis import MATERIALS, STORES, analyze, cross_material_insights
from scraper.models import Product
from scraper.utils import make_session

console = Console()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AXEN — competitive price scraper for accessories",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--stores",
        nargs="+",
        choices=["keydesign", "wbuscatti", "beroc"],
        default=["keydesign", "wbuscatti", "beroc"],
        metavar="STORE",
        help="Stores to scrape (default: all three).",
    )
    p.add_argument(
        "--cloudscraper",
        action="store_true",
        help="Use cloudscraper for Cloudflare bypass (install: pip install cloudscraper).",
    )
    p.add_argument(
        "--no-playwright",
        action="store_true",
        help="Disable Playwright headless-browser fallback for Key Design.",
    )
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    p.add_argument(
        "--output-csv",
        type=Path,
        default=Path(f"axen_stats_{ts}.csv"),
        help="Output CSV for the statistics table.",
    )
    p.add_argument(
        "--output-products",
        type=Path,
        default=None,
        help="(Optional) Output CSV with every individual product collected.",
    )
    p.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="(Optional) Output JSON with the full statistics dict.",
    )
    return p.parse_args()


# ── Output helpers ────────────────────────────────────────────────────────────

def print_stats_table(stats: dict) -> None:
    table = Table(
        title="Análise Competitiva de Preços — Acessórios Masculinos",
        header_style="bold cyan",
        show_lines=True,
    )
    table.add_column("Loja", style="bold white", no_wrap=True)
    table.add_column("Material", no_wrap=True)
    table.add_column("Qtd", justify="right")
    table.add_column("Mín (R$)", justify="right", style="green")
    table.add_column("Médiana (R$)", justify="right")
    table.add_column("Média (R$)", justify="right")
    table.add_column("Máx (R$)", justify="right", style="red")

    for store in STORES:
        store_data = stats.get(store, {})
        for material in MATERIALS:
            s = store_data.get(material)
            if s:
                table.add_row(
                    store, material.capitalize(),
                    str(s["count"]),
                    f"{s['min']:.2f}",
                    f"{s['median']:.2f}",
                    f"{s['avg']:.2f}",
                    f"{s['max']:.2f}",
                )
            else:
                table.add_row(store, material.capitalize(), "—", "—", "—", "—", "—")

    console.print()
    console.print(table)


def print_executive_summary(stats: dict) -> None:
    console.print("\n[bold yellow]══ Resumo Executivo — Insights de Posicionamento ══[/bold yellow]\n")
    for insight in cross_material_insights(stats):
        console.print(insight)
        console.print()


def export_stats_csv(stats: dict, path: Path) -> None:
    rows = []
    for store in STORES:
        store_data = stats.get(store, {})
        for material in MATERIALS:
            s = store_data.get(material, {})
            rows.append({
                "Loja": store,
                "Material": material.capitalize(),
                "Qtd Produtos": s.get("count", 0),
                "Preço Mínimo (R$)": f"{s['min']:.2f}" if s else "",
                "Preço Médio (R$)": f"{s['avg']:.2f}" if s else "",
                "Preço Mediano (R$)": f"{s['median']:.2f}" if s else "",
                "Preço Máximo (R$)": f"{s['max']:.2f}" if s else "",
            })
    _write_csv(rows, path)
    console.print(f"[dim]Tabela de estatísticas salva em: {path}[/dim]")


def export_products_csv(products: list[Product], path: Path) -> None:
    rows = [
        {
            "Loja": p.store,
            "Material": p.material.capitalize(),
            "Produto": p.name,
            "Preço (R$)": f"{p.price:.2f}",
            "URL": p.url,
        }
        for p in sorted(products, key=lambda x: (x.store, x.material, x.price))
    ]
    _write_csv(rows, path)
    console.print(f"[dim]Produtos individuais salvos em: {path}[/dim]")


def _write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


# ── Scraping orchestration ────────────────────────────────────────────────────

_SCRAPER_MAP = {
    "beroc": beroc,
    "wbuscatti": wbuscatti,
    "keydesign": keydesign,
}

_STORE_DISPLAY = {
    "beroc": "Beroc",
    "wbuscatti": "W. Buscatti",
    "keydesign": "Key Design",
}


def run_scrapers(
    store_keys: list[str],
    session: requests.Session,
    use_playwright: bool,
) -> list[Product]:
    all_products: list[Product] = []

    for key in store_keys:
        display = _STORE_DISPLAY[key]
        console.print(f"\n[cyan]⟶ Scraping {display}…[/cyan]")
        module = _SCRAPER_MAP[key]

        try:
            if key == "keydesign":
                products = module.scrape(session, use_playwright=use_playwright)
            else:
                products = module.scrape(session)
        except Exception as exc:
            console.print(f"  [red]✗ Erro em {display}: {exc}[/red]")
            logging.exception("Scraping %s raised an exception", display)
            continue

        all_products.extend(products)
        mat_counts = {}
        for p in products:
            mat_counts[p.material] = mat_counts.get(p.material, 0) + 1

        if products:
            summary = ", ".join(f"{m}: {c}" for m, c in sorted(mat_counts.items()))
            console.print(f"  [green]✓[/green] {len(products)} produtos  ({summary})")
        else:
            console.print(f"  [yellow]⚠[/yellow]  Nenhum produto coletado em {display}.")

    return all_products


# ── Main ──────────────────────────────────────────────────────────────────────

def _check_connectivity(session: requests.Session) -> bool:
    """Quick connectivity probe. Returns False if outbound HTTP is blocked."""
    try:
        r = session.get("https://beroc.com.br/products.json?limit=1", timeout=10)
        # Any HTTP response (even 403 from the site) means network is reachable
        return r.status_code != -1
    except requests.exceptions.ConnectionError as e:
        if "allowlist" in str(e).lower() or "host" in str(e).lower():
            return False
        return False
    except Exception:
        return True  # assume reachable on unexpected errors


def main() -> None:
    args = build_args()

    console.print("[bold]AXEN — Competitive Price Scraper[/bold]")
    console.print(f"Lojas: {', '.join(_STORE_DISPLAY[k] for k in args.stores)}")
    console.print(f"cloudscraper: {'on' if args.cloudscraper else 'off'}  |  "
                  f"Playwright: {'off' if args.no_playwright else 'on'}")

    session = make_session(use_cloudscraper=args.cloudscraper)

    # Pre-flight connectivity check
    try:
        probe = session.get("https://beroc.com.br/products.json?limit=1", timeout=10)
        if "allowlist" in probe.text or "not in allowlist" in probe.text:
            console.print(
                "\n[red bold]Sem acesso à internet externa.[/red bold]\n"
                "Este ambiente bloqueou conexões de saída ('Host not in allowlist').\n\n"
                "Execute o scraper na sua máquina local:\n"
                "  1. Clone o repositório\n"
                "  2. pip install -r requirements.txt\n"
                "  3. python main.py --cloudscraper\n\n"
                "Se os sites ainda bloquearem (IP de VPS/datacenter), configure um proxy residencial:\n"
                "  export HTTPS_PROXY=http://user:senha@proxy.exemplo.com:8080\n"
                "  python main.py --cloudscraper"
            )
            sys.exit(2)
    except Exception:
        pass  # network up but site responded unusually — continue
    products = run_scrapers(args.stores, session, use_playwright=not args.no_playwright)

    if not products:
        console.print(
            "\n[red bold]Nenhum produto coletado.[/red bold]\n"
            "Possíveis causas:\n"
            "  • Sites bloqueando o IP — tente --cloudscraper ou configure HTTPS_PROXY\n"
            "  • Playwright não instalado para Key Design — pip install playwright playwright-stealth\n"
            "  • Seletores CSS desatualizados — verifique o HTML atual com DevTools"
        )
        sys.exit(1)

    stats = analyze(products)
    print_stats_table(stats)
    print_executive_summary(stats)

    export_stats_csv(stats, args.output_csv)

    if args.output_products:
        export_products_csv(products, args.output_products)

    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        console.print(f"[dim]JSON salvo em: {args.output_json}[/dim]")

    console.print(
        f"\n[bold green]Concluído.[/bold green] "
        f"{len(products)} produtos coletados de {len(args.stores)} loja(s)."
    )


if __name__ == "__main__":
    main()
