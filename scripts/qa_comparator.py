#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QA Comparator - Compara resultados Before (baseline) vs After (QA execution).

Consome artefatos do Dev Pipeline (baseline/) e QA Pipeline (qa/) para
gerar relatorio de diferencas categorizado.

Categorias:
  - IDENTICAL: Sem diferenca
  - NEW_PASS: Antes falhava, agora passa (esperado para bug fixes)
  - REGRESSION: Antes passava, agora falha
  - PRE_EXISTING: Ja falhava antes e continua falhando
  - EXPECTED_CHANGE: Diferenca alinhada com o fix/feature
  - UNEXPECTED_CHANGE: Diferenca nao explicada

Uso:
  python qa_comparator.py --wi-id 1078744 \
    --before-dir tests/1078744/baseline/ \
    --after-dir tests/1078744/qa/ \
    --output tests/1078744/qa/comparison_report.json

  python qa_comparator.py --wi-id 1078744 --sql-only
  python qa_comparator.py --wi-id 1078744 --suite-only

  # Multi-ambiente (indicar qual ambiente no relatorio):
  python qa_comparator.py --wi-id 1078744 --env-label QA \
    --before-dir tests/1078744/baseline_qa/ \
    --after-dir tests/1078744/after_qa/
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def compare_sql_results(before_dir, after_dir):
    """Compara resultados SQL: before vs after.

    Returns:
        dict com comparacoes por cenario
    """
    before_file = before_dir / "sql_results_before.json"
    after_file = after_dir / "sql_results_after.json"

    if not before_file.exists():
        return {"status": "SKIP", "reason": "no_baseline_sql"}
    if not after_file.exists():
        return {"status": "SKIP", "reason": "no_after_sql"}

    before = json.loads(before_file.read_text(encoding="utf-8"))
    after = json.loads(after_file.read_text(encoding="utf-8"))

    comparisons = {}
    all_ids = set(list(before.keys()) + list(after.keys()))

    for scenario_id in sorted(all_ids):
        b = before.get(scenario_id, {})
        a = after.get(scenario_id, {})

        b_status = b.get("status", "MISSING")
        a_status = a.get("status", "MISSING")

        # Ambos SKIP — nada a comparar
        if b_status == "SKIP" and a_status == "SKIP":
            comparisons[scenario_id] = {
                "category": "SKIP",
                "reason": "Ambos SKIP (bind variables ou cenario nao executavel)"
            }
            continue

        # Antes MISSING, agora existe — cenario novo
        if b_status == "MISSING":
            comparisons[scenario_id] = {
                "category": "NEW_SCENARIO",
                "after_status": a_status,
                "after_rows": a.get("row_count", 0)
            }
            continue

        # Agora MISSING — cenario removido
        if a_status == "MISSING":
            comparisons[scenario_id] = {
                "category": "REMOVED_SCENARIO",
                "before_status": b_status
            }
            continue

        # Antes ERROR, agora OK — NEW_PASS
        if b_status == "ERROR" and a_status == "OK":
            comparisons[scenario_id] = {
                "category": "NEW_PASS",
                "before_error": b.get("error", ""),
                "after_rows": a.get("row_count", 0),
                "note": "Query que falhava agora funciona"
            }
            continue

        # Antes OK, agora ERROR — REGRESSION
        if b_status == "OK" and a_status == "ERROR":
            comparisons[scenario_id] = {
                "category": "REGRESSION",
                "before_rows": b.get("row_count", 0),
                "after_error": a.get("error", ""),
                "severity": "HIGH"
            }
            continue

        # Ambos ERROR — PRE_EXISTING
        if b_status == "ERROR" and a_status == "ERROR":
            comparisons[scenario_id] = {
                "category": "PRE_EXISTING",
                "before_error": b.get("error", ""),
                "after_error": a.get("error", "")
            }
            continue

        # Ambos OK — comparar dados
        if b_status == "OK" and a_status == "OK":
            b_rows = b.get("rows", [])
            a_rows = a.get("rows", [])
            b_cols = b.get("columns", [])
            a_cols = a.get("columns", [])

            if b_rows == a_rows and b_cols == a_cols:
                comparisons[scenario_id] = {
                    "category": "IDENTICAL",
                    "row_count": b.get("row_count", 0)
                }
            else:
                # Dados mudaram — precisa categorizar
                diff_details = _diff_sql_rows(b_cols, b_rows, a_cols, a_rows)
                comparisons[scenario_id] = {
                    "category": "CHANGED",
                    "before_rows": b.get("row_count", 0),
                    "after_rows": a.get("row_count", 0),
                    "columns_changed": b_cols != a_cols,
                    "diff": diff_details
                }
            continue

        # Outros casos
        comparisons[scenario_id] = {
            "category": "UNKNOWN",
            "before_status": b_status,
            "after_status": a_status
        }

    # Gerar sumario
    categories = {}
    for c in comparisons.values():
        cat = c.get("category", "UNKNOWN")
        categories[cat] = categories.get(cat, 0) + 1

    return {
        "status": "OK",
        "total_scenarios": len(comparisons),
        "summary": categories,
        "comparisons": comparisons
    }


def _diff_sql_rows(b_cols, b_rows, a_cols, a_rows):
    """Gera diff detalhado entre dois conjuntos de rows."""
    diff = {
        "rows_added": 0,
        "rows_removed": 0,
        "rows_changed": 0,
        "sample_changes": []
    }

    # Converter rows para sets de tuples para comparacao
    b_set = set(tuple(str(v) for v in row) for row in b_rows)
    a_set = set(tuple(str(v) for v in row) for row in a_rows)

    added = a_set - b_set
    removed = b_set - a_set

    diff["rows_added"] = len(added)
    diff["rows_removed"] = len(removed)
    diff["rows_changed"] = max(len(added), len(removed))

    # Amostras (max 3)
    for i, row in enumerate(list(added)[:3]):
        diff["sample_changes"].append({
            "type": "ADDED",
            "row": list(row)
        })
    for i, row in enumerate(list(removed)[:3]):
        diff["sample_changes"].append({
            "type": "REMOVED",
            "row": list(row)
        })

    return diff


def compare_suite_obtido(before_dir, after_dir):
    """Compara arquivos Obtido/ do baseline vs QA execution.

    Returns:
        dict com comparacoes por arquivo
    """
    before_obtido = before_dir / "obtido_before"
    after_obtido = after_dir / "obtido_after"

    if not before_obtido.exists():
        return {"status": "SKIP", "reason": "no_baseline_obtido"}
    if not after_obtido.exists():
        return {"status": "SKIP", "reason": "no_after_obtido"}

    comparisons = {}
    all_files = set()

    # Coletar todos os arquivos relativos
    for f in before_obtido.rglob("*"):
        if f.is_file():
            all_files.add(f.relative_to(before_obtido))
    for f in after_obtido.rglob("*"):
        if f.is_file():
            all_files.add(f.relative_to(after_obtido))

    for rel_path in sorted(all_files):
        b_file = before_obtido / rel_path
        a_file = after_obtido / rel_path

        key = str(rel_path).replace("\\", "/")

        if not b_file.exists():
            comparisons[key] = {
                "category": "NEW_FILE",
                "note": "Arquivo gerado apenas apos alteracao"
            }
            continue

        if not a_file.exists():
            comparisons[key] = {
                "category": "MISSING_AFTER",
                "severity": "HIGH",
                "note": "Arquivo existia no baseline mas nao foi gerado apos alteracao"
            }
            continue

        # Comparar conteudo (ISO-8859-1 para suite files)
        try:
            b_content = b_file.read_text(encoding="latin-1", errors="replace")
            a_content = a_file.read_text(encoding="latin-1", errors="replace")

            b_lines = [line.rstrip() for line in b_content.splitlines() if line.strip()]
            a_lines = [line.rstrip() for line in a_content.splitlines() if line.strip()]

            if b_lines == a_lines:
                comparisons[key] = {
                    "category": "IDENTICAL",
                    "lines": len(b_lines)
                }
            else:
                # Encontrar diferencas
                diff_details = _diff_file_lines(b_lines, a_lines)
                comparisons[key] = {
                    "category": "CHANGED",
                    "before_lines": len(b_lines),
                    "after_lines": len(a_lines),
                    "diff": diff_details
                }

        except Exception as e:
            comparisons[key] = {
                "category": "ERROR",
                "error": str(e)
            }

    # Sumario
    categories = {}
    for c in comparisons.values():
        cat = c.get("category", "UNKNOWN")
        categories[cat] = categories.get(cat, 0) + 1

    return {
        "status": "OK",
        "total_files": len(comparisons),
        "summary": categories,
        "comparisons": comparisons
    }


def _diff_file_lines(b_lines, a_lines):
    """Gera diff detalhado entre linhas de dois arquivos."""
    diff = {
        "lines_added": 0,
        "lines_removed": 0,
        "first_diff_line": -1,
        "sample_diffs": []
    }

    # Encontrar primeira diferenca
    for i, (b, a) in enumerate(zip(b_lines, a_lines)):
        if b != a:
            diff["first_diff_line"] = i + 1
            diff["sample_diffs"].append({
                "line": i + 1,
                "before": b[:200],
                "after": a[:200]
            })
            if len(diff["sample_diffs"]) >= 3:
                break

    if diff["first_diff_line"] == -1 and len(b_lines) != len(a_lines):
        diff["first_diff_line"] = min(len(b_lines), len(a_lines)) + 1

    # Contagem via sets
    b_set = set(b_lines)
    a_set = set(a_lines)
    diff["lines_added"] = len(a_set - b_set)
    diff["lines_removed"] = len(b_set - a_set)

    return diff


def compare_plsql_objects(before_dir, after_dir):
    """Compara estado dos objetos PL/SQL: before vs after.

    Returns:
        dict com mudancas detectadas
    """
    before_file = before_dir / "plsql_objects_before.json"
    after_file = after_dir / "plsql_objects_after.json"

    if not before_file.exists():
        return {"status": "SKIP", "reason": "no_baseline_objects"}
    if not after_file.exists():
        return {"status": "SKIP", "reason": "no_after_objects"}

    before = json.loads(before_file.read_text(encoding="utf-8"))
    after = json.loads(after_file.read_text(encoding="utf-8"))

    changes = {}
    all_keys = set(list(before.keys()) + list(after.keys()))

    for key in sorted(all_keys):
        b = before.get(key, {})
        a = after.get(key, {})

        if not b:
            changes[key] = {"category": "NEW_OBJECT", "after": a}
        elif not a:
            changes[key] = {"category": "REMOVED_OBJECT", "before": b}
        elif b.get("checksum") != a.get("checksum"):
            changes[key] = {
                "category": "MODIFIED",
                "before_ddl": b.get("last_ddl_time"),
                "after_ddl": a.get("last_ddl_time"),
                "before_status": b.get("status"),
                "after_status": a.get("status")
            }
        elif b.get("status") != a.get("status"):
            changes[key] = {
                "category": "STATUS_CHANGED",
                "before_status": b.get("status"),
                "after_status": a.get("status")
            }
        else:
            changes[key] = {"category": "UNCHANGED"}

    return {
        "status": "OK",
        "total_objects": len(changes),
        "changed": sum(1 for c in changes.values() if c["category"] != "UNCHANGED"),
        "changes": changes
    }


def generate_comparison_report(wi_id, sql_comp, suite_comp, objects_comp, output_dir,
                               env_label=""):
    """Gera relatorio consolidado de comparacao."""
    report = {
        "version": "1.1.0",
        "wi_id": wi_id,
        "generated_at": datetime.now().isoformat(),
        "environment": env_label or "N/A",
        "sql_comparison": sql_comp,
        "suite_comparison": suite_comp,
        "plsql_objects_comparison": objects_comp
    }

    # Determinar veredicto geral
    has_regression = False
    has_changes = False

    # Check SQL regressions
    if sql_comp.get("status") == "OK":
        for sc in sql_comp.get("comparisons", {}).values():
            if sc.get("category") == "REGRESSION":
                has_regression = True
            if sc.get("category") in ("CHANGED", "NEW_PASS"):
                has_changes = True

    # Check suite regressions
    if suite_comp.get("status") == "OK":
        for fc in suite_comp.get("comparisons", {}).values():
            if fc.get("category") == "MISSING_AFTER":
                has_regression = True
            if fc.get("category") == "CHANGED":
                has_changes = True

    # Check PL/SQL object issues
    if objects_comp.get("status") == "OK":
        for oc in objects_comp.get("changes", {}).values():
            if oc.get("after_status") == "INVALID":
                has_regression = True

    if has_regression:
        report["verdict"] = "REGRESSION_DETECTED"
        report["verdict_description"] = "Regressoes detectadas — requer investigacao"
    elif has_changes:
        report["verdict"] = "CHANGES_DETECTED"
        report["verdict_description"] = "Mudancas detectadas — verificar se sao esperadas"
    else:
        report["verdict"] = "NO_CHANGES"
        report["verdict_description"] = "Nenhuma diferenca detectada entre Before e After"

    # Salvar JSON
    output_file = output_dir / "comparison_report.json"
    output_file.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8"
    )

    # Gerar markdown summary
    md = generate_markdown_summary(report)
    md_file = output_dir / "comparison_report.md"
    md_file.write_text(md, encoding="utf-8")

    print(f"  Relatorio salvo: {output_file}", file=sys.stderr)
    print(f"  Resumo MD salvo: {md_file}", file=sys.stderr)

    return report


def generate_markdown_summary(report):
    """Gera resumo markdown legivel do relatorio de comparacao."""
    lines = []
    wi_id = report.get("wi_id", "?")
    verdict = report.get("verdict", "UNKNOWN")
    verdict_desc = report.get("verdict_description", "")

    env_label = report.get("environment", "")
    env_suffix = f" [{env_label}]" if env_label and env_label != "N/A" else ""
    lines.append(f"# Relatorio de Comparacao Before vs After{env_suffix} — WI #{wi_id}")
    lines.append(f"")
    lines.append(f"**Data:** {report.get('generated_at', '')}")
    if env_label and env_label != "N/A":
        lines.append(f"**Ambiente:** {env_label}")
    lines.append(f"**Veredicto:** **{verdict}** — {verdict_desc}")
    lines.append("")

    # SQL Comparison
    sql = report.get("sql_comparison", {})
    lines.append("## SQL Validation")
    if sql.get("status") == "OK":
        summary = sql.get("summary", {})
        lines.append(f"- Total cenarios: {sql.get('total_scenarios', 0)}")
        for cat, count in sorted(summary.items()):
            icon = "✓" if cat in ("IDENTICAL", "NEW_PASS") else "✗" if cat == "REGRESSION" else "○"
            lines.append(f"- {icon} {cat}: {count}")

        # Detalhar regressoes
        for sid, comp in sql.get("comparisons", {}).items():
            if comp.get("category") == "REGRESSION":
                lines.append(f"  - **REGRESSION** {sid}: {comp.get('after_error', '')}")
    else:
        lines.append(f"- Status: {sql.get('status', 'SKIP')} ({sql.get('reason', '')})")
    lines.append("")

    # Suite Comparison
    suite = report.get("suite_comparison", {})
    lines.append("## Suite Obtido Files")
    if suite.get("status") == "OK":
        summary = suite.get("summary", {})
        lines.append(f"- Total arquivos: {suite.get('total_files', 0)}")
        for cat, count in sorted(summary.items()):
            icon = "✓" if cat == "IDENTICAL" else "✗" if cat in ("MISSING_AFTER", "CHANGED") else "○"
            lines.append(f"- {icon} {cat}: {count}")
    else:
        lines.append(f"- Status: {suite.get('status', 'SKIP')} ({suite.get('reason', '')})")
    lines.append("")

    # PL/SQL Objects
    objects = report.get("plsql_objects_comparison", {})
    lines.append("## PL/SQL Objects")
    if objects.get("status") == "OK":
        lines.append(f"- Total objetos: {objects.get('total_objects', 0)}")
        lines.append(f"- Modificados: {objects.get('changed', 0)}")
        for key, change in objects.get("changes", {}).items():
            if change.get("category") != "UNCHANGED":
                lines.append(f"  - {key}: {change['category']}")
                if change.get("after_status") == "INVALID":
                    lines.append(f"    **ATENCAO: Objeto INVALID apos alteracao!**")
    else:
        lines.append(f"- Status: {objects.get('status', 'SKIP')} ({objects.get('reason', '')})")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="QA Comparator - Compara Before (baseline) vs After (QA)"
    )
    parser.add_argument("--wi-id", required=True, help="ID do work item")
    parser.add_argument(
        "--before-dir",
        help="Diretorio com baseline (default: tests/{wi_id}/baseline/)"
    )
    parser.add_argument(
        "--after-dir",
        help="Diretorio com resultados QA (default: tests/{wi_id}/qa/)"
    )
    parser.add_argument(
        "--output",
        help="Diretorio de saida do relatorio (default: mesmo que --after-dir)"
    )
    parser.add_argument(
        "--sql-only",
        action="store_true",
        help="Comparar apenas SQL results"
    )
    parser.add_argument(
        "--suite-only",
        action="store_true",
        help="Comparar apenas Suite Obtido files"
    )
    parser.add_argument(
        "--env-label",
        default="",
        help="Label do ambiente para incluir no relatorio (ex: LOCAL, QA)"
    )

    args = parser.parse_args()

    wi_id = args.wi_id
    before_dir = Path(args.before_dir) if args.before_dir else (
        PROJECT_ROOT / "tests" / str(wi_id) / "baseline"
    )
    after_dir = Path(args.after_dir) if args.after_dir else (
        PROJECT_ROOT / "tests" / str(wi_id) / "qa"
    )
    output_dir = Path(args.output) if args.output else after_dir

    print(f"=" * 60, file=sys.stderr)
    print(f"  QA COMPARATOR - WI #{wi_id}", file=sys.stderr)
    print(f"  Before: {before_dir}", file=sys.stderr)
    print(f"  After:  {after_dir}", file=sys.stderr)
    print(f"=" * 60, file=sys.stderr)

    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. SQL Comparison
    sql_comp = {"status": "SKIP", "reason": "disabled"}
    if not args.suite_only:
        print(f"\n[1/3] Comparando SQL results...", file=sys.stderr)
        sql_comp = compare_sql_results(before_dir, after_dir)
        print(f"  Status: {sql_comp['status']}", file=sys.stderr)
        if sql_comp.get("summary"):
            for cat, count in sql_comp["summary"].items():
                print(f"    {cat}: {count}", file=sys.stderr)

    # 2. Suite Obtido Comparison
    suite_comp = {"status": "SKIP", "reason": "disabled"}
    if not args.sql_only:
        print(f"\n[2/3] Comparando Suite Obtido files...", file=sys.stderr)
        suite_comp = compare_suite_obtido(before_dir, after_dir)
        print(f"  Status: {suite_comp['status']}", file=sys.stderr)
        if suite_comp.get("summary"):
            for cat, count in suite_comp["summary"].items():
                print(f"    {cat}: {count}", file=sys.stderr)

    # 3. PL/SQL Objects Comparison
    print(f"\n[3/3] Comparando PL/SQL objects...", file=sys.stderr)
    objects_comp = compare_plsql_objects(before_dir, after_dir)
    print(f"  Status: {objects_comp['status']}", file=sys.stderr)

    # 4. Gerar relatorio consolidado
    report = generate_comparison_report(wi_id, sql_comp, suite_comp, objects_comp, output_dir,
                                         env_label=args.env_label)

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"  VEREDICTO: {report['verdict']}", file=sys.stderr)
    print(f"  {report['verdict_description']}", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)

    # Saida JSON para consumo programatico
    print(json.dumps({
        "verdict": report["verdict"],
        "sql_summary": sql_comp.get("summary", {}),
        "suite_summary": suite_comp.get("summary", {}),
        "objects_changed": objects_comp.get("changed", 0)
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
