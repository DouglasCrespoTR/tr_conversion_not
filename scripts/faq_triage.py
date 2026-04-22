#!/usr/bin/env python
"""
FAQ Triage - Analisa titulo/descricao de WI e retorna sinais de Not-a-Bug.

Cruza SAFX_CATALOG.json, MODULE_INDEX.json e zendesk-patterns para detectar
se o problema reportado ja tem solucao documentada no FAQ.

Usage:
  python faq_triage.py --title "SAFX07 - Erro 90034 na importacao" --description "..."
  python faq_triage.py --wi-text "Texto completo do work item"
  python faq_triage.py --title "..." --format json
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path


# --- Configuration ---

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBHELP_DIR = os.path.join(PLUGIN_ROOT, "knowledge", "webhelp")
PATTERNS_DIR = os.path.join(PLUGIN_ROOT, "knowledge", "zendesk-patterns")

CATALOG_PATH = os.path.join(WEBHELP_DIR, "SAFX_CATALOG.json")
MODULE_INDEX_PATH = os.path.join(WEBHELP_DIR, "MODULE_INDEX.json")

# --- Scoring ---

SCORE_SAFX_CATALOG = 10
SCORE_FAQ_HIGH_MATCH = 5
SCORE_ZENDESK_PATTERN = 5
SCORE_CONFIG_KEYWORDS = 3

# Classification thresholds
THRESHOLD_FAQ_RESOLUTION = 15
THRESHOLD_POSSIVEL_FAQ = 10
THRESHOLD_VERIFICAR_FAQ = 5

# Config/parametrization keywords (signal that it's a usage question, not a bug)
CONFIG_KEYWORDS = [
    "como configurar", "como parametrizar", "como cadastrar", "como preencher",
    "como importar", "como exportar", "como gerar", "como ativar", "como habilitar",
    "parametro", "parametros", "configuracao", "configurar",
    "onde encontro", "onde fica", "como faço", "como faco",
    "nao aparece", "nao gera", "nao importa",
    "campo obrigatorio", "campo nao preenchido", "campo invalido",
    "manual de layout", "layout safx",
]

# Regex patterns
RE_SAFX = re.compile(r"SAFX\s*(\d+)", re.IGNORECASE)
RE_ERRO = re.compile(r"[Ee]rro\s*(\d{3,6})")
RE_ERRO_CODE_ONLY = re.compile(r"\b(9\d{4})\b")  # 9xxxx error codes common in TAX ONE


# --- Data Loading ---

def load_catalog():
    """Load SAFX catalog."""
    if not os.path.exists(CATALOG_PATH):
        return None
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_module_index():
    """Load MODULE_INDEX.json for keyword search."""
    if not os.path.exists(MODULE_INDEX_PATH):
        return None
    with open(MODULE_INDEX_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_not_a_bug_patterns():
    """Load not-a-bug pattern names from zendesk-patterns/not-a-bug/INDEX.md."""
    index_path = os.path.join(PATTERNS_DIR, "not-a-bug", "INDEX.md")
    if not os.path.exists(index_path):
        return []

    patterns = []
    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            # Parse table rows: | [pattern-name](path) | vertical | tickets | causa |
            m = re.match(r"\|\s*\[([^\]]+)\]", line)
            if m:
                patterns.append(m.group(1))
    return patterns


# --- Signal Detection ---

def detect_safx_match(text, catalog):
    """Detect SAFX code + error code in text and look up in catalog."""
    if not catalog:
        return []

    signals = []

    # Find all SAFX codes
    safx_matches = RE_SAFX.findall(text)
    # Find all error codes
    erro_matches = RE_ERRO.findall(text)

    # Also try bare 9xxxx codes if no explicit "Erro NNNNN" found
    if not erro_matches:
        erro_matches = RE_ERRO_CODE_ONLY.findall(text)

    for safx_num in safx_matches:
        safx_code = f"SAFX{safx_num.zfill(2)}"
        safx_data = catalog.get("by_safx_code", {}).get(safx_code)
        if not safx_data:
            continue

        for err_code in erro_matches:
            entry = safx_data["errors"].get(str(err_code))
            if entry:
                signals.append({
                    "type": "safx_catalog",
                    "score": SCORE_SAFX_CATALOG,
                    "safx_code": safx_code,
                    "error_code": err_code,
                    "description": entry.get("description", ""),
                    "solution": entry.get("solution", ""),
                    "article_id": entry.get("article_id", ""),
                    "article_path": entry.get("article_path", ""),
                    "facet": entry.get("facet", ""),
                })

    # If we have SAFX but no matched error, check if any error from that SAFX appears
    if safx_matches and not signals:
        for safx_num in safx_matches:
            safx_code = f"SAFX{safx_num.zfill(2)}"
            safx_data = catalog.get("by_safx_code", {}).get(safx_code)
            if safx_data and safx_data["errors"]:
                # Partial match: SAFX found but specific error not in catalog
                # Lower score since we can't confirm the exact error
                first_err = next(iter(safx_data["errors"].values()))
                signals.append({
                    "type": "safx_partial",
                    "score": 3,
                    "safx_code": safx_code,
                    "error_code": "(nao especificado)",
                    "description": f"{len(safx_data['errors'])} erros documentados para {safx_code}",
                    "solution": "",
                    "article_id": "",
                    "article_path": "",
                    "facet": "",
                })
                break  # Only one partial signal

    # If we have error codes but no full catalog match, search reverse index
    has_full_match = any(s["type"] == "safx_catalog" for s in signals)
    if erro_matches and not has_full_match:
        # Remove partial signals — reverse index will provide better results
        signals = [s for s in signals if s["type"] != "safx_partial"]
        for err_code in erro_matches:
            safx_list = catalog.get("by_error_code", {}).get(str(err_code), [])
            for safx_code in safx_list:
                entry = catalog["by_safx_code"][safx_code]["errors"].get(str(err_code))
                if entry:
                    signals.append({
                        "type": "safx_catalog",
                        "score": SCORE_SAFX_CATALOG,
                        "safx_code": safx_code,
                        "error_code": err_code,
                        "description": entry.get("description", ""),
                        "solution": entry.get("solution", ""),
                        "article_id": entry.get("article_id", ""),
                        "article_path": entry.get("article_path", ""),
                        "facet": entry.get("facet", ""),
                    })

    return signals


def detect_faq_keyword_match(text, module_index):
    """Search MODULE_INDEX.json for high-scoring keyword matches."""
    if not module_index:
        return []

    signals = []
    text_lower = text.lower()

    # Extract meaningful keywords from text
    words = re.findall(r"\b[a-zA-Z\u00C0-\u00FF]{3,}\b", text_lower)
    # Also extract SAFX-like codes and numeric codes
    codes = re.findall(r"\b(?:safx\d+|x\d{4}|[a-z]{2,}\d{3,})\b", text_lower)
    search_terms = list(set(words + codes))

    keyword_index = module_index.get("keyword_index", {})

    # Score each keyword match
    best_score = 0
    best_articles = []

    for term in search_terms:
        if term in keyword_index:
            articles = keyword_index[term]
            if isinstance(articles, list):
                for art in articles[:3]:
                    if isinstance(art, dict):
                        score = art.get("score", 0)
                        if score >= 15:
                            best_score = max(best_score, score)
                            best_articles.append(art)

    if best_score >= 15 and best_articles:
        # Deduplicate by article id
        seen = set()
        unique = []
        for art in best_articles:
            aid = art.get("id", art.get("article_id", ""))
            if aid not in seen:
                seen.add(aid)
                unique.append(art)

        signals.append({
            "type": "faq_keyword",
            "score": SCORE_FAQ_HIGH_MATCH,
            "description": f"FAQ keyword match (score>={best_score})",
            "articles": unique[:3],
        })

    return signals


def detect_zendesk_pattern(text, patterns):
    """Check if text matches known not-a-bug Zendesk patterns."""
    if not patterns:
        return []

    signals = []
    text_lower = text.lower()

    # Extract specific keywords from text for matching against pattern names
    # We require the pattern name to contain a keyword from the text (specific match)
    text_keywords = set()
    # Extract multi-word terms
    for kw in ["job servidor", "data warehouse", "report fiscal", "tax automation",
               "ativo permanente", "livre comercio", "meio magnetico"]:
        if kw in text_lower:
            text_keywords.add(kw.replace(" ", "-"))
    # Extract single meaningful terms (module-specific)
    specific_terms = re.findall(
        r"\b(safx\d*|icms|iss|ipi|pis|cofins|sped|efd|reinf|ecf|ecd|gia|dime|fci|"
        r"nfts|giss|simp|dimob|dmed|funrural|esocial|powerlock|sftp|licenciamento)\b",
        text_lower
    )
    text_keywords.update(specific_terms)

    if not text_keywords:
        return signals

    matched_patterns = []
    for pattern_name in patterns:
        pattern_lower = pattern_name.lower()
        for kw in text_keywords:
            if kw in pattern_lower:
                matched_patterns.append(pattern_name)
                break

    if matched_patterns:
        # Deduplicate
        matched_patterns = list(dict.fromkeys(matched_patterns))
        signals.append({
            "type": "zendesk_pattern",
            "score": SCORE_ZENDESK_PATTERN,
            "description": f"{len(matched_patterns)} pattern(s) not-a-bug correspondente(s)",
            "patterns": matched_patterns[:3],
        })

    return signals


def detect_config_keywords(text):
    """Detect configuration/parametrization keywords."""
    text_lower = text.lower()
    matched = []

    for kw in CONFIG_KEYWORDS:
        if kw in text_lower:
            matched.append(kw)

    if matched:
        return [{
            "type": "config_keywords",
            "score": SCORE_CONFIG_KEYWORDS,
            "description": f"Keywords de configuracao/parametrizacao detectadas",
            "keywords": matched[:5],
        }]

    return []


# --- Triage ---

def triage(title="", description="", wi_text=""):
    """Run full FAQ triage on the given text."""
    # Combine all text sources
    text = " ".join(filter(None, [title, description, wi_text]))
    if not text.strip():
        return {"score": 0, "classification": "", "signals": [], "recommendation": ""}

    # Load data sources
    catalog = load_catalog()
    module_index = load_module_index()
    patterns = load_not_a_bug_patterns()

    # Collect signals
    all_signals = []
    all_signals.extend(detect_safx_match(text, catalog))
    all_signals.extend(detect_faq_keyword_match(text, module_index))
    all_signals.extend(detect_zendesk_pattern(text, patterns))
    all_signals.extend(detect_config_keywords(text))

    # Calculate total score (use max per type to avoid double-counting)
    score_by_type = {}
    for sig in all_signals:
        t = sig["type"]
        score_by_type[t] = max(score_by_type.get(t, 0), sig["score"])

    total_score = sum(score_by_type.values())

    # Classify
    if total_score >= THRESHOLD_FAQ_RESOLUTION:
        classification = "[FAQ-RESOLUTION]"
        recommendation = "Erro documentado no FAQ. Verificar se cliente seguiu os passos antes de alterar codigo."
    elif total_score >= THRESHOLD_POSSIVEL_FAQ:
        classification = "[POSSIVEL-FAQ]"
        recommendation = "Verificar artigo FAQ antes de analisar codigo. Possivel duvida funcional."
    elif total_score >= THRESHOLD_VERIFICAR_FAQ:
        classification = "[VERIFICAR-FAQ]"
        recommendation = "Consultar FAQ como referencia antes de prosseguir."
    else:
        classification = ""
        recommendation = "Prosseguir normalmente com analise de codigo."

    # Extract SAFX match info for pipeline variables
    safx_match = None
    for sig in all_signals:
        if sig["type"] == "safx_catalog":
            safx_match = {
                "safx_code": sig["safx_code"],
                "error_code": sig["error_code"],
                "description": sig["description"],
                "solution": sig["solution"],
                "article_path": sig["article_path"],
            }
            break

    # Collect article references
    articles = []
    for sig in all_signals:
        if sig["type"] == "safx_catalog" and sig.get("article_path"):
            articles.append({
                "path": sig["article_path"],
                "id": sig.get("article_id", ""),
                "context": f"{sig['safx_code']} Erro {sig['error_code']}",
            })
        elif sig["type"] == "faq_keyword":
            for art in sig.get("articles", []):
                articles.append({
                    "path": art.get("path", ""),
                    "id": art.get("id", art.get("article_id", "")),
                    "context": "FAQ keyword match",
                })

    return {
        "score": total_score,
        "classification": classification,
        "signals": all_signals,
        "recommendation": recommendation,
        "safx_match": safx_match,
        "articles": articles,
    }


# --- Output Formatting ---

def format_markdown(result):
    """Format triage result as markdown."""
    lines = []
    lines.append("## Triagem FAQ")

    if result["classification"]:
        lines.append(f"**Classificacao: {result['classification']}** (Score: {result['score']})")
    else:
        lines.append(f"**Score: {result['score']}** (abaixo do limiar)")

    lines.append("")
    lines.append("### Sinais Detectados:")

    if not result["signals"]:
        lines.append("- Nenhum sinal detectado")
    else:
        for sig in result["signals"]:
            if sig["type"] == "safx_catalog":
                lines.append(
                    f"- [{sig['score']}pts] {sig['safx_code']} Erro {sig['error_code']}"
                    f" -> \"{sig['description']}\""
                )
                if sig["solution"]:
                    lines.append(f"  - Solucao: \"{sig['solution'][:200]}\"")
                if sig["article_path"]:
                    lines.append(f"  - Path: `{sig['article_path']}`")
            elif sig["type"] == "safx_partial":
                lines.append(
                    f"- [{sig['score']}pts] {sig['safx_code']} — {sig['description']}"
                )
            elif sig["type"] == "faq_keyword":
                lines.append(f"- [{sig['score']}pts] {sig['description']}")
                for art in sig.get("articles", []):
                    title = art.get("title", art.get("id", ""))
                    lines.append(f"  - Artigo: {title}")
            elif sig["type"] == "zendesk_pattern":
                lines.append(f"- [{sig['score']}pts] Zendesk pattern: {sig['description']}")
                for p in sig.get("patterns", []):
                    lines.append(f"  - {p}")
            elif sig["type"] == "config_keywords":
                lines.append(
                    f"- [{sig['score']}pts] {sig['description']}: "
                    f"{', '.join(sig.get('keywords', []))}"
                )

    lines.append("")
    lines.append("### Recomendacao:")
    lines.append(result["recommendation"])

    return "\n".join(lines)


def format_json(result):
    """Format triage result as JSON (for pipeline integration)."""
    output = {
        "score": result["score"],
        "classification": result["classification"],
        "recommendation": result["recommendation"],
        "safx_match": result.get("safx_match"),
        "articles": result.get("articles", []),
        "signal_count": len(result["signals"]),
        "signal_types": list(set(s["type"] for s in result["signals"])),
    }
    return json.dumps(output, ensure_ascii=False, indent=2)


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(
        description="FAQ Triage - Triagem Not-a-Bug via FAQ/SAFX/Zendesk patterns"
    )
    parser.add_argument(
        "--title", type=str, default="",
        help="Titulo do work item"
    )
    parser.add_argument(
        "--description", type=str, default="",
        help="Descricao do work item"
    )
    parser.add_argument(
        "--wi-text", type=str, default="",
        help="Texto completo do work item (alternativa a --title + --description)"
    )
    parser.add_argument(
        "--format", choices=["markdown", "json"], default="markdown",
        help="Formato de saida (default: markdown)"
    )

    args = parser.parse_args()

    if not any([args.title, args.description, args.wi_text]):
        parser.print_help()
        sys.exit(1)

    result = triage(
        title=args.title,
        description=args.description,
        wi_text=args.wi_text,
    )

    if args.format == "json":
        print(format_json(result))
    else:
        print(format_markdown(result))


if __name__ == "__main__":
    main()
