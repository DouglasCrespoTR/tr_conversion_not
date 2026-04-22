#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ADO Evidence Uploader - Upload screenshots e relatorio de evidencia ao Azure DevOps.

Fluxo:
  1. Coleta screenshots de --screenshots-dir (PNG/JPG)
  2. Le resultados JSON do Playwright (--results-json)
  3. Upload cada arquivo ao ADO (POST _apis/wit/attachments)
  4. Attach ao WI via PATCH relations (AttachedFile)
  5. Post comentario HTML na WI com resumo

Uso:
  python ado_evidence_uploader.py --wi-id 1071803 --screenshots-dir tests/1071803/screenshots
  python ado_evidence_uploader.py --wi-id 1071803 --screenshots-dir tests/1071803/screenshots --results-json tests/1071803/targeted_e2e_results.json
  python ado_evidence_uploader.py --wi-id 1071803 --screenshots-dir tests/1071803/screenshots --dry-run
"""

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERRO: modulo 'requests' nao encontrado. pip install requests", file=sys.stderr)
    sys.exit(1)

from env_loader import _load_dotenv

# --- ADO Config ---
ADO_ORG = "https://dev.azure.com/tr-ggo"
ADO_PROJECT = "Mastersaf Fiscal Solutions"
API_VER = "7.1"

SCREENSHOT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _get_az_bearer_token():
    """Tenta obter bearer token via az CLI (Azure Identity)."""
    import subprocess
    try:
        result = subprocess.run(
            ["az", "account", "get-access-token",
             "--resource", "499b84ac-1321-427f-aa17-267ca6975798",
             "--query", "accessToken", "-o", "tsv"],
            capture_output=True, text=True, timeout=15,
            shell=(os.name == "nt"),
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _get_auth():
    """Resolve auth: az CLI bearer token (preferido) ou PAT (Basic).

    Returns:
        tuple (auth_type, token) where auth_type is 'Basic' or 'Bearer'
    """
    # Preferir bearer token (az CLI) — mais confiavel que PAT que pode expirar
    bearer = _get_az_bearer_token()
    if bearer:
        return ("Bearer", bearer)

    pat = os.environ.get("ADO_PAT", "")
    if not pat:
        dotenv = _load_dotenv()
        pat = dotenv.get("ADO_PAT", "")

    if pat:
        auth = base64.b64encode(f":{pat}".encode()).decode()
        return ("Basic", auth)

    return ("", "")


def _build_auth_headers(auth_tuple):
    """Build Authorization headers for ADO API.

    Args:
        auth_tuple: (auth_type, token) from _get_auth()
    """
    auth_type, token = auth_tuple
    auth_value = f"{auth_type} {token}"
    return {
        "json": {
            "Content-Type": "application/json",
            "Authorization": auth_value,
        },
        "json_patch": {
            "Content-Type": "application/json-patch+json",
            "Authorization": auth_value,
        },
        "octet": {
            "Content-Type": "application/octet-stream",
            "Authorization": auth_value,
        },
    }


def collect_screenshots(screenshots_dir):
    """Collect screenshot files from directory.

    Returns:
        List of (filepath, filename) tuples sorted by name.
    """
    screenshots_dir = Path(screenshots_dir)
    if not screenshots_dir.exists():
        return []

    files = []
    for f in sorted(screenshots_dir.iterdir()):
        if f.is_file() and f.suffix.lower() in SCREENSHOT_EXTENSIONS:
            files.append((str(f), f.name))

    return files


def upload_attachment(filepath, filename, headers):
    """Upload a single file to ADO and return attachment URL.

    Returns:
        str: Attachment URL or None on failure.
    """
    url = f"{ADO_ORG}/{ADO_PROJECT}/_apis/wit/attachments?fileName={filename}&api-version={API_VER}"
    with open(filepath, "rb") as f:
        data = f.read()

    resp = requests.post(url, headers=headers["octet"], data=data)
    if resp.status_code in (200, 201):
        att_url = resp.json().get("url", "")
        print(f"  OK: {filename} ({len(data) // 1024}KB)")
        return att_url
    else:
        print(f"  FAIL: {filename} -> HTTP {resp.status_code}: {resp.text[:200]}")
        return None


def attach_files_to_wi(wi_id, attachment_urls, headers):
    """Attach uploaded files to Work Item via PATCH relations.

    Args:
        wi_id: Work Item ID
        attachment_urls: List of (filename, att_url) tuples
        headers: Auth headers dict

    Returns:
        bool: True if successful
    """
    patch_ops = []
    for filename, att_url in attachment_urls:
        patch_ops.append({
            "op": "add",
            "path": "/relations/-",
            "value": {
                "rel": "AttachedFile",
                "url": att_url,
                "attributes": {"comment": f"Screenshot E2E - {filename}"},
            },
        })

    if not patch_ops:
        return True

    url = f"{ADO_ORG}/{ADO_PROJECT}/_apis/wit/workitems/{wi_id}?api-version={API_VER}"
    resp = requests.patch(url, headers=headers["json_patch"], json=patch_ops)

    if resp.status_code in (200, 201):
        result = resp.json()
        rels = [r for r in result.get("relations", []) if r.get("rel") == "AttachedFile"]
        print(f"  Attached {len(patch_ops)} files (WI has {len(rels)} total attachments)")
        return True
    else:
        print(f"  FAIL: Attach -> HTTP {resp.status_code}: {resp.text[:300]}")
        return False


def post_evidence_comment(wi_id, screenshots, results_data, headers):
    """Post HTML comment with evidence summary on the WI.

    Args:
        wi_id: Work Item ID
        screenshots: List of (filename, att_url) tuples
        results_data: Parsed test results dict (or None)
        headers: Auth headers dict

    Returns:
        bool: True if successful
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Build results summary
    results_html = ""
    if results_data:
        total = results_data.get("total", 0)
        passed = results_data.get("passed", 0)
        failed = results_data.get("failed", 0)
        skipped = results_data.get("skipped", 0)
        duration_s = results_data.get("duration_s", 0)

        status_color = "#107c10" if failed == 0 else "#cf1322"
        status_text = "PASS" if failed == 0 else "FAIL"
        status_emoji = "&#x2705;" if failed == 0 else "&#x274C;"

        results_html = f"""
<h3>{status_emoji} Resultado E2E: {status_text}</h3>
<table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;">
<tr style="background:#004578;color:#fff;">
  <th>Total</th><th>Pass</th><th>Fail</th><th>Skip</th><th>Duracao</th>
</tr>
<tr style="background:{'#dff6dd' if failed == 0 else '#fde7e9'};">
  <td>{total}</td><td>{passed}</td><td>{failed}</td><td>{skipped}</td><td>{duration_s}s</td>
</tr>
</table>
"""

        # Detail specs with failures
        specs = results_data.get("specs", [])
        failed_specs = [s for s in specs if s.get("status") == "FAIL"]
        if failed_specs:
            results_html += "<h4>Specs com Falha:</h4><ul>"
            for spec in failed_specs:
                error = spec.get("error", "")[:150]
                results_html += f'<li><b>{spec.get("file", "?")}</b>: {spec.get("title", "?")} - {error}</li>'
            results_html += "</ul>"

    # Build screenshots section
    screenshots_html = ""
    if screenshots:
        screenshots_html = f"<h3>Screenshots ({len(screenshots)})</h3><ul>"
        for filename, att_url in screenshots:
            screenshots_html += f'<li><a href="{att_url}">{filename}</a></li>'
        screenshots_html += "</ul>"

    html = f"""<h2>Evidencia de Testes E2E &mdash; WI #{wi_id}</h2>
<p><b>Gerado:</b> {timestamp} | <b>Agente:</b> targeted-e2e-runner</p>
{results_html}
{screenshots_html}
<hr>
<p style="color:#888;font-size:11px;">
Gerado automaticamente por <b>ado_evidence_uploader.py</b> em {timestamp}
</p>"""

    url = f"{ADO_ORG}/{ADO_PROJECT}/_apis/wit/workitems/{wi_id}/comments?api-version=7.1-preview.4"
    resp = requests.post(url, headers=headers["json"], json={"text": html})

    if resp.status_code in (200, 201):
        comment_id = resp.json().get("id", "?")
        print(f"  Comment posted: ID={comment_id}")
        return True
    else:
        print(f"  FAIL: Comment -> HTTP {resp.status_code}: {resp.text[:300]}")
        return False


def main():
    parser = argparse.ArgumentParser(description="ADO Evidence Uploader - Screenshots + Relatorio E2E")
    parser.add_argument("--wi-id", required=True, type=int, help="Work Item ID no Azure DevOps")
    parser.add_argument("--screenshots-dir", required=True, help="Diretorio com screenshots (PNG/JPG)")
    parser.add_argument("--results-json", help="JSON de resultados do Playwright (targeted_e2e_results.json)")
    parser.add_argument("--dry-run", action="store_true", help="Apenas listar arquivos, nao fazer upload")

    args = parser.parse_args()

    print("=" * 60)
    print(f"  ADO Evidence Uploader - WI #{args.wi_id}")
    print(f"  Timestamp: {datetime.now().isoformat()}")
    print("=" * 60)

    # 1. Collect screenshots
    print(f"\n[1] Coletando screenshots de: {args.screenshots_dir}")
    screenshots = collect_screenshots(args.screenshots_dir)
    print(f"  Encontrados: {len(screenshots)} arquivo(s)")
    for filepath, filename in screenshots:
        size_kb = os.path.getsize(filepath) // 1024
        print(f"    - {filename} ({size_kb}KB)")

    if not screenshots:
        print("  Nenhum screenshot encontrado. Nada a fazer.")
        return 0

    # 2. Load test results (optional)
    results_data = None
    if args.results_json and Path(args.results_json).exists():
        print(f"\n[2] Carregando resultados: {args.results_json}")
        try:
            results_data = json.loads(Path(args.results_json).read_text(encoding="utf-8"))
            print(f"  Total: {results_data.get('total', '?')} specs, "
                  f"Pass: {results_data.get('passed', '?')}, "
                  f"Fail: {results_data.get('failed', '?')}")
        except (json.JSONDecodeError, Exception) as e:
            print(f"  Aviso: Nao foi possivel ler resultados: {e}")
    else:
        print("\n[2] Sem arquivo de resultados (--results-json nao fornecido ou nao existe)")

    # Dry run - stop here
    if args.dry_run:
        print("\n[DRY RUN] Os seguintes arquivos seriam enviados:")
        for filepath, filename in screenshots:
            print(f"  - {filename}")
        print(f"\n  Total: {len(screenshots)} arquivo(s) para WI #{args.wi_id}")
        return 0

    # 3. Authenticate
    auth = _get_auth()
    if not auth[1]:
        print("\nERRO: ADO_PAT nao encontrado e az CLI indisponivel. Configure em .env ou faca 'az login'.", file=sys.stderr)
        return 1
    print(f"  Auth: {auth[0]}", file=sys.stderr)
    headers = _build_auth_headers(auth)

    # 4. Upload screenshots
    print(f"\n[3] Uploading {len(screenshots)} arquivo(s) ao ADO...")
    uploaded = []
    for filepath, filename in screenshots:
        att_url = upload_attachment(filepath, filename, headers)
        if att_url:
            uploaded.append((filename, att_url))

    print(f"  Upload: {len(uploaded)}/{len(screenshots)} OK")

    if not uploaded:
        print("ERRO: Nenhum arquivo foi uploaded com sucesso.", file=sys.stderr)
        return 1

    # 5. Attach to WI
    print(f"\n[4] Attaching {len(uploaded)} arquivo(s) a WI #{args.wi_id}...")
    attach_ok = attach_files_to_wi(args.wi_id, uploaded, headers)

    # 6. Post comment
    print(f"\n[5] Postando comentario de evidencia na WI #{args.wi_id}...")
    comment_ok = post_evidence_comment(args.wi_id, uploaded, results_data, headers)

    # Summary
    print("\n" + "=" * 60)
    if attach_ok and comment_ok:
        print(f"  SUCESSO: {len(uploaded)} screenshots + comentario na WI #{args.wi_id}")
    elif attach_ok:
        print(f"  PARCIAL: {len(uploaded)} screenshots attached, mas comentario falhou")
    else:
        print(f"  ERRO: Falha ao anexar arquivos a WI #{args.wi_id}")
    print("=" * 60)

    return 0 if (attach_ok and comment_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
