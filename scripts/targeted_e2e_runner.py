#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Targeted E2E Runner - Orquestrador de testes E2E direcionados por Work Item.

Pipeline: WI ID -> Path Extraction -> Matching -> Dispatch (suite_runner/playwright_runner) -> Report

Exit codes:
  0 = Todos os testes passaram
  1 = Algum teste falhou
  2 = Gap (nenhum cenario E2E encontrado para o path)

Uso:
  python targeted_e2e_runner.py --wi-id 1053341 --env qa
  python targeted_e2e_runner.py --wi-id 1053341 --env qa --dry-run
  python targeted_e2e_runner.py --wi-id 1053341 --env LOCAL --suite-only
  python targeted_e2e_runner.py --wi-id 1053341 --env qa --playwright-only
  python targeted_e2e_runner.py --wi-id 1053341 --output tests/1053341/targeted_e2e_results.json
"""

import argparse
import json
import os
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from functional_path_extractor import (  # noqa: E402
    extract_functional_paths,
    fetch_wi_fields,
    normalize_path,
)

# Knowledge maps
WEBHELP_MAP_PATH = PROJECT_ROOT / "knowledge" / "suite-automation" / "webhelp-e2e-map.json"
PLAYWRIGHT_MAP_PATH = PROJECT_ROOT / "knowledge" / "suite-automation" / "playwright-test-map.json"
COMPONENT_MAP_PATH = PROJECT_ROOT / "knowledge" / "suite-automation" / "component-test-map.json"
MODULE_INDEX_PATH = PROJECT_ROOT / "knowledge" / "webhelp" / "MODULE_INDEX.json"


# ============================================================
# Matching engine
# ============================================================

def normalize_for_match(text: str) -> str:
    """Normaliza para comparacao."""
    if not text:
        return ""
    nfkd = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in nfkd if not unicodedata.combining(c))
    text = text.upper().strip()
    import re
    text = re.sub(r'[^A-Z0-9\s>]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def load_json(path: Path) -> dict:
    """Carrega JSON com fallback vazio."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, Exception):
        return {}


def find_matching_tests(extracted_paths: list, keywords: list) -> dict:
    """Encontra testes E2E que correspondem aos paths extraidos.

    Estrategia de scoring (consistente com component-test-map.json):
      - Exact path match in webhelp-e2e-map: 10 pts
      - Partial path match: 5 pts
      - Menu path match in playwright-test-map: 7 pts
      - Keyword overlap: 3 pts per keyword
      - Screen keyword match: 5 pts

    Returns:
        dict com playwright_specs, suite_xmls, matched_entries, gaps
    """
    webhelp_map = load_json(WEBHELP_MAP_PATH)
    pw_map = load_json(PLAYWRIGHT_MAP_PATH)
    comp_map = load_json(COMPONENT_MAP_PATH)

    wh_mappings = webhelp_map.get("mappings", [])
    pw_mappings = pw_map.get("mappings", [])
    comp_mappings = comp_map.get("mappings", [])

    min_score = webhelp_map.get("scoring", {}).get("min_score_to_include", 9)

    # Stop-words: common Portuguese words that cause false positives
    _STOP_WORDS = {
        "PARA", "COMO", "QUANDO", "ONDE", "QUAL", "QUE", "COM", "SEM",
        "POR", "DOS", "DAS", "NOS", "NAS", "UMA", "UNS", "UMAS",
        "NAO", "SIM", "MAS", "MAIS", "MENOS", "ENTRE", "SOBRE",
        "APOS", "ANTES", "DEPOIS", "AINDA", "TAMBEM", "APENAS",
        "CADA", "TODO", "TODA", "TODOS", "TODAS", "OUTRO", "OUTRA",
        "CASO", "ERRO", "CORRIGIR", "AJUSTE", "VERIFICAR", "EXECUTAR",
        "EMPRESA", "SISTEMA", "TELA", "CAMPO", "VALOR", "TIPO",
    }
    MIN_KEYWORD_LEN = 4  # Minimum keyword length for substring matching

    # Collect all matched test IDs with scores
    pw_scores = {}  # pw_id -> best_score
    sa_scores = {}  # sa_id -> best_score
    matched_entries = []

    normalized_paths = [normalize_for_match(p["path"]) for p in extracted_paths]
    # Filter keywords: remove stop-words and short keywords
    normalized_keywords = [
        k.upper() for k in keywords
        if k.upper() not in _STOP_WORDS and len(k) >= MIN_KEYWORD_LEN
    ]

    # --- Phase 1: Match against webhelp-e2e-map.json ---
    for wh in wh_mappings:
        wh_path = normalize_for_match(wh.get("menu_path_normalized", ""))
        score = 0
        match_reasons = []

        for np in normalized_paths:
            if np == wh_path:
                score += 10
                match_reasons.append(f"exact_path: {np}")
            elif np in wh_path or wh_path in np:
                score += 5
                match_reasons.append(f"partial_path: {np}")

        # Keyword overlap
        wh_keywords = [k.upper() for k in wh.get("keywords", [])]
        for kw in normalized_keywords:
            for wk in wh_keywords:
                if kw in wk or wk in kw:
                    score += 3
                    match_reasons.append(f"keyword: {kw}")
                    break

        if score >= min_score:
            for pw_id in wh.get("playwright_mapping_ids", []):
                pw_scores[pw_id] = max(pw_scores.get(pw_id, 0), score)
            for sa_id in wh.get("suite_automation_ids", []):
                sa_scores[sa_id] = max(sa_scores.get(sa_id, 0), score)
            matched_entries.append({
                "source": "webhelp_map",
                "id": wh["id"],
                "path": wh.get("webhelp_display", ""),
                "score": score,
                "reasons": match_reasons[:5],
            })

    # --- Phase 2: Direct match against playwright-test-map.json ---
    for pw in pw_mappings:
        pw_menu = normalize_for_match(pw.get("menu_path", ""))
        score = 0
        match_reasons = []

        for np in normalized_paths:
            if np == pw_menu:
                score += 7
                match_reasons.append(f"menu_path: {np}")
            elif np in pw_menu or pw_menu in np:
                score += 4
                match_reasons.append(f"partial_menu: {np}")

        # Screen keywords
        screen_kws = [k.upper() for k in pw.get("screen_keywords", [])]
        for kw in normalized_keywords:
            for sk in screen_kws:
                if kw in sk or sk in kw:
                    score += 5
                    match_reasons.append(f"screen_kw: {sk}")
                    break

        # General keywords
        gen_kws = [k.upper() for k in pw.get("keywords", [])]
        for kw in normalized_keywords:
            for gk in gen_kws:
                if kw in gk or gk in kw:
                    score += 3
                    match_reasons.append(f"keyword: {gk}")
                    break

        if score >= min_score:
            pw_scores[pw["id"]] = max(pw_scores.get(pw["id"], 0), score)
            if pw["id"] not in {e["id"] for e in matched_entries}:
                matched_entries.append({
                    "source": "playwright_map",
                    "id": pw["id"],
                    "path": pw.get("menu_path", ""),
                    "score": score,
                    "reasons": match_reasons[:5],
                })

    # --- Phase 3: Direct match against component-test-map.json ---
    for comp in comp_mappings:
        score = 0
        match_reasons = []

        comp_kws = [k.upper() for k in comp.get("keywords", [])]
        for kw in normalized_keywords:
            for ck in comp_kws:
                if kw in ck or ck in kw:
                    score += 3
                    match_reasons.append(f"comp_keyword: {ck}")
                    break

        if score >= min_score:
            sa_scores[comp["id"]] = max(sa_scores.get(comp["id"], 0), score)
            if comp["id"] not in {e["id"] for e in matched_entries}:
                matched_entries.append({
                    "source": "component_map",
                    "id": comp["id"],
                    "path": f"{comp.get('category', '')} > {comp.get('description', '')}",
                    "score": score,
                    "reasons": match_reasons[:5],
                })

    # --- Resolve to spec files and XML files ---
    playwright_specs = []
    suite_xmls = []

    # Resolve Playwright specs (top 5 by score)
    pw_by_id = {m["id"]: m for m in pw_mappings}
    for pw_id, score in sorted(pw_scores.items(), key=lambda x: -x[1])[:5]:
        if pw_id in pw_by_id:
            pw = pw_by_id[pw_id]
            for spec in pw.get("spec_files", []):
                if spec not in playwright_specs:
                    playwright_specs.append(spec)

    # Resolve SuiteAutomation XMLs (top 5 by score)
    comp_by_id = {m["id"]: m for m in comp_mappings}
    for sa_id, score in sorted(sa_scores.items(), key=lambda x: -x[1])[:5]:
        if sa_id in comp_by_id:
            comp = comp_by_id[sa_id]
            xml = comp.get("xml_file", "")
            if xml and xml not in suite_xmls:
                suite_xmls.append(xml)

    # Sort matched entries by score
    matched_entries.sort(key=lambda x: -x["score"])

    return {
        "playwright_specs": playwright_specs,
        "suite_xmls": suite_xmls,
        "matched_entries": matched_entries[:10],
        "has_matches": bool(playwright_specs or suite_xmls),
    }


# ============================================================
# Test dispatchers
# ============================================================

def run_suite_automation(wi_id, title, xml_files, env="LOCAL", range_arg="0;0"):
    """Despacha execucao para suite_runner.py."""
    cmd = [
        sys.executable, str(SCRIPTS_DIR / "suite_runner.py"),
        "--wi-id", str(wi_id),
        "--title", title,
        "--env", env.upper(),
        "--range", range_arg,
    ]
    for xml in xml_files:
        cmd.extend(["--xml", xml])

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"  SuiteAutomation: {len(xml_files)} XML(s)", file=sys.stderr)
    print(f"  XMLs: {', '.join(xml_files)}", file=sys.stderr)
    print(f"  Env: {env} | Range: {range_arg}", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=1800,  # 30 min
            encoding="utf-8", errors="replace",
        )
        duration = time.time() - start
        return {
            "type": "suite_automation",
            "status": "PASS" if result.returncode == 0 else "FAIL",
            "exit_code": result.returncode,
            "duration_s": round(duration, 1),
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else "",
            "xml_files": xml_files,
        }
    except subprocess.TimeoutExpired:
        return {
            "type": "suite_automation",
            "status": "TIMEOUT",
            "exit_code": -2,
            "duration_s": round(time.time() - start, 1),
            "xml_files": xml_files,
        }
    except Exception as e:
        return {
            "type": "suite_automation",
            "status": "ERROR",
            "exit_code": -3,
            "message": str(e),
            "xml_files": xml_files,
        }


def run_playwright(wi_id, title, spec_files, env="qa", screenshot="off",
                   screenshots_dir=None):
    """Despacha execucao para playwright_runner.py."""
    cmd = [
        sys.executable, str(SCRIPTS_DIR / "playwright_runner.py"),
        "--wi-id", str(wi_id),
        "--title", title,
        "--env", env.lower(),
    ]
    for spec in spec_files:
        cmd.extend(["--spec", spec])

    # Screenshot support
    if screenshot and screenshot != "off":
        cmd.extend(["--screenshot", screenshot])
    if screenshots_dir:
        cmd.extend(["--screenshots-dir", str(screenshots_dir)])

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"  Playwright E2E: {len(spec_files)} spec(s)", file=sys.stderr)
    print(f"  Specs: {', '.join(spec_files[:5])}", file=sys.stderr)
    print(f"  Env: {env}", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=1200,  # 20 min
            encoding="utf-8", errors="replace",
        )
        duration = time.time() - start
        return {
            "type": "playwright",
            "status": "PASS" if result.returncode == 0 else "FAIL",
            "exit_code": result.returncode,
            "duration_s": round(duration, 1),
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else "",
            "spec_files": spec_files,
        }
    except subprocess.TimeoutExpired:
        return {
            "type": "playwright",
            "status": "TIMEOUT",
            "exit_code": -2,
            "duration_s": round(time.time() - start, 1),
            "spec_files": spec_files,
        }
    except Exception as e:
        return {
            "type": "playwright",
            "status": "ERROR",
            "exit_code": -3,
            "message": str(e),
            "spec_files": spec_files,
        }


# ============================================================
# Gap handler
# ============================================================

def get_webhelp_reference(extracted_paths: list, keywords: list) -> dict:
    """Busca referencia no WebHelp para gaps (cenarios sem cobertura)."""
    module_index = load_json(MODULE_INDEX_PATH)
    modules = module_index.get("modules", {})

    if not modules:
        return {"found": False}

    # Tentar encontrar modulo/submodulo mais relevante
    best_match = None
    best_score = 0

    for mod_key, mod_data in modules.items():
        mod_name = normalize_for_match(mod_data.get("display_name", mod_key))
        for sub_key, sub_data in mod_data.get("submodules", {}).items():
            sub_name = normalize_for_match(sub_data.get("display_name", sub_key))
            full_path = f"{mod_name} > {sub_name}" if sub_key != "_geral" else mod_name

            score = 0
            for ep in extracted_paths:
                ep_norm = normalize_for_match(ep.get("path", ""))
                if ep_norm in full_path or full_path in ep_norm:
                    score += 5
                for kw in keywords:
                    if kw.upper() in full_path:
                        score += 2

            if score > best_score:
                best_score = score
                articles = sub_data.get("articles", [])[:5]
                best_match = {
                    "found": True,
                    "module": mod_key,
                    "submodule": sub_key,
                    "display": f"{mod_data.get('display_name', mod_key)} > {sub_data.get('display_name', sub_key)}",
                    "article_count": sub_data.get("count", 0),
                    "sample_articles": [
                        {"id": a["id"], "title": a.get("title", "")[:100]}
                        for a in articles
                    ],
                }

    return best_match or {"found": False}


# ============================================================
# Report generation
# ============================================================

def generate_report(wi_id, title, extraction_result, match_result,
                    exec_results, webhelp_ref=None):
    """Gera relatorio estruturado."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    total_tests = len(exec_results)
    passed = sum(1 for r in exec_results if r["status"] == "PASS")
    failed = sum(1 for r in exec_results if r["status"] == "FAIL")
    errors = sum(1 for r in exec_results if r["status"] in ("ERROR", "TIMEOUT"))

    lines = []
    lines.append("=" * 70)
    lines.append("  RELATORIO DE TESTES E2E DIRECIONADOS")
    lines.append("=" * 70)
    lines.append(f"  Work Item: #{wi_id} - {title}")
    lines.append(f"  Data:      {timestamp}")
    lines.append("")

    # Paths extraidos
    lines.append("  CAMINHOS FUNCIONAIS EXTRAIDOS:")
    for p in extraction_result.get("extracted_paths", [])[:5]:
        lines.append(f"    [{p['confidence']:.0%}] {p['path']} ({p['source']})")
    lines.append("")

    # Testes selecionados
    lines.append("  TESTES SELECIONADOS:")
    for entry in match_result.get("matched_entries", [])[:5]:
        lines.append(f"    [{entry['score']}pts] {entry['id']} - {entry['path']}")
    lines.append("")

    if not exec_results:
        if webhelp_ref and webhelp_ref.get("found"):
            lines.append("  STATUS: GAP - Nenhum cenario E2E encontrado")
            lines.append(f"  Referencia WebHelp: {webhelp_ref.get('display', '')}")
            lines.append(f"  Artigos disponiveis: {webhelp_ref.get('article_count', 0)}")
        else:
            lines.append("  STATUS: GAP - Nenhum cenario E2E encontrado")
    else:
        # Resultados de execucao
        lines.append(f"  RESULTADOS: {passed} PASS | {failed} FAIL | {errors} ERROR")
        lines.append("")
        for r in exec_results:
            icon = r["status"]
            test_type = r.get("type", "unknown")
            duration = r.get("duration_s", 0)
            lines.append(f"  [{icon}] {test_type} ({duration}s)")
            if test_type == "suite_automation":
                lines.append(f"       XMLs: {', '.join(r.get('xml_files', []))}")
            elif test_type == "playwright":
                lines.append(f"       Specs: {', '.join(r.get('spec_files', [])[:3])}")
            if r["status"] == "FAIL" and r.get("stderr"):
                lines.append(f"       Erro: {r['stderr'][:200]}")

    lines.append("")
    lines.append("=" * 70)

    return "\n".join(lines)


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Targeted E2E Runner - Testes direcionados por Work Item"
    )
    parser.add_argument("--wi-id", type=int, required=True, help="ID do Work Item")
    parser.add_argument(
        "--env",
        default="qa",
        help="Ambiente: qa, sat, uat para Playwright; LOCAL, AC, RC, QA para SuiteAutomation (default: qa)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra testes que seriam executados sem executar",
    )
    parser.add_argument(
        "--output",
        help="Caminho para salvar resultado JSON",
    )
    parser.add_argument(
        "--from-file",
        help="Arquivo JSON com campos da WI (alternativa a buscar do ADO)",
    )
    parser.add_argument(
        "--suite-only",
        action="store_true",
        help="Executar apenas SuiteAutomation (XML/JAR)",
    )
    parser.add_argument(
        "--playwright-only",
        action="store_true",
        help="Executar apenas Playwright E2E",
    )
    parser.add_argument(
        "--range",
        default="0;0",
        help='Range de testes SuiteAutomation (ex: "1;3"). Default: "0;0" (todos)',
    )
    parser.add_argument(
        "--screenshots",
        choices=["on", "only-on-failure", "off"],
        default="off",
        help="Capturar screenshots durante Playwright: on, only-on-failure, off (default)",
    )
    parser.add_argument(
        "--upload-evidence",
        action="store_true",
        help="Apos execucao, fazer upload de screenshots e resultados como evidencia na WI do ADO",
    )

    args = parser.parse_args()

    # ---- Step 1: Fetch WI data ----
    print(f"\n[1/4] Obtendo dados da WI #{args.wi_id}...", file=sys.stderr)
    if args.from_file:
        wi_data = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
    else:
        wi_data = fetch_wi_fields(args.wi_id)

    title = wi_data.get("title", f"WI-{args.wi_id}")
    print(f"  Titulo: {title}", file=sys.stderr)

    # ---- Step 2: Extract functional paths ----
    print(f"\n[2/4] Extraindo caminhos funcionais...", file=sys.stderr)
    extraction = extract_functional_paths(wi_data)

    paths = extraction.get("extracted_paths", [])
    keywords = extraction.get("keywords_detected", [])
    print(f"  Paths encontrados: {len(paths)}", file=sys.stderr)
    for p in paths[:3]:
        print(f"    [{p['confidence']:.0%}] {p['path']}", file=sys.stderr)

    # ---- Step 3: Match to E2E tests ----
    print(f"\n[3/4] Buscando cenarios E2E correspondentes...", file=sys.stderr)
    match_result = find_matching_tests(paths, keywords)

    pw_specs = match_result["playwright_specs"]
    sa_xmls = match_result["suite_xmls"]

    # Apply filters
    if args.suite_only:
        pw_specs = []
    if args.playwright_only:
        sa_xmls = []

    print(f"  Playwright specs: {len(pw_specs)}", file=sys.stderr)
    print(f"  SuiteAutomation XMLs: {len(sa_xmls)}", file=sys.stderr)
    for entry in match_result["matched_entries"][:5]:
        print(f"    [{entry['score']}pts] {entry['id']}", file=sys.stderr)

    # ---- Dry run: show results and exit ----
    if args.dry_run:
        result = {
            "wi_id": args.wi_id,
            "title": title,
            "mode": "dry_run",
            "extraction": extraction,
            "matching": match_result,
            "would_execute": {
                "playwright_specs": pw_specs,
                "suite_xmls": sa_xmls,
            },
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(
                json.dumps(result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        sys.exit(0 if match_result["has_matches"] else 2)

    # ---- Step 4: Execute tests ----
    if not match_result["has_matches"]:
        print(f"\n[4/4] GAP: Nenhum cenario E2E encontrado!", file=sys.stderr)
        webhelp_ref = get_webhelp_reference(paths, keywords)
        if webhelp_ref.get("found"):
            print(f"  Referencia WebHelp: {webhelp_ref['display']}", file=sys.stderr)
            print(f"  Artigos: {webhelp_ref['article_count']}", file=sys.stderr)

        report = generate_report(args.wi_id, title, extraction, match_result, [],
                                 webhelp_ref=webhelp_ref)
        print(report)

        result = {
            "wi_id": args.wi_id,
            "title": title,
            "status": "GAP",
            "extraction": extraction,
            "matching": match_result,
            "webhelp_reference": webhelp_ref,
            "exec_results": [],
        }
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(
                json.dumps(result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        sys.exit(2)

    print(f"\n[4/4] Executando testes direcionados...", file=sys.stderr)
    exec_results = []

    # Run SuiteAutomation
    if sa_xmls:
        sa_env = args.env.upper() if args.env.upper() in ("LOCAL", "AC", "RC", "QA") else "LOCAL"
        sa_result = run_suite_automation(args.wi_id, title, sa_xmls, env=sa_env,
                                         range_arg=args.range)
        exec_results.append(sa_result)
        print(f"  SuiteAutomation: {sa_result['status']} ({sa_result.get('duration_s', 0)}s)",
              file=sys.stderr)

    # Run Playwright
    screenshots_dir = None
    if pw_specs:
        pw_env = args.env.lower() if args.env.lower() in ("qa", "sat", "uat", "prod", "dev2") else "qa"
        # Screenshots dir (used if --screenshots or --upload-evidence)
        if args.screenshots != "off" or args.upload_evidence:
            screenshots_dir = Path(f"tests/{args.wi_id}/screenshots")
            screenshots_dir.mkdir(parents=True, exist_ok=True)
            # Default to "on" if upload-evidence requested but screenshots not explicitly set
            screenshot_mode = args.screenshots if args.screenshots != "off" else "on"
        else:
            screenshot_mode = args.screenshots

        pw_result = run_playwright(args.wi_id, title, pw_specs, env=pw_env,
                                   screenshot=screenshot_mode,
                                   screenshots_dir=str(screenshots_dir) if screenshots_dir else None)
        exec_results.append(pw_result)
        print(f"  Playwright: {pw_result['status']} ({pw_result.get('duration_s', 0)}s)",
              file=sys.stderr)

    # ---- Generate report ----
    report = generate_report(args.wi_id, title, extraction, match_result, exec_results)
    print(report)

    # Determine overall status
    has_failures = any(r["status"] in ("FAIL", "ERROR", "TIMEOUT") for r in exec_results)
    overall_status = "FAIL" if has_failures else "PASS"

    result = {
        "wi_id": args.wi_id,
        "title": title,
        "status": overall_status,
        "extraction": extraction,
        "matching": match_result,
        "exec_results": exec_results,
    }

    output_path = args.output or f"tests/{args.wi_id}/targeted_e2e_results.json"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nResultado salvo em: {output_path}", file=sys.stderr)

    # ---- Step 5: Upload evidence to ADO ----
    if args.upload_evidence and screenshots_dir and screenshots_dir.exists():
        print(f"\n[5/5] Uploading evidencia para WI #{args.wi_id}...", file=sys.stderr)
        upload_cmd = [
            sys.executable, str(SCRIPTS_DIR / "ado_evidence_uploader.py"),
            "--wi-id", str(args.wi_id),
            "--screenshots-dir", str(screenshots_dir),
            "--results-json", output_path,
        ]
        try:
            upload_result = subprocess.run(
                upload_cmd,
                capture_output=True, text=True,
                timeout=120,
                encoding="utf-8", errors="replace",
            )
            if upload_result.returncode == 0:
                print(f"  Upload evidencia: OK", file=sys.stderr)
            else:
                print(f"  Upload evidencia: FALHOU (exit {upload_result.returncode})",
                      file=sys.stderr)
                if upload_result.stderr:
                    print(f"  {upload_result.stderr[:300]}", file=sys.stderr)
        except Exception as e:
            print(f"  Upload evidencia: ERRO ({e})", file=sys.stderr)
    elif args.upload_evidence:
        print(f"\n[5/5] Upload evidencia: SKIP (sem screenshots)", file=sys.stderr)

    sys.exit(1 if has_failures else 0)


if __name__ == "__main__":
    main()
