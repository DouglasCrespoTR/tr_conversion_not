#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Baseline Capture - Captura estado "Antes" para comparacao Before vs After.

Executado pelo Dev Pipeline ANTES da implementacao, captura:
  1. Resultados SQL dos validation_query de test_scenarios.json
  2. Resultados SuiteAutomation (via suite_detached.py)
  3. Snapshot dos arquivos Obtido/ das suites
  4. Estado dos objetos PL/SQL no banco (checksums)

Uso:
  python baseline_capture.py --wi-id 1078744 \
    --scenarios tests/1078744/test_scenarios.json \
    --component-map knowledge/suite-automation/component-test-map.json \
    --output-dir tests/1078744/baseline/

  python baseline_capture.py --wi-id 1078744 --sql-only
  python baseline_capture.py --wi-id 1078744 --suite-only
  python baseline_capture.py --wi-id 1078744 --objects PKG_EFD_0460,EST_MM_115_FPROC

  # Multi-ambiente (QA/AC/RC):
  python baseline_capture.py --wi-id 1078744 --env QA --output-dir tests/1078744/baseline_qa/
  python baseline_capture.py --wi-id 1078744 --env QA --output-dir tests/1078744/after_qa/ --sql-only
"""

import argparse
import json
import hashlib
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
SUITE_OBTIDO_DIR = PROJECT_ROOT / "suite-automation" / "Arquivos" / "Obtido"

# Oracle connection — usa env_connection.py para multi-ambiente
# Fallback para env vars hardcoded quando --env nao e especificado (backward compat)
_ACTIVE_ENV = "LOCAL"  # Alterado via --env no CLI


def get_oracle_connection():
    """Cria conexao Oracle via env_connection (multi-ambiente) ou fallback LOCAL."""
    try:
        from env_connection import get_connection
        return get_connection(_ACTIVE_ENV)
    except ImportError:
        # Fallback para modo standalone (sem env_connection.py)
        db_user = os.environ.get("DB_USER", "V2R010AC")
        db_pass = os.environ.get("DB_PASS", "V2R010AC")
        db_dsn = os.environ.get("DB_DSN", "localhost:1521/MFSPDB")
        try:
            import oracledb
            oracledb.init_oracle_client()
        except Exception:
            pass
        import oracledb
        return oracledb.connect(user=db_user, password=db_pass, dsn=db_dsn)


def capture_sql_baseline(scenarios_file, output_dir):
    """Executa validation_query de cada cenario e salva resultados.

    Returns:
        dict com resultados por cenario (id -> {query, rows, columns, error})
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if not scenarios_file.exists():
        print(f"AVISO: test_scenarios.json nao encontrado: {scenarios_file}", file=sys.stderr)
        return {"status": "SKIP", "reason": "scenarios_file_not_found"}

    scenarios = json.loads(scenarios_file.read_text(encoding="utf-8"))
    scenario_list = scenarios.get("scenarios", [])

    if not scenario_list:
        print("AVISO: Nenhum cenario encontrado em test_scenarios.json", file=sys.stderr)
        return {"status": "SKIP", "reason": "no_scenarios"}

    # Filtrar cenarios com validation_query
    queries = []
    for s in scenario_list:
        vq = s.get("validation_query")
        if vq and vq.strip():
            queries.append({"id": s["id"], "name": s.get("name", ""), "query": vq.strip()})

    if not queries:
        print("AVISO: Nenhum cenario tem validation_query", file=sys.stderr)
        return {"status": "SKIP", "reason": "no_validation_queries"}

    results = {}
    conn = None
    try:
        conn = get_oracle_connection()
        cursor = conn.cursor()

        for q in queries:
            scenario_id = q["id"]
            query = q["query"]
            print(f"  Executando {scenario_id}: {query[:80]}...", file=sys.stderr)

            try:
                # Remover bind variables (:param) — substituir por NULL para baseline
                # O baseline captura o estado geral, nao dados especificos
                safe_query = query
                # Se tem bind variables, nao executar (precisa de dados do cenario)
                if ":proc_id" in safe_query.lower() or ":" in safe_query:
                    results[scenario_id] = {
                        "query": query,
                        "status": "SKIP",
                        "reason": "bind_variables_present",
                        "note": "Query tem bind variables, requer dados do cenario para executar"
                    }
                    continue

                cursor.execute(safe_query)
                columns = [col[0] for col in cursor.description] if cursor.description else []
                rows = cursor.fetchmany(100)  # Max 100 rows para baseline
                row_count = len(rows)

                # Converter rows para listas (Oracle retorna tuples)
                rows_serializable = [list(r) for r in rows]
                # Converter tipos Oracle para string (datetime, Decimal, etc.)
                for row in rows_serializable:
                    for i, val in enumerate(row):
                        if val is not None and not isinstance(val, (str, int, float, bool)):
                            row[i] = str(val)

                results[scenario_id] = {
                    "query": query,
                    "status": "OK",
                    "columns": columns,
                    "row_count": row_count,
                    "rows": rows_serializable,
                    "truncated": row_count >= 100
                }
                print(f"    -> {row_count} linhas", file=sys.stderr)

            except Exception as e:
                results[scenario_id] = {
                    "query": query,
                    "status": "ERROR",
                    "error": str(e)
                }
                print(f"    -> ERRO: {e}", file=sys.stderr)

    except Exception as e:
        print(f"ERRO ao conectar Oracle: {e}", file=sys.stderr)
        return {"status": "ERROR", "error": str(e)}
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    # Salvar resultados
    output_file = output_dir / "sql_results_before.json"
    output_file.write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8"
    )
    print(f"  SQL baseline salvo em: {output_file}", file=sys.stderr)

    return {"status": "OK", "scenarios_total": len(queries), "results": results}


def capture_plsql_objects(object_names, output_dir):
    """Captura estado (status, last_ddl_time) dos objetos PL/SQL no banco.

    Args:
        object_names: Lista de nomes de objetos (packages, procedures, etc.)
        output_dir: Diretorio de saida

    Returns:
        dict com estado de cada objeto
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if not object_names:
        return {"status": "SKIP", "reason": "no_objects"}

    conn = None
    results = {}
    try:
        conn = get_oracle_connection()
        cursor = conn.cursor()

        # Buscar estado dos objetos
        placeholders = ", ".join([f"'{name.upper()}'" for name in object_names])
        query = f"""
            SELECT object_name, object_type, status, last_ddl_time,
                   DBMS_UTILITY.GET_HASH_VALUE(
                       object_name || object_type || TO_CHAR(last_ddl_time, 'YYYYMMDDHH24MISS'),
                       0, 1073741824
                   ) AS checksum
            FROM user_objects
            WHERE object_name IN ({placeholders})
            ORDER BY object_name, object_type
        """
        cursor.execute(query)
        for row in cursor:
            key = f"{row[0]}:{row[1]}"
            results[key] = {
                "object_name": row[0],
                "object_type": row[1],
                "status": row[2],
                "last_ddl_time": str(row[3]),
                "checksum": row[4]
            }

        # Objetos nao encontrados
        found_names = {r["object_name"] for r in results.values()}
        for name in object_names:
            if name.upper() not in found_names:
                results[f"{name.upper()}:NOT_FOUND"] = {
                    "object_name": name.upper(),
                    "object_type": "NOT_FOUND",
                    "status": "MISSING",
                    "last_ddl_time": None,
                    "checksum": None
                }

    except Exception as e:
        print(f"ERRO ao capturar objetos PL/SQL: {e}", file=sys.stderr)
        return {"status": "ERROR", "error": str(e)}
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    output_file = output_dir / "plsql_objects_before.json"
    output_file.write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8"
    )
    print(f"  PL/SQL objects state salvo em: {output_file}", file=sys.stderr)

    return {"status": "OK", "objects": results}


def score_suite(mapping, wi_title, file_paths=None, packages=None):
    """Calcula score de relevancia de uma suite para a WI.

    Mesma logica do component-test-map.json scoring.
    """
    score = 0
    title_upper = wi_title.upper()

    # Keyword match (weight 3)
    for kw in mapping.get("keywords", []):
        if kw.upper() in title_upper:
            score += 3
            break

    # File path match (weight 5)
    if file_paths:
        for fp in file_paths:
            fp_lower = fp.lower().replace("\\", "/")
            for mp in mapping.get("file_paths", []):
                if mp.lower() in fp_lower:
                    score += 5
                    break
            if score >= 5:
                break

    # Package match (weight 4)
    if packages:
        for pkg in packages:
            pkg_upper = pkg.upper()
            for mp in mapping.get("packages", []):
                if mp.upper() == pkg_upper:
                    score += 4
                    break
            if score >= 4:
                break

    return score


def find_relevant_suites(component_map_file, wi_title, file_paths=None, packages=None):
    """Identifica suites relevantes para a WI baseado no component-test-map.json.

    Returns:
        Lista de suites com score >= min_score_to_include, ordenadas por score desc
    """
    if not component_map_file.exists():
        print(f"AVISO: component-test-map.json nao encontrado: {component_map_file}", file=sys.stderr)
        return []

    cmap = json.loads(component_map_file.read_text(encoding="utf-8"))
    scoring_config = cmap.get("scoring", {})
    min_score = scoring_config.get("min_score_to_include", 3)
    max_suites = scoring_config.get("max_suites", 5)

    results = []
    for mapping in cmap.get("mappings", []):
        score = score_suite(mapping, wi_title, file_paths, packages)
        if score >= min_score:
            results.append({
                "id": mapping["id"],
                "xml_file": mapping["xml_file"],
                "description": mapping.get("description", ""),
                "score": score
            })

    # Ordenar por score desc, limitar a max_suites
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:max_suites]


def capture_suite_baseline(wi_id, wi_title, suites, output_dir):
    """Executa suites relevantes e salva resultados (Obtido) como baseline.

    Args:
        wi_id: ID da WI
        wi_title: Titulo da WI
        suites: Lista de suites relevantes (de find_relevant_suites)
        output_dir: Diretorio base de saida

    Returns:
        dict com resultados por suite
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    obtido_before_dir = output_dir / "obtido_before"
    obtido_before_dir.mkdir(parents=True, exist_ok=True)

    if not suites:
        return {"status": "SKIP", "reason": "no_relevant_suites"}

    results = {}
    suite_detached = SCRIPTS_DIR / "suite_detached.py"
    python_exe = sys.executable

    for suite in suites:
        suite_id = suite["id"]
        xml_file = suite["xml_file"]
        print(f"\n  Executando suite baseline: {suite_id} ({xml_file})...", file=sys.stderr)

        try:
            # Executar via suite_detached.py --wait (desacoplado do node.exe)
            cmd = [
                python_exe, str(suite_detached),
                "--wi-id", str(wi_id),
                "--title", f"Baseline {wi_title[:50]}",
                "--xml", xml_file,
                "--wait",
                "--timeout", "600"  # 10 min timeout para baseline
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=660,  # 11 min total
                cwd=str(PROJECT_ROOT),
                encoding="utf-8",
                errors="replace"
            )

            # Salvar output
            suite_output_file = output_dir / f"suite_{suite_id}_before.txt"
            suite_output_file.write_text(
                result.stdout + "\n" + result.stderr,
                encoding="utf-8"
            )

            # Copiar Obtido/ files para snapshot
            suite_obtido_dst = obtido_before_dir / suite_id
            if SUITE_OBTIDO_DIR.exists():
                # Copiar todos os Obtido files que existem apos execucao
                for obtido_file in SUITE_OBTIDO_DIR.rglob("*"):
                    if obtido_file.is_file():
                        rel = obtido_file.relative_to(SUITE_OBTIDO_DIR)
                        dst_file = suite_obtido_dst / rel
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(obtido_file), str(dst_file))

            results[suite_id] = {
                "xml_file": xml_file,
                "status": "OK" if result.returncode == 0 else "FAIL",
                "exit_code": result.returncode,
                "output_file": str(suite_output_file),
                "obtido_snapshot": str(suite_obtido_dst)
            }
            print(f"    -> {'OK' if result.returncode == 0 else 'FAIL'} (exit {result.returncode})", file=sys.stderr)

        except subprocess.TimeoutExpired:
            results[suite_id] = {
                "xml_file": xml_file,
                "status": "TIMEOUT",
                "error": "Suite excedeu 10 minutos"
            }
            print(f"    -> TIMEOUT", file=sys.stderr)

        except Exception as e:
            results[suite_id] = {
                "xml_file": xml_file,
                "status": "ERROR",
                "error": str(e)
            }
            print(f"    -> ERRO: {e}", file=sys.stderr)

    # Salvar resumo
    output_file = output_dir / "suite_results_before.json"
    output_file.write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8"
    )
    print(f"\n  Suite baseline salvo em: {output_file}", file=sys.stderr)

    return {"status": "OK", "suites": results}


def generate_baseline_summary(output_dir, sql_result, suite_result, objects_result):
    """Gera resumo consolidado do baseline capturado."""
    summary = {
        "version": "1.1.0",
        "captured_at": datetime.now().isoformat(),
        "captured_on_branch": _get_current_branch(),
        "environment": _ACTIVE_ENV,
        "sql_baseline": {
            "status": sql_result.get("status", "SKIP"),
            "scenarios_total": sql_result.get("scenarios_total", 0),
            "file": "sql_results_before.json"
        },
        "suite_baseline": {
            "status": suite_result.get("status", "SKIP"),
            "suites_count": len(suite_result.get("suites", {})),
            "file": "suite_results_before.json",
            "obtido_dir": "obtido_before/"
        },
        "plsql_objects": {
            "status": objects_result.get("status", "SKIP"),
            "objects_count": len(objects_result.get("objects", {})),
            "file": "plsql_objects_before.json"
        }
    }

    # Gerar checksum do baseline para validacao futura
    content = json.dumps(summary, sort_keys=True)
    summary["checksum"] = hashlib.sha256(content.encode()).hexdigest()[:16]

    output_file = output_dir / "baseline_summary.json"
    output_file.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"\n  Baseline summary salvo em: {output_file}", file=sys.stderr)
    return summary


def _get_current_branch():
    """Retorna nome do branch git atual."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(PROJECT_ROOT)
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def main():
    parser = argparse.ArgumentParser(
        description="Baseline Capture - Captura estado 'Antes' para QA Pipeline"
    )
    parser.add_argument("--wi-id", required=True, help="ID do work item")
    parser.add_argument("--wi-title", default="", help="Titulo do work item (para scoring de suites)")
    parser.add_argument(
        "--scenarios",
        help="Path do test_scenarios.json (default: tests/{wi_id}/test_scenarios.json)"
    )
    parser.add_argument(
        "--component-map",
        default=str(PROJECT_ROOT / "knowledge" / "suite-automation" / "component-test-map.json"),
        help="Path do component-test-map.json"
    )
    parser.add_argument(
        "--output-dir",
        help="Diretorio de saida (default: tests/{wi_id}/baseline/)"
    )
    parser.add_argument(
        "--objects",
        help="Nomes de objetos PL/SQL para capturar estado (separados por virgula)"
    )
    parser.add_argument(
        "--file-paths",
        help="Paths de arquivos modificados para scoring de suites (separados por virgula)"
    )
    parser.add_argument(
        "--packages",
        help="Packages PL/SQL para scoring de suites (separados por virgula)"
    )
    parser.add_argument(
        "--sql-only",
        action="store_true",
        help="Capturar apenas baseline SQL (pular suites)"
    )
    parser.add_argument(
        "--suite-only",
        action="store_true",
        help="Capturar apenas baseline Suite (pular SQL)"
    )
    parser.add_argument(
        "--skip-suite",
        action="store_true",
        help="Pular captura de suite (mais rapido)"
    )
    parser.add_argument(
        "--env",
        default="LOCAL",
        choices=["LOCAL", "QA", "AC", "RC"],
        help="Ambiente Oracle para conectar (default: LOCAL)"
    )

    args = parser.parse_args()

    # Setar ambiente ativo para get_oracle_connection()
    global _ACTIVE_ENV
    _ACTIVE_ENV = args.env

    wi_id = args.wi_id
    scenarios_file = Path(args.scenarios) if args.scenarios else (
        PROJECT_ROOT / "tests" / str(wi_id) / "test_scenarios.json"
    )
    component_map_file = Path(args.component_map)
    output_dir = Path(args.output_dir) if args.output_dir else (
        PROJECT_ROOT / "tests" / str(wi_id) / "baseline"
    )

    print(f"=" * 60, file=sys.stderr)
    print(f"  BASELINE CAPTURE - WI #{wi_id}", file=sys.stderr)
    print(f"  Ambiente: {args.env}", file=sys.stderr)
    print(f"  Output: {output_dir}", file=sys.stderr)
    print(f"  Branch: {_get_current_branch()}", file=sys.stderr)
    print(f"=" * 60, file=sys.stderr)

    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. SQL Baseline
    sql_result = {"status": "SKIP", "reason": "disabled"}
    if not args.suite_only:
        print(f"\n[1/3] Capturando SQL baseline...", file=sys.stderr)
        sql_result = capture_sql_baseline(scenarios_file, output_dir)
        print(f"  Status: {sql_result['status']}", file=sys.stderr)

    # 2. PL/SQL Objects State
    object_names = []
    if args.objects:
        object_names = [o.strip() for o in args.objects.split(",") if o.strip()]
    objects_result = {"status": "SKIP", "reason": "no_objects_specified"}
    if object_names:
        print(f"\n[2/3] Capturando estado PL/SQL objects...", file=sys.stderr)
        objects_result = capture_plsql_objects(object_names, output_dir)
        print(f"  Status: {objects_result['status']}", file=sys.stderr)

    # 3. Suite Baseline
    suite_result = {"status": "SKIP", "reason": "disabled"}
    if not args.sql_only and not args.skip_suite:
        print(f"\n[3/3] Capturando Suite baseline...", file=sys.stderr)
        file_paths = [fp.strip() for fp in args.file_paths.split(",")] if args.file_paths else None
        packages = [pkg.strip() for pkg in args.packages.split(",")] if args.packages else None

        suites = find_relevant_suites(
            component_map_file,
            args.wi_title or f"WI {wi_id}",
            file_paths=file_paths,
            packages=packages
        )

        if suites:
            print(f"  Suites relevantes: {', '.join(s['id'] for s in suites)}", file=sys.stderr)
            suite_result = capture_suite_baseline(wi_id, args.wi_title or "", suites, output_dir)
        else:
            suite_result = {"status": "SKIP", "reason": "no_relevant_suites"}
            print(f"  Nenhuma suite relevante encontrada", file=sys.stderr)
    else:
        print(f"\n[3/3] Suite baseline: SKIP (desabilitado)", file=sys.stderr)

    # 4. Gerar summary
    summary = generate_baseline_summary(output_dir, sql_result, suite_result, objects_result)

    print(f"\n{'=' * 60}", file=sys.stderr)
    print(f"  BASELINE CAPTURE CONCLUIDO", file=sys.stderr)
    print(f"  SQL:     {sql_result['status']}", file=sys.stderr)
    print(f"  Objects: {objects_result['status']}", file=sys.stderr)
    print(f"  Suite:   {suite_result['status']}", file=sys.stderr)
    print(f"  Output:  {output_dir}", file=sys.stderr)
    print(f"{'=' * 60}", file=sys.stderr)

    # Saida JSON para consumo programatico
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
