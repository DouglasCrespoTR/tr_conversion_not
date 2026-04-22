#!/usr/bin/env python3
"""
verify_setup.py -Verificacao automatizada do ambiente taxone-support-dev.

Uso:
  python scripts/verify_setup.py

Resultado: tabela com PASS/WARN/FAIL por check.
Exit code: 0 se nenhum FAIL, 1 se algum FAIL.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUITE_CONFIG = PROJECT_ROOT / "suite-automation" / "config"

# ANSI colors (Windows 10+ suporta via VT100)
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"
BOLD = "\033[1m"


def pass_msg(label, detail=""):
    return ("PASS", f"{GREEN}PASS{RESET}", label, detail)


def warn_msg(label, detail=""):
    return ("WARN", f"{YELLOW}WARN{RESET}", label, detail)


def fail_msg(label, detail=""):
    return ("FAIL", f"{RED}FAIL{RESET}", label, detail)


def check_command(cmd, version_pattern=None, min_version=None):
    """Executa comando e retorna (ok, version_string)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10
        )
        output = result.stdout + result.stderr
        if version_pattern:
            match = re.search(version_pattern, output)
            if match:
                return True, match.group(1)
        return result.returncode == 0, output.strip()[:80]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, None


def load_dotenv(path):
    """Parse simples de .env."""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            values[key.strip()] = val.strip()
    return values


def run_checks():
    results = []

    # 1. Python >= 3.10
    py_ver = sys.version_info
    if py_ver >= (3, 10):
        results.append(pass_msg("Python >= 3.10", f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}"))
    else:
        results.append(fail_msg("Python >= 3.10", f"{py_ver.major}.{py_ver.minor} (minimo: 3.10)"))

    # 2. Java disponivel
    java_ok, java_ver = check_command(["java", "-version"], r'version "([^"]+)"')
    if java_ok:
        results.append(pass_msg("Java disponivel", java_ver or "OK"))
    else:
        results.append(fail_msg("Java disponivel", "NAO ENCONTRADO (instalar JDK 8+)"))

    # 3. GitHub CLI autenticado
    gh_ok, gh_out = check_command(["gh", "auth", "status"])
    if gh_ok:
        results.append(pass_msg("GitHub CLI autenticado", "OK"))
    else:
        results.append(fail_msg("GitHub CLI autenticado", "Rodar: gh auth login"))

    # 4. Node.js >= 18
    node_ok, node_ver = check_command(["node", "--version"], r"v(\d+\.\d+\.\d+)")
    if node_ok and node_ver:
        major = int(node_ver.split(".")[0])
        if major >= 18:
            results.append(pass_msg("Node.js >= 18", node_ver))
        else:
            results.append(warn_msg("Node.js >= 18", f"{node_ver} (recomendado: 18+)"))
    else:
        results.append(warn_msg("Node.js >= 18", "NAO ENCONTRADO (necessario para Claude Code)"))

    # 5. Claude Code CLI
    claude_ok, claude_ver = check_command(["claude", "--version"], r"(\d+\.\d+\.\d+)")
    if claude_ok:
        results.append(pass_msg("Claude Code CLI", claude_ver or "OK"))
    else:
        results.append(warn_msg("Claude Code CLI", "NAO ENCONTRADO (instalar: npm install -g @anthropic-ai/claude-code)"))

    # 6. .env na raiz com vars obrigatorias + opcionais
    env_file = PROJECT_ROOT / ".env"
    required_vars = ["TAXONE_DW_REPO", "QA_REPO"]
    optional_vars = ["PLAYWRIGHT_REPO", "TAXONE_FRONTEND_REPO"]
    all_vars = required_vars + optional_vars
    if env_file.exists():
        env_values = load_dotenv(env_file)
        missing_req = [v for v in required_vars if v not in env_values or not env_values[v]]
        missing_opt = [v for v in optional_vars if v not in env_values or not env_values[v]]
        if not missing_req and not missing_opt:
            results.append(pass_msg(".env com vars obrigatorias", f"{len(all_vars)}/{len(all_vars)} OK"))
        elif not missing_req:
            results.append(pass_msg(".env com vars obrigatorias", f"{len(required_vars)}/{len(required_vars)} OK (opcionais ausentes: {', '.join(missing_opt)})"))
        else:
            results.append(fail_msg(".env com vars obrigatorias", f"Faltando: {', '.join(missing_req)}"))
    else:
        results.append(fail_msg(".env na raiz", "NAO ENCONTRADO (copiar de .env.example)"))

    # 7. Paths do .env existem
    if env_file.exists():
        env_values = load_dotenv(env_file)
        missing_paths = []
        for var in all_vars:
            path = env_values.get(var, "")
            if path and not Path(path).exists():
                missing_paths.append(var)
        if not missing_paths:
            results.append(pass_msg("Paths do .env existem no disco", "Todos OK"))
        else:
            results.append(warn_msg("Paths do .env existem no disco", f"Nao encontrados: {', '.join(missing_paths)}"))
    else:
        results.append(warn_msg("Paths do .env", "Pulado (.env nao existe)"))

    # 8. ADO_PAT
    if os.environ.get("ADO_PAT"):
        results.append(pass_msg("ADO_PAT env var", "Definida"))
    else:
        results.append(fail_msg("ADO_PAT env var", "NAO DEFINIDA (export ADO_PAT=seu-token)"))

    # 9. suite-automation/config/.env
    suite_env = SUITE_CONFIG / ".env"
    if suite_env.exists():
        suite_values = load_dotenv(suite_env)
        has_user = "BD_USER" in suite_values and suite_values["BD_USER"]
        has_passwd = "BD_PASSWD" in suite_values and suite_values["BD_PASSWD"]
        if has_user and has_passwd:
            results.append(pass_msg("SuiteAutomation .env", "BD_USER e BD_PASSWD OK"))
        else:
            results.append(warn_msg("SuiteAutomation .env", "Faltando BD_USER ou BD_PASSWD"))
    else:
        results.append(warn_msg("SuiteAutomation .env", "NAO ENCONTRADO (copiar de .env.example)"))

    # 10. environments.json valido
    env_json = SUITE_CONFIG / "environments.json"
    if env_json.exists():
        try:
            with open(env_json, encoding="utf-8") as f:
                data = json.load(f)
            envs = list(data.keys())
            results.append(pass_msg("environments.json valido", f"Ambientes: {', '.join(envs)}"))
        except (json.JSONDecodeError, Exception) as e:
            results.append(fail_msg("environments.json valido", f"JSON invalido: {e}"))
    else:
        results.append(fail_msg("environments.json", "NAO ENCONTRADO"))

    # 11. Knowledge base legivel (spot check)
    knowledge_dir = PROJECT_ROOT / "knowledge"
    spot_files = [
        knowledge_dir / "architecture" / "overview.md",
        knowledge_dir / "schema" / "COLUMN_GLOSSARY.md",
        knowledge_dir / "conventions" / "code-standards.md",
    ]
    binary_found = []
    for sf in spot_files:
        if sf.exists():
            try:
                content = sf.read_bytes()[:200]
                # git-crypt encrypted files start with \x00GITCRYPT
                if b"\x00GITCRYPT" in content or content.count(0) > 10:
                    binary_found.append(sf.name)
            except Exception:
                pass
    if binary_found:
        results.append(warn_msg("Knowledge base legivel", f"Binarios detectados: {', '.join(binary_found)}"))
    else:
        results.append(pass_msg("Knowledge base legivel", "Plaintext OK"))

    # 12. Seguranca: git-crypt-key.bin
    key_file = PROJECT_ROOT / "git-crypt-key.bin"
    if key_file.exists():
        results.append(warn_msg("Seguranca: git-crypt-key.bin", "ENCONTRADO na raiz! Remover: rm git-crypt-key.bin"))
    else:
        results.append(pass_msg("Seguranca: git-crypt-key.bin", "Nao presente (OK)"))

    return results


def main():
    # Habilitar ANSI no Windows
    if sys.platform == "win32":
        os.system("")  # Ativa VT100 no Windows 10+

    print()
    print(f"{BOLD}{'=' * 65}{RESET}")
    print(f"{BOLD}  TAX ONE Support Dev - Verificacao de Setup{RESET}")
    print(f"{BOLD}{'=' * 65}{RESET}")
    print(f"  Projeto: {PROJECT_ROOT}")
    print()

    results = run_checks()

    # Tabela
    print(f"  {'#':<4} {'Status':<14} {'Check':<35} {'Detalhe'}")
    print(f"  {'-' * 4} {'-' * 6} {'-' * 35} {'-' * 40}")

    for i, (status_raw, status_color, label, detail) in enumerate(results, 1):
        detail_short = detail[:40] if detail else ""
        print(f"  {i:<4} {status_color:<22} {label:<35} {detail_short}")

    print()

    # Resumo
    fails = sum(1 for r in results if r[0] == "FAIL")
    warns = sum(1 for r in results if r[0] == "WARN")
    passes = sum(1 for r in results if r[0] == "PASS")

    if fails == 0:
        print(f"  {GREEN}{BOLD}Resultado: {passes} PASS, {warns} WARN, 0 FAIL - ambiente OK!{RESET}")
    else:
        print(f"  {RED}{BOLD}Resultado: {passes} PASS, {warns} WARN, {fails} FAIL - corrigir itens FAIL{RESET}")

    print(f"{BOLD}{'=' * 65}{RESET}")
    print()

    sys.exit(1 if fails > 0 else 0)


if __name__ == "__main__":
    main()
