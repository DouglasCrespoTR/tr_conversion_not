#!/usr/bin/env python
"""
WebHelp Search - Busca local por modulo/keyword nos artigos WebHelp.

Carrega MODULE_INDEX.json e permite busca por modulo, submodulo e keyword
com scoring para rankeamento de resultados.

Usage:
  python webhelp_search.py --module "SPED" --max 5
  python webhelp_search.py --module "SPED" --submodule "EFD - Contribuições" --max 5
  python webhelp_search.py --keyword "SAFX07 erro 90034" --max 5
  python webhelp_search.py --keyword "Livro Razao Auxiliar" --module "SPED" --max 3
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path


DEFAULT_INDEX = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "knowledge", "webhelp", "MODULE_INDEX.json"
)

# Scoring weights
SCORE_KEYWORD_TITLE = 5
SCORE_KEYWORD_SUBMODULE = 3
SCORE_MODULE_MATCH = 2
SCORE_LABEL_MATCH = 1
SCORE_KEYWORD_INDEX = 4
SCORE_FACET_TAXONE_BOOST = 1  # TaxOne articles get a small boost over DW


def normalize_key(text):
    """Normalize text to a stable key (lowercase, no accents, underscores)."""
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r'[\s-]+', '_', text)
    text = re.sub(r'[^\w]', '', text)
    return text


def load_index(index_path):
    """Load MODULE_INDEX.json."""
    if not os.path.exists(index_path):
        print(f"ERRO: MODULE_INDEX.json nao encontrado em {index_path}", file=sys.stderr)
        print("Execute: python scripts/webhelp_indexer.py --rebuild", file=sys.stderr)
        sys.exit(1)

    with open(index_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def search(index, module=None, submodule=None, keyword=None, max_results=10):
    """
    Search articles with scoring.

    Scoring:
    - Keyword match in title: 5 points
    - Keyword match in keyword_index: 4 points
    - Keyword match in submodule name: 3 points
    - Module match: 2 points
    - Label match: 1 point
    """
    scores = defaultdict(lambda: {'score': 0, 'title': '', 'path': '', 'module': '', 'submodule': '', 'facet': ''})

    # Normalize search terms
    mod_key = normalize_key(module) if module else None
    submod_key = normalize_key(submodule) if submodule else None
    kw_terms = []
    if keyword:
        # Split keyword into individual terms for matching
        kw_terms = [normalize_key(t) for t in keyword.split() if len(t) >= 2]

    # If keyword provided, first check keyword_index for fast lookup
    kw_article_ids = set()
    if kw_terms and 'keyword_index' in index:
        for term in kw_terms:
            for idx_key, article_ids in index['keyword_index'].items():
                if term in idx_key or idx_key in term:
                    for aid in article_ids:
                        kw_article_ids.add(aid)
                        scores[aid]['score'] += SCORE_KEYWORD_INDEX

    # Scan modules
    for m_key, m_data in index.get('modules', {}).items():
        module_matches = False
        if mod_key:
            if mod_key == m_key or mod_key in m_key or m_key in mod_key:
                module_matches = True

        for s_key, s_data in m_data.get('submodules', {}).items():
            submod_matches = False
            if submod_key:
                if submod_key == s_key or submod_key in s_key or s_key in submod_key:
                    submod_matches = True

            # Check keyword match in submodule name
            submod_kw_match = False
            if kw_terms:
                s_display = normalize_key(s_data.get('display_name', ''))
                for term in kw_terms:
                    if term in s_display or s_display in term:
                        submod_kw_match = True
                        break

            for article in s_data.get('articles', []):
                aid = article['id']
                title = article.get('title', '')
                path = article.get('path', '')
                facet = article.get('facet', '')

                # Initialize article data
                if scores[aid]['title'] == '':
                    scores[aid]['title'] = title
                    scores[aid]['path'] = path
                    scores[aid]['module'] = m_data.get('display_name', m_key)
                    scores[aid]['submodule'] = s_data.get('display_name', s_key)
                    scores[aid]['facet'] = facet

                    # TaxOne facet boost (preferred over DW)
                    if facet == 'taxone':
                        scores[aid]['score'] += SCORE_FACET_TAXONE_BOOST

                # Module match scoring
                if module_matches:
                    scores[aid]['score'] += SCORE_MODULE_MATCH

                # Submodule match scoring (also requires module match or no module filter)
                if submod_matches and (module_matches or not mod_key):
                    scores[aid]['score'] += SCORE_KEYWORD_SUBMODULE

                # Keyword match in title
                if kw_terms:
                    title_norm = normalize_key(title)
                    for term in kw_terms:
                        if term in title_norm:
                            scores[aid]['score'] += SCORE_KEYWORD_TITLE

                # Keyword match in submodule name
                if submod_kw_match:
                    scores[aid]['score'] += SCORE_KEYWORD_SUBMODULE

    # Filter: only return articles with score > 0
    results = [
        {
            'id': aid,
            'title': data['title'],
            'path': data['path'],
            'module': data['module'],
            'submodule': data['submodule'],
            'facet': data['facet'],
            'score': data['score'],
        }
        for aid, data in scores.items()
        if data['score'] > 0
    ]

    # Sort by score descending, then title
    results.sort(key=lambda x: (-x['score'], x['title']))

    return results[:max_results]


def format_results(results, output_format='markdown'):
    """Format search results."""
    if not results:
        return "Nenhum artigo encontrado."

    if output_format == 'json':
        return json.dumps(results, ensure_ascii=False, indent=2)

    lines = [f"## WebHelp - {len(results)} artigos encontrados\n"]
    for i, r in enumerate(results, 1):
        facet_tag = f" [{r['facet'].upper()}]" if r.get('facet') else ''
        lines.append(
            f"{i}. **[{r['module']}/{r['submodule']}]**{facet_tag} {r['title']}\n"
            f"   - Path: `{r['path']}` | Score: {r['score']}"
        )
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='WebHelp Search - Busca local por modulo/keyword'
    )
    parser.add_argument(
        '--module', type=str,
        help='Filtrar por modulo (ex: SPED, Estadual, Basicos)'
    )
    parser.add_argument(
        '--submodule', type=str,
        help='Filtrar por submodulo (ex: "EFD - Contribuições", "Job Servidor")'
    )
    parser.add_argument(
        '--keyword', type=str,
        help='Buscar por keyword (ex: "SAFX07 erro 90034", "Livro Razao")'
    )
    parser.add_argument(
        '--max', type=int, default=10,
        help='Maximo de resultados (default: 10)'
    )
    parser.add_argument(
        '--index', type=str, default=DEFAULT_INDEX,
        help=f'Caminho do MODULE_INDEX.json (default: {DEFAULT_INDEX})'
    )
    parser.add_argument(
        '--format', choices=['markdown', 'json'], default='markdown',
        help='Formato de saida (default: markdown)'
    )

    args = parser.parse_args()

    if not any([args.module, args.submodule, args.keyword]):
        parser.print_help()
        sys.exit(1)

    index = load_index(args.index)
    results = search(
        index,
        module=args.module,
        submodule=args.submodule,
        keyword=args.keyword,
        max_results=args.max,
    )

    print(format_results(results, args.format))


if __name__ == '__main__':
    main()
