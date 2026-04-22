#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
QA Test Publisher - Publica cenarios de teste no repo taxone_automacao_qa.

Copia artefatos de tests/{WI_ID}/ para cenarios/{WI_ID}/ no repo QA,
cria branch MFS{WI_ID} a partir de dev e abre PR com o Tester da WI.

Uso:
  python scripts/qa_test_publisher.py --wi-id 1066543
  python scripts/qa_test_publisher.py --wi-id 1066543 --skip-pr
  python scripts/qa_test_publisher.py --wi-id 1066543 --reviewer "FulanoQA"
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from env_loader import get_repo_path


def get_qa_repo_path():
    """Retorna path do repo taxone_automacao_qa."""
    return Path(get_repo_path("QA_REPO"))


def get_ado_pat():
    """Retorna PAT do Azure DevOps."""
    pat = os.environ.get("ADO_PAT", "")
    if not pat:
        env_file = PROJECT_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("ADO_PAT="):
                    pat = line.partition("=")[2].strip().strip('"').strip("'")
                    break
    return pat


def get_wi_tester(wi_id, ado_pat):
    """Busca o Custom.Tester da WI no Azure DevOps."""
    if not ado_pat:
        return None
    try:
        import requests
        ado_org = "https://dev.azure.com/tr-ggo"
        ado_project = "Mastersaf Fiscal Solutions"
        url = f"{ado_org}/{ado_project}/_apis/wit/workitems/{wi_id}?$expand=all&api-version=7.1"
        auth = base64.b64encode(f":{ado_pat}".encode()).decode()
        resp = requests.get(url, headers={"Authorization": f"Basic {auth}"}, timeout=15)
        if resp.status_code == 200:
            fields = resp.json().get("fields", {})
            tester = fields.get("Custom.Tester")
            if tester:
                # Pode ser dict com displayName ou string
                if isinstance(tester, dict):
                    return tester.get("uniqueName", tester.get("displayName", ""))
                return str(tester)
    except Exception as e:
        print(f"  AVISO: Nao foi possivel buscar Tester da WI: {e}", file=sys.stderr)
    return None


def validate_test_dir(test_dir, wi_id):
    """Valida que tests/{WI_ID}/ tem artefatos minimos."""
    manifest_file = test_dir / "test_data_manifest.json"
    if not manifest_file.exists():
        print(f"ERRO: Manifest nao encontrado: {manifest_file}")
        print("  Execute o taxone-test-data-engineer primeiro para gerar os dados de teste.")
        return False

    # Verificar se tem ao menos SAFX ou SQL
    has_safx = (test_dir / "safx_files").exists() and list((test_dir / "safx_files").glob("*"))
    has_sql = any(test_dir.glob("0*_*.sql"))

    if not has_safx and not has_sql:
        print(f"ERRO: Nenhum artefato de teste encontrado em {test_dir}")
        return False

    return True


def prepare_qa_branch(qa_repo, wi_id):
    """Prepara branch MFS{WI_ID} no repo QA a partir de dev."""
    branch_name = f"MFS{wi_id}"

    # Fetch origin
    print(f"  Fetching origin...")
    result = subprocess.run(
        ["git", "fetch", "origin"],
        cwd=qa_repo, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        print(f"  AVISO: git fetch falhou: {result.stderr.strip()}")

    # Verificar se branch ja existe local
    result = subprocess.run(
        ["git", "branch", "--list", branch_name],
        cwd=qa_repo, capture_output=True, text=True
    )
    if branch_name in result.stdout:
        # Checkout branch existente
        print(f"  Branch {branch_name} ja existe, fazendo checkout...")
        subprocess.run(["git", "checkout", branch_name], cwd=qa_repo, capture_output=True, text=True)
        return branch_name

    # Verificar se branch existe no remote
    result = subprocess.run(
        ["git", "branch", "-r", "--list", f"origin/{branch_name}"],
        cwd=qa_repo, capture_output=True, text=True
    )
    if f"origin/{branch_name}" in result.stdout:
        print(f"  Branch {branch_name} existe no remote, fazendo checkout...")
        subprocess.run(
            ["git", "checkout", "-b", branch_name, f"origin/{branch_name}"],
            cwd=qa_repo, capture_output=True, text=True
        )
        return branch_name

    # Criar branch nova a partir de origin/dev
    print(f"  Criando branch {branch_name} a partir de origin/dev...")
    result = subprocess.run(
        ["git", "checkout", "-b", branch_name, "origin/dev"],
        cwd=qa_repo, capture_output=True, text=True
    )
    if result.returncode != 0:
        # Fallback: tentar origin/master
        print(f"  AVISO: origin/dev nao encontrado, tentando origin/master...")
        result = subprocess.run(
            ["git", "checkout", "-b", branch_name, "origin/master"],
            cwd=qa_repo, capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"ERRO: Nao foi possivel criar branch: {result.stderr.strip()}")
            sys.exit(1)

    return branch_name


def copy_artifacts(test_dir, qa_repo, wi_id):
    """Copia artefatos de tests/{WI_ID}/ para cenarios/{WI_ID}/ no repo QA."""
    dest_base = qa_repo / "cenarios" / str(wi_id)
    copied = []

    # 1. Manifest
    manifest_src = test_dir / "test_data_manifest.json"
    if manifest_src.exists():
        dest_base.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest_src, dest_base / "test_data_manifest.json")
        copied.append("test_data_manifest.json")

    # 2. SAFX files
    safx_src = test_dir / "safx_files"
    if safx_src.exists():
        safx_dest = dest_base / "safx_files"
        safx_dest.mkdir(parents=True, exist_ok=True)
        for f in safx_src.iterdir():
            if f.is_file():
                shutil.copy2(f, safx_dest / f.name)
                copied.append(f"safx_files/{f.name}")

    # 3. SQL files (01_*, 02_*, 05_*)
    sql_dest = dest_base / "sql"
    sql_dest.mkdir(parents=True, exist_ok=True)
    for pattern in ["01_*.sql", "02_*.sql", "05_*.sql"]:
        for f in test_dir.glob(pattern):
            shutil.copy2(f, sql_dest / f.name)
            copied.append(f"sql/{f.name}")

    # 4. Reports (MD, TXT, HTML)
    reports_dest = dest_base / "reports"
    reports_dest.mkdir(parents=True, exist_ok=True)
    for pattern in ["*report*.md", "*report*.txt", "test_results*.txt", "evidencia_*.html"]:
        for f in test_dir.glob(pattern):
            shutil.copy2(f, reports_dest / f.name)
            copied.append(f"reports/{f.name}")

    # 5. Validation/roteiro scripts adicionais
    for pattern in ["03_*.txt", "04_*.sql", "SCRIPT_*.sql"]:
        for f in test_dir.glob(pattern):
            shutil.copy2(f, sql_dest / f.name)
            copied.append(f"sql/{f.name}")

    return dest_base, copied


def commit_and_push(qa_repo, wi_id, branch_name):
    """Commit e push dos artefatos."""
    cenarios_dir = f"cenarios/{wi_id}"

    # Stage cenarios/{WI_ID}/
    result = subprocess.run(
        ["git", "add", cenarios_dir],
        cwd=qa_repo, capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERRO: git add falhou: {result.stderr.strip()}")
        return False

    # Verificar se ha algo para commitar
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=qa_repo, capture_output=True, text=True
    )
    if not result.stdout.strip():
        print("  Nenhuma alteracao para commitar (artefatos identicos).")
        return True

    # Commit
    commit_msg = f"MFS{wi_id} - Cenario de teste para WI #{wi_id}"
    result = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=qa_repo, capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERRO: git commit falhou: {result.stderr.strip()}")
        return False
    print(f"  Commit: {commit_msg}")

    # Push
    print(f"  Pushing {branch_name}...")
    result = subprocess.run(
        ["git", "push", "-u", "origin", branch_name],
        cwd=qa_repo, capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        print(f"ERRO: git push falhou: {result.stderr.strip()}")
        return False
    print(f"  Push OK")

    return True


def create_pr(qa_repo, wi_id, branch_name, reviewer=None):
    """Cria PR no GitHub via gh CLI."""
    # Carregar manifest para body
    manifest_file = qa_repo / "cenarios" / str(wi_id) / "test_data_manifest.json"
    manifest = {}
    if manifest_file.exists():
        with open(manifest_file, encoding="utf-8") as f:
            manifest = json.load(f)

    title = f"MFS{wi_id} - Cenario de teste WI #{wi_id}"

    # Montar body
    body_lines = []
    body_lines.append("## Cenario de Teste")
    body_lines.append("")
    body_lines.append(f"- **WI**: #{wi_id}")
    body_lines.append(f"- **Branch**: `{branch_name}`")

    tables = manifest.get("tables", [])
    if tables:
        body_lines.append(f"- **Tabelas SAFX**: {', '.join(tables)}")

    scenarios = manifest.get("scenarios", [])
    if scenarios:
        body_lines.append("")
        body_lines.append("### Cenarios")
        body_lines.append("| # | Cenario | Tabela | Tipo |")
        body_lines.append("|---|---------|--------|------|")
        for i, sc in enumerate(scenarios, 1):
            name = sc.get("name", f"Cenario {i}")
            table = sc.get("table", "?")
            stype = sc.get("type", "?")
            body_lines.append(f"| {i} | {name} | {table} | {stype} |")

    body_lines.append("")
    body_lines.append("### Arquivos")
    body_lines.append("- `test_data_manifest.json` — Metadata dos cenarios")
    body_lines.append("- `safx_files/` — Flat-files SAFX pipe-delimited")
    body_lines.append("- `sql/` — Scripts SQL (import, validacao, rollback)")
    body_lines.append("- `reports/` — Relatorios de teste")
    body_lines.append("")
    body_lines.append(f"AB#{wi_id}")

    body = "\n".join(body_lines)

    # gh pr create
    cmd = [
        "gh", "pr", "create",
        "--title", title,
        "--body", body,
        "--base", "dev",
        "--head", branch_name,
    ]
    if reviewer:
        cmd.extend(["--reviewer", reviewer])

    result = subprocess.run(
        cmd, cwd=qa_repo, capture_output=True, text=True, timeout=180
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "already exists" in stderr.lower():
            print(f"  PR ja existe para {branch_name}.")
            # Tentar buscar URL da PR existente
            list_result = subprocess.run(
                ["gh", "pr", "list", "--head", branch_name, "--json", "url", "--limit", "1"],
                cwd=qa_repo, capture_output=True, text=True, timeout=30
            )
            if list_result.returncode == 0:
                try:
                    prs = json.loads(list_result.stdout)
                    if prs:
                        return prs[0]["url"]
                except (json.JSONDecodeError, KeyError, IndexError):
                    pass
            return None
        print(f"ERRO: gh pr create falhou: {stderr}")
        return None

    pr_url = result.stdout.strip()
    return pr_url


def main():
    parser = argparse.ArgumentParser(
        description="Publica cenarios de teste no repo taxone_automacao_qa"
    )
    parser.add_argument("--wi-id", required=True, help="ID do work item")
    parser.add_argument("--skip-pr", action="store_true", help="Nao criar PR (apenas push)")
    parser.add_argument("--reviewer", default=None, help="GitHub username do reviewer (default: Tester da WI)")
    parser.add_argument("--test-dir", default=None,
                        help="Diretorio com artefatos (default: tests/{WI_ID})")

    args = parser.parse_args()
    wi_id = args.wi_id

    print("=" * 60)
    print("  QA Test Publisher")
    print("=" * 60)
    print(f"  WI: #{wi_id}")
    print()

    # 1. Validar repo QA
    try:
        qa_repo = get_qa_repo_path()
    except EnvironmentError as e:
        print(f"ERRO: {e}")
        sys.exit(1)

    if not qa_repo.exists():
        print(f"ERRO: Repo QA nao encontrado: {qa_repo}")
        print("  Configure QA_REPO no .env (veja .env.example)")
        sys.exit(1)

    print(f"  Repo QA: {qa_repo}")

    # 2. Validar diretorio de testes
    if args.test_dir:
        test_dir = Path(args.test_dir)
    else:
        test_dir = PROJECT_ROOT / "tests" / str(wi_id)

    if not test_dir.exists():
        print(f"ERRO: Diretorio de testes nao encontrado: {test_dir}")
        sys.exit(1)

    if not validate_test_dir(test_dir, wi_id):
        sys.exit(1)

    print(f"  Test dir: {test_dir}")

    # 3. Preparar branch
    print()
    print("[1/4] Preparando branch no repo QA...")
    branch_name = prepare_qa_branch(qa_repo, wi_id)
    print(f"  Branch: {branch_name}")

    # 4. Copiar artefatos
    print()
    print("[2/4] Copiando artefatos...")
    dest_dir, copied = copy_artifacts(test_dir, qa_repo, wi_id)
    print(f"  Destino: {dest_dir}")
    for f in copied:
        print(f"    + {f}")
    print(f"  Total: {len(copied)} arquivo(s)")

    # 5. Commit + Push
    print()
    print("[3/4] Commit e push...")
    if not commit_and_push(qa_repo, wi_id, branch_name):
        sys.exit(1)

    # 6. Criar PR
    if args.skip_pr:
        print()
        print("[4/4] PR skip (--skip-pr)")
    else:
        print()
        print("[4/4] Criando PR...")
        reviewer = args.reviewer
        if not reviewer:
            # Buscar Tester da WI
            ado_pat = get_ado_pat()
            if ado_pat:
                tester = get_wi_tester(wi_id, ado_pat)
                if tester:
                    print(f"  Tester da WI: {tester}")
                    # Converter email para GitHub username (simplificado: usar parte antes do @)
                    # Em ADO o tester pode vir como email ou displayName
                    reviewer = tester
                else:
                    print("  AVISO: Nao foi possivel determinar o Tester da WI")

        pr_url = create_pr(qa_repo, wi_id, branch_name, reviewer)
        if pr_url:
            print(f"  PR: {pr_url}")
        else:
            print("  AVISO: PR nao criada (verifique manualmente)")

    # Resumo
    print()
    print("=" * 60)
    print("  Publicacao concluida!")
    print(f"  Branch: {branch_name}")
    print(f"  Cenarios: cenarios/{wi_id}/")
    print(f"  Arquivos: {len(copied)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
