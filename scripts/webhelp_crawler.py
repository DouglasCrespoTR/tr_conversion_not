#!/usr/bin/env python
"""
WebHelp Crawler - Zendesk Help Center -> Markdown

Downloads articles from Zendesk Help Center API and converts them to Markdown
for use as knowledge base in the TAX ONE support pipeline.

Usage:
  python webhelp_crawler.py --search "Livro Razao Auxiliar"
  python webhelp_crawler.py --category-id 360003086392
  python webhelp_crawler.py --all
  python webhelp_crawler.py --list-categories
  python webhelp_crawler.py --update

Credentials: $ZENDESK_EMAIL, $ZENDESK_TOKEN, $ZENDESK_URL
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import html2text

# --- Configuration ---

TAXONE_CATEGORIES = {
    360003086392: "onesource-tax-one",
    360003109751: "mastersaf-dw",
    360003086952: "onesource-tax-one-sap",
}

DEFAULT_OUTPUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "knowledge", "webhelp"
)

RATE_LIMIT_DELAY = 0.35  # ~170 req/min (safe margin under 200/min)
PER_PAGE = 100


# --- Zendesk API Client ---

class ZendeskClient:
    def __init__(self):
        self.base_url = os.environ.get("ZENDESK_URL", "").rstrip("/")
        self.email = os.environ.get("ZENDESK_EMAIL", "")
        self.token = os.environ.get("ZENDESK_TOKEN", "")

        if not all([self.base_url, self.email, self.token]):
            print("ERROR: Missing ZENDESK_URL, ZENDESK_EMAIL, or ZENDESK_TOKEN env vars")
            sys.exit(1)

        # Basic auth: email/token:api_token
        import base64
        credentials = f"{self.email}/token:{self.token}"
        self.auth_header = "Basic " + base64.b64encode(credentials.encode()).decode()

    def _request(self, endpoint, params=None, use_locale=True):
        """Make authenticated GET request to Zendesk API."""
        locale = "pt-br/" if use_locale else ""
        url = f"{self.base_url}/api/v2/help_center/{locale}{endpoint}"
        if params:
            url += "?" + urlencode(params)

        req = Request(url)
        req.add_header("Authorization", self.auth_header)
        req.add_header("Accept", "application/json")

        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 429:
                retry_after = int(e.headers.get("Retry-After", 10))
                print(f"  Rate limited, waiting {retry_after}s...")
                time.sleep(retry_after)
                return self._request(endpoint, params)
            print(f"  HTTP Error {e.code}: {e.reason} - {url}")
            return None
        except URLError as e:
            print(f"  URL Error: {e.reason} - {url}")
            return None

    def _paginate(self, endpoint, key, params=None, use_locale=True):
        """Paginate through all results."""
        if params is None:
            params = {}
        params["per_page"] = PER_PAGE
        page = 1
        all_items = []

        while True:
            params["page"] = page
            data = self._request(endpoint, params, use_locale=use_locale)
            if not data or key not in data:
                break

            items = data[key]
            if not items:
                break

            all_items.extend(items)
            print(f"  Page {page}: {len(items)} items (total: {len(all_items)})")

            if len(items) < PER_PAGE:
                break

            page += 1
            time.sleep(RATE_LIMIT_DELAY)

        return all_items

    def list_categories(self):
        return self._paginate("categories.json", "categories")

    def list_sections(self, category_id):
        return self._paginate(f"categories/{category_id}/sections.json", "sections")

    def list_articles(self, section_id):
        return self._paginate(f"sections/{section_id}/articles.json", "articles")

    def search_articles(self, query, category_id=None):
        params = {"query": query}
        if category_id:
            params["category"] = category_id
        return self._paginate("articles/search.json", "results", params, use_locale=False)

    def get_article(self, article_id):
        data = self._request(f"articles/{article_id}.json")
        return data.get("article") if data else None


# --- HTML to Markdown Converter ---

def create_converter():
    """Create configured html2text converter."""
    h = html2text.HTML2Text()
    h.body_width = 0  # No wrapping
    h.unicode_snob = True
    h.protect_links = True
    h.wrap_links = False
    h.skip_internal_links = False
    h.inline_links = True
    h.ignore_images = False
    h.ignore_emphasis = False
    h.ignore_tables = False
    h.single_line_break = False
    return h


def html_to_markdown(html_body, converter=None):
    """Convert HTML body to clean Markdown."""
    if not html_body:
        return ""

    if converter is None:
        converter = create_converter()

    md = converter.handle(html_body)

    # Clean up excessive blank lines
    md = re.sub(r"\n{4,}", "\n\n\n", md)
    # Strip trailing whitespace per line
    md = "\n".join(line.rstrip() for line in md.splitlines())

    return md.strip()


# --- Slug/Path Utilities ---

def slugify(text):
    """Convert text to a filesystem-safe slug."""
    text = unicodedata.normalize("NFKD", text)
    text = text.lower()
    # Keep alphanumeric, spaces, hyphens
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text).strip("-")
    return text[:80] if text else "untitled"


# --- Article Writer ---

def write_article(article, section_name, category_slug, output_dir, converter):
    """Write a single article as Markdown file."""
    section_slug = slugify(section_name)
    article_dir = Path(output_dir) / category_slug / section_slug
    article_dir.mkdir(parents=True, exist_ok=True)

    article_id = article["id"]
    title = article.get("title", "Untitled")
    body = article.get("body", "")
    created = article.get("created_at", "")
    updated = article.get("updated_at", "")
    labels = article.get("label_names", [])
    url = article.get("html_url", "")

    md_body = html_to_markdown(body, converter)

    # Build frontmatter-style header
    content = f"# {title}\n\n"
    content += f"- **ID:** {article_id}\n"
    if url:
        content += f"- **URL:** {url}\n"
    if labels:
        content += f"- **Labels:** {', '.join(labels)}\n"
    if created:
        content += f"- **Criado:** {created[:10]}\n"
    if updated:
        content += f"- **Atualizado:** {updated[:10]}\n"
    content += "\n---\n\n"
    content += md_body
    content += "\n"

    filepath = article_dir / f"{article_id}.md"
    filepath.write_text(content, encoding="utf-8")
    return filepath


# --- Index Generator ---

def generate_index(output_dir):
    """Generate INDEX.md with full listing of downloaded articles."""
    output_path = Path(output_dir)
    index_lines = [
        "# WebHelp Knowledge Base - Indice\n",
        f"Gerado em: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
        "Fonte: Zendesk Help Center (atendimentotr.zendesk.com)\n\n",
        "---\n\n",
    ]

    total_articles = 0

    for category_dir in sorted(output_path.iterdir()):
        if not category_dir.is_dir() or category_dir.name.startswith("."):
            continue

        cat_name = category_dir.name.replace("-", " ").title()
        index_lines.append(f"## {cat_name}\n\n")

        for section_dir in sorted(category_dir.iterdir()):
            if not section_dir.is_dir():
                continue

            sec_name = section_dir.name.replace("-", " ").title()
            md_files = sorted(section_dir.glob("*.md"))

            if not md_files:
                continue

            index_lines.append(f"### {sec_name} ({len(md_files)} artigos)\n\n")

            for md_file in md_files:
                # Extract title from first line
                first_line = md_file.read_text(encoding="utf-8").split("\n")[0]
                title = first_line.lstrip("# ").strip() or md_file.stem
                rel_path = md_file.relative_to(output_path)
                index_lines.append(f"- [{title}]({rel_path.as_posix()})\n")
                total_articles += 1

            index_lines.append("\n")

    index_lines.insert(3, f"Total de artigos: **{total_articles}**\n")

    index_path = output_path / "INDEX.md"
    index_path.write_text("".join(index_lines), encoding="utf-8")
    print(f"\nINDEX.md gerado: {total_articles} artigos indexados")
    return index_path


# --- State Management ---

def load_state(output_dir):
    """Load download state (last update timestamps)."""
    state_file = Path(output_dir) / ".state.json"
    if state_file.exists():
        return json.loads(state_file.read_text(encoding="utf-8"))
    return {"last_run": None, "articles": {}}


def save_state(output_dir, state):
    """Save download state."""
    state["last_run"] = datetime.now().isoformat()
    state_file = Path(output_dir) / ".state.json"
    state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# --- Main Commands ---

def cmd_list_categories(client):
    """List all Help Center categories."""
    print("Buscando categorias do Help Center...\n")
    categories = client.list_categories()

    for cat in categories:
        marker = " <-- TAX ONE" if cat["id"] in TAXONE_CATEGORIES else ""
        print(f"  [{cat['id']}] {cat['name']} ({cat.get('description', 'N/A')}){marker}")

    print(f"\nTotal: {len(categories)} categorias")
    print(f"Categorias TAX ONE configuradas: {list(TAXONE_CATEGORIES.keys())}")


def cmd_search(client, query, category_id=None, output_dir=None, max_results=10):
    """Search articles and optionally save them."""
    print(f"Buscando: '{query}'...")
    if category_id:
        print(f"  Filtro categoria: {category_id}")

    articles = client.search_articles(query, category_id)

    if not articles:
        print("Nenhum artigo encontrado.")
        return

    print(f"\n{len(articles)} artigos encontrados (mostrando top {min(max_results, len(articles))}):\n")

    converter = create_converter()

    for i, article in enumerate(articles[:max_results]):
        title = article.get("title", "Untitled")
        article_id = article.get("id", "?")
        snippet = article.get("snippet", "")
        url = article.get("html_url", "")
        section_id = article.get("section_id", "")

        print(f"  {i+1}. [{article_id}] {title}")
        if snippet:
            # Clean HTML from snippet
            clean_snippet = re.sub(r"<[^>]+>", "", snippet)[:150]
            print(f"     {clean_snippet}...")
        if url:
            print(f"     URL: {url}")
        print()

    if output_dir:
        print(f"Salvando {min(max_results, len(articles))} artigos em {output_dir}...")
        saved = 0
        for article in articles[:max_results]:
            if article.get("body"):
                # Fetch full article if body not in search results
                full = client.get_article(article["id"])
                if full and full.get("body"):
                    article = full
                    time.sleep(RATE_LIMIT_DELAY)

            section_name = article.get("section_id", "search-results")
            filepath = write_article(
                article, str(section_name), "search-results", output_dir, converter
            )
            saved += 1
            print(f"  Salvo: {filepath}")

        print(f"\n{saved} artigos salvos.")


def cmd_download_category(client, category_id, output_dir, state, update_only=False):
    """Download all articles from a category."""
    cat_slug = TAXONE_CATEGORIES.get(category_id, slugify(str(category_id)))
    print(f"\n{'='*60}")
    print(f"Categoria: {cat_slug} (ID: {category_id})")
    print(f"{'='*60}")

    sections = client.list_sections(category_id)
    if not sections:
        print("  Nenhuma secao encontrada.")
        return 0

    print(f"  {len(sections)} secoes encontradas")

    converter = create_converter()
    total_saved = 0
    total_skipped = 0

    for section in sections:
        section_id = section["id"]
        section_name = section.get("name", str(section_id))
        print(f"\n  Secao: {section_name} (ID: {section_id})")

        articles = client.list_articles(section_id)
        if not articles:
            print("    Nenhum artigo.")
            continue

        for article in articles:
            article_id = str(article["id"])
            updated_at = article.get("updated_at", "")

            # Skip if not updated since last download
            if update_only and article_id in state.get("articles", {}):
                last_updated = state["articles"][article_id]
                if updated_at <= last_updated:
                    total_skipped += 1
                    continue

            filepath = write_article(article, section_name, cat_slug, output_dir, converter)
            state.setdefault("articles", {})[article_id] = updated_at
            total_saved += 1

            if total_saved % 50 == 0:
                print(f"    ... {total_saved} artigos salvos")

        time.sleep(RATE_LIMIT_DELAY)

    print(f"\n  Categoria {cat_slug}: {total_saved} salvos, {total_skipped} sem alteracao")
    return total_saved


def cmd_download_all(client, output_dir, update_only=False):
    """Download all TAX ONE categories."""
    print(f"Download {'incremental' if update_only else 'completo'} de categorias TAX ONE")
    print(f"Destino: {output_dir}\n")

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    state = load_state(output_dir)
    grand_total = 0

    for cat_id, cat_slug in TAXONE_CATEGORIES.items():
        total = cmd_download_category(client, cat_id, output_dir, state, update_only)
        grand_total += total
        save_state(output_dir, state)

    print(f"\n{'='*60}")
    print(f"TOTAL: {grand_total} artigos salvos")
    print(f"{'='*60}")

    # Generate index
    generate_index(output_dir)

    # Auto-generate MODULE_INDEX.json
    _run_indexer(output_dir)

    return grand_total


# --- Auto-Indexer Integration ---

def _run_indexer(output_dir):
    """Auto-invoke webhelp_indexer.py to regenerate MODULE_INDEX.json."""
    try:
        import subprocess
        indexer_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webhelp_indexer.py")
        if os.path.exists(indexer_path):
            print(f"\nAuto-gerando MODULE_INDEX.json...")
            result = subprocess.run(
                [sys.executable, indexer_path, "--rebuild", "--webhelp-dir", output_dir],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                print(result.stdout)
            else:
                print(f"Aviso: indexer retornou codigo {result.returncode}")
                if result.stderr:
                    print(result.stderr)
        else:
            print(f"Aviso: webhelp_indexer.py nao encontrado em {indexer_path}")
    except Exception as e:
        print(f"Aviso: falha ao executar indexer: {e}")

    # Auto-invoke SAFX catalog builder after indexer
    _run_safx_catalog_builder(output_dir)


def _run_safx_catalog_builder(output_dir):
    """Auto-invoke safx_catalog_builder.py to regenerate SAFX_CATALOG.json."""
    try:
        import subprocess
        builder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "safx_catalog_builder.py")
        if os.path.exists(builder_path):
            print(f"\nAuto-gerando SAFX_CATALOG.json...")
            result = subprocess.run(
                [sys.executable, builder_path, "--rebuild", "--webhelp-dir", output_dir],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                print(result.stdout)
            else:
                print(f"Aviso: safx_catalog_builder retornou codigo {result.returncode}")
                if result.stderr:
                    print(result.stderr)
        else:
            print(f"Aviso: safx_catalog_builder.py nao encontrado em {builder_path}")
    except Exception as e:
        print(f"Aviso: falha ao executar safx_catalog_builder: {e}")


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(
        description="WebHelp Crawler - Zendesk Help Center -> Markdown"
    )
    parser.add_argument(
        "--list-categories", action="store_true",
        help="Listar todas as categorias do Help Center"
    )
    parser.add_argument(
        "--search", type=str, metavar="QUERY",
        help="Buscar artigos por keyword"
    )
    parser.add_argument(
        "--category-id", type=int, metavar="ID",
        help="Baixar artigos de uma categoria especifica"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Baixar todas as categorias TAX ONE"
    )
    parser.add_argument(
        "--update", action="store_true",
        help="Atualizar apenas artigos modificados desde ultimo download"
    )
    parser.add_argument(
        "--output", type=str, default=DEFAULT_OUTPUT,
        help=f"Diretorio de saida (default: {DEFAULT_OUTPUT})"
    )
    parser.add_argument(
        "--max-results", type=int, default=10,
        help="Maximo de resultados para --search (default: 10)"
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Com --search, salvar artigos encontrados em disco"
    )

    args = parser.parse_args()

    if not any([args.list_categories, args.search, args.category_id, args.all]):
        parser.print_help()
        sys.exit(1)

    client = ZendeskClient()

    if args.list_categories:
        cmd_list_categories(client)

    elif args.search:
        cmd_search(
            client, args.search,
            output_dir=args.output if args.save else None,
            max_results=args.max_results
        )

    elif args.category_id:
        Path(args.output).mkdir(parents=True, exist_ok=True)
        state = load_state(args.output)
        cmd_download_category(client, args.category_id, args.output, state, args.update)
        save_state(args.output, state)
        generate_index(args.output)

    elif args.all:
        cmd_download_all(client, args.output, args.update)


if __name__ == "__main__":
    main()
