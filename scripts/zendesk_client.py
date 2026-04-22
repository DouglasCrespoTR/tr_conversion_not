#!/usr/bin/env python3
"""
Zendesk Client - Busca tickets do Zendesk para enriquecimento de Work Items.

Trata SSL corporativo + encoding Windows. Usa API v2 do Zendesk.

Usage:
  python zendesk_client.py --ticket 123456 --comments --format json
  python zendesk_client.py --ticket 123456 --format text
  python zendesk_client.py --ticket 123456 --comments --format json --max-comments 20

Requer variaveis de ambiente:
  ZENDESK_SUBDOMAIN  - Subdominio Zendesk (ex: "taxone")
  ZENDESK_EMAIL      - Email do agente (ex: "user@company.com")
  ZENDESK_API_TOKEN  - API token do Zendesk

Configurar no .env na raiz do projeto.
"""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from base64 import b64encode
from datetime import datetime
from pathlib import Path

# --- Configuration ---

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

try:
    from env_loader import _load_dotenv
    _load_dotenv()
except ImportError:
    pass


def _get_env(name: str) -> str:
    """Get env var from system or .env, raise clear error if missing."""
    value = os.environ.get(name)
    if value:
        return value
    # Try .env
    env_file = PLUGIN_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            if key == name:
                return val
    raise EnvironmentError(
        f"Variavel '{name}' nao configurada.\n"
        f"Configure ZENDESK_SUBDOMAIN, ZENDESK_EMAIL e ZENDESK_API_TOKEN no .env"
    )


def _build_auth_header() -> str:
    """Build Basic auth header for Zendesk API (email/token)."""
    email = _get_env("ZENDESK_EMAIL")
    token = _get_env("ZENDESK_API_TOKEN")
    credentials = f"{email}/token:{token}"
    encoded = b64encode(credentials.encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def _create_ssl_context() -> ssl.SSLContext:
    """Create SSL context that handles corporate proxy/certificates."""
    ctx = ssl.create_default_context()
    # Try system certificates first
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except (ImportError, Exception):
        pass
    # If corporate env blocks SSL, allow fallback (user can override)
    if os.environ.get("ZENDESK_SKIP_SSL", "").lower() in ("1", "true", "yes"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _api_get(url: str) -> dict:
    """Make authenticated GET request to Zendesk API."""
    auth = _build_auth_header()
    req = urllib.request.Request(url, headers={
        "Authorization": auth,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    ctx = _create_ssl_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(
            f"Zendesk API error {e.code}: {e.reason}\n{body[:500]}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Zendesk connection error: {e.reason}\n"
            f"Verifique ZENDESK_SUBDOMAIN e conectividade de rede.\n"
            f"Se estiver atras de proxy corporativo, tente ZENDESK_SKIP_SSL=1"
        ) from e


def fetch_ticket(ticket_id: int) -> dict:
    """Fetch ticket details from Zendesk API v2."""
    subdomain = _get_env("ZENDESK_SUBDOMAIN")
    url = f"https://{subdomain}.zendesk.com/api/v2/tickets/{ticket_id}.json"
    data = _api_get(url)
    ticket = data.get("ticket", {})
    return {
        "id": ticket.get("id"),
        "subject": ticket.get("subject", ""),
        "description": ticket.get("description", ""),
        "status": ticket.get("status", "unknown"),
        "priority": ticket.get("priority"),
        "created_at": ticket.get("created_at", ""),
        "updated_at": ticket.get("updated_at", ""),
        "requester_id": ticket.get("requester_id"),
        "assignee_id": ticket.get("assignee_id"),
        "tags": ticket.get("tags", []),
        "custom_fields": ticket.get("custom_fields", []),
    }


def fetch_requester(requester_id: int) -> dict:
    """Fetch requester details."""
    if not requester_id:
        return {"email": "unknown", "name": "unknown"}
    subdomain = _get_env("ZENDESK_SUBDOMAIN")
    url = f"https://{subdomain}.zendesk.com/api/v2/users/{requester_id}.json"
    try:
        data = _api_get(url)
        user = data.get("user", {})
        return {
            "email": user.get("email", "unknown"),
            "name": user.get("name", "unknown"),
        }
    except Exception:
        return {"email": "unknown", "name": "unknown"}


def fetch_comments(ticket_id: int, max_comments: int = 50) -> list:
    """Fetch ticket comments with attachments."""
    subdomain = _get_env("ZENDESK_SUBDOMAIN")
    url = (
        f"https://{subdomain}.zendesk.com/api/v2/tickets/{ticket_id}"
        f"/comments.json?per_page={min(max_comments, 100)}"
    )
    data = _api_get(url)
    comments_raw = data.get("comments", [])

    comments = []
    for c in comments_raw[:max_comments]:
        attachments = []
        for att in c.get("attachments", []):
            attachments.append({
                "filename": att.get("file_name", ""),
                "content_type": att.get("content_type", ""),
                "size": att.get("size", 0),
                "url": att.get("content_url", ""),
            })
        comments.append({
            "id": c.get("id"),
            "author_id": c.get("author_id"),
            "created_at": c.get("created_at", ""),
            "body": c.get("body", ""),
            "public": c.get("public", True),
            "attachments": attachments,
        })
    return comments


def build_summary(ticket: dict, comments: list) -> dict:
    """Build enriched summary for N3 triage / PM agents."""
    # Detect if solved without code change
    is_solved_no_code = False
    root_cause_mentioned = False
    code_keywords = ["commit", "pr ", "pull request", "deploy", "hotfix", "fix", "merge"]
    cause_keywords = ["causa raiz", "root cause", "causa:", "motivo:", "identificamos que"]

    for c in comments:
        body_lower = (c.get("body") or "").lower()
        if any(kw in body_lower for kw in cause_keywords):
            root_cause_mentioned = True

    if ticket.get("status") in ("solved", "closed"):
        has_code_ref = False
        for c in comments:
            body_lower = (c.get("body") or "").lower()
            if any(kw in body_lower for kw in code_keywords):
                has_code_ref = True
                break
        if not has_code_ref:
            is_solved_no_code = True

    # Build timeline (max 10 entries)
    timeline = []
    for c in comments[:10]:
        timeline.append({
            "date": c.get("created_at", ""),
            "actor": f"user_{c.get('author_id', 'unknown')}",
            "action": "public_comment" if c.get("public") else "internal_note",
        })

    # Comments summary (max 5 lines)
    summaries = []
    for c in comments[:5]:
        body = (c.get("body") or "")[:150]
        date = (c.get("created_at") or "")[:10]
        visibility = "pub" if c.get("public") else "int"
        summaries.append(f"[{date}][{visibility}] {body}")

    return {
        "ticket_id": ticket.get("id"),
        "subject": ticket.get("subject", ""),
        "status": ticket.get("status", "unknown"),
        "created_at": ticket.get("created_at", ""),
        "updated_at": ticket.get("updated_at", ""),
        "requester": "pending",  # filled by caller
        "comments_count": len(comments),
        "comments_summary": "\n".join(summaries),
        "root_cause_mentioned": root_cause_mentioned,
        "is_solved_without_code_change": is_solved_no_code,
        "timeline": timeline,
        "comments": comments,
    }


def format_text(result: dict) -> str:
    """Format result as human-readable text."""
    lines = [
        f"Ticket #{result.get('ticket_id', '?')} — {result.get('subject', '')}",
        f"Status: {result.get('status', 'unknown')}",
        f"Requester: {result.get('requester', 'unknown')}",
        f"Created: {result.get('created_at', '')}",
        f"Updated: {result.get('updated_at', '')}",
        f"Comments: {result.get('comments_count', 0)}",
        f"Root cause mentioned: {result.get('root_cause_mentioned', False)}",
        f"Solved without code change: {result.get('is_solved_without_code_change', False)}",
        "",
        "--- Comments Summary ---",
        result.get("comments_summary", "(none)"),
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Zendesk Client - Busca tickets para enriquecimento de Work Items"
    )
    parser.add_argument(
        "--ticket", type=int, required=True,
        help="ID do ticket Zendesk"
    )
    parser.add_argument(
        "--comments", action="store_true", default=False,
        help="Incluir comentarios e attachments"
    )
    parser.add_argument(
        "--max-comments", type=int, default=50,
        help="Maximo de comentarios a buscar (default: 50)"
    )
    parser.add_argument(
        "--format", choices=["json", "text"], default="text",
        dest="output_format",
        help="Formato de saida (default: text)"
    )

    args = parser.parse_args()

    try:
        # Fetch ticket
        ticket = fetch_ticket(args.ticket)

        # Fetch requester
        requester = fetch_requester(ticket.get("requester_id"))

        # Fetch comments if requested
        comments = []
        if args.comments:
            comments = fetch_comments(args.ticket, args.max_comments)

        # Build result
        result = build_summary(ticket, comments)
        result["requester"] = requester.get("email", "unknown")
        result["requester_name"] = requester.get("name", "unknown")

        # Output
        if args.output_format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(format_text(result))

    except EnvironmentError as e:
        print(f"ERRO CONFIG: {e}", file=sys.stderr)
        sys.exit(2)
    except RuntimeError as e:
        print(f"ERRO API: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERRO: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
