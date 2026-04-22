#!/usr/bin/env python3
"""
Test Coverage Reporter - Gera relatorio de cobertura com veredicto GO/NO-GO.

Apos todos os testes de uma WI, analisa evidencias e gera relatorio de
cobertura cruzando cenarios planejados vs executados, tabelas testadas
vs impactadas, e procedures exercitadas.

Uso:
    python scripts/test_coverage_reporter.py \
        --wi-id 1058668 \
        --tests-dir tests/1058668/ \
        --repo "$TAXONE_DW_REPO" \
        --branch MFS1058668
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

from env_loader import get_repo_path
DEFAULT_REPO = get_repo_path("TAXONE_DW_REPO")
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


# ---------------------------------------------------------------------------
# Evidence collectors
# ---------------------------------------------------------------------------

def collect_test_results(tests_dir: Path) -> dict:
    """Coleta resultados de test_results.txt."""
    results = {"pass": 0, "fail": 0, "skip": 0, "scenarios": []}
    path = tests_dir / "test_results.txt"
    if not path.exists():
        return results

    content = path.read_text(encoding="utf-8", errors="replace")
    for line in content.splitlines():
        line_upper = line.upper().strip()
        if not line_upper:
            continue
        if "PASS" in line_upper or "OK" in line_upper:
            results["pass"] += 1
            results["scenarios"].append({"line": line.strip(), "status": "pass"})
        elif "FAIL" in line_upper or "FALHA" in line_upper or "ERRO" in line_upper:
            results["fail"] += 1
            results["scenarios"].append({"line": line.strip(), "status": "fail"})
        elif "SKIP" in line_upper or "PULAR" in line_upper:
            results["skip"] += 1
            results["scenarios"].append({"line": line.strip(), "status": "skip"})

    return results


def collect_regression_report(tests_dir: Path) -> dict:
    """Coleta dados do regression_report.txt."""
    results = {"found": False, "pass": 0, "fail": 0, "tables_tested": set()}
    path = tests_dir / "regression_report.txt"
    if not path.exists():
        return results

    results["found"] = True
    content = path.read_text(encoding="utf-8", errors="replace")

    for line in content.splitlines():
        line_upper = line.upper().strip()
        if "PASS" in line_upper or "OK" in line_upper:
            results["pass"] += 1
        elif "FAIL" in line_upper or "FALHA" in line_upper:
            results["fail"] += 1

    # Extract table names from SQL content
    table_pattern = re.compile(
        r"\b((?:SAFX|X|EST|ICT|PRT|TMP|DWT|CBT|APT|LOG|DET|ITEM)\w+)\b",
        re.IGNORECASE
    )
    for m in table_pattern.finditer(content):
        results["tables_tested"].add(m.group(1).upper())

    return results


def collect_sql_scripts(tests_dir: Path) -> dict:
    """Coleta tabelas e procedures dos scripts SQL de teste."""
    results = {"tables": set(), "procedures": set(), "script_count": 0}

    for sql_file in tests_dir.glob("*.sql"):
        results["script_count"] += 1
        content = sql_file.read_text(encoding="utf-8", errors="replace")

        # Extract tables
        table_pattern = re.compile(
            r"\bFROM\s+(\w+)|\bJOIN\s+(\w+)|\bINTO\s+(\w+)|\bUPDATE\s+(\w+)",
            re.IGNORECASE
        )
        for m in table_pattern.finditer(content):
            for g in m.groups():
                if g:
                    results["tables"].add(g.upper())

        # Extract procedure/package calls
        proc_pattern = re.compile(
            r"\b(PKG_\w+|SAF_\w+|OL_\w+|LIB_\w+|\w+_FPROC|\w+_CPROC|PRC_\w+|FNC_\w+)\b",
            re.IGNORECASE
        )
        for m in proc_pattern.finditer(content):
            results["procedures"].add(m.group(1).upper())

    return results


def collect_exploratory_report(tests_dir: Path) -> dict:
    """Coleta dados do exploratory_report.md."""
    results = {"found": False, "screenshots": 0}
    path = tests_dir / "exploratory_report.md"
    if not path.exists():
        return results

    results["found"] = True
    content = path.read_text(encoding="utf-8", errors="replace")

    # Count screenshots
    img_pattern = re.compile(r"!\[.*?\]\(.*?\)|<img\s", re.IGNORECASE)
    results["screenshots"] = len(img_pattern.findall(content))

    return results


def collect_suite_results(tests_dir: Path) -> dict:
    """Coleta resultados de SuiteAutomation (XMLs executados)."""
    results = {"found": False, "xmls_executed": [], "tables_tested": set()}

    # Look for suite output files
    for f in tests_dir.iterdir():
        if f.name.startswith("suite_") and f.suffix in (".txt", ".log", ".xml"):
            results["found"] = True
            content = f.read_text(encoding="utf-8", errors="replace")
            # Extract XML names
            xml_pattern = re.compile(r"CT_\w+\.xml|[\w/]+\.xml", re.IGNORECASE)
            for m in xml_pattern.finditer(content):
                results["xmls_executed"].append(m.group(0))

    return results


# ---------------------------------------------------------------------------
# Coverage analysis
# ---------------------------------------------------------------------------

def get_changed_objects(repo: str, branch: str) -> tuple[set[str], set[str]]:
    """Retorna tabelas e procedures alterados no branch."""
    tables = set()
    procedures = set()

    if not repo or not branch:
        return tables, procedures

    cmd = ["git", "-C", repo, "diff", "--unified=0", f"DEV...{branch}"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60,
        )
        diff = result.stdout
    except Exception:
        return tables, procedures

    table_pattern = re.compile(
        r"\b((?:SAFX|X|EST|ICT|PRT|TMP|DWT|CBT|APT|LOG|DET|ITEM)\w+)\b",
        re.IGNORECASE
    )
    proc_pattern = re.compile(
        r"\b(PKG_\w+|SAF_\w+|OL_\w+|LIB_\w+|\w+_FPROC|\w+_CPROC|PRC_\w+|FNC_\w+)\b",
        re.IGNORECASE
    )

    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            for m in table_pattern.finditer(line):
                tables.add(m.group(1).upper())
            for m in proc_pattern.finditer(line):
                procedures.add(m.group(1).upper())

    return tables, procedures


def analyze_coverage(
    wi_id: str,
    tests_dir: Path,
    repo: str = "",
    branch: str = "",
) -> dict:
    """Analisa cobertura de testes e gera veredicto."""

    # Load test_scenarios.json (enriched)
    scenarios_path = tests_dir / "test_scenarios.json"
    scenarios_data = {}
    scenarios = []
    if scenarios_path.exists():
        with open(scenarios_path, "r", encoding="utf-8") as f:
            scenarios_data = json.load(f)
        scenarios = scenarios_data.get("scenarios", [])

    # Collect all evidence
    test_results = collect_test_results(tests_dir)
    regression = collect_regression_report(tests_dir)
    sql_scripts = collect_sql_scripts(tests_dir)
    exploratory = collect_exploratory_report(tests_dir)
    suite = collect_suite_results(tests_dir)

    # Get changed objects from git
    changed_tables, changed_procedures = get_changed_objects(repo, branch)

    # --- Dimension 1: Scenarios covered ---
    scenarios_by_priority = {"critical": [], "high": [], "medium": [], "low": []}
    for s in scenarios:
        priority = s.get("priority", "medium")
        scenarios_by_priority.setdefault(priority, []).append(s)

    # Check which scenarios have evidence
    total_evidence = test_results["pass"] + test_results["fail"] + test_results["skip"]
    scenarios_covered = min(total_evidence, len(scenarios)) if scenarios else total_evidence
    scenarios_total = max(len(scenarios), 1)

    critical_total = len(scenarios_by_priority.get("critical", []))
    high_total = len(scenarios_by_priority.get("high", []))
    critical_high_total = critical_total + high_total

    # Estimate critical/high coverage based on ratio
    coverage_ratio = scenarios_covered / scenarios_total if scenarios_total > 0 else 0
    critical_high_covered = min(
        int(coverage_ratio * critical_high_total),
        critical_high_total
    )

    # --- Dimension 2: Tables tested ---
    all_tested_tables = sql_scripts["tables"] | regression.get("tables_tested", set())
    tables_not_tested = changed_tables - all_tested_tables if changed_tables else set()

    # --- Dimension 3: Procedures exercised ---
    procedures_tested = sql_scripts["procedures"]
    procedures_not_tested = changed_procedures - procedures_tested if changed_procedures else set()

    # --- Dimension 4: Developer suggestions covered ---
    dev_suggestions = []
    for s in scenarios:
        if s.get("source") == "developer_suggestion":
            dev_suggestions.append(s)
    dev_suggestions_covered = min(len(dev_suggestions), scenarios_covered)

    # --- Verdict ---
    has_critical_gap = critical_total > 0 and critical_high_covered < critical_total
    procedure_coverage = (
        (len(changed_procedures) - len(procedures_not_tested)) / len(changed_procedures)
        if changed_procedures else 1.0
    )
    critical_high_pct = (
        critical_high_covered / critical_high_total
        if critical_high_total > 0 else 1.0
    )

    if has_critical_gap or (test_results["fail"] > 0):
        verdict = "NO-GO"
        verdict_reason = []
        if has_critical_gap:
            verdict_reason.append(
                f"Cenario(s) critical sem evidencia: {critical_total - critical_high_covered}"
            )
        if test_results["fail"] > 0:
            verdict_reason.append(f"Testes falhando: {test_results['fail']}")
        if procedure_coverage < 0.5:
            verdict_reason.append(
                f">50% procedures sem teste: {len(procedures_not_tested)}/{len(changed_procedures)}"
            )
    elif critical_high_pct >= 0.8 and procedure_coverage >= 0.5:
        if critical_high_pct >= 1.0 and procedure_coverage >= 0.8:
            verdict = "GO"
            verdict_reason = ["Todos cenarios critical/high cobertos, procedures exercitadas"]
        else:
            verdict = "CONDITIONAL GO"
            verdict_reason = []
            if critical_high_pct < 1.0:
                verdict_reason.append(
                    f"Cobertura critical/high: {critical_high_pct:.0%} (>=80%)"
                )
            if procedure_coverage < 0.8:
                verdict_reason.append(
                    f"Procedures testadas: {procedure_coverage:.0%}"
                )
    else:
        verdict = "NO-GO"
        verdict_reason = [
            f"Cobertura insuficiente: critical/high={critical_high_pct:.0%}, "
            f"procedures={procedure_coverage:.0%}"
        ]

    report = {
        "work_item_id": wi_id,
        "generated_at": datetime.now().isoformat(),
        "generated_by": "test_coverage_reporter",
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "dimensions": {
            "scenarios": {
                "total": len(scenarios),
                "covered": scenarios_covered,
                "by_priority": {
                    p: len(s) for p, s in scenarios_by_priority.items()
                },
                "critical_high_covered": critical_high_covered,
                "critical_high_total": critical_high_total,
            },
            "tables": {
                "changed": sorted(changed_tables),
                "tested": sorted(all_tested_tables),
                "not_tested": sorted(tables_not_tested),
                "coverage_pct": (
                    f"{(len(changed_tables) - len(tables_not_tested)) / len(changed_tables):.0%}"
                    if changed_tables else "N/A"
                ),
            },
            "procedures": {
                "changed": sorted(changed_procedures),
                "tested": sorted(procedures_tested),
                "not_tested": sorted(procedures_not_tested),
                "coverage_pct": f"{procedure_coverage:.0%}",
            },
            "developer_suggestions": {
                "total": len(dev_suggestions),
                "covered": dev_suggestions_covered,
            },
        },
        "evidence": {
            "test_results": {
                "pass": test_results["pass"],
                "fail": test_results["fail"],
                "skip": test_results["skip"],
            },
            "regression_report": regression["found"],
            "sql_scripts": sql_scripts["script_count"],
            "exploratory_report": exploratory["found"],
            "exploratory_screenshots": exploratory["screenshots"],
            "suite_automation": suite["found"],
            "suite_xmls": suite.get("xmls_executed", []),
        },
        "suite_recommendations": scenarios_data.get("suite_recommendations", []),
    }

    return report


def format_markdown(report: dict) -> str:
    """Formata relatorio como Markdown para Discussion comment."""
    verdict = report["verdict"]
    verdict_emoji = {"GO": "✅", "CONDITIONAL GO": "⚠️", "NO-GO": "❌"}.get(verdict, "❓")
    dims = report["dimensions"]
    evidence = report["evidence"]

    lines = [
        f"## Relatorio de Cobertura de Testes - WI #{report['work_item_id']}",
        f"",
        f"### Veredicto: {verdict_emoji} {verdict}",
    ]

    for reason in report.get("verdict_reason", []):
        lines.append(f"- {reason}")

    lines.extend([
        f"",
        f"### Cenarios",
        f"| Dimensao | Valor |",
        f"|----------|-------|",
        f"| Total | {dims['scenarios']['total']} |",
        f"| Cobertos | {dims['scenarios']['covered']} |",
        f"| Critical/High total | {dims['scenarios']['critical_high_total']} |",
        f"| Critical/High cobertos | {dims['scenarios']['critical_high_covered']} |",
    ])

    for p, count in dims["scenarios"]["by_priority"].items():
        if count > 0:
            lines.append(f"| {p.capitalize()} | {count} |")

    lines.extend([
        f"",
        f"### Tabelas",
        f"- Alteradas: {len(dims['tables']['changed'])}",
        f"- Testadas: {len(dims['tables']['tested'])}",
        f"- Sem teste: {len(dims['tables']['not_tested'])}",
        f"- Cobertura: {dims['tables']['coverage_pct']}",
    ])

    if dims["tables"]["not_tested"]:
        lines.append(f"- **Gaps:** {', '.join(dims['tables']['not_tested'][:10])}")

    lines.extend([
        f"",
        f"### Procedures",
        f"- Alteradas: {len(dims['procedures']['changed'])}",
        f"- Testadas: {len(dims['procedures']['tested'])}",
        f"- Sem teste: {len(dims['procedures']['not_tested'])}",
        f"- Cobertura: {dims['procedures']['coverage_pct']}",
    ])

    if dims["procedures"]["not_tested"]:
        lines.append(f"- **Gaps:** {', '.join(dims['procedures']['not_tested'][:10])}")

    lines.extend([
        f"",
        f"### Evidencias",
        f"| Tipo | Status |",
        f"|------|--------|",
        f"| Test results | PASS={evidence['test_results']['pass']} FAIL={evidence['test_results']['fail']} SKIP={evidence['test_results']['skip']} |",
        f"| Regression report | {'✅' if evidence['regression_report'] else '❌'} |",
        f"| SQL scripts | {evidence['sql_scripts']} |",
        f"| Exploratory | {'✅' if evidence['exploratory_report'] else '❌'} ({evidence['exploratory_screenshots']} screenshots) |",
        f"| SuiteAutomation | {'✅' if evidence['suite_automation'] else '❌'} |",
    ])

    if evidence.get("suite_xmls"):
        lines.append(f"| Suite XMLs | {', '.join(evidence['suite_xmls'][:5])} |")

    suites = report.get("suite_recommendations", [])
    if suites:
        lines.extend([
            f"",
            f"### Suites Recomendadas (nao executadas?)",
        ])
        for s in suites:
            executed = "✅" if s["xml_file"] in evidence.get("suite_xmls", []) else "⬜"
            lines.append(f"- {executed} [{s['score']}pts] {s['id']} - {s['xml_file']}")

    lines.extend([
        f"",
        f"---",
        f"*Gerado por test_coverage_reporter em {report['generated_at'][:19]}*",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Test Coverage Reporter - Relatorio GO/NO-GO de cobertura de testes"
    )
    parser.add_argument(
        "--wi-id", type=str, required=True,
        help="ID do work item"
    )
    parser.add_argument(
        "--tests-dir", type=str, default="",
        help="Diretorio de testes (default: tests/{wi-id}/)"
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
        "--format", choices=["json", "markdown"], default="json",
        help="Formato de saida (default: json)"
    )

    args = parser.parse_args()

    tests_dir = Path(args.tests_dir) if args.tests_dir else PROJECT_ROOT / "tests" / str(args.wi_id)

    if not tests_dir.exists():
        print(f"[WARN] Diretorio de testes nao encontrado: {tests_dir}", file=sys.stderr)
        print(f"[INFO] Criando diretorio vazio para gerar relatorio baseline", file=sys.stderr)
        tests_dir.mkdir(parents=True, exist_ok=True)

    report = analyze_coverage(
        wi_id=args.wi_id,
        tests_dir=tests_dir,
        repo=args.repo,
        branch=args.branch,
    )

    # Save JSON report
    report_path = tests_dir / "coverage_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    print(f"[OK] Salvo: {report_path}", file=sys.stderr)

    # Output
    if args.format == "json":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        md = format_markdown(report)
        # Also save markdown
        md_path = tests_dir / "coverage_report.md"
        md_path.write_text(md, encoding="utf-8")
        print(f"[OK] Salvo: {md_path}", file=sys.stderr)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(md)


if __name__ == "__main__":
    main()
