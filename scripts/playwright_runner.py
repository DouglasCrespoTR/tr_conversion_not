#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Playwright E2E Runner - Wrapper para execucao de testes E2E via Playwright
Integrado ao pipeline taxone-us-auto-implement (Phase 5.7)

Uso:
  python playwright_runner.py --wi-id 996339 --title "TKT163246" --spec "e2e/basicos/jobServidor/importacao/importacao.spec.ts" --env qa
  python playwright_runner.py --check-only
  python playwright_runner.py --smoke-only --wi-id 0 --title "smoke" --env qa
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path


# === Configuracao ===
# No Windows, comandos .cmd (npx, npm) precisam de shell=True ou extensao explicita
IS_WINDOWS = platform.system() == "Windows"
from env_loader import get_repo_path
PLAYWRIGHT_REPO = Path(get_repo_path("PLAYWRIGHT_REPO"))
DEFAULT_ENV = "qa"
DEFAULT_TIMEOUT = 1200  # 20 min
DEFAULT_WORKERS = 3
SMOKE_DIR = "e2e/smokingTesting/"

# JSON report path (inside playwright repo)
REPORT_DIR = PLAYWRIGHT_REPO / "test-results"
JSON_REPORT = REPORT_DIR / "results.json"


def check_prerequisites():
    """Verifica pre-requisitos: Node.js, npx, repo clonado, browsers instalados."""
    errors = []

    # Verificar Node.js
    try:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            errors.append("Node.js nao encontrado ou versao invalida")
        else:
            version = result.stdout.strip()
            print(f"  Node.js: {version}", file=sys.stderr)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        errors.append("Node.js nao encontrado no PATH")

    # Verificar npx (no Windows, .cmd precisa de shell=True)
    try:
        result = subprocess.run(
            ["npx", "--version"], capture_output=True, text=True, timeout=10,
            shell=IS_WINDOWS, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            errors.append("npx nao encontrado")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        errors.append("npx nao encontrado no PATH")

    # Verificar repo clonado
    if not PLAYWRIGHT_REPO.exists():
        errors.append(
            f"Repo Playwright nao encontrado em: {PLAYWRIGHT_REPO}. "
            "Clonar com: gh repo clone tr/taxone_automacao_qa_playwright"
        )
    elif not (PLAYWRIGHT_REPO / "package.json").exists():
        errors.append(f"package.json nao encontrado em {PLAYWRIGHT_REPO}")

    # Verificar node_modules
    if PLAYWRIGHT_REPO.exists() and not (PLAYWRIGHT_REPO / "node_modules").exists():
        errors.append(
            f"node_modules nao encontrado em {PLAYWRIGHT_REPO}. "
            "Executar: cd {PLAYWRIGHT_REPO} && npm ci"
        )

    # Verificar browsers instalados (Chromium)
    if PLAYWRIGHT_REPO.exists():
        try:
            result = subprocess.run(
                ["npx", "playwright", "install", "--dry-run"],
                capture_output=True, text=True, timeout=15,
                cwd=str(PLAYWRIGHT_REPO),
                shell=IS_WINDOWS, encoding="utf-8", errors="replace",
            )
            # Se o dry-run menciona "browser is not installed", reportar
            output = (result.stdout or "") + (result.stderr or "")
            if "not installed" in output.lower():
                errors.append(
                    "Browsers Playwright nao instalados. "
                    "Executar: npx playwright install chromium"
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass  # nao conseguiu verificar, seguir em frente

    return errors


def sync_repo(branch="main", no_sync=False):
    """Sincroniza o repositorio Playwright: git fetch + pull + npm ci se necessario.

    Returns:
        dict com synced (bool), message (str)
    """
    if no_sync:
        return {"synced": False, "message": "Sync desabilitado (--no-sync)"}

    if not (PLAYWRIGHT_REPO / ".git").exists():
        return {"synced": False, "message": "Repo nao encontrado"}

    # git fetch
    try:
        subprocess.run(
            ["git", "-C", str(PLAYWRIGHT_REPO), "fetch", "origin", "--quiet"],
            capture_output=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"  Aviso: fetch falhou ({e}), usando cache local", file=sys.stderr)

    # git pull (fast-forward only)
    try:
        result = subprocess.run(
            ["git", "-C", str(PLAYWRIGHT_REPO), "pull", "--ff-only", "origin", branch],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"  Aviso: pull falhou, usando versao local", file=sys.stderr)
    except (subprocess.TimeoutExpired, Exception):
        pass

    # npm ci se package-lock.json mudou (heuristica: verificar mtime)
    lock_file = PLAYWRIGHT_REPO / "package-lock.json"
    node_modules = PLAYWRIGHT_REPO / "node_modules"
    needs_install = False

    if not node_modules.exists():
        needs_install = True
    elif lock_file.exists():
        lock_mtime = lock_file.stat().st_mtime
        nm_mtime = node_modules.stat().st_mtime
        if lock_mtime > nm_mtime:
            needs_install = True

    if needs_install:
        print("  Executando npm ci...", file=sys.stderr)
        try:
            subprocess.run(
                ["npm", "ci"],
                capture_output=True, timeout=120,
                cwd=str(PLAYWRIGHT_REPO),
                shell=IS_WINDOWS,
            )
        except (subprocess.TimeoutExpired, Exception) as e:
            print(f"  Aviso: npm ci falhou ({e})", file=sys.stderr)

    return {"synced": True, "message": f"Sync branch {branch} concluido"}


def run_playwright(specs, env=DEFAULT_ENV, workers=DEFAULT_WORKERS,
                   grep=None, timeout=DEFAULT_TIMEOUT, screenshot="off"):
    """Executa npx playwright test com reporter JSON+line.

    Args:
        specs: Lista de spec files/globs relativos ao repo
        env: Ambiente (qa, sat, uat, prod, dev2)
        workers: Numero de workers paralelos
        grep: Filtro de teste por nome (--grep)
        timeout: Timeout total em segundos
        screenshot: Modo de captura: "on", "only-on-failure", "off"

    Returns:
        dict com status, exit_code, duration_s, json_report_path
    """
    # Garantir diretorio de report existe
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # Construir comando
    cmd = [
        "npx", "playwright", "test",
        "--reporter=json",
        f"--workers={workers}",
        f"--project={env}",
    ]

    # Adicionar spec files
    for spec in specs:
        cmd.append(spec)

    # Grep filter
    if grep:
        cmd.extend(["--grep", grep])

    # Configurar environment via variavel
    env_vars = os.environ.copy()
    env_vars["TEST_ENV"] = env

    # Screenshot mode via env var (Playwright nao aceita --screenshot como CLI flag)
    if screenshot and screenshot != "off":
        env_vars["PLAYWRIGHT_SCREENSHOT"] = screenshot

    # Output JSON para arquivo (variavel oficial do Playwright)
    env_vars["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(JSON_REPORT)

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PLAYWRIGHT_REPO),
            env=env_vars,
            encoding="utf-8",
            errors="replace",
            shell=IS_WINDOWS,
        )
        duration = time.time() - start

        # Playwright exit codes: 0=pass, 1=fail, 2=config error
        status = "PASS" if result.returncode == 0 else "FAIL"
        if result.returncode == 2:
            status = "ERROR"

        return {
            "status": status,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "duration_s": round(duration, 1),
            "json_report_path": str(JSON_REPORT) if JSON_REPORT.exists() else None,
        }
    except subprocess.TimeoutExpired:
        duration = time.time() - start
        return {
            "status": "TIMEOUT",
            "message": f"Execucao excedeu {timeout}s",
            "exit_code": -2,
            "duration_s": round(duration, 1),
            "json_report_path": None,
        }
    except Exception as e:
        duration = time.time() - start
        return {
            "status": "ERROR",
            "message": str(e),
            "exit_code": -3,
            "duration_s": round(duration, 1),
            "json_report_path": None,
        }


def _clean_test_results():
    """Remove screenshots antigos do test-results/ antes de nova execucao."""
    test_results_dir = PLAYWRIGHT_REPO / "test-results"
    if not test_results_dir.exists():
        return
    patterns = ["**/*.png", "**/*.jpg", "**/*.jpeg"]
    removed = 0
    for pattern in patterns:
        for f in test_results_dir.glob(pattern):
            if f.is_file():
                f.unlink()
                removed += 1
    if removed:
        print(f"  Limpou {removed} screenshot(s) antigo(s) de test-results/", file=sys.stderr)


def _collect_screenshots(target_dir):
    """Copia screenshots do test-results/ para o diretorio alvo.

    Args:
        target_dir: Diretorio destino (ex: tests/{WI_ID}/screenshots/)
    """
    test_results_dir = PLAYWRIGHT_REPO / "test-results"
    if not test_results_dir.exists():
        print("  Nenhum test-results/ encontrado para coletar screenshots", file=sys.stderr)
        return 0

    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    collected = 0
    for png in test_results_dir.glob("**/*.png"):
        if png.is_file():
            dest = target / png.name
            # Evitar colisao de nomes
            if dest.exists():
                stem = dest.stem
                suffix = dest.suffix
                counter = 1
                while dest.exists():
                    dest = target / f"{stem}_{counter}{suffix}"
                    counter += 1
            shutil.copy2(png, dest)
            collected += 1

    print(f"  Coletou {collected} screenshot(s) em {target_dir}", file=sys.stderr)
    return collected


def parse_results(json_path):
    """Parseia JSON report do Playwright.

    Returns:
        dict com total, passed, failed, skipped, flaky, duration_ms, specs (detalhes)
    """
    results = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "flaky": 0,
        "duration_ms": 0,
        "specs": [],
    }

    if not json_path or not Path(json_path).exists():
        return results

    try:
        data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, Exception):
        return results

    # Playwright JSON report structure:
    # { suites: [...], stats: { expected, unexpected, flaky, skipped, duration } }
    stats = data.get("stats", {})
    results["passed"] = stats.get("expected", 0)
    results["failed"] = stats.get("unexpected", 0)
    results["flaky"] = stats.get("flaky", 0)
    results["skipped"] = stats.get("skipped", 0)
    results["total"] = (
        results["passed"] + results["failed"] + results["flaky"] + results["skipped"]
    )
    results["duration_ms"] = stats.get("duration", 0)

    # Extrair detalhes por spec
    for suite in data.get("suites", []):
        _extract_specs(suite, results["specs"])

    return results


def _extract_specs(suite, specs_list):
    """Recursivamente extrai specs de suites aninhadas do Playwright JSON."""
    for spec_data in suite.get("specs", []):
        title = spec_data.get("title", "unknown")
        file_path = spec_data.get("file", suite.get("file", "unknown"))
        ok = spec_data.get("ok", True)
        tests = spec_data.get("tests", [])

        # Determinar status do spec
        status = "PASS"
        error_msg = ""
        duration_ms = 0

        for test in tests:
            for result in test.get("results", []):
                duration_ms += result.get("duration", 0)
                test_status = result.get("status", "passed")
                if test_status == "failed" or test_status == "timedOut":
                    status = "FAIL"
                    # Extrair mensagem de erro
                    error = result.get("error", {})
                    error_msg = error.get("message", "")[:200] if error else ""
                elif test_status == "skipped":
                    status = "SKIP"
                elif test_status == "flaky":
                    status = "FLAKY"

        if not ok:
            status = "FAIL"

        specs_list.append({
            "title": title,
            "file": file_path,
            "status": status,
            "duration_s": round(duration_ms / 1000, 1),
            "error": error_msg,
        })

    # Recurse into child suites
    for child in suite.get("suites", []):
        _extract_specs(child, specs_list)


def generate_report(wi_id, title, exec_result, parsed_results, output_path=None,
                    env=DEFAULT_ENV):
    """Gera relatorio de evidencia de testes E2E.

    Args:
        wi_id: ID do work item
        title: Titulo da WI
        exec_result: Dict com resultados da execucao Playwright
        parsed_results: Dict parseado do JSON report
        output_path: Caminho para salvar o relatorio (opcional)
        env: Ambiente de execucao

    Returns:
        String com o relatorio completo
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    total = parsed_results["total"]
    passed = parsed_results["passed"]
    failed = parsed_results["failed"]
    skipped = parsed_results["skipped"]
    flaky = parsed_results["flaky"]

    lines = []
    lines.append("=" * 70)
    lines.append("  RELATORIO DE TESTES E2E - Playwright")
    lines.append("=" * 70)
    lines.append(f"  Work Item: #{wi_id} - {title}")
    lines.append(f"  Data:      {timestamp}")
    lines.append(f"  Ambiente:  {env} | Specs: {total} executados")
    lines.append(
        f"  Resultado: {passed} PASS | {failed} FAIL | "
        f"{skipped} SKIP | {flaky} FLAKY"
    )
    lines.append(f"  Duracao:   {exec_result.get('duration_s', 0)}s total")
    lines.append("=" * 70)
    lines.append("")

    # Detalhe por spec
    for spec in parsed_results.get("specs", []):
        status_icon = spec["status"]
        file_name = spec.get("file", "unknown")
        duration = spec.get("duration_s", 0)
        lines.append(f"  [{status_icon}] {file_name} ({duration}s)")

        if spec["status"] in ("FAIL",) and spec.get("error"):
            # Limitar mensagem de erro a 100 chars por linha
            error = spec["error"][:200]
            lines.append(f"         -> FAIL: \"{spec['title']}\" - {error}")

    lines.append("")
    lines.append("-" * 70)
    lines.append("  RESUMO")
    lines.append("-" * 70)
    lines.append(f"  Total specs executados: {total}")
    lines.append(f"  Specs OK:              {passed}")
    lines.append(f"  Specs com falha:       {failed}")
    lines.append(f"  Specs skipped:         {skipped}")
    lines.append(f"  Specs flaky:           {flaky}")
    lines.append("")

    if failed == 0:
        lines.append("  >>> TODOS OS TESTES E2E PASSARAM <<<")
    else:
        lines.append(f"  >>> {failed} SPEC(S) COM FALHA - VERIFICAR <<<")

    lines.append("")
    lines.append("=" * 70)

    report = "\n".join(lines)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(report, encoding="utf-8")
        print(f"Relatorio salvo em: {output_path}", file=sys.stderr)

    return report


def main():
    parser = argparse.ArgumentParser(description="Playwright E2E Runner")
    parser.add_argument("--wi-id", help="ID do work item")
    parser.add_argument("--title", help="Titulo do work item")
    parser.add_argument(
        "--spec",
        action="append",
        help="Spec file ou glob (caminho relativo ao repo). Pode ser repetido.",
    )
    parser.add_argument(
        "--env",
        default=DEFAULT_ENV,
        choices=["qa", "sat", "uat", "prod", "dev2"],
        help=f"Ambiente de execucao (default: {DEFAULT_ENV})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Numero de workers paralelos (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--grep",
        help="Filtro de teste por nome (--grep do Playwright)",
    )
    parser.add_argument(
        "--grep-describe",
        help="Filtro por test.describe title (passado como --grep ao Playwright). "
             "Util para rodar apenas os testes de uma tela especifica dentro do spec.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout total em segundos (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument("--output", help="Caminho para salvar o relatorio")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Apenas verificar pre-requisitos",
    )
    parser.add_argument(
        "--smoke-only",
        action="store_true",
        help="Executar apenas specs de smoke testing (15 specs)",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Pular sync do repositorio (modo offline)",
    )
    parser.add_argument(
        "--screenshots-dir",
        help="Diretorio para salvar screenshots dos testes. "
             "Se fornecido, configura PLAYWRIGHT_SCREENSHOTS_DIR e copia screenshots apos execucao.",
    )
    parser.add_argument(
        "--screenshot",
        choices=["on", "only-on-failure", "off"],
        default="off",
        help="Modo de captura de screenshots: on (todos), only-on-failure (apenas falhas), off (default)",
    )

    args = parser.parse_args()

    # Verificar pre-requisitos
    errors = check_prerequisites()
    if errors:
        for e in errors:
            print(f"ERRO: {e}", file=sys.stderr)
        if args.check_only:
            sys.exit(1)
        print("Pre-requisitos nao atendidos. Abortando.", file=sys.stderr)
        sys.exit(1)

    if args.check_only:
        print("Pre-requisitos OK")
        sys.exit(0)

    # Validar parametros obrigatorios para execucao
    if not args.wi_id or not args.title:
        print("ERRO: --wi-id e --title sao obrigatorios para execucao", file=sys.stderr)
        sys.exit(1)

    # Sync repositorio
    sync_result = sync_repo(no_sync=args.no_sync)
    print(f"  Sync: {sync_result['message']}", file=sys.stderr)

    # Determinar specs a executar
    if args.smoke_only:
        specs = [SMOKE_DIR]
        print("  Modo: Smoke testing", file=sys.stderr)
    elif args.spec:
        specs = args.spec
    else:
        print("ERRO: Especifique --spec ou --smoke-only", file=sys.stderr)
        sys.exit(1)

    # Resolver grep: --grep-describe tem prioridade sobre --grep
    grep_filter = args.grep_describe if args.grep_describe else args.grep

    print(f"  Specs: {specs}", file=sys.stderr)
    if grep_filter:
        print(f"  Grep filter: {grep_filter}", file=sys.stderr)
    print(f"  Ambiente: {args.env} | Workers: {args.workers}", file=sys.stderr)

    # Limpar screenshots antigos antes de nova execucao
    if args.screenshot and args.screenshot != "off":
        _clean_test_results()

    # Executar Playwright
    exec_result = run_playwright(
        specs=specs,
        env=args.env,
        workers=args.workers,
        grep=grep_filter,
        timeout=args.timeout,
        screenshot=args.screenshot,
    )

    print(
        f"  Execucao: {exec_result['status']} ({exec_result['duration_s']}s)",
        file=sys.stderr,
    )

    # Coletar screenshots se solicitado
    if args.screenshots_dir:
        _collect_screenshots(args.screenshots_dir)

    # Parsear resultados
    parsed = parse_results(exec_result.get("json_report_path"))

    # Se nao tem JSON report, tentar extrair info do stdout/stderr
    if parsed["total"] == 0:
        stdout = exec_result.get("stdout", "")
        stderr = exec_result.get("stderr", "")
        combined = stdout + stderr

        # Fallback: contar linhas de PASS/FAIL no output
        passed_count = combined.count("✓") + combined.count("[pass]")
        failed_count = combined.count("✘") + combined.count("[fail]")
        if passed_count + failed_count > 0:
            parsed["total"] = passed_count + failed_count
            parsed["passed"] = passed_count
            parsed["failed"] = failed_count

        # Se global-setup falhou, registrar o erro
        if "Error" in combined and parsed["total"] == 0:
            # Extrair primeira linha de erro significativa
            for line in combined.splitlines():
                if "Error" in line or "ERROR" in line:
                    error_msg = line.strip()[:200]
                    parsed["specs"].append({
                        "title": "global-setup",
                        "file": "env/global-setup.ts",
                        "status": "FAIL",
                        "duration_s": 0,
                        "error": error_msg,
                    })
                    parsed["total"] = 1
                    parsed["failed"] = 1
                    break

    # Gerar relatorio
    output_path = args.output or f"tests/{args.wi_id}/e2e_report.txt"
    report = generate_report(
        wi_id=args.wi_id,
        title=args.title,
        exec_result=exec_result,
        parsed_results=parsed,
        output_path=output_path,
        env=args.env,
    )
    print(report)

    # Exit code baseado nos resultados
    sys.exit(1 if parsed["failed"] > 0 else 0)


if __name__ == "__main__":
    main()
