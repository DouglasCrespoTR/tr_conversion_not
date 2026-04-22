#!/usr/bin/env python3
"""
Test Enricher - Enriquece test_scenarios.json com schema, impacto git e suites.

Dado um work item com test_scenarios.json pre-gerado pelo PM, este script:
1. Enriquece sql_hints com colunas tipadas, JOINs via FK, WHERE type-safe
2. Adiciona cenarios de regressao para tabelas indiretamente impactadas (git diff)
3. Marca cenarios que tocam hotspots como priority: critical
4. Mapeia e pontua suites XML recomendadas com scoring enriquecido

Uso:
    python scripts/test_enricher.py \
        --wi-id 1058668 \
        --repo "$TAXONE_DW_REPO" \
        --branch MFS1058668 \
        --format json
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

from env_loader import get_repo_path
DEFAULT_REPO = get_repo_path("TAXONE_DW_REPO")
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"
SCHEMA_DIR = KNOWLEDGE_DIR / "schema"
SUITE_MAP_PATH = KNOWLEDGE_DIR / "suite-automation" / "component-test-map.json"

# Scoring weights for suite recommendations
SCORE_GIT_DIFF_MATCH = 8
SCORE_TABLE_OVERLAP = 6
SCORE_HOTSPOT_BONUS = 3
SCORE_RECENCY_BONUS = 2


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def run_git(repo: str, args: list[str]) -> str:
    """Executa comando git e retorna stdout."""
    cmd = ["git", "-C", repo] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, Exception):
        return ""


def get_diff_files(repo: str, branch: str) -> list[str]:
    """Retorna lista de arquivos alterados no branch vs DEV."""
    output = run_git(repo, ["diff", "--name-only", f"DEV...{branch}"])
    return [f for f in output.splitlines() if f.strip()] if output else []


def get_diff_tables_and_packages(repo: str, branch: str) -> tuple[set[str], set[str]]:
    """Extrai tabelas e packages referenciados no diff."""
    diff = run_git(repo, ["diff", "DEV...", branch, "--unified=0"])
    if not diff:
        return set(), set()

    tables = set()
    packages = set()
    table_pattern = re.compile(
        r"\b((?:SAFX|X|EST|ICT|PRT|TMP|DWT|CBT|APT|LOG|DET|ITEM)\w+)\b",
        re.IGNORECASE
    )
    pkg_pattern = re.compile(
        r"\b(PKG_\w+|SAF_\w+|OL_\w+|LIB_\w+|\w+_FPROC|\w+_CPROC|PRC_\w+|FNC_\w+)\b",
        re.IGNORECASE
    )

    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            for m in table_pattern.finditer(line):
                tables.add(m.group(1).upper())
            for m in pkg_pattern.finditer(line):
                packages.add(m.group(1).upper())

    return tables, packages


def get_change_frequency(repo: str, objects: list[str], days: int = 60) -> dict[str, int]:
    """Conta quantas vezes cada objeto foi alterado nos ultimos N dias."""
    freq = {}
    since = f"--since={days} days ago"
    all_files = run_git(repo, ["ls-files"])
    if not all_files:
        return freq

    file_list = all_files.splitlines()
    for obj in objects:
        obj_lower = obj.lower()
        matched = [f for f in file_list if obj_lower in Path(f).stem.lower()]
        if matched:
            log = run_git(repo, ["log", "--oneline", since, "--no-merges", "--"] + matched)
            freq[obj] = len(log.splitlines()) if log else 0
    return freq


# ---------------------------------------------------------------------------
# Knowledge loaders
# ---------------------------------------------------------------------------

def load_column_glossary() -> dict[str, dict]:
    """Carrega COLUMN_GLOSSARY.md e retorna dict {col_name: {type, description}}."""
    path = SCHEMA_DIR / "COLUMN_GLOSSARY.md"
    if not path.exists():
        return {}

    glossary = {}
    content = path.read_text(encoding="utf-8", errors="replace")

    # Primary pattern: markdown table row | COL_NAME | freq | TYPE | description |
    table_pattern = re.compile(
        r"\|\s*(\w+)\s*\|\s*\d+\s*\|\s*(\w[\w() ,]+?)\s*\|\s*(.+?)\s*\|"
    )
    for m in table_pattern.finditer(content):
        col = m.group(1).upper()
        if col in ("Coluna", "COLUNA", "---"):  # Skip header
            continue
        glossary[col] = {
            "type": m.group(2).strip(),
            "description": m.group(3).strip(),
        }

    # Fallback: backtick pattern - `COL_NAME` - Type - description
    if len(glossary) < 10:
        simple = re.compile(r"`(\w+)`\s*[-–—]\s*(.+)")
        for m in simple.finditer(content):
            col = m.group(1).upper()
            if col not in glossary:
                glossary[col] = {"type": "unknown", "description": m.group(2).strip()}

    return glossary


def load_relationships() -> dict[str, list[str]]:
    """Carrega RELATIONSHIPS.md e retorna dict {table: [related_tables]}."""
    path = SCHEMA_DIR / "RELATIONSHIPS.md"
    if not path.exists():
        return {}

    rels = {}
    content = path.read_text(encoding="utf-8", errors="replace")
    # Mermaid pattern: SRC -->|N FKs| DST  or  SRC --> DST
    mermaid_pattern = re.compile(r"(\w+)\s*--+>?\|?[^|]*\|?\s*(\w+)")
    for m in mermaid_pattern.finditer(content):
        src = m.group(1).upper()
        dst = m.group(2).upper()
        if src in ("graph", "GRAPH", "subgraph"):
            continue
        rels.setdefault(src, []).append(dst)
        rels.setdefault(dst, []).append(src)

    # Deduplicate
    return {k: sorted(set(v)) for k, v in rels.items()}


def load_hotspots() -> dict[str, int]:
    """Carrega TABLE_HOTSPOTS.md e retorna dict {name: bug_count}."""
    path = SCHEMA_DIR / "TABLE_HOTSPOTS.md"
    if not path.exists():
        return {}

    hotspots = {}
    content = path.read_text(encoding="utf-8", errors="replace")

    # Primary pattern: markdown table row | NAME | N | work_items |
    table_pattern = re.compile(r"\|\s*(\w+)\s*\|\s*(\d+)\s*\|")
    for m in table_pattern.finditer(content):
        name = m.group(1).upper()
        if name in ("Tabela", "TABELA", "Objeto", "OBJETO", "Módulo", "MODULO", "---"):
            continue
        count = int(m.group(2))
        if count > 0:
            hotspots[name] = max(hotspots.get(name, 0), count)

    # Fallback: bullet pattern - TABLE_NAME (N bugs: ...)
    if len(hotspots) < 3:
        bullet_pattern = re.compile(r"[-*]\s*`?(\w+)`?\s*\((\d+)\s*bugs?", re.IGNORECASE)
        for m in bullet_pattern.finditer(content):
            hotspots[m.group(1).upper()] = int(m.group(2))

    return hotspots


def load_plsql_map() -> dict[str, int]:
    """Carrega PLSQL_MAP.md e retorna dict {object_name: table_ref_count}.

    O PLSQL_MAP lista objetos PL/SQL com contagem de tabelas referenciadas
    (nao lista as tabelas individualmente). Util para identificar procedures
    de alto impacto.
    """
    path = SCHEMA_DIR / "PLSQL_MAP.md"
    if not path.exists():
        return {}

    plsql_map = {}
    content = path.read_text(encoding="utf-8", errors="replace")

    # Primary: markdown table row | OBJECT | TYPE | N |
    table_pattern = re.compile(
        r"\|\s*(\w+)\s*\|\s*(?:PROCEDURE|PACKAGE BODY|FUNCTION|VIEW)\s*\|\s*(\d+)\s*\|",
        re.IGNORECASE
    )
    for m in table_pattern.finditer(content):
        obj = m.group(1).upper()
        if obj in ("Objeto", "OBJETO", "---"):
            continue
        plsql_map[obj] = int(m.group(2))

    return plsql_map


def load_suite_map() -> dict:
    """Carrega component-test-map.json."""
    if not SUITE_MAP_PATH.exists():
        return {}
    with open(SUITE_MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Enrichment modules
# ---------------------------------------------------------------------------

def enrich_sql_hints(scenarios: list[dict], glossary: dict, relationships: dict) -> list[dict]:
    """Enriquece sql_hint com colunas tipadas, JOINs via FK, WHERE type-safe."""
    for scenario in scenarios:
        sql_hint = scenario.get("sql_hint", "")
        if not sql_hint:
            continue

        tables = scenario.get("tables_involved", [])
        enrichments = []

        # Add column type info
        col_pattern = re.compile(r"\b([A-Z_]{3,})\b")
        cols_found = col_pattern.findall(sql_hint.upper())
        typed_cols = []
        for col in cols_found:
            if col in glossary:
                typed_cols.append(f"{col} ({glossary[col]['type']})")

        if typed_cols:
            enrichments.append(f"-- Colunas tipadas: {', '.join(typed_cols[:10])}")

        # Add JOIN hints via FK relationships
        if len(tables) >= 2:
            join_hints = []
            for i, t1 in enumerate(tables):
                t1_upper = t1.upper()
                related = relationships.get(t1_upper, [])
                for t2 in tables[i + 1:]:
                    if t2.upper() in related:
                        join_hints.append(f"-- FK: {t1} <-> {t2}")
            if join_hints:
                enrichments.extend(join_hints[:5])

        # Add WHERE type-safe hints
        for col in cols_found:
            if col in glossary:
                col_type = glossary[col]["type"].upper()
                if "VARCHAR" in col_type:
                    enrichments.append(f"-- WHERE {col} = 'valor' (VARCHAR)")
                elif "NUMBER" in col_type or "NUMERIC" in col_type:
                    enrichments.append(f"-- WHERE {col} = 0 (NUMBER)")
                elif "DATE" in col_type:
                    enrichments.append(f"-- WHERE {col} = TO_DATE('2026-01-01','YYYY-MM-DD')")

        if enrichments:
            scenario["sql_hint_enriched"] = sql_hint + "\n" + "\n".join(enrichments[:8])
            scenario["typed_columns"] = typed_cols[:10]

    return scenarios


def enrich_with_git_impact(
    scenarios: list[dict],
    repo: str,
    branch: str,
    plsql_map: dict,
    relationships: dict,
) -> list[dict]:
    """Adiciona cenarios de regressao para tabelas indiretamente impactadas."""
    diff_tables, diff_packages = get_diff_tables_and_packages(repo, branch)

    if not diff_tables and not diff_packages:
        return scenarios

    # Find indirectly impacted tables via PLSQL_MAP
    # plsql_map is {object: ref_count} — we mark high-ref-count objects as high impact
    indirect_tables = set()
    high_impact_packages = []
    for pkg in diff_packages:
        if pkg in plsql_map and plsql_map[pkg] > 10:
            high_impact_packages.append(f"{pkg} ({plsql_map[pkg]} refs)")

    # Find indirectly impacted tables via FK relationships
    for table in list(diff_tables):
        related = relationships.get(table, [])
        for rel in related:
            if rel not in diff_tables:
                indirect_tables.add(rel)

    # Generate regression scenarios for indirect tables
    new_scenarios = []
    for table in sorted(indirect_tables)[:5]:  # Limit to 5 most relevant
        new_scenarios.append({
            "id": f"regression_indirect_{table.lower()}",
            "type": "regression",
            "priority": "medium",
            "description": f"Regressao: verificar integridade de {table} (impactada indiretamente via FK/procedure)",
            "tables_involved": [table],
            "sql_hint": f"SELECT COUNT(*) FROM {table} WHERE ROWNUM <= 100",
            "expected_result": "Dados consistentes apos alteracao",
            "source": "git_impact_analysis",
            "preconditions": ["Executar apos aplicar alteracoes do branch"],
        })

    scenarios.extend(new_scenarios)

    # Add git impact metadata to existing scenarios
    for scenario in scenarios:
        tables = set(t.upper() for t in scenario.get("tables_involved", []))
        if tables & diff_tables:
            scenario.setdefault("git_impact", {})["directly_changed"] = True
        if tables & indirect_tables:
            scenario.setdefault("git_impact", {})["indirectly_impacted"] = True

    return scenarios


def enrich_with_hotspots(scenarios: list[dict], hotspots: dict) -> list[dict]:
    """Flag priority: critical em cenarios que tocam hotspots."""
    for scenario in scenarios:
        tables = [t.upper() for t in scenario.get("tables_involved", [])]
        packages = [p.upper() for p in scenario.get("packages_involved", [])]

        hotspot_matches = []
        for t in tables:
            if t in hotspots:
                hotspot_matches.append(f"{t} ({hotspots[t]} bugs)")
        for p in packages:
            if p in hotspots:
                hotspot_matches.append(f"{p} ({hotspots[p]} bugs)")

        if hotspot_matches:
            scenario["priority"] = "critical"
            scenario["hotspot_matches"] = hotspot_matches

    return scenarios


def map_suite_xml(
    scenarios: list[dict],
    suite_map: dict,
    diff_files: list[str],
    diff_tables: set[str],
    hotspots: dict,
    change_freq: dict,
) -> list[dict]:
    """Mapeia e pontua suites XML recomendadas com scoring enriquecido."""
    mappings = suite_map.get("mappings", [])
    base_scoring = suite_map.get("scoring", {})
    max_suites = base_scoring.get("max_suites", 5)

    # Collect all tables and packages from scenarios
    all_tables = set()
    all_packages = set()
    for scenario in scenarios:
        all_tables.update(t.upper() for t in scenario.get("tables_involved", []))
        all_packages.update(p.upper() for p in scenario.get("packages_involved", []))

    suite_scores = []

    for mapping in mappings:
        score = 0
        reasons = []

        # Original keyword match (from existing scoring)
        keyword_score = base_scoring.get("keyword_match", 3)

        # Git diff match (+8): file paths in diff match mapping file_paths
        for fp in mapping.get("file_paths", []):
            fp_lower = fp.lower()
            for df in diff_files:
                if fp_lower in df.lower():
                    score += SCORE_GIT_DIFF_MATCH
                    reasons.append(f"git_diff: {df}")
                    break

        # Package match (+4 base): packages in diff match mapping packages
        for pkg in mapping.get("packages", []):
            if pkg.upper() in all_packages:
                score += base_scoring.get("package_match", 4)
                reasons.append(f"package: {pkg}")

        # Table overlap (+6): tables from change coincide with suite tables
        mapping_keywords_upper = [k.upper() for k in mapping.get("keywords", [])]
        for table in all_tables:
            for kw in mapping_keywords_upper:
                if table in kw or kw in table:
                    score += SCORE_TABLE_OVERLAP
                    reasons.append(f"table_overlap: {table}")
                    break

        # Hotspot bonus (+3): table in overlap also in TABLE_HOTSPOTS
        for table in all_tables:
            if table in hotspots:
                score += SCORE_HOTSPOT_BONUS
                reasons.append(f"hotspot: {table} ({hotspots[table]} bugs)")

        # Recency bonus (+2): object altered >3x in last 60 days
        for pkg in mapping.get("packages", []):
            if change_freq.get(pkg.upper(), 0) > 3:
                score += SCORE_RECENCY_BONUS
                reasons.append(f"recency: {pkg} ({change_freq[pkg.upper()]}x in 60d)")

        if score >= base_scoring.get("min_score_to_include", 3):
            suite_scores.append({
                "id": mapping["id"],
                "xml_file": mapping["xml_file"],
                "description": mapping.get("description", ""),
                "category": mapping.get("category", ""),
                "score": score,
                "reasons": reasons,
            })

    # Sort by score descending
    suite_scores.sort(key=lambda s: s["score"], reverse=True)
    return suite_scores[:max_suites]


# ---------------------------------------------------------------------------
# Main enrichment pipeline
# ---------------------------------------------------------------------------

def load_test_scenarios(wi_id: str) -> dict:
    """Carrega test_scenarios.json existente ou cria esqueleto."""
    tests_dir = PROJECT_ROOT / "tests" / str(wi_id)
    path = tests_dir / "test_scenarios.json"

    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # Create skeleton
    return {
        "work_item_id": wi_id,
        "generated_by": "test_enricher_skeleton",
        "generated_at": datetime.now().isoformat(),
        "scenarios": [],
        "suite_recommendations": [],
    }


def save_test_scenarios(wi_id: str, data: dict):
    """Salva test_scenarios.json enriquecido."""
    tests_dir = PROJECT_ROOT / "tests" / str(wi_id)
    tests_dir.mkdir(parents=True, exist_ok=True)
    path = tests_dir / "test_scenarios.json"

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    print(f"[OK] Salvo: {path}", file=sys.stderr)


def enrich(
    wi_id: str,
    repo: str = DEFAULT_REPO,
    branch: str = "",
    output_format: str = "json",
) -> dict:
    """Executa pipeline completo de enriquecimento."""

    # Load test_scenarios
    data = load_test_scenarios(wi_id)
    scenarios = data.get("scenarios", [])

    # Load knowledge
    glossary = load_column_glossary()
    relationships = load_relationships()
    hotspots = load_hotspots()
    plsql_map = load_plsql_map()
    suite_map = load_suite_map()

    print(f"[INFO] Knowledge carregado: glossary={len(glossary)} cols, "
          f"relationships={len(relationships)} tables, hotspots={len(hotspots)} entries, "
          f"plsql_map={len(plsql_map)} procs", file=sys.stderr)

    # Module 1: Enrich SQL hints
    scenarios = enrich_sql_hints(scenarios, glossary, relationships)

    # Module 2: Enrich with git impact (if branch provided)
    diff_files = []
    diff_tables = set()
    change_freq = {}
    if branch and repo:
        scenarios = enrich_with_git_impact(scenarios, repo, branch, plsql_map, relationships)
        diff_files = get_diff_files(repo, branch)
        diff_tables, diff_packages = get_diff_tables_and_packages(repo, branch)

        # Get change frequency for recency scoring
        all_packages = set()
        for s in scenarios:
            all_packages.update(p.upper() for p in s.get("packages_involved", []))
        all_packages.update(diff_packages)
        if all_packages:
            change_freq = get_change_frequency(repo, list(all_packages))

    # Module 3: Enrich with hotspots
    scenarios = enrich_with_hotspots(scenarios, hotspots)

    # Module 4: Map suite XMLs
    suite_recommendations = map_suite_xml(
        scenarios, suite_map, diff_files, diff_tables, hotspots, change_freq
    )

    # Update data
    data["scenarios"] = scenarios
    data["suite_recommendations"] = suite_recommendations
    data["enriched_by"] = "test_enricher"
    data["enriched_at"] = datetime.now().isoformat()
    data["enrichment_stats"] = {
        "scenarios_total": len(scenarios),
        "scenarios_with_enriched_hints": sum(1 for s in scenarios if "sql_hint_enriched" in s),
        "scenarios_from_git_impact": sum(1 for s in scenarios if s.get("source") == "git_impact_analysis"),
        "scenarios_critical_hotspot": sum(1 for s in scenarios if s.get("priority") == "critical"),
        "suites_recommended": len(suite_recommendations),
        "diff_files_analyzed": len(diff_files),
        "knowledge_sources": {
            "column_glossary": len(glossary),
            "relationships": len(relationships),
            "hotspots": len(hotspots),
            "plsql_map": len(plsql_map),
        },
    }

    # Save enriched data
    save_test_scenarios(wi_id, data)

    return data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Test Enricher - Enriquece test_scenarios.json com schema, git e suites"
    )
    parser.add_argument(
        "--wi-id", type=str, required=True,
        help="ID do work item (ex: 1058668)"
    )
    parser.add_argument(
        "--repo", type=str, default=DEFAULT_REPO,
        help=f"Path do repositorio git (default: {DEFAULT_REPO})"
    )
    parser.add_argument(
        "--branch", type=str, default="",
        help="Branch de trabalho (ex: MFS1058668)"
    )
    parser.add_argument(
        "--format", choices=["json", "summary"], default="json",
        help="Formato de saida (default: json)"
    )

    args = parser.parse_args()

    result = enrich(
        wi_id=args.wi_id,
        repo=args.repo,
        branch=args.branch,
        output_format=args.format,
    )

    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        # Summary format
        stats = result.get("enrichment_stats", {})
        print(f"## Test Enrichment Summary - WI #{args.wi_id}")
        print(f"")
        print(f"- Cenarios total: {stats.get('scenarios_total', 0)}")
        print(f"- SQL hints enriquecidos: {stats.get('scenarios_with_enriched_hints', 0)}")
        print(f"- Cenarios git impact: {stats.get('scenarios_from_git_impact', 0)}")
        print(f"- Cenarios critical (hotspot): {stats.get('scenarios_critical_hotspot', 0)}")
        print(f"- Suites recomendadas: {stats.get('suites_recommended', 0)}")
        print(f"- Arquivos no diff: {stats.get('diff_files_analyzed', 0)}")
        print(f"")
        suites = result.get("suite_recommendations", [])
        if suites:
            print(f"### Suites Recomendadas")
            for s in suites:
                print(f"- [{s['score']}pts] {s['id']} - {s['xml_file']}")
                for r in s.get("reasons", []):
                    print(f"  - {r}")


if __name__ == "__main__":
    main()
