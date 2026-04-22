#!/usr/bin/env python
"""
WebHelp Indexer - Gera MODULE_INDEX.json a partir dos artigos baixados.

Parseia tags [Modulo][SubModulo] dos titulos e cria indice estruturado
por modulo, submodulo e keywords para busca rapida pelos agents.

Trata deduplicacao entre FAQ TaxOne e FAQ-DW (facetas do mesmo produto).
Quando ha artigos duplicados sobre o mesmo tema, prefere FAQ TaxOne (mais novo).

Usage:
  python webhelp_indexer.py [--rebuild] [--output knowledge/webhelp/MODULE_INDEX.json]
"""

import argparse
import json
import os
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path


DEFAULT_WEBHELP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "knowledge", "webhelp"
)

# Regex to extract [Tag1][Tag2]... from title
# Handles: [A][B], [A] [B], [A][B][C], and broken cases like "A][B]"
TAG_RE = re.compile(r'\[([^\]]+)\]')

# Keywords to extract from title (SAFX codes, registros, erros, telas)
KEYWORD_PATTERNS = [
    re.compile(r'\bSAFX\d+\b', re.IGNORECASE),
    re.compile(r'\bRegistro\s+[A-Z]\d{3,4}\b', re.IGNORECASE),
    re.compile(r'\bBloco\s+[A-Z0-9]+\b', re.IGNORECASE),
    re.compile(r'\b(?:ORA|ERR|ERRO)\s*[-:]?\s*\d+\b', re.IGNORECASE),
    re.compile(r'\bTela\s+\w+\b', re.IGNORECASE),
    re.compile(r'\bGIA[\s-]*(?:SP|MG|RS|PR|RJ|SC)?\b', re.IGNORECASE),
    re.compile(r'\bDIME\b', re.IGNORECASE),
    re.compile(r'\bEFD[\s-]*(?:Contribui|REINF|ICMS|IPI|ECD|ECF)\w*\b', re.IGNORECASE),
    re.compile(r'\bSPED[\s-]*(?:Fiscal|Cont|ECD|ECF|Contribui)\w*\b', re.IGNORECASE),
    re.compile(r'\bDCTF\b', re.IGNORECASE),
    re.compile(r'\bDIRF\b', re.IGNORECASE),
    re.compile(r'\bECF\b', re.IGNORECASE),
    re.compile(r'\bICMS[\s-]*(?:ST|DIFAL)?\b', re.IGNORECASE),
    re.compile(r'\bPIS[\s/]*COFINS\b', re.IGNORECASE),
    re.compile(r'\bISS\b', re.IGNORECASE),
    re.compile(r'\bNFS-?e\b', re.IGNORECASE),
    re.compile(r'\bJob\s+Servidor\b', re.IGNORECASE),
    re.compile(r'\bImporta[cç][aã]o\b', re.IGNORECASE),
    re.compile(r'\bOBI\b', re.IGNORECASE),
    re.compile(r'\bTax\s+Automation\b', re.IGNORECASE),
]

# Module name normalization map
MODULE_NORMALIZE = {
    'basicos': 'basicos',
    'básicos': 'basicos',
    'estadual': 'estadual',
    'federal': 'federal',
    'municipal': 'municipal',
    'sped': 'sped',
    'interfaces': 'interfaces',
    'interface': 'interfaces',
    'especificos': 'especificos',
    'específicos': 'especificos',
    'especifico': 'especificos',
    'específico': 'especificos',
    'migracao': 'migracao',
    'migração': 'migracao',
    'atualizacao': 'atualizacao',
    'atualização': 'atualizacao',
    'reforma tributaria': 'reforma_tributaria',
    'reforma tributária': 'reforma_tributaria',
}

# Facet detection: path patterns -> facet name
# onesource-tax-one/faq/ = "taxone" (preferred, newer)
# mastersaf-dw/faq-dw/   = "dw" (legacy, superseded when duplicate exists)
FACET_PATTERNS = [
    ('onesource-tax-one/faq', 'taxone'),
    ('onesource-tax-one/', 'taxone'),
    ('mastersaf-dw/faq-dw', 'dw'),
    ('mastersaf-dw/', 'dw'),
    ('onesource-tax-one-sap/', 'taxone_sap'),
]


def normalize_key(text):
    """Normalize text to a stable key (lowercase, no accents, underscores)."""
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r'[\s-]+', '_', text)
    text = re.sub(r'[^\w]', '', text)
    return text


def normalize_topic(title):
    """Normalize title to a topic key for deduplication.

    Strips [tags], product prefixes (DW/TAX ONE/ONESOURCE), and module names
    to extract the core topic for comparison across facets.
    """
    # Remove markdown heading
    title = title.lstrip('# ').strip()
    # Remove [tags]
    title = TAG_RE.sub('', title).strip()
    # Remove product prefix
    title = re.sub(
        r'^(ONESOURCE\s+Tax\s+One|TAX\s+ONE|Mastersaf\s+DW|DW)\s*[-\u2013\u2014:]\s*',
        '', title, flags=re.IGNORECASE
    ).strip()
    # Remove module prefix after product (e.g., "SPED Fiscal - ", "Administration - ")
    title = re.sub(r'^[A-Za-z\u00e1\u00e9\u00ed\u00f3\u00fa\u00e3\u00f5\u00e7\s]+ [-\u2013\u2014] ', '', title).strip()
    # Normalize whitespace and case
    title = re.sub(r'\s+', ' ', title).lower().strip()
    # Remove accents
    title = unicodedata.normalize('NFKD', title)
    title = ''.join(c for c in title if not unicodedata.combining(c))
    return title


def detect_facet(rel_path):
    """Detect which facet (taxone/dw/taxone_sap) an article belongs to."""
    path_str = rel_path.replace('\\', '/')
    for pattern, facet in FACET_PATTERNS:
        if pattern in path_str:
            return facet
    return 'other'


def parse_article(filepath):
    """Parse a webhelp article markdown file and extract metadata."""
    try:
        content = filepath.read_text(encoding='utf-8')
    except Exception:
        return None

    lines = content.split('\n')
    if not lines:
        return None

    # Extract title from first line (# Title)
    title_line = lines[0].lstrip('# ').strip()
    if not title_line:
        return None

    # Extract article ID and updated date from metadata
    article_id = filepath.stem  # filename without .md
    labels = []
    updated_at = ''

    for line in lines[1:15]:  # scan first 15 lines for metadata
        if line.startswith('- **ID:**'):
            article_id = line.split('**ID:**')[1].strip()
        elif line.startswith('- **Labels:**'):
            labels_str = line.split('**Labels:**')[1].strip()
            labels = [l.strip() for l in labels_str.split(',') if l.strip()]
        elif line.startswith('- **Atualizado:**'):
            updated_at = line.split('**Atualizado:**')[1].strip()

    # Extract tags from title
    # Handle broken case like "Municipal][ISS]" (missing opening bracket)
    title_for_tags = title_line
    if not title_for_tags.startswith('[') and '][' in title_for_tags:
        title_for_tags = '[' + title_for_tags

    tags = TAG_RE.findall(title_for_tags)

    # Determine module and submodule
    module = None
    submodule = None
    display_module = None
    display_submodule = None

    if tags:
        display_module = tags[0].strip()
        module_key = normalize_key(display_module)
        module = MODULE_NORMALIZE.get(module_key, module_key)
        display_module = tags[0].strip()

        if len(tags) > 1:
            display_submodule = tags[1].strip()
            submodule = normalize_key(display_submodule)

    # If no tags found, try to infer from directory structure
    if not module:
        rel_parts = filepath.parts
        # Look for category/section in path
        for part in rel_parts:
            part_lower = part.lower()
            if part_lower in MODULE_NORMALIZE:
                module = MODULE_NORMALIZE[part_lower]
                display_module = part.title()
                break

    if not module:
        module = 'outros'
        display_module = 'Outros'

    # Extract keywords from title
    keywords = set()
    for pattern in KEYWORD_PATTERNS:
        matches = pattern.findall(title_line)
        for m in matches:
            keywords.add(normalize_key(m))

    # Also add significant words from title (3+ chars, not stopwords)
    stopwords = {
        'tax', 'one', 'onesource', 'mastersaf', 'como', 'para', 'que',
        'com', 'por', 'uma', 'dos', 'das', 'nos', 'nas', 'the', 'and',
        'fiscal', 'geral', 'novo', 'nova', 'sobre', 'quando', 'onde',
    }
    # Strip tags from title for word extraction
    clean_title = TAG_RE.sub('', title_line).strip()
    clean_title = re.sub(r'\s*ONESOURCE\s+Tax\s+One\s*[-\u2013\u2014]?\s*', '', clean_title, flags=re.IGNORECASE)
    clean_title = re.sub(r'\s*Mastersaf\s+DW\s*[-\u2013\u2014]?\s*', '', clean_title, flags=re.IGNORECASE)
    for word in re.findall(r'\b\w{3,}\b', clean_title):
        wl = word.lower()
        if wl not in stopwords:
            keywords.add(normalize_key(wl))

    # Compute relative path from webhelp dir
    rel_path = None
    try:
        webhelp_idx = list(filepath.parts).index('webhelp')
        rel_path = '/'.join(filepath.parts[webhelp_idx + 1:])
    except ValueError:
        rel_path = filepath.name

    # Detect facet
    facet = detect_facet(rel_path)

    # Compute topic key for dedup
    topic_key = normalize_topic(title_line)

    return {
        'id': article_id,
        'title': title_line,
        'path': rel_path,
        'module': module,
        'display_module': display_module,
        'submodule': submodule,
        'display_submodule': display_submodule,
        'keywords': list(keywords),
        'labels': labels,
        'facet': facet,
        'updated_at': updated_at,
        'topic_key': topic_key,
    }


def deduplicate_articles(articles):
    """Deduplicate articles across facets.

    When DW and TaxOne have articles about the same topic (same topic_key),
    mark the DW article as superseded. TaxOne FAQ (section 360008595232) is
    always preferred as it's the newer, maintained version.

    Returns: (active_articles, superseded_map)
        - active_articles: list of articles to include in index
        - superseded_map: dict {dw_id: taxone_id} for reference
    """
    # Group by topic_key
    by_topic = defaultdict(list)
    for article in articles:
        if article['topic_key'] and len(article['topic_key']) > 10:
            by_topic[article['topic_key']].append(article)

    superseded = {}  # dw_id -> taxone_id
    superseded_ids = set()

    for topic_key, group in by_topic.items():
        if len(group) < 2:
            continue

        # Check if we have both facets
        taxone_arts = [a for a in group if a['facet'] == 'taxone']
        dw_arts = [a for a in group if a['facet'] == 'dw']

        if taxone_arts and dw_arts:
            # Prefer TaxOne — mark DW as superseded
            preferred = taxone_arts[0]
            for dw_art in dw_arts:
                superseded[dw_art['id']] = preferred['id']
                superseded_ids.add(dw_art['id'])

    active = [a for a in articles if a['id'] not in superseded_ids]
    return active, superseded


def build_index(webhelp_dir):
    """Build the full module index from all articles."""
    webhelp_path = Path(webhelp_dir)

    # Find all .md files (exclude INDEX.md and hidden files)
    md_files = [
        f for f in webhelp_path.rglob('*.md')
        if f.name != 'INDEX.md' and not f.name.startswith('.')
    ]

    print(f"Encontrados {len(md_files)} arquivos .md em {webhelp_dir}")

    # Parse all articles
    all_articles = []
    errors = 0
    for filepath in md_files:
        article = parse_article(filepath)
        if not article:
            errors += 1
            continue
        all_articles.append(article)
        if len(all_articles) % 500 == 0:
            print(f"  Processados: {len(all_articles)}...")

    print(f"  Total parseados: {len(all_articles)}, erros: {errors}")

    # Deduplicate
    active_articles, superseded_map = deduplicate_articles(all_articles)
    print(f"  Deduplicacao: {len(superseded_map)} artigos DW superseded por TaxOne")
    print(f"  Artigos ativos: {len(active_articles)}")

    # Count facets
    facet_counts = defaultdict(int)
    for a in all_articles:
        facet_counts[a['facet']] += 1
    active_facet_counts = defaultdict(int)
    for a in active_articles:
        active_facet_counts[a['facet']] += 1

    # Build module structure from active articles only
    modules = defaultdict(lambda: {
        'display_name': '',
        'count': 0,
        'submodules': defaultdict(lambda: {
            'display_name': '',
            'count': 0,
            'articles': []
        })
    })

    keyword_index = defaultdict(set)

    for article in active_articles:
        mod = article['module']
        submod = article['submodule'] or '_geral'

        # Update module
        mod_data = modules[mod]
        if article['display_module']:
            mod_data['display_name'] = article['display_module']
        mod_data['count'] += 1

        # Update submodule
        submod_data = mod_data['submodules'][submod]
        if article['display_submodule']:
            submod_data['display_name'] = article['display_submodule']
        elif submod == '_geral':
            submod_data['display_name'] = 'Geral'
        submod_data['count'] += 1
        submod_data['articles'].append({
            'id': article['id'],
            'title': article['title'],
            'path': article['path'],
            'facet': article['facet'],
        })

        # Update keyword index
        for kw in article['keywords']:
            keyword_index[kw].add(article['id'])

    # Convert to serializable format
    modules_json = {}
    for mod_key in sorted(modules.keys()):
        mod_data = modules[mod_key]
        submodules_json = {}
        for sub_key in sorted(mod_data['submodules'].keys()):
            sub_data = mod_data['submodules'][sub_key]
            submodules_json[sub_key] = {
                'display_name': sub_data['display_name'],
                'count': sub_data['count'],
                'articles': sorted(sub_data['articles'], key=lambda a: a['title']),
            }
        modules_json[mod_key] = {
            'display_name': mod_data['display_name'],
            'count': mod_data['count'],
            'submodules': submodules_json,
        }

    keyword_json = {k: sorted(v) for k, v in sorted(keyword_index.items())}

    index = {
        'version': '2.0',
        'generated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'total_articles': len(active_articles),
        'total_parsed': len(all_articles),
        'total_errors': errors,
        'total_superseded': len(superseded_map),
        'facets': {
            'total': dict(facet_counts),
            'active': dict(active_facet_counts),
            'note': 'taxone preferred over dw when duplicate topic exists',
        },
        'superseded': superseded_map,
        'modules': modules_json,
        'keyword_index': keyword_json,
    }

    return index


def main():
    parser = argparse.ArgumentParser(
        description='WebHelp Indexer - Gera MODULE_INDEX.json'
    )
    parser.add_argument(
        '--rebuild', action='store_true',
        help='Forcar reconstrucao completa do indice'
    )
    parser.add_argument(
        '--webhelp-dir', type=str, default=DEFAULT_WEBHELP_DIR,
        help=f'Diretorio dos artigos WebHelp (default: {DEFAULT_WEBHELP_DIR})'
    )
    parser.add_argument(
        '--output', type=str, default=None,
        help='Caminho do arquivo de saida (default: {webhelp-dir}/MODULE_INDEX.json)'
    )
    parser.add_argument(
        '--stats', action='store_true',
        help='Mostrar estatisticas detalhadas apos indexacao'
    )

    args = parser.parse_args()

    output_path = args.output or os.path.join(args.webhelp_dir, 'MODULE_INDEX.json')

    # Check if rebuild needed
    if not args.rebuild and os.path.exists(output_path):
        print(f"MODULE_INDEX.json ja existe em {output_path}")
        print("Use --rebuild para forcar reconstrucao.")
        return

    print(f"Indexando artigos WebHelp em {args.webhelp_dir}...")
    index = build_index(args.webhelp_dir)

    # Write output
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"\nMODULE_INDEX.json gerado: {output_path}")
    print(f"  Total parseados: {index['total_parsed']}")
    print(f"  Artigos ativos: {index['total_articles']}")
    print(f"  Superseded (DW->TaxOne): {index['total_superseded']}")
    print(f"  Erros de parse: {index['total_errors']}")
    print(f"  Modulos: {len(index['modules'])}")
    print(f"  Keywords indexadas: {len(index['keyword_index'])}")
    print(f"  Facetas total: {index['facets']['total']}")
    print(f"  Facetas ativas: {index['facets']['active']}")

    if args.stats:
        print(f"\n{'='*60}")
        print("Distribuicao por Modulo (artigos ativos):")
        print(f"{'='*60}")
        for mod_key, mod_data in sorted(index['modules'].items(), key=lambda x: -x[1]['count']):
            print(f"  {mod_data['display_name']} ({mod_key}): {mod_data['count']} artigos")
            for sub_key, sub_data in sorted(mod_data['submodules'].items(), key=lambda x: -x[1]['count']):
                print(f"    {sub_data['display_name']} ({sub_key}): {sub_data['count']}")


if __name__ == '__main__':
    main()
