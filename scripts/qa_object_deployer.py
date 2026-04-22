#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qa_object_deployer.py - Deploy PL/SQL objects from a branch to a remote Oracle environment.

Compiles PL/SQL objects changed in branch MFS{WI_ID} (relative to DEV) into a
remote database (QA, AC, RC).  Local deployments are blocked — use the DBA agent
for LOCAL compilations.

Usage:
    python scripts/qa_object_deployer.py --wi-id 1078744 --env QA
    python scripts/qa_object_deployer.py --wi-id 1078744 --env QA --dry-run
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from env_connection import get_connection, get_owner, load_env_config  # noqa: E402
import env_loader  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_OWNER = "V2R010AC"
SP_DIR = "artifacts/sp"

# File extensions recognised as PL/SQL source files
_PLSQL_EXTENSIONS = {".sql", ".pck", ".fon", ".trg", ".typ"}

# Regex patterns to detect object type from SQL content
_TYPE_PATTERNS = [
    (r"CREATE\s+OR\s+REPLACE\s+PACKAGE\s+BODY\b", "PACKAGE BODY"),
    (r"CREATE\s+OR\s+REPLACE\s+PACKAGE\b", "PACKAGE"),
    (r"CREATE\s+OR\s+REPLACE\s+PROCEDURE\b", "PROCEDURE"),
    (r"CREATE\s+OR\s+REPLACE\s+FUNCTION\b", "FUNCTION"),
    (r"CREATE\s+OR\s+REPLACE\s+(?:FORCE\s+)?VIEW\b", "VIEW"),
    (r"CREATE\s+OR\s+REPLACE\s+TRIGGER\b", "TRIGGER"),
    (r"CREATE\s+OR\s+REPLACE\s+TYPE\b", "TYPE"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _infer_object_type(sql_content: str) -> str:
    """Infer PL/SQL object type from the SQL source text."""
    upper = sql_content[:2000].upper()
    for pattern, obj_type in _TYPE_PATTERNS:
        if re.search(pattern, upper):
            return obj_type
    return "UNKNOWN"


def _infer_object_name(sql_content: str, file_path: str) -> str:
    """Extract the object name from the CREATE OR REPLACE statement.

    Falls back to the filename (without extension) if parsing fails.
    """
    # Match: CREATE OR REPLACE [PACKAGE [BODY]|PROCEDURE|FUNCTION|VIEW|TRIGGER] [owner.]name
    m = re.search(
        r"CREATE\s+OR\s+REPLACE\s+"
        r"(?:PACKAGE\s+BODY|PACKAGE|PROCEDURE|FUNCTION|(?:FORCE\s+)?VIEW|TRIGGER)\s+"
        r"(?:\w+\.)?"           # optional owner prefix
        r"(\w+)",
        sql_content[:2000],
        re.IGNORECASE,
    )
    if m:
        return m.group(1).upper()
    return Path(file_path).stem.upper()


def _split_pck(sql_content: str) -> list[str]:
    """Split a .pck file into individual compilation units.

    Oracle .pck files conventionally contain the PACKAGE spec followed by
    the PACKAGE BODY, separated by a line containing only ``/``.  Each unit
    is returned as a standalone SQL string ready for ``cursor.execute()``.
    Empty units are discarded.
    """
    # Split on lines that are just "/" (the Oracle SQL*Plus statement terminator)
    parts = re.split(r"(?m)^\s*/\s*$", sql_content)
    return [p.strip() for p in parts if p.strip()]


def _oracle_type_keyword(object_type: str) -> str:
    """Return the keyword used in ALTER ... COMPILE for the given type."""
    mapping = {
        "PACKAGE": "PACKAGE",
        "PACKAGE BODY": "PACKAGE",  # ALTER PACKAGE x COMPILE BODY
        "PROCEDURE": "PROCEDURE",
        "FUNCTION": "FUNCTION",
        "VIEW": "VIEW",
        "TRIGGER": "TRIGGER",
    }
    return mapping.get(object_type, object_type)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def find_changed_objects(wi_id: str, dw_repo_path: str) -> list[dict]:
    """Identify PL/SQL files changed in MFS{wi_id} relative to DEV.

    Returns a list of dicts with keys: file_path, object_name, object_type.
    """
    branch = f"MFS{wi_id}"
    result = subprocess.run(
        ["git", "diff", "--name-only", f"DEV..{branch}", "--", f"{SP_DIR}/"],
        capture_output=True,
        text=True,
        cwd=dw_repo_path,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git diff failed (exit {result.returncode}):\n{result.stderr.strip()}"
        )

    changed_files = [
        line.strip() for line in result.stdout.splitlines() if line.strip()
    ]

    objects = []
    for rel_path in changed_files:
        full_path = Path(dw_repo_path) / rel_path
        if not full_path.exists():
            print(f"  WARN: file listed in diff but not found on disk: {rel_path}")
            continue
        if full_path.suffix.lower() not in _PLSQL_EXTENSIONS:
            continue

        sql_content = full_path.read_text(encoding="latin-1", errors="replace")

        # .pck files may contain both PACKAGE spec and BODY separated by "/"
        # on its own line.  Split into individual compilation units.
        if full_path.suffix.lower() == ".pck":
            units = _split_pck(sql_content)
            for unit_sql in units:
                obj_type = _infer_object_type(unit_sql)
                obj_name = _infer_object_name(unit_sql, rel_path)
                objects.append({
                    "file_path": str(full_path),
                    "rel_path": rel_path,
                    "object_name": obj_name,
                    "object_type": obj_type,
                    "_sql_override": unit_sql,
                })
        else:
            obj_type = _infer_object_type(sql_content)
            obj_name = _infer_object_name(sql_content, rel_path)
            objects.append({
                "file_path": str(full_path),
                "rel_path": rel_path,
                "object_name": obj_name,
                "object_type": obj_type,
            })

    return objects


def adapt_schema_owner(sql_content: str, source_owner: str, target_owner: str) -> str:
    """Replace schema owner references in the SQL source.

    Handles patterns like:
      - CREATE OR REPLACE PACKAGE V2R010AC.PKG_NAME
      - owner.table_name references
    """
    if source_owner == target_owner:
        return sql_content

    # Case-insensitive replacement of owner prefix (e.g. V2R010AC. -> TARGET.)
    pattern = re.compile(re.escape(source_owner), re.IGNORECASE)
    return pattern.sub(target_owner, sql_content)


def deploy_object(
    conn,
    object_sql: str,
    object_name: str,
    object_type: str,
    owner: str,
) -> dict:
    """Execute CREATE OR REPLACE for a single object, verify, and recompile if needed.

    Returns dict with keys: name, type, status, errors.
    """
    errors = []
    cursor = conn.cursor()

    # --- Execute CREATE OR REPLACE ---
    try:
        cursor.execute(object_sql)
    except Exception as exc:
        errors.append(f"CREATE failed: {exc}")
        cursor.close()
        return {
            "name": object_name,
            "type": object_type,
            "status": "ERROR",
            "errors": errors,
        }

    # --- Verify compilation status ---
    # For PACKAGE BODY, the object type stored in all_objects is "PACKAGE BODY"
    db_object_type = object_type.upper()
    cursor.execute(
        "SELECT status FROM all_objects "
        "WHERE owner = :owner AND object_name = :name AND object_type = :otype",
        {"owner": owner.upper(), "name": object_name.upper(), "otype": db_object_type},
    )
    row = cursor.fetchone()
    status = row[0] if row else "NOT FOUND"

    # --- Recompile if INVALID ---
    if status == "INVALID":
        try:
            alter_type = _oracle_type_keyword(object_type)
            compile_stmt = f"ALTER {alter_type} {owner}.{object_name} COMPILE"
            if object_type == "PACKAGE BODY":
                compile_stmt += " BODY"
            cursor.execute(compile_stmt)
        except Exception as exc:
            errors.append(f"RECOMPILE failed: {exc}")

        # Re-check status after recompile attempt
        cursor.execute(
            "SELECT status FROM all_objects "
            "WHERE owner = :owner AND object_name = :name AND object_type = :otype",
            {"owner": owner.upper(), "name": object_name.upper(), "otype": db_object_type},
        )
        row = cursor.fetchone()
        status = row[0] if row else "NOT FOUND"

    # --- Collect compilation errors if still invalid ---
    if status in ("INVALID", "NOT FOUND"):
        cursor.execute(
            "SELECT line, position, text FROM all_errors "
            "WHERE owner = :owner AND name = :name AND type = :otype "
            "ORDER BY sequence",
            {"owner": owner.upper(), "name": object_name.upper(), "otype": db_object_type},
        )
        for err_row in cursor.fetchall():
            errors.append(f"Line {err_row[0]}, Col {err_row[1]}: {err_row[2].strip()}")

    cursor.close()
    return {
        "name": object_name,
        "type": object_type,
        "status": status,
        "errors": errors,
    }


def deploy_all(wi_id: str, env: str, dry_run: bool = False) -> dict:
    """Orchestrate full deployment of changed PL/SQL objects to the target environment.

    Returns a result dict suitable for writing to the deploy log JSON.
    """
    env = env.upper()

    # --- Safety: block LOCAL deployments ---
    if env == "LOCAL":
        raise ValueError(
            "Deployer is for remote environments only (QA, AC, RC). "
            "For LOCAL, use the DBA agent."
        )

    # --- Resolve paths and config ---
    dw_repo_path = env_loader.get_repo_path("TAXONE_DW_REPO")
    target_owner = get_owner(env)
    branch = f"MFS{wi_id}"

    print(f"=== QA Object Deployer ===")
    print(f"  WI        : {wi_id}")
    print(f"  Branch    : {branch}")
    print(f"  Target Env: {env}")
    print(f"  Owner     : {target_owner}")
    print(f"  DW Repo   : {dw_repo_path}")
    print(f"  Dry Run   : {dry_run}")
    print()

    # --- Find changed objects ---
    print("Scanning changed PL/SQL objects...")
    objects = find_changed_objects(wi_id, dw_repo_path)

    if not objects:
        print("  No changed PL/SQL objects found in the branch.")
        return {
            "wi_id": wi_id,
            "env": env,
            "deployed_at": datetime.now(timezone.utc).isoformat(),
            "source_branch": branch,
            "target_owner": target_owner,
            "objects": [],
            "summary": {"total": 0, "valid": 0, "invalid": 0, "errors": 0},
        }

    print(f"  Found {len(objects)} object(s):\n")
    for obj in objects:
        print(f"    {obj['object_type']:15s}  {obj['object_name']}")
        print(f"                   {obj['rel_path']}")
    print()

    if dry_run:
        print("DRY RUN — no objects will be compiled.")
        return {
            "wi_id": wi_id,
            "env": env,
            "deployed_at": datetime.now(timezone.utc).isoformat(),
            "source_branch": branch,
            "target_owner": target_owner,
            "dry_run": True,
            "objects": [
                {"name": o["object_name"], "type": o["object_type"], "status": "PENDING", "errors": []}
                for o in objects
            ],
            "summary": {"total": len(objects), "valid": 0, "invalid": 0, "errors": 0},
        }

    # --- Connect to target environment ---
    print(f"Connecting to {env}...")
    conn = get_connection(env)
    print("  Connected.\n")

    # --- Deploy each object ---
    results = []
    for obj in objects:
        print(f"  Deploying {obj['object_type']} {obj['object_name']}...", end=" ")
        # .pck files are pre-split; other files are read from disk
        if "_sql_override" in obj:
            sql_content = obj["_sql_override"]
        else:
            sql_content = Path(obj["file_path"]).read_text(encoding="latin-1", errors="replace")
        adapted_sql = adapt_schema_owner(sql_content, SOURCE_OWNER, target_owner)

        result = deploy_object(conn, adapted_sql, obj["object_name"], obj["object_type"], target_owner)
        results.append(result)

        if result["status"] == "VALID":
            print("VALID")
        else:
            print(f"{result['status']}")
            for err in result["errors"]:
                print(f"    ERROR: {err}")

    conn.close()

    # --- Summary ---
    total = len(results)
    valid = sum(1 for r in results if r["status"] == "VALID")
    invalid = sum(1 for r in results if r["status"] == "INVALID")
    error_count = sum(1 for r in results if r["status"] == "ERROR" or r["status"] == "NOT FOUND")

    print(f"\n=== Deploy Summary ===")
    print(f"  Total  : {total}")
    print(f"  VALID  : {valid}")
    print(f"  INVALID: {invalid}")
    print(f"  ERROR  : {error_count}")

    return {
        "wi_id": wi_id,
        "env": env,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "source_branch": branch,
        "target_owner": target_owner,
        "objects": results,
        "summary": {
            "total": total,
            "valid": valid,
            "invalid": invalid,
            "errors": error_count,
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Deploy PL/SQL objects from a branch to a remote Oracle environment."
    )
    parser.add_argument(
        "--wi-id",
        required=True,
        help="Work Item ID (e.g. 1078744). Branch MFS{WI_ID} must exist in taxone_dw.",
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=["QA", "AC", "RC", "qa", "ac", "rc"],
        help="Target environment (QA, AC, RC). LOCAL is not allowed.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="List objects that would be deployed without actually compiling them.",
    )
    args = parser.parse_args()

    env = args.env.upper()
    wi_id = args.wi_id

    try:
        result = deploy_all(wi_id, env, dry_run=args.dry_run)
    except Exception as exc:
        print(f"\nFATAL: {exc}", file=sys.stderr)
        sys.exit(1)

    # --- Write deploy log ---
    log_dir = PROJECT_ROOT / "tests" / wi_id
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "qa_deploy_log.json"

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\nDeploy log saved to: {log_path}")

    # Exit with error code if any object failed
    if result["summary"]["invalid"] > 0 or result["summary"]["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
