#!/usr/bin/env python3
"""
Git History Analyzer - Identifica commits suspeitos que podem ter causado um bug.

Dado um conjunto de objetos PL/SQL/PB/Java afetados, consulta o historico git
do repo taxone_dw e retorna os commits mais provaveis de terem introduzido o problema.

Uso:
    python scripts/git_history_analyzer.py \
        --repo "$TAXONE_DW_REPO" \
        --objects "PKG_TMP_DOCTO_FISCAL_FPROC,LIB_IMPORT" \
        --timeframe 90 \
        --wi-title "Erro no calculo de ICMS" \
        --wi-date "2026-03-10" \
        --format json|html
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

from env_loader import get_repo_path
DEFAULT_REPO = get_repo_path("TAXONE_DW_REPO")
DEFAULT_TIMEFRAME_DAYS = 90
MAX_DIFF_LINES = 200
TOP_SUSPECTS = 5

# Patterns para extrair nomes de objetos de titulos/descricoes de WIs
OBJECT_PATTERNS = [
    r"PKG_\w+",
    r"SAF_\w+",
    r"OL_\w+",
    r"LIB_\w+",
    r"\w+_FPROC",
    r"\w+_CPROC",
    r"PRC_\w+",
    r"FNC_\w+",
    r"TRG_\w+",
    r"VW_\w+",
]

# Keywords criticas em diffs (indicam alteracoes de alto impacto)
CRITICAL_KEYWORDS = [
    r"\bWHERE\b",
    r"\bCURSOR\b",
    r"\bCALCUL",
    r"\bROUND\b",
    r"\bTRUNC\b",
    r"\bNVL\b",
    r"\bDECODE\b",
    r"\bCASE\s+WHEN\b",
    r"\bINSERT\s+INTO\b",
    r"\bUPDATE\b.*\bSET\b",
    r"\bDELETE\s+FROM\b",
    r"\bEXCEPTION\b",
    r"\bRAISE\b",
]


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def run_git(repo: str, args: list[str], encoding: str = "utf-8") -> str:
    """Executa um comando git no repo e retorna stdout."""
    cmd = ["git", "-C", repo] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding=encoding,
            errors="replace",
            timeout=60,
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""
    except Exception as e:
        print(f"[WARN] git command failed: {' '.join(cmd)} -> {e}", file=sys.stderr)
        return ""


def resolve_objects_to_paths(repo: str, objects: list[str]) -> dict[str, list[str]]:
    """Mapeia nomes de objetos PL/SQL/PB/Java para paths no repositorio.

    Retorna dict: {object_name: [path1, path2, ...]}
    """
    all_files = run_git(repo, ["ls-files"])
    if not all_files:
        return {}

    file_list = all_files.splitlines()
    result = {}

    for obj_name in objects:
        obj_lower = obj_name.strip().lower()
        if not obj_lower:
            continue
        matched = []
        for fpath in file_list:
            fname = Path(fpath).stem.lower()
            if fname == obj_lower or obj_lower in fname:
                matched.append(fpath)
        result[obj_name] = matched

    return result


def get_commits_for_files(
    repo: str, file_paths: list[str], since_days: int, until_date: str | None = None
) -> list[dict]:
    """Retorna commits que tocaram os arquivos no periodo."""
    if not file_paths:
        return []

    args = [
        "log",
        "--format=%H|%an|%ai|%s",
        f"--since={since_days} days ago",
        "--no-merges",
        "--",
    ] + file_paths

    if until_date:
        args.insert(3, f"--until={until_date}")

    output = run_git(repo, args)
    if not output:
        return []

    commits = []
    for line in output.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append({
                "sha": parts[0],
                "author": parts[1],
                "date": parts[2],
                "message": parts[3],
            })
    return commits


def get_diff_for_commit(repo: str, sha: str, file_paths: list[str]) -> str:
    """Retorna diff truncado de um commit para os arquivos especificados."""
    args = ["log", "-1", "-p", "--diff-filter=M", sha, "--"] + file_paths
    output = run_git(repo, args)
    if not output:
        return ""

    lines = output.splitlines()
    if len(lines) > MAX_DIFF_LINES:
        return "\n".join(lines[:MAX_DIFF_LINES]) + f"\n... [truncado, {len(lines) - MAX_DIFF_LINES} linhas omitidas]"
    return output


def get_blame_for_file(repo: str, file_path: str, line_ranges: list[tuple[int, int]] | None = None) -> list[dict]:
    """Retorna git blame para um arquivo (ou linhas especificas)."""
    args = ["blame", "--porcelain", file_path]
    output = run_git(repo, args)
    if not output:
        return []

    blame_entries = []
    current = {}
    for line in output.splitlines():
        if line.startswith("author "):
            current["author"] = line[7:]
        elif line.startswith("author-time "):
            try:
                ts = int(line[12:])
                current["date"] = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            except ValueError:
                pass
        elif line.startswith("summary "):
            current["summary"] = line[8:]
        elif line.startswith("\t"):
            current["line_content"] = line[1:]
            if current:
                blame_entries.append(dict(current))
            current = {}

    return blame_entries


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def extract_objects_from_text(text: str) -> list[str]:
    """Extrai nomes de objetos PL/SQL/PB/Java de texto livre."""
    found = set()
    for pattern in OBJECT_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for m in matches:
            found.add(m.upper())
    return sorted(found)


def extract_mfs_ids(message: str) -> list[int]:
    """Extrai IDs MFS e AB# de uma mensagem de commit."""
    ids = set()
    for m in re.finditer(r"MFS(\d+)", message, re.IGNORECASE):
        ids.add(int(m.group(1)))
    for m in re.finditer(r"AB#(\d+)", message):
        ids.add(int(m.group(1)))
    return sorted(ids)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_commit(
    commit: dict,
    wi_title: str,
    wi_date: str | None,
    diff_text: str,
) -> int:
    """Pontua um commit com base na relevancia para o bug."""
    score = 0
    title_words = set(re.findall(r"\w{4,}", wi_title.upper())) if wi_title else set()

    # 1. Proximidade temporal (max +30)
    if wi_date and commit.get("date"):
        try:
            bug_dt = datetime.strptime(wi_date[:10], "%Y-%m-%d")
            commit_dt = datetime.strptime(commit["date"][:10], "%Y-%m-%d")
            delta = abs((bug_dt - commit_dt).days)
            if delta <= 14:
                score += 30
            elif delta <= 30:
                score += 20
            elif delta <= 60:
                score += 10
        except ValueError:
            pass

    # 2. Keywords em comum com titulo da WI (max +40)
    msg_words = set(re.findall(r"\w{4,}", commit["message"].upper()))
    common = title_words & msg_words
    score += min(len(common) * 20, 40)

    # 3. Linhas criticas alteradas no diff (max +30)
    if diff_text:
        critical_count = 0
        for pattern in CRITICAL_KEYWORDS:
            added_lines = [l for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++")]
            for line in added_lines:
                if re.search(pattern, line, re.IGNORECASE):
                    critical_count += 1
                    break
        score += min(critical_count * 10, 30)

    return score


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def analyze(
    repo: str,
    objects: list[str],
    timeframe_days: int = DEFAULT_TIMEFRAME_DAYS,
    wi_title: str = "",
    wi_date: str | None = None,
) -> dict:
    """Executa analise completa e retorna resultado estruturado."""

    # Passo 1: Resolver nomes para paths
    object_paths = resolve_objects_to_paths(repo, objects)
    all_paths = []
    path_map = {}
    for obj, paths in object_paths.items():
        all_paths.extend(paths)
        for p in paths:
            path_map[p] = obj

    resolved_info = {
        obj: {"paths": paths, "is_pbl": any(p.endswith((".pbl", ".pbd")) for p in paths)}
        for obj, paths in object_paths.items()
    }

    if not all_paths:
        return {
            "status": "no_files_found",
            "objects_requested": objects,
            "resolved": resolved_info,
            "top_suspects": [],
            "blame_highlights": [],
        }

    # Passo 2: git log nos arquivos
    commits = get_commits_for_files(repo, all_paths, timeframe_days, wi_date)

    if not commits:
        return {
            "status": "no_commits_found",
            "objects_requested": objects,
            "resolved": resolved_info,
            "files_tracked": all_paths,
            "timeframe_days": timeframe_days,
            "top_suspects": [],
            "blame_highlights": [],
        }

    # Passo 3 & 4: Extrair MFS IDs e diffs para top commits
    for commit in commits:
        commit["mfs_ids"] = extract_mfs_ids(commit["message"])

    # Passo 5: Scoring
    scored = []
    for commit in commits[:20]:  # Limitar a 20 para performance
        diff = get_diff_for_commit(repo, commit["sha"], all_paths)
        commit_score = score_commit(commit, wi_title, wi_date, diff)
        scored.append({
            **commit,
            "score": commit_score,
            "diff_snippet": diff[:3000] if diff else "",
        })

    scored.sort(key=lambda c: c["score"], reverse=True)
    top = scored[:TOP_SUSPECTS]

    # Blame highlights para o commit top
    blame_highlights = []
    if top and top[0].get("diff_snippet"):
        diff_lines = top[0]["diff_snippet"].splitlines()
        changed_files = set()
        for line in diff_lines:
            if line.startswith("+++ b/"):
                changed_files.add(line[6:])
        for fpath in list(changed_files)[:3]:
            blame = get_blame_for_file(repo, fpath)
            if blame:
                recent = [
                    b for b in blame
                    if b.get("date") and b["date"] >= (datetime.now() - timedelta(days=timeframe_days)).strftime("%Y-%m-%d")
                ]
                blame_highlights.extend(recent[:5])

    return {
        "status": "ok",
        "objects_requested": objects,
        "resolved": resolved_info,
        "files_tracked": all_paths,
        "timeframe_days": timeframe_days,
        "total_commits_found": len(commits),
        "top_suspects": top,
        "blame_highlights": blame_highlights,
    }


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_json(result: dict) -> str:
    """Formata resultado como JSON."""
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)


def format_html(result: dict) -> str:
    """Formata resultado como HTML para Discussion comment no ADO."""
    if result["status"] == "no_files_found":
        return (
            "<h3>Git History Analysis</h3>"
            "<p>Nenhum arquivo encontrado no repositorio para os objetos: "
            f"{', '.join(result['objects_requested'])}</p>"
        )

    if result["status"] == "no_commits_found":
        return (
            "<h3>Git History Analysis</h3>"
            "<p>Nenhum commit encontrado nos ultimos "
            f"{result['timeframe_days']} dias para: "
            f"{', '.join(result['objects_requested'])}</p>"
        )

    suspects = result.get("top_suspects", [])
    resolved = result.get("resolved", {})

    html_parts = ["<h3>Git History Analysis</h3>"]

    # Objetos resolvidos
    html_parts.append("<p><b>Objetos analisados:</b></p><ul>")
    for obj, info in resolved.items():
        paths_str = ", ".join(info["paths"][:3]) if info["paths"] else "nao encontrado"
        pbl_tag = " (PBL - apenas metadata)" if info.get("is_pbl") else ""
        html_parts.append(f"<li><code>{obj}</code> &rarr; {paths_str}{pbl_tag}</li>")
    html_parts.append("</ul>")

    # Tabela de commits suspeitos
    if suspects:
        html_parts.append(
            f"<p><b>Top {len(suspects)} commits suspeitos</b> "
            f"(de {result['total_commits_found']} no periodo):</p>"
        )
        html_parts.append(
            "<table border='1' cellpadding='4' cellspacing='0'>"
            "<tr><th>Score</th><th>SHA</th><th>Data</th>"
            "<th>Autor</th><th>WI Origem</th><th>Mensagem</th></tr>"
        )
        for s in suspects:
            sha_short = s["sha"][:8]
            mfs_str = ", ".join(f"MFS{mid}" for mid in s.get("mfs_ids", [])) or "-"
            msg_short = s["message"][:80]
            html_parts.append(
                f"<tr><td>{s['score']}</td><td><code>{sha_short}</code></td>"
                f"<td>{s['date'][:10]}</td><td>{s['author']}</td>"
                f"<td>{mfs_str}</td><td>{msg_short}</td></tr>"
            )
        html_parts.append("</table>")

        # Diff do commit mais provavel
        top_diff = suspects[0].get("diff_snippet", "")
        if top_diff:
            html_parts.append(
                f"<p><b>Diff do commit mais provavel ({suspects[0]['sha'][:8]}):</b></p>"
                f"<pre>{_escape_html(top_diff[:2000])}</pre>"
            )

    # Blame highlights
    blame = result.get("blame_highlights", [])
    if blame:
        html_parts.append("<p><b>Git Blame - alteracoes recentes:</b></p><ul>")
        for b in blame[:5]:
            html_parts.append(
                f"<li>{b.get('date', '?')} - {b.get('author', '?')}: "
                f"<code>{_escape_html(b.get('line_content', '')[:100])}</code> "
                f"({b.get('summary', '')[:60]})</li>"
            )
        html_parts.append("</ul>")

    return "\n".join(html_parts)


def _escape_html(text: str) -> str:
    """Escapa caracteres HTML."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# Test-focus analysis
# ---------------------------------------------------------------------------

def analyze_test_focus(
    repo: str,
    objects: list[str],
    timeframe_days: int = DEFAULT_TIMEFRAME_DAYS,
) -> dict:
    """Analise orientada a testes: frequencia de mudanca e objetos co-alterados."""

    object_paths = resolve_objects_to_paths(repo, objects)
    all_paths = []
    for paths in object_paths.values():
        all_paths.extend(paths)

    if not all_paths:
        return {
            "change_frequency": {},
            "co_changed_objects": [],
            "regression_priority": "LOW",
            "recommended_test_scope": "minimal",
        }

    # 1. Change frequency: how many commits touched each object in the period
    change_frequency = {}
    for obj, paths in object_paths.items():
        if not paths:
            continue
        log = run_git(repo, [
            "log", "--oneline", f"--since={timeframe_days} days ago",
            "--no-merges", "--"
        ] + paths)
        change_frequency[obj] = len(log.splitlines()) if log else 0

    # 2. Co-changed objects: objects that typically change together
    # Get all commits that touched our files
    commits_output = run_git(repo, [
        "log", "--format=%H", f"--since={timeframe_days} days ago",
        "--no-merges", "--"
    ] + all_paths)

    co_changed = Counter()
    if commits_output:
        commit_shas = commits_output.splitlines()[:30]  # Limit for performance
        for sha in commit_shas:
            # Get all files changed in this commit
            files_in_commit = run_git(repo, ["diff-tree", "--no-commit-id", "--name-only", "-r", sha])
            if not files_in_commit:
                continue
            # Extract object names from changed files (exclude our own objects)
            for fpath in files_in_commit.splitlines():
                stem = Path(fpath).stem.upper()
                # Match known PL/SQL patterns
                for pattern in OBJECT_PATTERNS:
                    if re.match(pattern, stem, re.IGNORECASE):
                        if stem not in {o.upper() for o in objects}:
                            co_changed[stem] += 1
                        break

    # Top co-changed objects (appeared in >=2 commits)
    co_changed_list = [obj for obj, count in co_changed.most_common(10) if count >= 2]

    # 3. Regression priority based on change frequency
    max_freq = max(change_frequency.values()) if change_frequency else 0
    if max_freq > 3:
        regression_priority = "HIGH"
        recommended_scope = "extended"
    elif max_freq > 1:
        regression_priority = "MEDIUM"
        recommended_scope = "standard"
    else:
        regression_priority = "LOW"
        recommended_scope = "minimal"

    return {
        "change_frequency": change_frequency,
        "co_changed_objects": co_changed_list,
        "regression_priority": regression_priority,
        "recommended_test_scope": recommended_scope,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Git History Analyzer - Identifica commits suspeitos para um bug"
    )
    parser.add_argument(
        "--repo", type=str, default=DEFAULT_REPO,
        help=f"Path do repositorio git (default: {DEFAULT_REPO})"
    )
    parser.add_argument(
        "--objects", type=str, default="",
        help="Objetos PL/SQL/PB/Java separados por virgula (ex: PKG_FOO,LIB_BAR)"
    )
    parser.add_argument(
        "--timeframe", type=int, default=DEFAULT_TIMEFRAME_DAYS,
        help=f"Periodo em dias para busca (default: {DEFAULT_TIMEFRAME_DAYS})"
    )
    parser.add_argument(
        "--wi-title", type=str, default="",
        help="Titulo do work item (para scoring por keywords)"
    )
    parser.add_argument(
        "--wi-date", type=str, default="",
        help="Data do bug/WI no formato YYYY-MM-DD (para scoring temporal)"
    )
    parser.add_argument(
        "--wi-text", type=str, default="",
        help="Texto livre do WI para auto-extrair nomes de objetos"
    )
    parser.add_argument(
        "--format", choices=["json", "html"], default="json",
        help="Formato de saida (default: json)"
    )
    parser.add_argument(
        "--test-focus", action="store_true", default=False,
        help="Modo orientado a testes: output com change_frequency, co_changed_objects, regression_priority"
    )

    args = parser.parse_args()

    # Resolver lista de objetos
    objects = []
    if args.objects:
        objects = [o.strip() for o in args.objects.split(",") if o.strip()]
    if args.wi_text:
        objects.extend(extract_objects_from_text(args.wi_text))
    if args.wi_title and not objects:
        objects.extend(extract_objects_from_text(args.wi_title))

    if not objects:
        print("Erro: fornecer --objects ou --wi-text com nomes de objetos PL/SQL/PB/Java", file=sys.stderr)
        parser.print_help()
        sys.exit(1)

    # Deduplicate
    objects = sorted(set(objects))

    result = analyze(
        repo=args.repo,
        objects=objects,
        timeframe_days=args.timeframe,
        wi_title=args.wi_title,
        wi_date=args.wi_date or None,
    )

    # --test-focus: enrich result with test-oriented data
    if args.test_focus:
        result["test_focus"] = analyze_test_focus(
            repo=args.repo,
            objects=objects,
            timeframe_days=args.timeframe,
        )

    if args.format == "json":
        print(format_json(result))
    else:
        print(format_html(result))


if __name__ == "__main__":
    main()
