#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WebHelp E2E Map Builder - Gera webhelp-e2e-map.json cruzando 3 fontes:
  1. MODULE_INDEX.json (39 modulos, 3440 artigos WebHelp)
  2. playwright-test-map.json (200+ mappings com menu_path)
  3. component-test-map.json (52 mappings com keywords/category)

Roda uma vez para bootstrap. Depois manter manualmente.

Uso:
  python webhelp_e2e_map_builder.py
  python webhelp_e2e_map_builder.py --output knowledge/suite-automation/webhelp-e2e-map.json
  python webhelp_e2e_map_builder.py --stats-only
"""

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Input files
MODULE_INDEX_PATH = PROJECT_ROOT / "knowledge" / "webhelp" / "MODULE_INDEX.json"
PLAYWRIGHT_MAP_PATH = PROJECT_ROOT / "knowledge" / "suite-automation" / "playwright-test-map.json"
COMPONENT_MAP_PATH = PROJECT_ROOT / "knowledge" / "suite-automation" / "component-test-map.json"

# Output
DEFAULT_OUTPUT = PROJECT_ROOT / "knowledge" / "suite-automation" / "webhelp-e2e-map.json"

# Module name normalization (from webhelp_indexer.py)
MODULE_NORMALIZE = {
    'basicos': 'basicos', 'básicos': 'basicos',
    'estadual': 'estadual', 'federal': 'federal',
    'municipal': 'municipal', 'sped': 'sped',
    'interfaces': 'interfaces', 'interface': 'interfaces',
    'especificos': 'especificos', 'específicos': 'especificos',
    'especifico': 'especificos', 'específico': 'especificos',
    'migracao': 'migracao', 'migração': 'migracao',
    'atualizacao': 'atualizacao', 'atualização': 'atualizacao',
    'reforma tributaria': 'reforma_tributaria',
    'reforma tributária': 'reforma_tributaria',
}

# Category mapping: WebHelp module -> SuiteAutomation category
MODULE_TO_CATEGORY = {
    'basicos': 'BASICOS',
    'estadual': 'ESTADUAL',
    'federal': 'FEDERAL',
    'municipal': 'MUNICIPAL',
    'sped': 'SPED',
    'especificos': 'ESPECIFICOS',
    'interfaces': 'INTERFACES',
}


def normalize_key(text: str) -> str:
    """Normaliza texto: sem acentos, uppercase, trim."""
    nfkd = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return text.upper().strip()


def normalize_for_match(text: str) -> str:
    """Normaliza para comparacao: uppercase, sem acentos, sem pontuacao."""
    text = normalize_key(text)
    text = re.sub(r'[^A-Z0-9\s>]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def load_module_index() -> dict:
    """Carrega MODULE_INDEX.json e extrai estrutura de modulos/submodulos."""
    if not MODULE_INDEX_PATH.exists():
        print(f"AVISO: MODULE_INDEX.json nao encontrado em {MODULE_INDEX_PATH}", file=sys.stderr)
        return {}
    data = json.loads(MODULE_INDEX_PATH.read_text(encoding="utf-8"))
    return data.get("modules", {})


def load_playwright_map() -> list:
    """Carrega playwright-test-map.json."""
    if not PLAYWRIGHT_MAP_PATH.exists():
        print(f"AVISO: playwright-test-map.json nao encontrado", file=sys.stderr)
        return []
    data = json.loads(PLAYWRIGHT_MAP_PATH.read_text(encoding="utf-8"))
    return data.get("mappings", [])


def load_component_map() -> list:
    """Carrega component-test-map.json."""
    if not COMPONENT_MAP_PATH.exists():
        print(f"AVISO: component-test-map.json nao encontrado", file=sys.stderr)
        return []
    data = json.loads(COMPONENT_MAP_PATH.read_text(encoding="utf-8"))
    return data.get("mappings", [])


def build_playwright_index(pw_mappings: list) -> dict:
    """Indexa playwright mappings por menu_path normalizado e por keywords."""
    by_path = {}
    by_keyword = defaultdict(list)

    for m in pw_mappings:
        menu_path = m.get("menu_path", "")
        if menu_path:
            key = normalize_for_match(menu_path)
            by_path[key] = m

        for kw in m.get("screen_keywords", []) + m.get("keywords", []):
            by_keyword[normalize_key(kw)].append(m)

    return {"by_path": by_path, "by_keyword": by_keyword}


def build_component_index(comp_mappings: list) -> dict:
    """Indexa component mappings por category e keywords."""
    by_category = defaultdict(list)
    by_keyword = defaultdict(list)

    for m in comp_mappings:
        cat = m.get("category", "").upper()
        if cat:
            by_category[cat].append(m)

        for kw in m.get("keywords", []):
            by_keyword[normalize_key(kw)].append(m)

    return {"by_category": by_category, "by_keyword": by_keyword}


def match_module_to_tests(module_key, module_data, submodule_key, submodule_data,
                          pw_index, comp_index):
    """Tenta mapear um modulo/submodulo WebHelp para testes existentes.

    Returns:
        dict com playwright_mapping_ids, suite_automation_ids, status, keywords
    """
    display_module = module_data.get("display_name", module_key)
    display_sub = submodule_data.get("display_name", submodule_key)

    # Construir path normalizado para busca
    if submodule_key == "_geral":
        search_path = normalize_for_match(display_module)
    else:
        search_path = normalize_for_match(f"{display_module} > {display_sub}")

    # Extrair keywords dos titulos dos artigos
    article_keywords = set()
    for article in submodule_data.get("articles", []):
        title = article.get("title", "")
        # Extrair palavras-chave do titulo (entre [])
        tags = re.findall(r'\[([^\]]+)\]', title)
        for tag in tags:
            normalized = normalize_key(tag)
            if len(normalized) > 2:
                article_keywords.add(normalized)

    # --- Match Playwright ---
    pw_matches = set()

    # Exact path match
    if search_path in pw_index["by_path"]:
        pw_matches.add(pw_index["by_path"][search_path]["id"])

    # Partial path match: check if search_path is contained in any pw path
    for pw_path, pw_m in pw_index["by_path"].items():
        if search_path in pw_path or pw_path in search_path:
            pw_matches.add(pw_m["id"])

    # Keyword match
    for kw in article_keywords:
        for pw_m in pw_index["by_keyword"].get(kw, []):
            pw_matches.add(pw_m["id"])

    # --- Match Component (SuiteAutomation) ---
    comp_matches = set()
    category = MODULE_TO_CATEGORY.get(module_key, "").upper()

    # Category match
    for comp_m in comp_index["by_category"].get(category, []):
        # Check if description or keywords overlap
        comp_desc = normalize_key(comp_m.get("description", ""))
        sub_norm = normalize_key(display_sub)
        if sub_norm in comp_desc or comp_desc in sub_norm:
            comp_matches.add(comp_m["id"])

    # Keyword match
    for kw in article_keywords:
        for comp_m in comp_index["by_keyword"].get(kw, []):
            comp_matches.add(comp_m["id"])

    # --- Determine status ---
    if pw_matches and comp_matches:
        status = "covered"
    elif pw_matches or comp_matches:
        status = "partial"
    else:
        status = "gap"

    # Build webhelp_path
    if submodule_key == "_geral":
        webhelp_path = module_key
    else:
        webhelp_path = f"{module_key}/{submodule_key}"

    # Build menu_path_normalized
    if submodule_key == "_geral":
        menu_path = normalize_key(display_module)
    else:
        menu_path = f"{normalize_key(display_module)} > {normalize_key(display_sub)}"

    # Collect relevant keywords
    keywords = []
    for kw in article_keywords:
        if len(kw) > 2 and kw not in {"TAX", "ONE", "ONESOURCE", "SAP", "FOR", "COMO", "QUE"}:
            keywords.append(kw)

    return {
        "webhelp_path": webhelp_path,
        "webhelp_display": f"{display_module} > {display_sub}" if submodule_key != "_geral" else display_module,
        "menu_path_normalized": menu_path,
        "playwright_mapping_ids": sorted(pw_matches),
        "suite_automation_ids": sorted(comp_matches),
        "status": status,
        "keywords": sorted(keywords)[:10],
        "article_count": submodule_data.get("count", 0),
    }


def build_map():
    """Constroi o mapeamento completo WebHelp -> E2E."""
    modules = load_module_index()
    pw_mappings = load_playwright_map()
    comp_mappings = load_component_map()

    pw_index = build_playwright_index(pw_mappings)
    comp_index = build_component_index(comp_mappings)

    mappings = []
    counter = 0

    for module_key, module_data in sorted(modules.items()):
        submodules = module_data.get("submodules", {})

        for sub_key, sub_data in sorted(submodules.items()):
            counter += 1
            result = match_module_to_tests(
                module_key, module_data,
                sub_key, sub_data,
                pw_index, comp_index,
            )

            # Generate ID
            id_suffix = f"{module_key}_{sub_key}".upper().replace("/", "_")
            result["id"] = f"WH_{id_suffix}"

            mappings.append(result)

    return {
        "version": "1.0.0",
        "description": (
            "Mapeamento de paths funcionais do WebHelp para cenarios E2E existentes "
            "(Playwright + SuiteAutomation). Gerado por webhelp_e2e_map_builder.py. "
            "Entries com status 'gap' indicam cenarios sem cobertura de teste."
        ),
        "scoring": {
            "exact_path_match": 10,
            "partial_path_match": 5,
            "keyword_overlap": 3,
            "min_score_to_include": 5,
        },
        "stats": {
            "total_mappings": len(mappings),
            "covered": sum(1 for m in mappings if m["status"] == "covered"),
            "partial": sum(1 for m in mappings if m["status"] == "partial"),
            "gap": sum(1 for m in mappings if m["status"] == "gap"),
        },
        "mappings": mappings,
    }


def print_stats(result: dict):
    """Imprime estatisticas do mapeamento."""
    stats = result["stats"]
    print(f"\n{'=' * 60}")
    print(f"  WebHelp E2E Map - Estatisticas")
    print(f"{'=' * 60}")
    print(f"  Total mappings:  {stats['total_mappings']}")
    print(f"  Cobertos:        {stats['covered']} ({stats['covered']/max(stats['total_mappings'],1)*100:.0f}%)")
    print(f"  Parciais:        {stats['partial']} ({stats['partial']/max(stats['total_mappings'],1)*100:.0f}%)")
    print(f"  Gaps:            {stats['gap']} ({stats['gap']/max(stats['total_mappings'],1)*100:.0f}%)")
    print(f"{'=' * 60}")

    # Top gaps
    gaps = [m for m in result["mappings"] if m["status"] == "gap"]
    if gaps:
        print(f"\n  Top gaps (sem cobertura de teste):")
        for g in sorted(gaps, key=lambda x: x["article_count"], reverse=True)[:15]:
            print(f"    - {g['webhelp_display']} ({g['article_count']} artigos)")

    # Top covered
    covered = [m for m in result["mappings"] if m["status"] == "covered"]
    if covered:
        print(f"\n  Top cobertos:")
        for c in sorted(covered, key=lambda x: x["article_count"], reverse=True)[:10]:
            pw = len(c["playwright_mapping_ids"])
            sa = len(c["suite_automation_ids"])
            print(f"    - {c['webhelp_display']} (PW:{pw} SA:{sa})")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Gera webhelp-e2e-map.json cruzando MODULE_INDEX, playwright-test-map e component-test-map"
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Caminho de saida (default: {DEFAULT_OUTPUT.relative_to(PROJECT_ROOT)})",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Apenas mostrar estatisticas, sem gerar arquivo",
    )

    args = parser.parse_args()

    print("Carregando fontes de dados...", file=sys.stderr)
    result = build_map()

    print_stats(result)

    if not args.stats_only:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Mapa gerado em: {output_path}", file=sys.stderr)

    # Exit code: 0 if some coverage, 1 if all gaps
    coverage = result["stats"]["covered"] + result["stats"]["partial"]
    sys.exit(0 if coverage > 0 else 1)


if __name__ == "__main__":
    main()
