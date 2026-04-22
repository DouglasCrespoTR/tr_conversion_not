#!/usr/bin/env python
"""
SAFX Catalog Builder - Extracts SAFX error codes and solutions from WebHelp articles.

Parses WebHelp markdown articles to build a structured catalog of SAFX import errors
and their documented solutions, used for FAQ-based triage in the pipeline.

Usage:
  python safx_catalog_builder.py --rebuild [--stats]
  python safx_catalog_builder.py --lookup SAFX07 90034
  python safx_catalog_builder.py --lookup-error 90034
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path


# --- Configuration ---

DEFAULT_WEBHELP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "knowledge", "webhelp"
)

CATALOG_FILENAME = "SAFX_CATALOG.json"

# Facet priority: taxone > dw > taxone_sap
FACET_PRIORITY = {"taxone": 3, "dw": 2, "taxone_sap": 1}

# Directories mapped to facet names
FACET_DIRS = {
    "onesource-tax-one/faq": "taxone",
    "mastersaf-dw/faq-dw": "dw",
    "onesource-tax-one-sap/faq": "taxone_sap",
}

# --- Regex Patterns ---

# Title pattern: SAFX{NN} ... Erro {NNNNN} or "erro {NNNNN}"
RE_TITLE_SAFX = re.compile(
    r"SAFX\s*(\d+)", re.IGNORECASE
)
RE_TITLE_ERRO = re.compile(
    r"[Ee]rro\s*(\d+)"
)

# Also capture error code from title patterns like "Como corrigir o erro NNNNN"
RE_TITLE_CORRIGIR_ERRO = re.compile(
    r"[Cc]omo\s+corrigir\s+o\s+erro\s+(\d+)"
)

# Body: solution paragraph
RE_SOLUTION = re.compile(
    r"(?:^|\n)\s*\d*\\?\.?\s*(?:Para\s+(?:resolver|solucionar|corrigir|corrigi-l[oa])|Passo\s+a\s+passo\s+para\s+corrigir)"
    r"(.*?)(?:\n\n|\n\s*\d+[\.\)]|\Z)",
    re.IGNORECASE | re.DOTALL
)

# Body: error description after "Erro NNNNN" with dash separator
RE_ERRO_DESC = re.compile(
    r"[Ee]rro\s+\d+\s*[-:–—]\s*\*{0,4}\s*(.*?)(?:\n|$)"
)

# Body: "Preencha o campo" instruction pattern (common in TaxOne articles)
RE_PREENCHA = re.compile(
    r"(?:^|\n)\s*\d*\.?\s*Preencha\s+o\s+campo\s+(.*?)(?:\n\n|\Z)",
    re.IGNORECASE | re.DOTALL
)

# Body: "O problema ocorre pois" cause pattern
RE_CAUSA = re.compile(
    r"(?:^|\n)\s*\d*\.?\s*O\s+problema\s+ocorre\s+(?:pois|porque|quando)(.*?)(?:\n\n|\Z)",
    re.IGNORECASE | re.DOTALL
)

# Metadata: article ID
RE_ID = re.compile(r"\*\*ID:\*\*\s*(\d+)")


# --- Extraction Logic ---

def extract_article_info(filepath, facet):
    """Extract SAFX code, error code, description and solution from a WebHelp article."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return None

    lines = content.split("\n")
    if not lines:
        return None

    title = lines[0].lstrip("# ").strip()

    # Extract SAFX code from title
    m_safx = RE_TITLE_SAFX.search(title)
    if not m_safx:
        return None

    safx_num = m_safx.group(1).lstrip("0") or "0"
    safx_code = f"SAFX{safx_num.zfill(2)}"

    # Extract error code from title
    m_erro = RE_TITLE_ERRO.search(title)
    if not m_erro:
        m_erro = RE_TITLE_CORRIGIR_ERRO.search(title)
    if not m_erro:
        return None

    error_code = m_erro.group(1)

    # Extract article ID from metadata
    m_id = RE_ID.search(content)
    article_id = m_id.group(1) if m_id else Path(filepath).stem

    # Extract error description
    description = ""
    m_desc = RE_ERRO_DESC.search(content)
    if m_desc:
        description = _clean_text(m_desc.group(1))

    # Extract solution - try multiple patterns
    solution = ""

    # Pattern 1: "Para resolver/solucionar/corrigir..."
    m_sol = RE_SOLUTION.search(content)
    if m_sol:
        solution = _clean_text(m_sol.group(0))

    # Pattern 2: "Preencha o campo..."
    if not solution:
        m_preencha = RE_PREENCHA.search(content)
        if m_preencha:
            solution = "Preencha o campo " + _clean_text(m_preencha.group(1))

    # Pattern 3: "O problema ocorre pois..." (use as cause + implicit solution)
    if not solution:
        m_causa = RE_CAUSA.search(content)
        if m_causa:
            solution = _clean_text(m_causa.group(0))

    # Pattern 4: fallback - grab text after "Passo a passo" or numbered step with content
    if not solution:
        # Look for any instructional content after the error description
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("1.", "1)")) and len(stripped) > 10:
                if "Erro" not in stripped and "erro" not in stripped:
                    solution = _clean_text(stripped)
                    break

    # Truncate solution to 300 chars
    if len(solution) > 300:
        solution = solution[:297] + "..."

    if not description:
        # Try to extract from title after the error code
        title_after_erro = title.split(error_code, 1)
        if len(title_after_erro) > 1:
            desc_part = title_after_erro[1].strip().lstrip(":- –—").strip()
            if desc_part:
                description = desc_part

    # Build relative path from webhelp dir
    rel_path = ""
    for dir_suffix, fct in FACET_DIRS.items():
        if fct == facet:
            rel_path = f"{dir_suffix}/{Path(filepath).name}"
            break

    return {
        "safx_code": safx_code,
        "error_code": error_code,
        "description": description,
        "solution": solution,
        "article_id": article_id,
        "article_path": rel_path,
        "facet": facet,
        "title": title,
    }


def _clean_text(text):
    """Clean extracted text: remove markdown, extra whitespace, image refs."""
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)  # remove images
    text = re.sub(r"\[([^\]]*)\]\([^\)]*\)", r"\1", text)  # links -> text
    text = re.sub(r"\*{1,2}(.*?)\*{1,2}", r"\1", text)  # bold/italic -> text
    text = re.sub(r"\s+", " ", text).strip()
    text = text.lstrip("0123456789.)\\ ").strip()
    text = text.strip("'\"?").strip()  # remove surrounding quotes and question marks
    return text


# --- Catalog Building ---

def build_catalog(webhelp_dir):
    """Scan all WebHelp articles and build the SAFX catalog."""
    webhelp_path = Path(webhelp_dir)

    # Load superseded map to skip DW articles that have TaxOne equivalents
    superseded = {}
    index_path = webhelp_path / "MODULE_INDEX.json"
    if index_path.exists():
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                idx = json.load(f)
                superseded = idx.get("superseded", {})
        except (json.JSONDecodeError, OSError):
            pass

    superseded_ids = set(superseded.keys())

    entries = []
    files_scanned = 0

    for dir_suffix, facet in FACET_DIRS.items():
        faq_dir = webhelp_path / dir_suffix.replace("/", os.sep)
        if not faq_dir.exists():
            continue

        for md_file in faq_dir.glob("*.md"):
            if md_file.name.startswith("INDEX") or md_file.name.startswith("MODULE"):
                continue

            files_scanned += 1

            # Skip superseded DW articles
            article_id = md_file.stem
            if article_id in superseded_ids:
                continue

            info = extract_article_info(str(md_file), facet)
            if info:
                entries.append(info)

    # Build structured catalog with dedup (higher facet priority wins)
    by_safx_code = {}
    by_error_code = {}

    for entry in entries:
        safx = entry["safx_code"]
        err = entry["error_code"]
        facet = entry["facet"]

        if safx not in by_safx_code:
            by_safx_code[safx] = {"errors": {}}

        existing = by_safx_code[safx]["errors"].get(err)
        if existing:
            # Dedup: higher priority facet wins
            if FACET_PRIORITY.get(facet, 0) <= FACET_PRIORITY.get(existing["facet"], 0):
                continue

        by_safx_code[safx]["errors"][err] = {
            "description": entry["description"],
            "solution": entry["solution"],
            "article_id": entry["article_id"],
            "article_path": entry["article_path"],
            "facet": facet,
        }

        # Reverse index: error_code -> [safx_codes]
        if err not in by_error_code:
            by_error_code[err] = []
        if safx not in by_error_code[err]:
            by_error_code[err].append(safx)

    # Sort error codes within each SAFX
    for safx in by_safx_code:
        by_safx_code[safx]["errors"] = dict(
            sorted(by_safx_code[safx]["errors"].items(), key=lambda x: int(x[0]))
        )

    # Sort SAFX codes and error codes
    by_safx_code = dict(sorted(by_safx_code.items()))
    by_error_code = dict(sorted(by_error_code.items(), key=lambda x: int(x[0])))
    for err in by_error_code:
        by_error_code[err].sort()

    total_entries = sum(len(v["errors"]) for v in by_safx_code.values())

    catalog = {
        "version": "1.0",
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "total_entries": total_entries,
        "total_safx_codes": len(by_safx_code),
        "total_error_codes": len(by_error_code),
        "files_scanned": files_scanned,
        "by_safx_code": by_safx_code,
        "by_error_code": by_error_code,
    }

    return catalog


def save_catalog(catalog, webhelp_dir):
    """Save catalog to JSON file."""
    output_path = os.path.join(webhelp_dir, CATALOG_FILENAME)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
    return output_path


def load_catalog(webhelp_dir):
    """Load existing catalog from JSON file."""
    catalog_path = os.path.join(webhelp_dir, CATALOG_FILENAME)
    if not os.path.exists(catalog_path):
        print(f"ERRO: Catalogo nao encontrado em {catalog_path}")
        print("Execute: python safx_catalog_builder.py --rebuild")
        sys.exit(1)

    with open(catalog_path, "r", encoding="utf-8") as f:
        return json.load(f)


# --- Lookup Functions ---

def lookup_safx_error(catalog, safx_code, error_code):
    """Look up a specific SAFX code + error code combination."""
    safx_code = safx_code.upper()
    if not safx_code.startswith("SAFX"):
        safx_code = f"SAFX{safx_code.zfill(2)}"

    safx_data = catalog.get("by_safx_code", {}).get(safx_code)
    if not safx_data:
        return None

    return safx_data["errors"].get(str(error_code))


def lookup_error_code(catalog, error_code):
    """Look up all SAFX codes that reference a given error code."""
    error_code = str(error_code)
    safx_list = catalog.get("by_error_code", {}).get(error_code, [])

    results = []
    for safx_code in safx_list:
        entry = catalog["by_safx_code"][safx_code]["errors"].get(error_code)
        if entry:
            results.append({
                "safx_code": safx_code,
                **entry,
            })

    return results


# --- CLI ---

def print_stats(catalog):
    """Print catalog statistics."""
    print(f"SAFX Catalog v{catalog['version']} — Gerado: {catalog['generated']}")
    print(f"Arquivos escaneados: {catalog['files_scanned']}")
    print(f"Total de entradas: {catalog['total_entries']}")
    print(f"Codigos SAFX: {catalog['total_safx_codes']}")
    print(f"Codigos de erro: {catalog['total_error_codes']}")
    print()

    # Per-SAFX breakdown
    print("Entradas por SAFX:")
    for safx, data in catalog["by_safx_code"].items():
        count = len(data["errors"])
        facets = {}
        for err_data in data["errors"].values():
            f = err_data.get("facet", "unknown")
            facets[f] = facets.get(f, 0) + 1
        facet_str = ", ".join(f"{k}:{v}" for k, v in sorted(facets.items()))
        print(f"  {safx}: {count} erros ({facet_str})")

    # Top error codes (appearing in multiple SAFXs)
    multi_safx = {k: v for k, v in catalog["by_error_code"].items() if len(v) > 1}
    if multi_safx:
        print(f"\nErros compartilhados ({len(multi_safx)}):")
        for err, safxs in sorted(multi_safx.items(), key=lambda x: len(x[1]), reverse=True)[:10]:
            print(f"  Erro {err}: {', '.join(safxs)}")


def main():
    parser = argparse.ArgumentParser(
        description="SAFX Catalog Builder - Extrai erros SAFX e solucoes do WebHelp"
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Reconstruir catalogo a partir dos artigos WebHelp"
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Exibir estatisticas do catalogo (usar com --rebuild ou sozinho)"
    )
    parser.add_argument(
        "--lookup", nargs=2, metavar=("SAFX_CODE", "ERROR_CODE"),
        help="Buscar SAFX + erro especifico (ex: SAFX07 90034)"
    )
    parser.add_argument(
        "--lookup-error", metavar="ERROR_CODE",
        help="Buscar todos os SAFXs que referenciam um erro (ex: 90034)"
    )
    parser.add_argument(
        "--webhelp-dir", default=DEFAULT_WEBHELP_DIR,
        help=f"Diretorio dos artigos WebHelp (default: {DEFAULT_WEBHELP_DIR})"
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Formato de saida (default: text)"
    )

    args = parser.parse_args()

    if args.rebuild:
        print("Construindo catalogo SAFX...")
        catalog = build_catalog(args.webhelp_dir)
        output_path = save_catalog(catalog, args.webhelp_dir)
        print(f"Catalogo salvo em: {output_path}")
        if args.stats:
            print()
            print_stats(catalog)
        return

    if args.stats:
        catalog = load_catalog(args.webhelp_dir)
        print_stats(catalog)
        return

    if args.lookup:
        catalog = load_catalog(args.webhelp_dir)
        safx_code, error_code = args.lookup
        result = lookup_safx_error(catalog, safx_code, error_code)
        if result:
            if args.format == "json":
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(f"{safx_code.upper()} - Erro {error_code}")
                print(f"  Descricao: {result.get('description', '(sem descricao)')}")
                print(f"  Solucao:   {result.get('solution', '(sem solucao)')}")
                print(f"  Artigo:    {result.get('article_id', '')}")
                print(f"  Path:      {result.get('article_path', '')}")
                print(f"  Facet:     {result.get('facet', '')}")
        else:
            print(f"Nenhuma entrada encontrada para {safx_code} erro {error_code}")
            sys.exit(1)
        return

    if args.lookup_error:
        catalog = load_catalog(args.webhelp_dir)
        results = lookup_error_code(catalog, args.lookup_error)
        if results:
            if args.format == "json":
                print(json.dumps(results, ensure_ascii=False, indent=2))
            else:
                print(f"Erro {args.lookup_error} encontrado em {len(results)} SAFX(s):")
                for r in results:
                    print(f"\n  {r['safx_code']} - Erro {args.lookup_error}")
                    print(f"    Descricao: {r.get('description', '(sem descricao)')}")
                    print(f"    Solucao:   {r.get('solution', '(sem solucao)')}")
                    print(f"    Artigo:    {r.get('article_id', '')}")
                    print(f"    Facet:     {r.get('facet', '')}")
        else:
            print(f"Nenhuma entrada encontrada para erro {args.lookup_error}")
            sys.exit(1)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
