#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Functional Path Extractor - Extrai caminhos de navegacao funcional do reproSteps/descricao de WIs.

Parseia HTML de campos reproSteps e description do Azure DevOps para identificar
caminhos de menu (ex: "Menu > Estadual > ICMS > Apuracao") que podem ser mapeados
para cenarios E2E especificos.

Algoritmo em 4 passes:
  1. HTML cleanup (strip tags, decode entities)
  2. Menu path direto (regex para padroes "Menu > X > Y")
  3. URL do WebHelp (regex para URLs com path funcional)
  4. Fuzzy keywords (fallback: match contra screen_keywords)

Uso:
  python functional_path_extractor.py --wi-id 1234567
  python functional_path_extractor.py --from-file tests/1234567/wi_data.json
  python functional_path_extractor.py --wi-id 1234567 --format text
"""

import argparse
import html
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

# Projeto
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from env_loader import get_repo_path  # noqa: E402

# Knowledge bases
PLAYWRIGHT_MAP_PATH = PROJECT_ROOT / "knowledge" / "suite-automation" / "playwright-test-map.json"
COMPONENT_MAP_PATH = PROJECT_ROOT / "knowledge" / "suite-automation" / "component-test-map.json"

# Azure DevOps config
ADO_ORG = os.environ.get("ADO_ORG", "tr-ggo")
ADO_PROJECT = os.environ.get("ADO_PROJECT", "Mastersaf Fiscal Solutions")
ADO_API_VERSION = "7.0"


# ============================================================
# Pass 1: HTML cleanup
# ============================================================

_TAG_RE = re.compile(r'<[^>]+>')
_MULTI_SPACE = re.compile(r'[ \t]+')
_MULTI_NEWLINE = re.compile(r'\n{3,}')


def strip_html(text: str) -> str:
    """Remove tags HTML, decodifica entities, normaliza whitespace."""
    if not text:
        return ""
    # Decode HTML entities
    text = html.unescape(text)
    # Replace <br>, <p>, <div> with newlines
    text = re.sub(r'<(?:br|/p|/div|/li|/tr)\s*/?>', '\n', text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = _TAG_RE.sub(' ', text)
    # Normalize spaces (not newlines)
    text = _MULTI_SPACE.sub(' ', text)
    # Normalize multiple newlines
    text = _MULTI_NEWLINE.sub('\n\n', text)
    return text.strip()


def normalize_path(text: str) -> str:
    """Normaliza path funcional: uppercase, sem acentos, separadores uniformes."""
    # Strip accents
    nfkd = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in nfkd if not unicodedata.combining(c))
    # Uppercase
    text = text.upper().strip()
    # Normalize separators: >>, ->, -, /, \ to >
    text = re.sub(r'\s*(?:>>|->|>|/|\\|-)\s*', ' > ', text)
    # Collapse multiple >
    text = re.sub(r'(\s*>\s*){2,}', ' > ', text)
    # Trim whitespace around >
    text = re.sub(r'\s*>\s*', ' > ', text)
    # Remove leading/trailing >
    text = text.strip(' >')
    return text


# ============================================================
# Pass 2: Menu path extraction (direct patterns)
# ============================================================

# Pattern A: Explicit arrow/separator notation
# "Menu > Estadual > ICMS > Apuracao" or "Acessar: SPED > Contribuicoes"
_MENU_PATTERNS = [
    # "Menu > X > Y > Z" or "Menu: X > Y" (single line only)
    re.compile(
        r'(?:Menu|Acessar|Navegar\s+para|Ir\s+em|Caminho|Tela|Acesso)[^\S\n]*[:>\-][^\S\n]*'
        r'([\w\u00C0-\u00FF][\w \t\u00C0-\u00FF.]*(?:[^\S\n]*>[^\S\n]*[\w\u00C0-\u00FF][\w \t\u00C0-\u00FF.]*){1,8})',
        re.IGNORECASE | re.UNICODE
    ),
    # Standalone arrow chain on single line: "X > Y > Z" (at least 3 segments)
    re.compile(
        r'(?:^|\n)[^\S\n]*([\w\u00C0-\u00FF][\w \t\u00C0-\u00FF.]*'
        r'(?:[^\S\n]*>[^\S\n]*[\w\u00C0-\u00FF][\w \t\u00C0-\u00FF.]*){2,7})',
        re.IGNORECASE | re.UNICODE
    ),
]


def extract_menu_paths(text: str) -> list:
    """Pass 2: Extrai caminhos de menu do texto limpo."""
    results = []
    for pattern in _MENU_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(1) if match.lastindex else match.group(0)
            normalized = normalize_path(raw)
            segments = [s.strip() for s in normalized.split('>') if s.strip()]
            if len(segments) >= 2:
                results.append({
                    "path": " > ".join(segments),
                    "confidence": 0.90 if pattern == _MENU_PATTERNS[0] else 0.70,
                    "source": "menu_pattern",
                    "raw_match": raw.strip(),
                })
    return results


# ============================================================
# Pass 3: WebHelp URL extraction
# ============================================================

_WEBHELP_URL_RE = re.compile(
    r'(?:href=["\']?|https?://\S*?)'
    r'Webhelp_ONESOURCE_TAXONE/Manuais_Funcionais[^"\'>\s]*?/([^"\'>\s]+)',
    re.IGNORECASE
)

# Also match direct WebHelp paths in text
_WEBHELP_PATH_RE = re.compile(
    r'Manuais_Funcionais\s*(?:Roteiro\s+de\s+utiliza[cç][aã]o\s*)?[/\\]?\s*([^\n"\'<>]+)',
    re.IGNORECASE | re.UNICODE
)


def extract_webhelp_paths(text: str) -> list:
    """Pass 3: Extrai paths do WebHelp de URLs ou referencias textuais."""
    results = []
    seen = set()

    for pattern in [_WEBHELP_URL_RE, _WEBHELP_PATH_RE]:
        for match in pattern.finditer(text):
            raw_path = match.group(1)
            # URL decode
            raw_path = urllib.parse.unquote(raw_path)
            # Remove file extensions
            raw_path = re.sub(r'\.(htm|html|aspx|php)(\?.*)?$', '', raw_path, flags=re.IGNORECASE)
            # Convert path separators to >
            normalized = normalize_path(raw_path.replace('_', ' '))

            if normalized and normalized not in seen:
                seen.add(normalized)
                results.append({
                    "path": normalized,
                    "confidence": 0.85,
                    "source": "webhelp_url",
                    "raw_match": match.group(0).strip()[:200],
                })
    return results


# ============================================================
# Pass 4: Fuzzy keyword matching
# ============================================================

def load_screen_keywords() -> list:
    """Carrega screen_keywords do playwright-test-map.json."""
    if not PLAYWRIGHT_MAP_PATH.exists():
        return []
    try:
        data = json.loads(PLAYWRIGHT_MAP_PATH.read_text(encoding="utf-8"))
        result = []
        for m in data.get("mappings", []):
            keywords = m.get("screen_keywords", []) + m.get("keywords", [])
            menu_path = m.get("menu_path", "")
            if menu_path or keywords:
                result.append({
                    "id": m.get("id", ""),
                    "menu_path": menu_path,
                    "keywords": [k.upper() for k in keywords],
                })
        return result
    except (json.JSONDecodeError, Exception):
        return []


def load_component_keywords() -> list:
    """Carrega keywords do component-test-map.json."""
    if not COMPONENT_MAP_PATH.exists():
        return []
    try:
        data = json.loads(COMPONENT_MAP_PATH.read_text(encoding="utf-8"))
        result = []
        for m in data.get("mappings", []):
            keywords = m.get("keywords", [])
            category = m.get("category", "")
            description = m.get("description", "")
            if keywords:
                result.append({
                    "id": m.get("id", ""),
                    "category": category,
                    "description": description,
                    "keywords": [k.upper() for k in keywords],
                })
        return result
    except (json.JSONDecodeError, Exception):
        return []


def extract_keywords_fuzzy(text: str) -> list:
    """Pass 4: Match de keywords do texto contra bases conhecidas."""
    # Stop-words: common Portuguese words that cause false keyword matches
    _STOP_WORDS = {
        "PARA", "COMO", "QUANDO", "ONDE", "QUAL", "QUE", "COM", "SEM",
        "POR", "DOS", "DAS", "NOS", "NAS", "UMA", "UNS", "UMAS",
        "NAO", "SIM", "MAS", "MAIS", "MENOS", "ENTRE", "SOBRE",
        "APOS", "ANTES", "DEPOIS", "AINDA", "TAMBEM", "APENAS",
        "CADA", "TODO", "TODA", "TODOS", "TODAS", "OUTRO", "OUTRA",
        "CASO", "ERRO", "CORRIGIR", "AJUSTE", "VERIFICAR", "EXECUTAR",
        "EMPRESA", "SISTEMA", "TELA", "CAMPO", "VALOR", "TIPO",
    }
    text_upper = normalize_path(text)
    results = []
    detected_keywords = set()

    # Match against playwright screen_keywords
    for mapping in load_screen_keywords():
        score = 0
        matched = []
        for kw in mapping["keywords"]:
            if kw in text_upper:
                score += 1
                matched.append(kw)
                detected_keywords.add(kw)
        if score >= 2 and mapping["menu_path"]:
            results.append({
                "path": mapping["menu_path"],
                "confidence": min(0.30 + (score * 0.15), 0.75),
                "source": "keyword_fuzzy",
                "raw_match": f"keywords matched: {', '.join(matched[:5])}",
            })

    # Match against component keywords
    for mapping in load_component_keywords():
        score = 0
        matched = []
        for kw in mapping["keywords"]:
            if kw in text_upper:
                score += 1
                matched.append(kw)
                detected_keywords.add(kw)
        if score >= 2:
            results.append({
                "path": f"{mapping['category']} > {mapping['description']}".upper(),
                "confidence": min(0.25 + (score * 0.12), 0.65),
                "source": "keyword_fuzzy_component",
                "raw_match": f"component keywords: {', '.join(matched[:5])}",
            })

    # Filter out stop-words and short keywords
    filtered_keywords = [
        kw for kw in detected_keywords
        if kw not in _STOP_WORDS and len(kw) >= 4
    ]
    return results, filtered_keywords


# ============================================================
# Azure DevOps integration
# ============================================================

def _get_az_bearer_token() -> str:
    """Tenta obter bearer token via az CLI (Azure Identity)."""
    import subprocess as _sp
    try:
        result = _sp.run(
            ["az", "account", "get-access-token",
             "--resource", "499b84ac-1321-427f-aa17-267ca6975798",
             "--query", "accessToken", "-o", "tsv"],
            capture_output=True, text=True, timeout=15,
            shell=(os.name == "nt"),
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def fetch_wi_fields(wi_id: int) -> dict:
    """Busca campos reproSteps e description da WI via Azure DevOps API."""
    import base64

    pat = os.environ.get("ADO_PAT") or os.environ.get("AZURE_DEVOPS_PAT", "")
    if not pat:
        # Tentar via env_loader
        try:
            from env_loader import _load_dotenv
            env = _load_dotenv()
            pat = env.get("ADO_PAT", "") or env.get("AZURE_DEVOPS_PAT", "")
        except Exception:
            pass

    # URL-encode project name (spaces etc.)
    project_encoded = urllib.parse.quote(ADO_PROJECT)
    url = (
        f"https://dev.azure.com/{ADO_ORG}/{project_encoded}/"
        f"_apis/wit/workitems/{wi_id}"
        f"?fields=System.Title,Microsoft.VSTS.TCM.ReproSteps,"
        f"System.Description,LegalOne.Context,System.AreaPath"
        f"&api-version={ADO_API_VERSION}"
    )

    # Try Basic auth with PAT first
    auth_header = None
    if pat:
        auth = base64.b64encode(f":{pat}".encode()).decode()
        auth_header = f"Basic {auth}"

    # If PAT fails or not set, try az CLI bearer token
    if not auth_header:
        bearer = _get_az_bearer_token()
        if bearer:
            auth_header = f"Bearer {bearer}"

    if not auth_header:
        raise EnvironmentError(
            "ADO_PAT/AZURE_DEVOPS_PAT nao configurado e az CLI nao disponivel. "
            "Configure no .env, como variavel de ambiente, ou faca 'az login'."
        )

    req = urllib.request.Request(url, headers={
        "Authorization": auth_header,
        "Content-Type": "application/json",
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            fields = data.get("fields", {})
            return {
                "wi_id": wi_id,
                "title": fields.get("System.Title", ""),
                "repro_steps": fields.get("Microsoft.VSTS.TCM.ReproSteps", ""),
                "description": fields.get("System.Description", ""),
                "context": fields.get("LegalOne.Context", ""),
                "area_path": fields.get("System.AreaPath", ""),
            }
    except urllib.error.HTTPError as e:
        if e.code == 401 and auth_header.startswith("Basic"):
            # PAT expirado, tentar bearer token
            bearer = _get_az_bearer_token()
            if bearer:
                req = urllib.request.Request(url, headers={
                    "Authorization": f"Bearer {bearer}",
                    "Content-Type": "application/json",
                })
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        fields = data.get("fields", {})
                        return {
                            "wi_id": wi_id,
                            "title": fields.get("System.Title", ""),
                            "repro_steps": fields.get("Microsoft.VSTS.TCM.ReproSteps", ""),
                            "description": fields.get("System.Description", ""),
                            "context": fields.get("LegalOne.Context", ""),
                            "area_path": fields.get("System.AreaPath", ""),
                        }
                except Exception as e2:
                    raise RuntimeError(f"Erro ao buscar WI {wi_id} (bearer fallback): {e2}")
        raise RuntimeError(f"Erro ao buscar WI {wi_id}: {e}")
    except Exception as e:
        raise RuntimeError(f"Erro ao buscar WI {wi_id}: {e}")


# ============================================================
# Main extraction pipeline
# ============================================================

def extract_functional_paths(wi_data: dict) -> dict:
    """Pipeline completo de extracao de caminhos funcionais.

    Args:
        wi_data: Dict com campos da WI (repro_steps, description, title, etc.)

    Returns:
        Dict com extracted_paths, keywords_detected, webhelp_urls_found
    """
    # Concatenar texto relevante
    raw_html = "\n\n".join(filter(None, [
        wi_data.get("repro_steps", ""),
        wi_data.get("description", ""),
    ]))
    title = wi_data.get("title", "")

    # Pass 1: HTML cleanup
    clean_text = strip_html(raw_html)
    full_text = f"{title}\n\n{clean_text}"

    all_paths = []
    webhelp_urls = []

    # Pass 2: Menu path extraction (highest confidence)
    menu_paths = extract_menu_paths(clean_text)
    all_paths.extend(menu_paths)

    # Also extract from raw HTML (URLs might be in href attributes)
    menu_paths_raw = extract_menu_paths(raw_html)
    for p in menu_paths_raw:
        if p["path"] not in {x["path"] for x in all_paths}:
            all_paths.append(p)

    # Pass 3: WebHelp URL extraction
    wh_paths = extract_webhelp_paths(raw_html)
    all_paths.extend(wh_paths)
    webhelp_urls = [p["raw_match"] for p in wh_paths]

    # Pass 4: Fuzzy keyword matching (only if passes 2-3 yielded nothing)
    keywords_detected = []
    if not all_paths:
        fuzzy_paths, keywords_detected = extract_keywords_fuzzy(full_text)
        all_paths.extend(fuzzy_paths)
    else:
        # Still detect keywords for metadata
        _, keywords_detected = extract_keywords_fuzzy(full_text)

    # Deduplicate and sort by confidence
    seen = set()
    unique_paths = []
    for p in sorted(all_paths, key=lambda x: x["confidence"], reverse=True):
        if p["path"] not in seen:
            seen.add(p["path"])
            unique_paths.append(p)

    return {
        "wi_id": wi_data.get("wi_id"),
        "title": title,
        "extracted_paths": unique_paths,
        "keywords_detected": keywords_detected,
        "webhelp_urls_found": webhelp_urls,
    }


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Extrai caminhos de navegacao funcional de Work Items"
    )
    parser.add_argument("--wi-id", type=int, help="ID do Work Item no Azure DevOps")
    parser.add_argument(
        "--from-file",
        help="Arquivo JSON com campos da WI (alternativa a --wi-id)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="json",
        help="Formato de saida (default: json)",
    )

    args = parser.parse_args()

    if not args.wi_id and not args.from_file:
        parser.error("Informe --wi-id ou --from-file")

    # Carregar dados da WI
    if args.from_file:
        wi_data = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
    else:
        print(f"Buscando WI #{args.wi_id} no Azure DevOps...", file=sys.stderr)
        wi_data = fetch_wi_fields(args.wi_id)

    # Extrair paths
    result = extract_functional_paths(wi_data)

    # Output
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"WI #{result['wi_id']} - {result.get('title', '')}")
        print(f"Paths encontrados: {len(result['extracted_paths'])}")
        print()
        for i, p in enumerate(result["extracted_paths"], 1):
            print(f"  {i}. [{p['confidence']:.0%}] {p['path']}")
            print(f"     Fonte: {p['source']}")
            print(f"     Match: {p['raw_match'][:100]}")
            print()
        if result["keywords_detected"]:
            print(f"Keywords: {', '.join(result['keywords_detected'][:15])}")

    # Exit code: 0 if paths found, 2 if no paths
    sys.exit(0 if result["extracted_paths"] else 2)


if __name__ == "__main__":
    main()
