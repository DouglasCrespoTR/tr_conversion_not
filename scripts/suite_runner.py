#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SuiteAutomation Runner - Wrapper para execucao do SuiteTeste.jar
Integrado ao pipeline taxone-us-auto-implement (Phase 5.5)

Uso:
  python suite_runner.py --wi-id 1234567 --title "Titulo da WI" --xml SPED/CT_SPED_CONTRIBUICOES.xml [--range 1;3]
  python suite_runner.py --wi-id 1234567 --title "Titulo" --xml SPED/CT_SPED_CONTRIBUICOES.xml --range "1;3" --output /tmp/report.txt
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# === Configuracao ===
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUITE_DIR = PROJECT_ROOT / "suite-automation"

JAR_FILE = Path(os.environ.get("SUITE_JAR_PATH", str(SUITE_DIR / "jar" / "SuiteTeste.jar")))
TESTE_DIR = Path(os.environ.get("SUITE_TESTE_DIR", str(SUITE_DIR / "teste")))
ESPERADO_DIR = Path(os.environ.get("SUITE_ESPERADO_DIR", str(SUITE_DIR / "Arquivos" / "esperado")))
OBTIDO_DIR = Path(os.environ.get("SUITE_OBTIDO_DIR", str(SUITE_DIR / "Arquivos" / "Obtido")))

# PROPERTIES_FILE é resolvido dinamicamente via get_properties_file(env)
_ENV_NAME = "LOCAL"  # default, sobrescrito pelo argparse


def get_properties_file(env_name=None):
    """Retorna path do .properties para o ambiente especificado."""
    env = env_name or _ENV_NAME
    override = os.environ.get("SUITE_PROPERTIES")
    if override:
        return Path(override)
    return SUITE_DIR / "config" / f"suiteteste_{env}.properties"

# QA repo: fonte de verdade para arquivos esperados (branch rc)
from env_loader import get_repo_path
QA_REPO = Path(get_repo_path("QA_REPO"))
QA_BRANCH = "origin/rc"
SYNC_ENABLED = True



def check_prerequisites(env_name=None):
    """Verifica pre-requisitos: Java e JAR disponivel."""
    errors = []
    props_file = get_properties_file(env_name)

    # Verificar Java
    try:
        result = subprocess.run(
            ["java", "-version"], capture_output=True, text=True, timeout=10
        )
        java_output = result.stderr or result.stdout
        if "version" not in java_output.lower():
            errors.append("Java nao encontrado ou versao invalida")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        errors.append("Java nao encontrado no PATH")

    # Verificar JAR
    if not JAR_FILE.exists():
        errors.append(f"SuiteTeste.jar nao encontrado em: {JAR_FILE}")

    # Verificar properties — auto-setup se nao existe
    env = env_name or _ENV_NAME
    if not props_file.exists():
        setup_script = SUITE_DIR / "setup.py"
        if setup_script.exists():
            print(f"Properties nao encontrado, executando setup.py --env {env}...", file=sys.stderr)
            setup_result = subprocess.run(
                [sys.executable, str(setup_script), "--env", env],
                capture_output=True, text=True, timeout=30
            )
            if setup_result.returncode == 0 and props_file.exists():
                print("Properties gerado com sucesso pelo setup.py", file=sys.stderr)
            else:
                errors.append(
                    f"Properties nao encontrado em: {props_file}. "
                    f"Execute: python suite-automation/setup.py --env {env}"
                )
        else:
            errors.append(f"Properties nao encontrado em: {props_file}")

    return errors


def sync_esperado(module_name, qa_repo=QA_REPO, qa_branch=QA_BRANCH):
    """Sync expected files for a module from QA repo rc branch.

    Steps:
      1. Validate QA repo exists
      2. Fetch latest rc (soft fail)
      3. Checkout module dir from origin/rc (without switching branch)
      4. Copy to LOCAL esperado dir
      5. Restore QA repo working tree

    Returns:
        dict with synced (bool), files_count (int), is_new (bool)
    """
    result = {"synced": False, "files_count": 0, "is_new": False}

    # 1. Validate QA repo exists
    if not (qa_repo / ".git").exists():
        print(f"  Aviso: QA repo nao encontrado em {qa_repo}, pulando sync", file=sys.stderr)
        return result

    # 2. Fetch latest rc (silent, soft fail)
    try:
        subprocess.run(
            ["git", "-C", str(qa_repo), "fetch", "origin", "rc", "--quiet"],
            capture_output=True, timeout=30
        )
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"  Aviso: fetch rc falhou ({e}), usando cache local", file=sys.stderr)

    # 3. Checkout module dir from origin/rc (without switching branch)
    qa_esperado_rel = f"arquivos/esperado/{module_name}/"
    checkout_result = subprocess.run(
        ["git", "-C", str(qa_repo), "checkout", qa_branch, "--", qa_esperado_rel],
        capture_output=True, text=True, timeout=30
    )
    if checkout_result.returncode != 0:
        print(f"  Aviso: modulo {module_name} nao encontrado no branch rc", file=sys.stderr)
        return result

    # 4. Copy to LOCAL esperado dir
    src = qa_repo / "arquivos" / "esperado" / module_name
    dst = ESPERADO_DIR / module_name
    is_new = not dst.exists()

    shutil.copytree(str(src), str(dst), dirs_exist_ok=True)

    # Count files copied
    files_count = sum(1 for _ in src.rglob("*") if _.is_file())

    # 5. Restore QA repo working tree (don't pollute with rc files)
    # First reset staged changes, then checkout HEAD to restore, then clean untracked
    try:
        subprocess.run(
            ["git", "-C", str(qa_repo), "reset", "HEAD", "--", qa_esperado_rel],
            capture_output=True, timeout=15
        )
        subprocess.run(
            ["git", "-C", str(qa_repo), "checkout", "--", qa_esperado_rel],
            capture_output=True, timeout=15
        )
        # Clean any new files that exist in rc but not in current branch
        subprocess.run(
            ["git", "-C", str(qa_repo), "clean", "-fd", "--", qa_esperado_rel],
            capture_output=True, timeout=15
        )
    except Exception:
        pass  # best effort

    result["synced"] = True
    result["files_count"] = files_count
    result["is_new"] = is_new

    label = "NOVO" if is_new else "atualizado"
    print(f"  Sync esperado: {module_name} ({files_count} arquivos, {label})", file=sys.stderr)
    return result


def sync_modules_from_xml(xml_file, no_sync=False):
    """Parse XML to find comparison modules and sync each from QA repo.

    Returns:
        list of dicts with module_name and sync result
    """
    if no_sync:
        return []

    comparison_dirs = find_comparison_dirs(xml_file)
    if not comparison_dirs:
        return []

    # Extract top-level module names (e.g., UT_SPED_CONTRIBUICOES from UT_SPED_CONTRIBUICOES/MFS81308)
    modules = set()
    for d in comparison_dirs:
        top = d.split("/")[0]
        modules.add(top)

    sync_results = []
    for module_name in sorted(modules):
        sr = sync_esperado(module_name)
        sync_results.append({"module": module_name, **sr})

    return sync_results


def create_temp_properties(env_name=None):
    """Cria copia temporaria do .properties com dirCasoTeste ajustado.

    O JAR prepende dirCasoTeste ao path do XML. Como passamos paths absolutos,
    precisamos limpar dirCasoTeste para evitar duplicacao do drive letter.
    """
    props_file = get_properties_file(env_name)
    content = props_file.read_text(encoding="latin-1")

    # Corrigir dirCasoTeste: setar para vazio para que paths absolutos funcionem
    content = re.sub(
        r"dirCasoTeste=.*",
        "dirCasoTeste=",
        content,
    )

    # Criar arquivo temporario (Windows precisa de delete=False)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".properties", delete=False, encoding="latin-1"
    )
    tmp.write(content)
    tmp.close()
    return tmp.name


def run_suite(xml_file, test_range, title, properties_path):
    """Executa o SuiteTeste.jar para um XML de teste.

    Args:
        xml_file: Caminho relativo do XML (ex: SPED/CT_SPED_CONTRIBUICOES.xml)
        test_range: Range de testes (ex: "1;3" para testes 1 a 3, "0;0" para todos)
        title: Titulo do projeto/WI
        properties_path: Caminho do .properties temporario

    Returns:
        dict com status, stdout, stderr, exit_code, duration_s
    """
    xml_path = str(TESTE_DIR / xml_file)
    if not Path(xml_path).exists():
        return {
            "status": "ERROR",
            "message": f"XML nao encontrado: {xml_path}",
            "exit_code": -1,
            "duration_s": 0,
        }

    # Montar comando:
    # java -Dfile.encoding=Cp1252 -Dsun.jnu.encoding=ISO-8859-1 -jar SuiteTeste.jar
    #   <xml_path> RA<range> PRO"<title>" <properties_path>
    # NOTA: O JAR SuiteTeste prepend "C:\" ao XML path internamente.
    # Para paths absolutos em Windows, strip o drive letter (ex: "C:\foo" -> "foo").
    xml_arg = xml_path
    if len(xml_arg) >= 3 and xml_arg[1] == ":" and xml_arg[2] == "\\":
        xml_arg = xml_arg[3:]

    cmd = [
        "java",
        "-Dfile.encoding=Cp1252",
        "-Dsun.jnu.encoding=ISO-8859-1",
        "-jar",
        str(JAR_FILE),
        xml_arg,
        f"RA{test_range}",
        f'PRO"{title}"',
        properties_path,
    ]

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1200,  # 20 min timeout
            cwd=str(JAR_FILE.parent),
            encoding="latin-1",
            errors="replace",
        )
        duration = time.time() - start
        return {
            "status": "OK" if result.returncode == 0 else "FAIL",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "duration_s": round(duration, 1),
        }
    except subprocess.TimeoutExpired:
        duration = time.time() - start
        return {
            "status": "TIMEOUT",
            "message": "Execucao excedeu 20 minutos",
            "exit_code": -2,
            "duration_s": round(duration, 1),
        }
    except Exception as e:
        duration = time.time() - start
        return {
            "status": "ERROR",
            "message": str(e),
            "exit_code": -3,
            "duration_s": round(duration, 1),
        }


def find_comparison_dirs(xml_file):
    """A partir do XML, identifica as pastas de esperado/obtido para comparacao.

    Retorna lista de tuplas (esperado_dir, obtido_dir, test_id).
    """
    # Ler o XML para extrair caminhos de comparacao
    xml_path = TESTE_DIR / xml_file
    if not xml_path.exists():
        return []

    content = xml_path.read_text(encoding="latin-1", errors="replace")

    # Extrair padroes de comparacao:
    # <compararArqSaida>0;/Esperado/UT_DW/MFS585105/ARQUIVO.txt;/Obtido/UT_DW/MFS585105/ARQUIVO.txt</compararArqSaida>
    # <compararArqCursor>/esperado/UT_ICMS_LIVRO_P3/MFS81308/file.txt;/obtido/UT_ICMS_LIVRO_P3/MFS81308/file.txt;</compararArqCursor>
    patterns = re.findall(
        r"<compararArq(?:Saida|Cursor)>([^<]+)</compararArq(?:Saida|Cursor)>",
        content,
    )

    dirs = set()
    for pattern in patterns:
        parts = pattern.split(";")
        for part in parts:
            part = part.strip().replace("\\", "/")
            # Extrair diretorio de esperado ou obtido
            match = re.search(r"/(?:esperado|Esperado)/([^/]+(?:/[^/]+)?)/", part)
            if match:
                dirs.add(match.group(1))

    return list(dirs)


def compare_results(xml_file):
    """Compara arquivos esperado vs obtido para um XML de teste.

    Returns:
        dict com total, passed, failed, details (lista de comparacoes)
    """
    comparison_dirs = find_comparison_dirs(xml_file)
    results = {"total": 0, "passed": 0, "failed": 0, "errors": 0, "details": []}

    if not comparison_dirs:
        results["details"].append(
            {
                "test_dir": "(nenhum)",
                "status": "SKIP",
                "message": "Nenhum diretorio de comparacao encontrado no XML",
            }
        )
        return results

    for test_dir in comparison_dirs:
        esperado_path = ESPERADO_DIR / test_dir
        obtido_path = OBTIDO_DIR / test_dir

        if not esperado_path.exists():
            results["details"].append(
                {
                    "test_dir": test_dir,
                    "status": "SKIP",
                    "message": f"Diretorio esperado nao existe: {esperado_path}",
                }
            )
            continue

        if not obtido_path.exists():
            results["total"] += 1
            results["errors"] += 1
            results["details"].append(
                {
                    "test_dir": test_dir,
                    "status": "ERROR",
                    "message": f"Diretorio obtido nao existe: {obtido_path}",
                }
            )
            continue

        # Comparar cada arquivo esperado com seu correspondente obtido
        for esp_file in esperado_path.rglob("*"):
            if not esp_file.is_file():
                continue

            relative = esp_file.relative_to(esperado_path)
            obt_file = obtido_path / relative

            results["total"] += 1

            if not obt_file.exists():
                results["failed"] += 1
                results["details"].append(
                    {
                        "test_dir": test_dir,
                        "file": str(relative),
                        "status": "FAIL",
                        "message": "Arquivo obtido nao gerado",
                    }
                )
                continue

            try:
                esp_content = esp_file.read_text(encoding="latin-1", errors="replace")
                obt_content = obt_file.read_text(encoding="latin-1", errors="replace")

                # Normalizar whitespace e line endings para comparacao
                esp_lines = [
                    line.rstrip() for line in esp_content.splitlines() if line.strip()
                ]
                obt_lines = [
                    line.rstrip() for line in obt_content.splitlines() if line.strip()
                ]

                if esp_lines == obt_lines:
                    results["passed"] += 1
                    results["details"].append(
                        {
                            "test_dir": test_dir,
                            "file": str(relative),
                            "status": "PASS",
                        }
                    )
                else:
                    results["failed"] += 1
                    # Encontrar primeira diferenca
                    diff_line = -1
                    for i, (e, o) in enumerate(zip(esp_lines, obt_lines)):
                        if e != o:
                            diff_line = i + 1
                            break
                    if diff_line == -1 and len(esp_lines) != len(obt_lines):
                        diff_line = min(len(esp_lines), len(obt_lines)) + 1

                    results["details"].append(
                        {
                            "test_dir": test_dir,
                            "file": str(relative),
                            "status": "FAIL",
                            "message": f"Diferenca na linha {diff_line} (esperado: {len(esp_lines)} linhas, obtido: {len(obt_lines)} linhas)",
                        }
                    )
            except Exception as e:
                results["errors"] += 1
                results["details"].append(
                    {
                        "test_dir": test_dir,
                        "file": str(relative),
                        "status": "ERROR",
                        "message": str(e),
                    }
                )

    return results


def generate_report(wi_id, title, suite_results, output_path=None, sync_results=None):
    """Gera relatorio de evidencia de regressao.

    Args:
        wi_id: ID do work item
        title: Titulo da WI
        suite_results: Lista de dicts com resultados por suite
        output_path: Caminho para salvar o relatorio (opcional)
        sync_results: Lista de dicts com resultados de sync (opcional)

    Returns:
        String com o relatorio completo
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    total_suites = len(suite_results)
    passed_suites = sum(1 for s in suite_results if s["comparison"]["failed"] == 0 and s["comparison"]["errors"] == 0)
    failed_suites = total_suites - passed_suites

    lines = []
    lines.append("=" * 70)
    lines.append("  RELATORIO DE TESTES DE REGRESSAO - SuiteAutomation")
    lines.append("=" * 70)
    lines.append(f"  Work Item: #{wi_id} - {title}")
    lines.append(f"  Data:      {timestamp}")
    lines.append(f"  Suites:    {total_suites} executadas")
    lines.append(f"  Resultado: {passed_suites} PASS | {failed_suites} FAIL")

    # Sync info
    if sync_results:
        synced = [s for s in sync_results if s["synced"]]
        if synced:
            lines.append(f"  Esperado sincronizado de: taxone_automacao_qa@rc (fetch {time.strftime('%Y-%m-%d')})")
        else:
            lines.append("  Esperado: sem sync (modo offline ou QA repo indisponivel)")
    else:
        lines.append("  Esperado: sem sync (--no-sync)")

    lines.append("=" * 70)
    lines.append("")

    for sr in suite_results:
        suite_id = sr["suite_id"]
        xml_file = sr["xml_file"]
        exec_result = sr["execution"]
        comp_result = sr["comparison"]

        status_icon = "PASS" if comp_result["failed"] == 0 and comp_result["errors"] == 0 else "FAIL"
        lines.append(f"  [{status_icon}] {suite_id}")
        lines.append(f"         XML: {xml_file}")
        lines.append(f"         Execucao: {exec_result['status']} ({exec_result['duration_s']}s)")
        lines.append(
            f"         Comparacao: {comp_result['total']} arquivos | "
            f"{comp_result['passed']} ok | {comp_result['failed']} falhas | "
            f"{comp_result['errors']} erros"
        )

        # Detalhar falhas
        failures = [d for d in comp_result["details"] if d["status"] in ("FAIL", "ERROR")]
        if failures:
            for f in failures[:5]:  # Max 5 falhas por suite
                msg = f.get("message", "")
                file_name = f.get("file", f.get("test_dir", ""))
                lines.append(f"         -> {f['status']}: {file_name} - {msg}")
            if len(failures) > 5:
                lines.append(f"         -> ... e mais {len(failures) - 5} falhas")

        lines.append("")

    lines.append("-" * 70)
    lines.append("  RESUMO")
    lines.append("-" * 70)

    total_files = sum(s["comparison"]["total"] for s in suite_results)
    total_passed = sum(s["comparison"]["passed"] for s in suite_results)
    total_failed = sum(s["comparison"]["failed"] for s in suite_results)
    total_errors = sum(s["comparison"]["errors"] for s in suite_results)

    lines.append(f"  Total arquivos comparados: {total_files}")
    lines.append(f"  Arquivos OK:              {total_passed}")
    lines.append(f"  Arquivos com falha:       {total_failed}")
    lines.append(f"  Erros de comparacao:      {total_errors}")
    lines.append("")

    if failed_suites == 0:
        lines.append("  >>> TODOS OS TESTES DE REGRESSAO PASSARAM <<<")
    else:
        lines.append(f"  >>> {failed_suites} SUITE(S) COM FALHA - VERIFICAR <<<")

    lines.append("")
    lines.append("=" * 70)

    report = "\n".join(lines)

    if output_path:
        Path(output_path).write_text(report, encoding="utf-8")
        print(f"Relatorio salvo em: {output_path}", file=sys.stderr)

    return report


def main():
    parser = argparse.ArgumentParser(description="SuiteAutomation Runner")
    parser.add_argument("--wi-id", required=True, help="ID do work item")
    parser.add_argument("--title", required=True, help="Titulo do work item")
    parser.add_argument(
        "--xml",
        required=True,
        action="append",
        help="XML de teste (caminho relativo). Pode ser repetido.",
    )
    parser.add_argument(
        "--suite-id",
        action="append",
        help="ID da suite (para o relatorio). Deve corresponder aos --xml.",
    )
    parser.add_argument(
        "--range",
        default="0;0",
        help='Range de testes (ex: "1;3"). Default: "0;0" (todos)',
    )
    parser.add_argument("--output", help="Caminho para salvar o relatorio")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Apenas verificar pre-requisitos",
    )
    parser.add_argument(
        "--compare-only",
        action="store_true",
        help="Apenas comparar resultados (sem executar JAR)",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Pular sync de esperados do QA repo (modo offline)",
    )
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Apenas sincronizar esperados do QA repo sem rodar testes",
    )
    parser.add_argument(
        "--env",
        default="LOCAL",
        choices=["LOCAL", "AC", "RC", "QA"],
        help="Ambiente alvo (default: LOCAL). Define qual .properties usar.",
    )

    args = parser.parse_args()

    # Setar env global para funcoes que usam _ENV_NAME
    global _ENV_NAME
    _ENV_NAME = args.env

    # Verificar pre-requisitos
    errors = check_prerequisites(args.env)
    if errors:
        for e in errors:
            print(f"ERRO: {e}", file=sys.stderr)
        if args.check_only:
            sys.exit(1)
        if not args.compare_only:
            print("Pre-requisitos nao atendidos. Abortando.", file=sys.stderr)
            sys.exit(1)

    if args.check_only:
        print("Pre-requisitos OK")
        sys.exit(0)

    # Preparar suite IDs
    suite_ids = args.suite_id or [
        Path(x).stem.replace("CT_", "") for x in args.xml
    ]

    # Criar properties temporario
    properties_path = None
    if not args.compare_only and not args.sync_only:
        properties_path = create_temp_properties(args.env)

    # Track sync info for report
    all_sync_results = []

    try:
        suite_results = []

        for i, xml_file in enumerate(args.xml):
            suite_id = suite_ids[i] if i < len(suite_ids) else Path(xml_file).stem
            print(f"\n--- {'Sync' if args.sync_only else 'Executando'} suite: {suite_id} ({xml_file}) ---", file=sys.stderr)

            # Sync esperados from QA repo (before JAR execution)
            sync_results = sync_modules_from_xml(xml_file, no_sync=args.no_sync)
            all_sync_results.extend(sync_results)

            # If sync-only, skip execution and comparison
            if args.sync_only:
                continue

            # Executar JAR (ou pular se compare-only)
            if args.compare_only:
                exec_result = {"status": "SKIP", "exit_code": 0, "duration_s": 0}
            else:
                exec_result = run_suite(xml_file, args.range, args.title, properties_path)
                print(
                    f"    Execucao: {exec_result['status']} ({exec_result['duration_s']}s)",
                    file=sys.stderr,
                )

            # Comparar resultados
            comp_result = compare_results(xml_file)
            print(
                f"    Comparacao: {comp_result['passed']}/{comp_result['total']} PASS",
                file=sys.stderr,
            )

            suite_results.append(
                {
                    "suite_id": suite_id,
                    "xml_file": xml_file,
                    "execution": exec_result,
                    "comparison": comp_result,
                }
            )

        # sync-only: report and exit
        if args.sync_only:
            synced = [s for s in all_sync_results if s["synced"]]
            new_mods = [s for s in synced if s["is_new"]]
            total_files = sum(s["files_count"] for s in synced)
            print(f"\nSync concluido: {len(synced)} modulos ({total_files} arquivos)", file=sys.stderr)
            if new_mods:
                print(f"  Novos modulos: {', '.join(s['module'] for s in new_mods)}", file=sys.stderr)
            sys.exit(0)

        # Gerar relatorio
        report = generate_report(args.wi_id, args.title, suite_results, args.output,
                                 sync_results=all_sync_results)
        print(report)

        # Exit code baseado nos resultados
        has_failures = any(
            s["comparison"]["failed"] > 0 or s["comparison"]["errors"] > 0
            for s in suite_results
        )
        sys.exit(1 if has_failures else 0)

    finally:
        # Limpar properties temporario
        if properties_path and os.path.exists(properties_path):
            os.unlink(properties_path)


if __name__ == "__main__":
    main()
