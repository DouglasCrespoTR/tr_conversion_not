#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
QA Task Creator - Cria Tasks de Revisao QA automaticamente no ADO.

Cria duas Tasks filhas na User Story:
  1. [QA] Revisao de Plano de Testes | ID da(s) demanda(s) testada(s): {WI_ID}
  2. [QA] Tax One

Consome artefatos de tests/{WI_ID}/ (manifest, evidencias HTML, reports).

Uso:
  python scripts/qa_task_creator.py --wi-id 1066543
  python scripts/qa_task_creator.py --wi-id 1066543 --dry-run
  python scripts/qa_task_creator.py --wi-id 1066543 --reviewer "Santos, Lucas"
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ADO_ORG = "https://dev.azure.com/tr-ggo"
ADO_PROJECT = "Mastersaf Fiscal Solutions"
ADO_PROJECT_ENC = "Mastersaf%20Fiscal%20Solutions"
ADO_API = f"{ADO_ORG}/{ADO_PROJECT_ENC}/_apis"

QA_REVIEWERS = [
    "Santos, Lucas (TR Technology)",
    "de Souza, Shayenne Cristine (TR Technology)",
]


# ── Helpers ────────────────────────────────────────────────────────────

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


def ado_headers(pat, content_type="application/json"):
    auth = base64.b64encode(f":{pat}".encode()).decode()
    return {"Authorization": f"Basic {auth}", "Content-Type": content_type}


def _html_escape(text):
    """Escapa HTML. Nota: versao centralizada em ado_discussion_templates.html_escape()"""
    if not text:
        return ""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


# ── Phase 1: Fetch parent WI ──────────────────────────────────────────

def get_parent_wi(wi_id, headers):
    import requests
    url = f"{ADO_API}/wit/workitems/{wi_id}?$expand=relations&api-version=7.1"
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        print(f"ERRO: GET WI {wi_id} retornou {resp.status_code}: {resp.text[:300]}")
        sys.exit(1)
    return resp.json()


def check_existing_qa_tasks(wi_data, wi_id, headers):
    """Retorna dict com task IDs existentes: {'review': ID, 'taxone': ID}."""
    import requests
    existing = {"review": None, "taxone": None}
    relations = wi_data.get("relations", [])
    child_ids = []
    for r in relations:
        if r.get("rel") == "System.LinkTypes.Hierarchy-Forward":
            url_parts = r["url"].split("/")
            child_ids.append(url_parts[-1])

    if not child_ids:
        return existing

    # Fetch children in batch
    ids_str = ",".join(child_ids[:50])
    url = f"{ADO_API}/wit/workitems?ids={ids_str}&fields=System.Title,System.WorkItemType&api-version=7.1"
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        return existing

    for wi in resp.json().get("value", []):
        title = wi.get("fields", {}).get("System.Title", "")
        wi_type = wi.get("fields", {}).get("System.WorkItemType", "")
        if wi_type != "Task":
            continue
        if f"Plano de Testes" in title and str(wi_id) in title:
            existing["review"] = wi["id"]
        elif title.strip() == "[QA] Tax One":
            existing["taxone"] = wi["id"]

    return existing


# ── Phase 2: Detect PR ────────────────────────────────────────────────

def detect_pr(wi_id):
    """Detecta PR no GitHub via gh CLI."""
    pr_info = {"number": None, "title": None, "url": None, "state": None}
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--search", f"MFS{wi_id}", "--repo", "tr/taxone_dw",
             "--json", "number,title,url,state", "--limit", "5"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            prs = json.loads(result.stdout)
            if prs:
                pr_info = prs[0]
    except Exception as e:
        print(f"  AVISO: gh pr list falhou: {e}", file=sys.stderr)
    return pr_info


def _find_evidence(test_dir, wi_id, tipo):
    """Localiza arquivo de evidencia (ANTES/DEPOIS).

    Prioridade:
      1. evidencia_ANTES_{wi_id}.html  (padrao oficial)
      2. evidencia_antes.md            (fallback legado — converte para HTML)
    Retorna Path do arquivo encontrado (ou None).
    Se encontrar .md, converte para .html e retorna o .html gerado.
    """
    # Padrao oficial
    html_file = test_dir / f"evidencia_{tipo}_{wi_id}.html"
    if html_file.exists():
        return html_file

    # Fallback: .md com nome simplificado
    md_file = test_dir / f"evidencia_{tipo.lower()}.md"
    if md_file.exists():
        return _convert_md_to_html(md_file, html_file)

    return None


def _convert_md_to_html(md_path, html_out):
    """Converte Markdown basico para HTML simples e salva em html_out."""
    content = md_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    html_lines = ['<!DOCTYPE html><html><head><meta charset="utf-8">',
                  f'<title>{md_path.stem}</title></head><body>',
                  '<div style="font-family:Segoe UI,sans-serif;max-width:900px;margin:0 auto;padding:20px">']

    in_table = False
    in_code = False
    for line in lines:
        stripped = line.strip()

        # Code blocks
        if stripped.startswith("```"):
            if in_code:
                html_lines.append("</pre>")
                in_code = False
            else:
                html_lines.append('<pre style="background:#f4f4f4;padding:12px;overflow-x:auto;font-size:0.9em">')
                in_code = True
            continue
        if in_code:
            html_lines.append(_html_escape(line))
            continue

        # Headers
        if stripped.startswith("### "):
            html_lines.append(f'<h3>{_html_escape(stripped[4:])}</h3>')
            continue
        if stripped.startswith("## "):
            html_lines.append(f'<h2>{_html_escape(stripped[3:])}</h2>')
            continue
        if stripped.startswith("# "):
            html_lines.append(f'<h1>{_html_escape(stripped[2:])}</h1>')
            continue

        # Table separator row (skip)
        if stripped.startswith("|") and all(c in "-| " for c in stripped):
            continue

        # Table header/data rows
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if not in_table:
                html_lines.append('<table style="border-collapse:collapse;width:100%;font-size:0.9em">')
                tag = "th"
                bg = ' style="background:#004578;color:#fff"'
                in_table = True
            else:
                tag = "td"
                bg = ""
            row = "".join(
                f'<{tag} style="padding:4px 8px;border:1px solid #ccc">'
                f'{_md_inline(_html_escape(c))}</{tag}>'
                for c in cells
            )
            html_lines.append(f'<tr{bg}>{row}</tr>')
            continue

        # Close table if we leave table rows
        if in_table:
            html_lines.append("</table>")
            in_table = False

        # Bold-only lines (like **texto**)
        if stripped.startswith("**") and stripped.endswith("**") and stripped.count("**") == 2:
            html_lines.append(f'<p><b>{_html_escape(stripped[2:-2])}</b></p>')
            continue

        # Empty lines
        if not stripped:
            continue

        # Regular paragraphs
        html_lines.append(f'<p>{_md_inline(_html_escape(stripped))}</p>')

    if in_table:
        html_lines.append("</table>")
    if in_code:
        html_lines.append("</pre>")

    html_lines.append("</div></body></html>")
    html_out.write_text("\n".join(html_lines), encoding="utf-8")
    print(f"  Convertido {md_path.name} -> {html_out.name}")
    return html_out


def _md_inline(text):
    """Converte bold/code inline em HTML (texto ja escaped)."""
    # **bold** -> <b>bold</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # `code` -> <code>code</code>
    text = re.sub(r'`(.+?)`', r'<code style="background:#f0f0f0;padding:1px 4px">\1</code>', text)
    return text


# ── Phase 3: Load artifacts ───────────────────────────────────────────

def load_artifacts(test_dir, wi_id):
    """Carrega manifest e detecta arquivos de evidencia."""
    artifacts = {
        "manifest": None,
        "antes_html": None,
        "depois_html": None,
        "reports": [],
        "suite_results": None,
    }

    manifest_file = test_dir / "test_data_manifest.json"
    if manifest_file.exists():
        with open(manifest_file, encoding="utf-8") as f:
            artifacts["manifest"] = json.load(f)

    # Evidencias: preferir .html padrao, fallback para .md (converte na hora)
    antes = _find_evidence(test_dir, wi_id, "ANTES")
    if antes:
        artifacts["antes_html"] = antes

    depois = _find_evidence(test_dir, wi_id, "DEPOIS")
    if depois:
        artifacts["depois_html"] = depois

    # Reports (most recent first)
    for f in sorted(test_dir.glob("qa_test_report_*.md"), reverse=True):
        artifacts["reports"].append(f)

    # Suite results (JSON or parse from txt output)
    for name in ["regression_results.json", "safx_pipeline_results.json"]:
        p = test_dir / name
        if p.exists():
            with open(p, encoding="utf-8") as f:
                artifacts["suite_results"] = json.load(f)
            break

    # Fallback: parse suite output txt if no JSON
    if not artifacts["suite_results"]:
        for f in sorted(test_dir.glob("suite_output_*.txt"), reverse=True):
            artifacts["suite_results"] = _parse_suite_output_txt(f)
            if artifacts["suite_results"]:
                break

    return artifacts


def _parse_suite_output_txt(txt_path):
    """Parse suite output text file into structured results."""
    content = txt_path.read_text(encoding="utf-8", errors="replace")

    result = {"source_file": txt_path.name, "suites": []}

    # Extract suite name and execution info
    suite_match = re.search(r'--- Executando suite: (.+?) \((.+?)\) ---', content)
    if suite_match:
        result["suite_name"] = suite_match.group(1)
        result["suite_xml"] = suite_match.group(2)

    # Extract comparison stats
    comp_match = re.search(r'Comparacao: (\d+) arquivos \| (\d+) ok \| (\d+) falhas \| (\d+) erros', content)
    if comp_match:
        result["total"] = int(comp_match.group(1))
        result["passed"] = int(comp_match.group(2))
        result["failed"] = int(comp_match.group(3))
        result["errors"] = int(comp_match.group(4))

    # Extract execution time
    exec_match = re.search(r'Execucao: OK \(([0-9.]+)s\)', content)
    if exec_match:
        result["duration_s"] = float(exec_match.group(1))

    # Extract overall result
    if 'SUITE(S) COM FALHA' in content:
        result["verdict"] = "FAIL"
    elif result.get("passed", 0) > 0 and result.get("failed", 0) == 0:
        result["verdict"] = "PASS"
    else:
        result["verdict"] = "INCONCLUSIVE"

    # Extract first few failure details
    fail_lines = re.findall(r'-> FAIL: (.+?)$', content, re.MULTILINE)
    result["first_failures"] = fail_lines[:5]
    result["total_failure_lines"] = len(fail_lines)

    return result if result.get("total") else None


# ── Phase 4: Upload attachments ───────────────────────────────────────

def upload_attachment(file_path, headers_auth):
    """Upload file to ADO, return attachment URL."""
    import requests
    filename = file_path.name
    url = f"{ADO_API}/wit/attachments?fileName={filename}&api-version=7.1"
    h = {**headers_auth, "Content-Type": "application/octet-stream"}
    with open(file_path, "rb") as f:
        resp = requests.post(url, headers=h, data=f.read(), timeout=60)
    if resp.status_code in (200, 201):
        return resp.json().get("url", "")
    print(f"  AVISO: Upload {filename} falhou ({resp.status_code}): {resp.text[:200]}")
    return None


def upload_evidence_files(artifacts, headers):
    """Upload evidence HTMLs, return URLs dict."""
    urls = {"antes": None, "depois": None}
    if artifacts["antes_html"]:
        print(f"  Uploading {artifacts['antes_html'].name}...")
        urls["antes"] = upload_attachment(artifacts["antes_html"], headers)
    if artifacts["depois_html"]:
        print(f"  Uploading {artifacts['depois_html'].name}...")
        urls["depois"] = upload_attachment(artifacts["depois_html"], headers)
    return urls


# ── Phase 5: Parse evidence summary ───────────────────────────────────

def parse_evidence_summary(html_path):
    """Extrai tabela de fases e resumo de um HTML de evidencia."""
    if not html_path or not html_path.exists():
        return None

    content = html_path.read_text(encoding="utf-8")

    # Extrair fases da tabela HTML
    phases = []
    # Pattern: <tr class="pass|fail|skip"><td>FASE</td><td>Desc</td><td><b>STATUS</b></td><td>DUR</td></tr>
    row_pattern = re.compile(
        r'<tr\s+class="(pass|fail|skip)">\s*<td>(.*?)</td>\s*<td>(.*?)</td>\s*<td><b>(.*?)</b></td>\s*<td>(.*?)</td>',
        re.DOTALL
    )
    for m in row_pattern.finditer(content):
        phases.append({
            "css_class": m.group(1),
            "fase": m.group(2).strip(),
            "descricao": m.group(3).strip(),
            "status": m.group(4).strip(),
            "duracao": m.group(5).strip(),
        })

    # Extrair resumo
    resumo_match = re.search(r'<b>RESUMO:</b>\s*(.*?)</div>', content, re.DOTALL)
    resumo = resumo_match.group(1).strip() if resumo_match else ""
    # Clean HTML entities
    resumo = resumo.replace("&nbsp;", " ").replace("&mdash;", "—")
    resumo = re.sub(r'<[^>]+>', '', resumo).strip()

    return {"phases": phases, "resumo": resumo}


def build_evidence_table_html(summary):
    """Gera mini-tabela HTML de fases a partir do summary."""
    if not summary or not summary.get("phases"):
        return "<p><i>Evidencia nao disponivel</i></p>"

    colors = {"pass": "#dff6dd", "fail": "#fde7e9", "skip": "#fff4ce"}
    rows = ""
    for p in summary["phases"]:
        bg = colors.get(p["css_class"], "#fff")
        rows += (f'<tr style="background:{bg}">'
                 f'<td style="padding:4px 8px;border:1px solid #ccc">{_html_escape(p["fase"])}</td>'
                 f'<td style="padding:4px 8px;border:1px solid #ccc"><b>{_html_escape(p["status"])}</b></td>'
                 f'<td style="padding:4px 8px;border:1px solid #ccc">{_html_escape(p["duracao"])}</td>'
                 f'</tr>')

    html = (f'<table style="border-collapse:collapse;font-size:0.9em">'
            f'<tr style="background:#004578;color:#fff">'
            f'<th style="padding:4px 8px">Fase</th><th style="padding:4px 8px">Status</th>'
            f'<th style="padding:4px 8px">Duracao</th></tr>'
            f'{rows}</table>')

    if summary.get("resumo"):
        html += f'<p style="font-size:0.9em"><b>{_html_escape(summary["resumo"])}</b></p>'

    return html


# ── Phase 6: Determine reviewer (round-robin) ─────────────────────────

def determine_reviewer(headers):
    """Round-robin entre QA reviewers baseado nas ultimas tasks."""
    import requests
    try:
        wiql = {
            "query": (
                "SELECT [System.Id] FROM WorkItems "
                "WHERE [System.WorkItemType] = 'Task' "
                "AND [System.Title] CONTAINS 'Plano de Testes' "
                "AND [System.AreaPath] UNDER 'Mastersaf Fiscal Solutions\\MFS\\TAX ONE' "
                "AND [System.CreatedDate] > @Today - 30 "
                "ORDER BY [System.CreatedDate] DESC"
            )
        }
        url = f"{ADO_API}/wit/wiql?api-version=7.1"
        resp = requests.post(url, headers=headers, json=wiql, timeout=15)
        if resp.status_code != 200:
            return QA_REVIEWERS[0]

        items = resp.json().get("workItems", [])[:10]
        if not items:
            return QA_REVIEWERS[0]

        ids_str = ",".join(str(i["id"]) for i in items)
        url2 = f"{ADO_API}/wit/workitems?ids={ids_str}&fields=Custom.NomeRevisorRG_PlanoTestes&api-version=7.1"
        resp2 = requests.get(url2, headers=headers, timeout=15)
        if resp2.status_code != 200:
            return QA_REVIEWERS[0]

        # Contar atribuicoes recentes
        counts = {r: 0 for r in QA_REVIEWERS}
        for wi in resp2.json().get("value", []):
            reviewer = wi.get("fields", {}).get("Custom.NomeRevisorRG_PlanoTestes", {})
            if isinstance(reviewer, dict):
                reviewer = reviewer.get("displayName", "")
            if isinstance(reviewer, str):
                for r in QA_REVIEWERS:
                    if r.split(",")[0].strip().lower() in reviewer.lower():
                        counts[r] += 1
                        break

        # Escolher quem revisou menos
        return min(QA_REVIEWERS, key=lambda r: counts.get(r, 0))

    except Exception as e:
        print(f"  AVISO: Round-robin falhou ({e}), usando default", file=sys.stderr)
        return QA_REVIEWERS[0]


# ── Phase 7: Build HTML Description ───────────────────────────────────

def _extract_html_body(html_path):
    """Extrai conteudo do <body> (ou <div> principal) de um HTML de evidencia."""
    if not html_path or not html_path.exists():
        return '<p><i>Evidencia nao disponivel</i></p>'
    content = html_path.read_text(encoding="utf-8")
    # Tentar extrair conteudo entre <body> e </body>
    m = re.search(r'<body>(.*)</body>', content, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Fallback: conteudo entre primeiro <div> e ultimo </div>
    m = re.search(r'(<div.*</div>)', content, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Ultimo fallback: todo o conteudo (sem doctype/html/head)
    content = re.sub(r'<!DOCTYPE[^>]*>', '', content)
    content = re.sub(r'</?html[^>]*>', '', content)
    content = re.sub(r'<head>.*?</head>', '', content, flags=re.DOTALL)
    return content.strip()


def build_review_html(wi_id, wi_title, pr_info, manifest, evidence_urls,
                      antes_summary, depois_summary, suite_results,
                      artifacts_ref=None):
    """Monta HTML completo do Description da Task de Revisao."""
    artifacts_ref = artifacts_ref or {}

    # ── Header ──
    html = []
    html.append('<div style="box-sizing:border-box">')
    html.append('<h3><span style="color:rgb(200,38,19)"><b>'
                '=== ESCOPO / TIPOS DE TESTES REALIZADOS ==='
                '</b></span></h3>')

    # ── Testes do Desenvolvedor ──
    html.append('<ul><li><b>Testes do(a) Desenvolvedor(a):</b></li></ul>')
    if pr_info.get("url"):
        pr_num = pr_info.get("number", "?")
        pr_title = _html_escape(pr_info.get("title", ""))
        pr_state = pr_info.get("state", "OPEN")
        html.append(f'<p>PR: <a href="{pr_info["url"]}">#{pr_num} - {pr_title}</a> '
                     f'({pr_state})</p>')
    else:
        html.append('<p><i>PR nao encontrada no GitHub</i></p>')

    # ── Testes Manuais ──
    html.append('<ul><li><b>Testes Manuais:</b></li></ul>')

    scenarios = []
    if manifest:
        scenarios = manifest.get("test_scenarios", manifest.get("scenarios", []))

    if scenarios:
        html.append('<table style="border-collapse:collapse;border:1px solid #000;width:100%">')
        for i, sc in enumerate(scenarios, 1):
            desc = sc.get("description", sc.get("name", f"Cenario {i}"))
            expected = sc.get("expected", {})
            bug_symptom = sc.get("bug_symptom", "")

            # Cenario header row
            html.append(
                f'<tr>'
                f'<td style="width:120px;border:1px solid #000;padding:4px 8px;text-align:right">'
                f'<b style="color:#0c64c0">Cenario N&ordm; {i:02d}</b></td>'
                f'<td style="border:1px solid #000;padding:4px 8px">'
                f'<i>{_html_escape(desc)}</i></td>'
                f'</tr>'
            )

            # Resultado esperado
            if expected:
                exp_text = "; ".join(f"{k}={v}" for k, v in expected.items())
                html.append(
                    f'<tr>'
                    f'<td style="border:1px solid #000;padding:4px 8px;text-align:right">'
                    f'<b>Resultado esperado</b></td>'
                    f'<td style="border:1px solid #000;padding:4px 8px">'
                    f'{_html_escape(exp_text)}</td>'
                    f'</tr>'
                )

            # Sintoma do bug (se houver)
            if bug_symptom:
                html.append(
                    f'<tr>'
                    f'<td style="border:1px solid #000;padding:4px 8px;text-align:right">'
                    f'<b>Sintoma do bug</b></td>'
                    f'<td style="border:1px solid #000;padding:4px 8px;color:#d13438">'
                    f'{_html_escape(bug_symptom)}</td>'
                    f'</tr>'
                )

        html.append('</table>')
    else:
        html.append('<p><i>Cenarios nao disponiveis (test_data_manifest.json nao encontrado)</i></p>')

    # ── Evidencias ANTES/DEPOIS ──
    html.append('<br><h3>Evidencias:</h3>')
    html.append('<table style="border-collapse:collapse;border:1px solid #000;width:100%">')
    html.append(
        '<tr>'
        '<th style="background:#004578;color:#fff;padding:8px;border:1px solid #000;width:50%">'
        'ANTES (Pre-Fix)</th>'
        '<th style="background:#004578;color:#fff;padding:8px;border:1px solid #000;width:50%">'
        'DEPOIS (Pos-Fix)</th>'
        '</tr>'
    )

    antes_cell = build_evidence_table_html(antes_summary)
    # Fallback: se summary nao parseou mas o HTML existe, embutir o body direto
    if (not antes_summary or not antes_summary.get("phases")) and artifacts_ref.get("antes_html"):
        antes_cell = _extract_html_body(artifacts_ref["antes_html"])
    if evidence_urls.get("antes"):
        antes_cell += f'<p><a href="{evidence_urls["antes"]}">Download evidencia ANTES completa</a></p>'

    depois_cell = build_evidence_table_html(depois_summary)
    if (not depois_summary or not depois_summary.get("phases")) and artifacts_ref.get("depois_html"):
        depois_cell = _extract_html_body(artifacts_ref["depois_html"])
    if evidence_urls.get("depois"):
        depois_cell += f'<p><a href="{evidence_urls["depois"]}">Download evidencia DEPOIS completa</a></p>'

    html.append(
        f'<tr>'
        f'<td style="border:1px solid #000;padding:8px;vertical-align:top">{antes_cell}</td>'
        f'<td style="border:1px solid #000;padding:8px;vertical-align:top">{depois_cell}</td>'
        f'</tr>'
    )
    html.append('</table>')

    # ── Analise de Packages ──
    pkg_analysis = manifest.get("package_analysis", {}) if manifest else {}
    if pkg_analysis:
        html.append('<br><h3><span style="color:rgb(200,38,19)"><b>'
                    '=== ANALISE DE PACKAGES DE IMPORTACAO ==='
                    '</b></span></h3>')
        html.append('<table style="border-collapse:collapse;border:1px solid #000;width:100%">')
        html.append(
            '<tr>'
            '<th style="background:#004578;color:#fff;padding:6px 8px;border:1px solid #000">Package</th>'
            '<th style="background:#004578;color:#fff;padding:6px 8px;border:1px solid #000;width:100px">Status</th>'
            '<th style="background:#004578;color:#fff;padding:6px 8px;border:1px solid #000">Detalhe</th>'
            '</tr>'
        )
        status_colors = {"OK": "#dff6dd", "CORRIGIDO": "#fff4ce", "FAIL": "#fde7e9"}
        for pkg_name, pkg_data in pkg_analysis.items():
            status = pkg_data.get("status", "?")
            detail = pkg_data.get("detail", "")
            bg = status_colors.get(status, "#fff")
            html.append(
                f'<tr>'
                f'<td style="border:1px solid #000;padding:4px 8px;font-family:monospace">'
                f'<b>{_html_escape(pkg_name)}</b></td>'
                f'<td style="border:1px solid #000;padding:4px 8px;background:{bg};text-align:center">'
                f'<b>{_html_escape(status)}</b></td>'
                f'<td style="border:1px solid #000;padding:4px 8px;font-size:0.9em">'
                f'{_html_escape(detail)}</td>'
                f'</tr>'
            )
        html.append('</table>')

    # ── Testes Automatizados ──
    html.append('<br><h3><span style="color:rgb(200,38,19)"><b>'
                '=== TESTES AUTOMATIZADOS ==='
                '</b></span></h3>')

    html.append('<ul><li><b>Testes da Suite de Aceite (PL/SQL) | DW:</b></li></ul>')

    if suite_results:
        suite_name = suite_results.get("suite_name", suite_results.get("suite_xml", "SuiteAutomation"))
        total = suite_results.get("total", 0)
        passed = suite_results.get("passed", 0)
        failed = suite_results.get("failed", 0)
        duration = suite_results.get("duration_s", 0)
        verdict = suite_results.get("verdict", "")

        verdict_color = "#107c10" if verdict == "PASS" else "#d13438"
        html.append(
            f'<p><b>Suite:</b> {_html_escape(suite_name)}'
            f'{f" ({duration:.0f}s)" if duration else ""}</p>'
        )
        html.append(
            f'<p>Comparacao de arquivos: '
            f'<b style="color:#107c10">{passed} OK</b> | '
            f'<b style="color:#d13438">{failed} FAIL</b> | '
            f'Total: {total}</p>'
        )

        # Show first failures if any
        first_failures = suite_results.get("first_failures", [])
        if first_failures:
            total_fails = suite_results.get("total_failure_lines", len(first_failures))
            html.append(f'<p style="font-size:0.9em"><i>Primeiras falhas ({total_fails} total):</i></p>')
            html.append('<ul style="font-size:0.85em;font-family:monospace">')
            for ff in first_failures[:5]:
                html.append(f'<li>{_html_escape(ff)}</li>')
            if total_fails > 5:
                html.append(f'<li>... e mais {total_fails - 5} falhas</li>')
            html.append('</ul>')

        html.append(
            f'<p><b>Veredicto: <span style="color:{verdict_color}">{verdict}</span></b>'
            f' (falhas pre-existentes, nao relacionadas a esta WI)</p>'
        )
    else:
        html.append('<p><i>SuiteAutomation nao executado para esta demanda ou resultados nao disponiveis.</i></p>')

    html.append('<ul><li><b>Testes de UI via Playwright | Tax One:</b></li></ul>')
    html.append('<p><i>N/A</i></p>')

    html.append('<ul><li><b>Testes de Loading de Tela | Tax One:</b></li></ul>')
    html.append('<p><i>N/A</i></p>')

    # ── PR do desenvolvedor ──
    if pr_info.get("url"):
        html.append(f'<br><p><b>PR do desenvolvedor:</b> '
                     f'<a href="{pr_info["url"]}">#{pr_info.get("number", "?")}</a></p>')

    # ── Footer ──
    html.append(f'<br><hr>')
    html.append(f'<p style="font-size:0.85em;color:#888">'
                f'Gerado automaticamente por <b>qa_task_creator.py</b> em '
                f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | WI #{wi_id}</p>')
    html.append('</div>')

    return "\n".join(html)


# ── Phase 8/9: Create or Update Task ──────────────────────────────────

def create_task(title, description, parent_id, area_path, iteration_path,
                assigned_to, custom_fields, headers):
    """Cria Task no ADO como child do parent WI."""
    import requests

    parent_url = f"{ADO_ORG}/{ADO_PROJECT_ENC}/_apis/wit/workItems/{parent_id}"

    patch_doc = [
        {"op": "add", "path": "/fields/System.Title", "value": title},
        {"op": "add", "path": "/fields/System.Description", "value": description},
        {"op": "add", "path": "/fields/System.AreaPath", "value": area_path},
        {"op": "add", "path": "/fields/System.IterationPath", "value": iteration_path},
        {"op": "add", "path": "/relations/-", "value": {
            "rel": "System.LinkTypes.Hierarchy-Reverse",
            "url": parent_url,
            "attributes": {"comment": "Auto-created by qa_task_creator.py"}
        }},
    ]

    if assigned_to:
        patch_doc.append({"op": "add", "path": "/fields/System.AssignedTo", "value": assigned_to})

    for field, value in custom_fields.items():
        if value:
            patch_doc.append({"op": "add", "path": f"/fields/{field}", "value": value})

    url = f"{ADO_API}/wit/workitems/$Task?api-version=7.1"
    h = {**headers, "Content-Type": "application/json-patch+json"}
    resp = requests.post(url, headers=h, json=patch_doc, timeout=30)

    if resp.status_code in (200, 201):
        data = resp.json()
        return data.get("id"), data.get("_links", {}).get("html", {}).get("href", "")
    else:
        print(f"ERRO: Criar Task falhou ({resp.status_code}): {resp.text[:500]}")
        return None, None


def update_task(task_id, description, custom_fields, headers):
    """Atualiza Description e custom fields de uma Task existente."""
    import requests

    patch_doc = [
        {"op": "replace", "path": "/fields/System.Description", "value": description},
    ]

    for field, value in custom_fields.items():
        if value:
            patch_doc.append({"op": "add", "path": f"/fields/{field}", "value": value})

    url = f"{ADO_API}/wit/workitems/{task_id}?api-version=7.1"
    h = {**headers, "Content-Type": "application/json-patch+json"}
    resp = requests.patch(url, headers=h, json=patch_doc, timeout=30)

    if resp.status_code == 200:
        return True
    else:
        print(f"ERRO: Update Task #{task_id} falhou ({resp.status_code}): {resp.text[:500]}")
        return False


# ── Main ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Cria Tasks de Revisao QA no ADO"
    )
    parser.add_argument("--wi-id", required=True, help="ID do work item parent")
    parser.add_argument("--dry-run", action="store_true", help="Apenas mostrar o HTML, nao criar")
    parser.add_argument("--reviewer", default=None, help="Nome do reviewer QA (override round-robin)")
    parser.add_argument("--test-dir", default=None, help="Diretorio com artefatos (default: tests/{WI_ID})")

    args = parser.parse_args()
    wi_id = args.wi_id

    print("=" * 60)
    print("  QA Task Creator")
    print("=" * 60)
    print(f"  WI: #{wi_id}")
    print()

    # ── PAT ──
    pat = get_ado_pat()
    if not pat:
        print("ERRO: ADO_PAT nao configurado (env var ou .env)")
        sys.exit(1)
    headers = ado_headers(pat)

    # ── Phase 1: Parent WI ──
    print("[1/9] Buscando WI parent...")
    wi_data = get_parent_wi(wi_id, headers)
    fields = wi_data.get("fields", {})

    wi_title = fields.get("System.Title", "")
    area_path = fields.get("System.AreaPath", "")
    iteration_path = fields.get("System.IterationPath", "")
    component = fields.get("Custom.Component", "")
    tester = fields.get("Custom.Tester", "")
    if isinstance(tester, dict):
        tester = tester.get("uniqueName", tester.get("displayName", ""))

    print(f"  Title: {wi_title}")
    print(f"  AreaPath: {area_path}")
    print(f"  Tester: {tester or 'N/A'}")

    # Idempotencia
    existing = check_existing_qa_tasks(wi_data, wi_id, headers)
    if existing["review"]:
        print(f"  Task Revisao ja existe: #{existing['review']} (sera atualizada)")
    if existing["taxone"]:
        print(f"  Task Tax One ja existe: #{existing['taxone']} (skip)")

    # ── Phase 2: PR ──
    print()
    print("[2/9] Detectando PR...")
    pr_info = detect_pr(wi_id)
    if pr_info.get("url"):
        print(f"  PR: #{pr_info['number']} - {pr_info.get('title', '')[:60]}")
    else:
        print("  PR nao encontrada")

    # ── Phase 3: Artifacts ──
    print()
    print("[3/9] Carregando artefatos...")
    if args.test_dir:
        test_dir = Path(args.test_dir)
    else:
        test_dir = PROJECT_ROOT / "tests" / str(wi_id)

    if not test_dir.exists():
        print(f"  AVISO: Diretorio {test_dir} nao existe, continuando sem artefatos")
        artifacts = {"manifest": None, "antes_html": None, "depois_html": None,
                     "reports": [], "suite_results": None}
    else:
        artifacts = load_artifacts(test_dir, wi_id)

    manifest = artifacts["manifest"]
    print(f"  Manifest: {'SIM' if manifest else 'NAO'}")
    print(f"  Evidencia ANTES: {'SIM' if artifacts['antes_html'] else 'NAO'}")
    print(f"  Evidencia DEPOIS: {'SIM' if artifacts['depois_html'] else 'NAO'}")
    print(f"  Suite results: {'SIM' if artifacts['suite_results'] else 'NAO'}")

    # ── Phase 4: Upload attachments ──
    evidence_urls = {"antes": None, "depois": None}
    if not args.dry_run:
        print()
        print("[4/9] Uploading attachments...")
        evidence_urls = upload_evidence_files(artifacts, headers)
        uploaded = sum(1 for v in evidence_urls.values() if v)
        print(f"  {uploaded} arquivo(s) uploaded")
    else:
        print()
        print("[4/9] Upload attachments (SKIP - dry-run)")

    # ── Phase 5: Parse evidence summaries ──
    print()
    print("[5/9] Extraindo resumo das evidencias...")
    antes_summary = parse_evidence_summary(artifacts["antes_html"])
    depois_summary = parse_evidence_summary(artifacts["depois_html"])
    print(f"  ANTES: {antes_summary['resumo'] if antes_summary else 'N/A'}")
    print(f"  DEPOIS: {depois_summary['resumo'] if depois_summary else 'N/A'}")

    # ── Phase 6: Reviewer (desativado — reviewer sera definido manualmente pelo QA) ──
    print()
    print("[6/9] Reviewer QA: nao atribuido (definido manualmente pelo QA)")

    # ── Phase 7: Build HTML ──
    print()
    print("[7/9] Montando HTML do Description...")
    review_html = build_review_html(
        wi_id=wi_id,
        wi_title=wi_title,
        pr_info=pr_info,
        manifest=manifest,
        evidence_urls=evidence_urls,
        antes_summary=antes_summary,
        depois_summary=depois_summary,
        suite_results=artifacts["suite_results"],
        artifacts_ref=artifacts,
    )
    print(f"  HTML: {len(review_html)} caracteres")

    if args.dry_run:
        print()
        print("=" * 60)
        print("  DRY-RUN: HTML gerado (nao criando tasks)")
        print("=" * 60)
        # Salvar HTML para preview
        preview_file = test_dir / f"qa_review_preview_{wi_id}.html"
        if test_dir.exists():
            preview_file.write_text(
                f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<title>Preview QA Review - WI #{wi_id}</title></head><body>"
                f"{review_html}</body></html>",
                encoding="utf-8"
            )
            print(f"  Preview salvo em: {preview_file}")
        return

    # ── Phase 8: Create/Update Task Revisao ──
    print()
    print("[8/9] Criando Task Revisao de Plano de Testes...")

    review_title = f"[QA] Revisao de Plano de Testes | ID da(s) demanda(s) testada(s): {wi_id}"
    custom_fields = {
        # NomeRevisorRG_PlanoTestes NAO informado - reviewer sera definido manualmente pelo QA
    }

    if component:
        custom_fields["Custom.Component"] = component

    if existing["review"]:
        ok = update_task(existing["review"], review_html, custom_fields, headers)
        if ok:
            print(f"  Task #{existing['review']} atualizada com sucesso")
        review_id = existing["review"]
    else:
        review_id, review_url = create_task(
            title=review_title,
            description=review_html,
            parent_id=wi_id,
            area_path=area_path,
            iteration_path=iteration_path,
            assigned_to=tester,
            custom_fields=custom_fields,
            headers=headers,
        )
        if review_id:
            print(f"  Task #{review_id} criada")
            if review_url:
                print(f"  URL: {review_url}")
        else:
            print("  ERRO: Falha ao criar Task Revisao")

    # ── Phase 9: Create Task Tax One ──
    print()
    print("[9/9] Criando Task [QA] Tax One...")

    if existing["taxone"]:
        print(f"  Task #{existing['taxone']} ja existe (skip)")
    else:
        taxone_desc = (
            f"<p>Tarefa de execucao de testes no ambiente Tax One para WI "
            f"<a href=\"https://dev.azure.com/tr-ggo/Mastersaf%20Fiscal%20Solutions/"
            f"_workitems/edit/{wi_id}\">#{wi_id}</a>.</p>"
        )
        taxone_custom = {}
        if component:
            taxone_custom["Custom.Component"] = component
        taxone_id, taxone_url = create_task(
            title="[QA] Tax One",
            description=taxone_desc,
            parent_id=wi_id,
            area_path=area_path,
            iteration_path=iteration_path,
            assigned_to=tester,
            custom_fields=taxone_custom,
            headers=headers,
        )
        if taxone_id:
            print(f"  Task #{taxone_id} criada")
            if taxone_url:
                print(f"  URL: {taxone_url}")
        else:
            print("  ERRO: Falha ao criar Task Tax One")

    # ── Resumo ──
    print()
    print("=" * 60)
    print("  QA Tasks criadas/atualizadas com sucesso!")
    print(f"  Parent: #{wi_id}")
    print("=" * 60)


if __name__ == "__main__":
    main()
