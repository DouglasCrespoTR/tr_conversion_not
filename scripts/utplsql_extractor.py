#!/usr/bin/env python3
"""
utplsql_extractor.py — Parse PL/SQL .pck files from the utPLSQL test repo
and produce structured knowledge JSON files.

Usage:
    python scripts/utplsql_extractor.py [--source DIR] [--output DIR]

Defaults:
    --source  C:\\@@Dev\\Git\\taxone_automacao_qa\\utplsql
    --output  knowledge/utplsql/
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_SOURCE = r"C:\@@Dev\Git\taxone_automacao_qa\utplsql"
DEFAULT_OUTPUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "knowledge", "utplsql",
)

SAFX_INSERT_FILES = [
    "msaf_util_insere_safx0.pck",
    "msaf_util_insere_safx1.pck",
    "msaf_util_insere_safx2.pck",
    "msaf_util_insere_safx3.pck",
    "msaf_util_insere_safx4.pck",
    "msaf_util_insere_safx5.pck",
    "msaf_util_insere_safxx.pck",
]

UTIL_FILES = {
    "msaf_util.pck": "MSAF_UTIL",
    "msaf_util_insere.pck": "MSAF_UTIL_INSERE",
    "msaf_util_remove.pck": "MSAF_UTIL_REMOVE",
    "msaf_util_testa.pck": "MSAF_UTIL_TESTA",
}

ENCODINGS_TO_TRY = ["utf-8", "latin-1", "cp1252"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_file(path: str) -> str:
    """Read a file trying multiple encodings."""
    for enc in ENCODINGS_TO_TRY:
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    # Last resort: latin-1 with errors replaced
    with open(path, "r", encoding="latin-1", errors="replace") as f:
        return f.read()


def strip_schema(name: str) -> str:
    """Remove schema prefix like 'utpl010ac.' from a name."""
    if "." in name:
        return name.split(".", 1)[1]
    return name


def extract_package_name(text: str) -> str:
    """Extract package name from 'create or replace package [schema.]NAME is'."""
    m = re.search(
        r"create\s+or\s+replace\s+package\s+(?:body\s+)?(\S+)\s+is",
        text, re.IGNORECASE,
    )
    if m:
        return strip_schema(m.group(1)).upper()
    return ""


def extract_spec_section(text: str) -> str:
    """Extract the package spec (before 'create or replace package body')."""
    # Find the end of the spec: either 'end PKGNAME;' followed by '/' or
    # 'create or replace package body'
    m = re.search(
        r"create\s+or\s+replace\s+package\s+body\s+",
        text, re.IGNORECASE,
    )
    if m:
        return text[:m.start()]
    # Fallback: entire text up to first standalone '/'
    m = re.search(r"^/\s*$", text, re.MULTILINE)
    if m:
        return text[:m.start()]
    return text


def extract_body_section(text: str) -> str:
    """Extract the package body."""
    m = re.search(
        r"create\s+or\s+replace\s+package\s+body\s+",
        text, re.IGNORECASE,
    )
    if m:
        return text[m.start():]
    return ""


def strip_comments_from_spec(spec: str) -> str:
    """Remove block comments /* ... */ but preserve -- line comments (they carry descriptions)."""
    # Remove block comments that span spec content — but be careful not to remove
    # commented-out procedure declarations we need (they start with /*Procedure or /*Function)
    # For safety, only remove block comments that don't contain Procedure/Function keywords
    result = re.sub(r"/\*(?!.*?(?:Procedure|Function)).*?\*/", "", spec, flags=re.DOTALL | re.IGNORECASE)
    return result


def parse_procedure_signatures(spec_text: str) -> list:
    """
    Parse procedure/function signatures from a package spec section.
    Returns list of dicts: {name, params: [{name, type, default, direction}], kind}
    Handles multiline signatures ending with ');' or just ';' (no params).
    Skips signatures inside /* ... */ block comments.
    """
    results = []

    # Remove block comments entirely to avoid parsing commented-out code
    clean = re.sub(r"/\*.*?\*/", "", spec_text, flags=re.DOTALL)

    # Match procedure/function declarations — may span multiple lines
    # Pattern: (Procedure|Function) NAME(params); or (Procedure|Function) NAME;
    # We collect from keyword to the closing ';' that ends the declaration
    pattern = re.compile(
        r"\b(Procedure|Function)\s+(\w+)\s*"
        r"(?:\((.*?)\))?\s*"
        r"(?:return\s+(\w+))?\s*;",
        re.IGNORECASE | re.DOTALL,
    )

    for m in pattern.finditer(clean):
        kind = m.group(1).upper()
        name = m.group(2)
        raw_params = m.group(3)
        return_type = m.group(4)

        params = []
        if raw_params:
            params = parse_params(raw_params)

        entry = {
            "name": name,
            "params": params,
            "kind": kind,
        }
        if return_type:
            entry["return_type"] = return_type.upper()

        results.append(entry)

    return results


def parse_params(raw: str) -> list:
    """Parse parameter list from a raw param string like 'p_name type default val, ...'."""
    params = []
    # Split by comma, but be careful with nested parens (unlikely here, but safe)
    # Simple approach: split on commas that are at top level
    parts = split_params(raw)

    for part in parts:
        part = part.strip()
        if not part:
            continue
        p = parse_single_param(part)
        if p:
            params.append(p)

    return params


def split_params(raw: str) -> list:
    """Split parameter string by commas, respecting parentheses."""
    parts = []
    depth = 0
    current = []
    for ch in raw:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def parse_single_param(text: str) -> dict:
    """
    Parse a single parameter like:
        p_name  IN OUT  type  DEFAULT  null
    or  p_name  type  default  null
    """
    text = text.strip()
    if not text:
        return None

    # Tokenize
    tokens = text.split()
    if not tokens:
        return None

    name = tokens[0]
    direction = "IN"
    ptype = ""
    default = None

    i = 1
    # Check for direction keywords
    dirs = []
    while i < len(tokens) and tokens[i].upper() in ("IN", "OUT"):
        dirs.append(tokens[i].upper())
        i += 1
    if dirs:
        direction = " ".join(dirs)

    # Next token(s) should be the type
    if i < len(tokens):
        # Check if next token is DEFAULT
        if tokens[i].upper() == "DEFAULT":
            ptype = "VARCHAR2"  # fallback
            i += 1
            if i < len(tokens):
                default = " ".join(tokens[i:])
        else:
            ptype = tokens[i].upper()
            i += 1

    # Check for DEFAULT keyword
    if i < len(tokens) and tokens[i].upper() == "DEFAULT":
        i += 1
        if i < len(tokens):
            default = " ".join(tokens[i:])
    elif i < len(tokens) and tokens[i].upper() == ":=":
        i += 1
        if i < len(tokens):
            default = " ".join(tokens[i:])

    result = {"name": name, "type": ptype, "direction": direction}
    if default is not None:
        result["default"] = default
    return result


def extract_description(text: str) -> str:
    """Extract Purpose comment from package header."""
    m = re.search(r"--\s*Purpose\s*:\s*(.*)", text, re.IGNORECASE)
    if m:
        desc = m.group(1).strip()
        if desc:
            return desc
    return ""


def extract_test_proc_descriptions(spec: str) -> list:
    """
    Extract test procedures with their comment descriptions from spec.
    Pattern: Procedure ut_test_NNN; -- Description
    """
    results = []
    # Remove block comments
    clean = re.sub(r"/\*.*?\*/", "", spec, flags=re.DOTALL)

    pattern = re.compile(
        r"Procedure\s+(ut_test_\w+)\s*;\s*(?:--\s*(.*))?",
        re.IGNORECASE,
    )
    for m in pattern.finditer(clean):
        name = m.group(1)
        desc = m.group(2).strip() if m.group(2) else ""
        results.append({"name": name, "description": desc})

    return results


def extract_tables_from_body(body: str) -> list:
    """Extract table names referenced in INSERT INTO, DELETE FROM, SELECT FROM, UPDATE patterns."""
    tables = set()

    # INSERT INTO table
    for m in re.finditer(r"\bINSERT\s+INTO\s+(\w+)", body, re.IGNORECASE):
        tables.add(m.group(1).upper())

    # DELETE FROM table (or DELETE table)
    for m in re.finditer(r"\bDELETE\s+(?:FROM\s+)?(\w+)", body, re.IGNORECASE):
        tbl = m.group(1).upper()
        if tbl != "FROM":
            tables.add(tbl)

    # UPDATE table
    for m in re.finditer(r"\bUPDATE\s+(\w+)", body, re.IGNORECASE):
        tables.add(m.group(1).upper())

    # SELECT ... FROM table
    for m in re.finditer(r"\bFROM\s+(\w+)", body, re.IGNORECASE):
        tables.add(m.group(1).upper())

    # Filter out PL/SQL keywords that might be caught
    noise = {
        "DUAL", "BEGIN", "END", "EXCEPTION", "WHEN", "THEN", "ELSE", "IF",
        "LOOP", "EXIT", "RETURN", "NULL", "INTO", "WHERE", "AND", "OR",
        "NOT", "IN", "IS", "AS", "SET", "VALUES", "SELECT", "CURSOR",
        "TABLE", "NUMBER", "VARCHAR2", "CHAR", "DATE", "LONG", "CLOB",
        "BLOB", "INTEGER", "PLS_INTEGER", "BOOLEAN", "BINARY_INTEGER",
        "DECLARE", "PROCEDURE", "FUNCTION", "PACKAGE", "BODY", "CREATE",
        "REPLACE", "TYPE", "RECORD", "OUT", "NOCOPY", "DEFAULT",
        "COMMIT", "ROLLBACK", "SAVEPOINT", "PRAGMA",
    }
    tables -= noise

    return sorted(tables)


def extract_util_calls(body: str) -> list:
    """Extract MSAF_UTIL* procedure calls from body."""
    calls = set()
    pattern = re.compile(r"\b(MSAF_UTIL\w*\.\w+)", re.IGNORECASE)
    for m in pattern.finditer(body):
        calls.add(m.group(1).upper())
    return sorted(calls)


def has_procedure(spec: str, proc_name: str) -> bool:
    """Check if a procedure name exists in spec (case-insensitive)."""
    pattern = re.compile(
        r"\bProcedure\s+" + re.escape(proc_name) + r"\b",
        re.IGNORECASE,
    )
    return bool(pattern.search(spec))


# ---------------------------------------------------------------------------
# Extraction functions for each output
# ---------------------------------------------------------------------------

def build_safx_table_index(source_dir: str) -> dict:
    """Build the safx-table-index.json from msaf_util_insere_safx*.pck files."""
    tables = {}

    for filename in SAFX_INSERT_FILES:
        filepath = os.path.join(source_dir, filename)
        if not os.path.exists(filepath):
            print(f"  WARN: {filename} not found, skipping")
            continue

        text = read_file(filepath)
        pkg_name = extract_package_name(text)
        spec = extract_spec_section(text)
        procs = parse_procedure_signatures(spec)

        for proc in procs:
            name_lower = proc["name"].lower()
            # Match insere_safx{NN} pattern
            m = re.match(r"insere_(safx\w+)", name_lower, re.IGNORECASE)
            if m:
                table_name = m.group(1).upper()
                params = []
                for p in proc["params"]:
                    entry = {"name": p["name"], "type": p["type"]}
                    if "default" in p:
                        entry["default"] = p["default"]
                    params.append(entry)

                tables[table_name] = {
                    "insere_procedure": f"{pkg_name}.{proc['name']}",
                    "package_file": filename,
                    "params": params,
                    "param_count": len(params),
                }

    return {
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": source_dir,
        "tables": dict(sorted(tables.items())),
    }


def build_utility_procedures_index(source_dir: str) -> dict:
    """Build utility-procedures-index.json from msaf_util*.pck files."""
    packages = {}

    for filename, expected_name in UTIL_FILES.items():
        filepath = os.path.join(source_dir, filename)
        if not os.path.exists(filepath):
            print(f"  WARN: {filename} not found, skipping")
            continue

        text = read_file(filepath)
        pkg_name = extract_package_name(text) or expected_name
        spec = extract_spec_section(text)
        procs = parse_procedure_signatures(spec)

        proc_list = []
        for proc in procs:
            entry = {
                "name": proc["name"],
                "params": proc["params"],
            }
            if proc["kind"] == "FUNCTION":
                entry["kind"] = "FUNCTION"
                if "return_type" in proc:
                    entry["return_type"] = proc["return_type"]
            # Mark suite procedures
            if proc["name"].upper().endswith("_SUITE"):
                entry["is_suite"] = True
            proc_list.append(entry)

        packages[pkg_name] = {
            "file": filename,
            "procedures": proc_list,
        }

    return {
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "packages": packages,
    }


def build_cleanup_patterns(source_dir: str) -> dict:
    """Build cleanup-patterns.json from msaf_util_remove.pck."""
    procedures = {}
    filepath = os.path.join(source_dir, "msaf_util_remove.pck")
    if not os.path.exists(filepath):
        print("  WARN: msaf_util_remove.pck not found")
        return {"extracted_at": datetime.now(timezone.utc).isoformat(), "procedures": {}}

    text = read_file(filepath)
    spec = extract_spec_section(text)

    # Extract block comments before procedure declarations for purpose
    # Pattern: /*COMMENT*/ \n Procedure name(...)
    comment_map = {}
    for m in re.finditer(
        r"/\*([^*]*(?:\*(?!/)[^*]*)*)\*/\s*(?:[-]+\s*)?(?:Procedure|procedure|PROCEDURE)\s+(\w+)",
        spec, re.IGNORECASE,
    ):
        comment_text = m.group(1).strip()
        proc_name = m.group(2).lower()
        comment_map[proc_name] = comment_text

    procs = parse_procedure_signatures(spec)

    for proc in procs:
        name_lower = proc["name"].lower()
        if name_lower.startswith("remove_") or name_lower.startswith("limpa_"):
            params = proc["params"]
            purpose = comment_map.get(name_lower, "")
            procedures[name_lower] = {
                "package": "MSAF_UTIL_REMOVE",
                "params": params,
            }
            if purpose:
                procedures[name_lower]["purpose"] = purpose

    return {
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "procedures": dict(sorted(procedures.items())),
    }


def build_module_test_map(source_dir: str) -> dict:
    """Build module-test-map.json from ut_*.pck files."""
    tests = {}
    ut_files = sorted([
        f for f in os.listdir(source_dir)
        if f.lower().startswith("ut_") and f.lower().endswith(".pck")
    ])

    for filename in ut_files:
        filepath = os.path.join(source_dir, filename)
        text = read_file(filepath)

        pkg_name = extract_package_name(text)
        if not pkg_name:
            continue

        spec = extract_spec_section(text)
        body = extract_body_section(text)

        description = extract_description(spec)
        test_procs = extract_test_proc_descriptions(spec)
        has_setup = has_procedure(spec, "ut_setup")
        has_teardown = has_procedure(spec, "ut_teardown")
        tables = extract_tables_from_body(body)
        util_calls = extract_util_calls(body)

        tests[pkg_name] = {
            "file": filename,
            "description": description,
            "has_setup": has_setup,
            "has_teardown": has_teardown,
            "test_procedures": test_procs,
            "test_count": len(test_procs),
            "tables_referenced": tables,
            "util_calls": util_calls,
        }

    return {
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": source_dir,
        "tests": tests,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parse utPLSQL .pck files and produce structured knowledge JSON."
    )
    parser.add_argument(
        "--source", default=DEFAULT_SOURCE,
        help=f"Source directory with .pck files (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT,
        help=f"Output directory for JSON files (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    source_dir = os.path.abspath(args.source)
    output_dir = os.path.abspath(args.output)

    if not os.path.isdir(source_dir):
        print(f"ERROR: Source directory not found: {source_dir}")
        sys.exit(1)

    pck_files = [f for f in os.listdir(source_dir) if f.lower().endswith(".pck")]
    print(f"Source: {source_dir} ({len(pck_files)} .pck files)")
    print(f"Output: {output_dir}")
    print()

    os.makedirs(output_dir, exist_ok=True)

    # 1. SAFX Table Index
    print("[1/4] Building safx-table-index.json ...")
    safx_index = build_safx_table_index(source_dir)
    safx_path = os.path.join(output_dir, "safx-table-index.json")
    with open(safx_path, "w", encoding="utf-8") as f:
        json.dump(safx_index, f, indent=2, ensure_ascii=False)
    safx_count = len(safx_index["tables"])
    print(f"  -> {safx_count} SAFX tables indexed")

    # 2. Utility Procedures Index
    print("[2/4] Building utility-procedures-index.json ...")
    util_index = build_utility_procedures_index(source_dir)
    util_path = os.path.join(output_dir, "utility-procedures-index.json")
    with open(util_path, "w", encoding="utf-8") as f:
        json.dump(util_index, f, indent=2, ensure_ascii=False)
    total_util_procs = sum(len(pkg["procedures"]) for pkg in util_index["packages"].values())
    suite_count = sum(
        1 for pkg in util_index["packages"].values()
        for p in pkg["procedures"] if p.get("is_suite")
    )
    print(f"  -> {len(util_index['packages'])} packages, {total_util_procs} procedures ({suite_count} suites)")

    # 3. Cleanup Patterns
    print("[3/4] Building cleanup-patterns.json ...")
    cleanup = build_cleanup_patterns(source_dir)
    cleanup_path = os.path.join(output_dir, "cleanup-patterns.json")
    with open(cleanup_path, "w", encoding="utf-8") as f:
        json.dump(cleanup, f, indent=2, ensure_ascii=False)
    cleanup_count = len(cleanup["procedures"])
    remove_count = sum(1 for k in cleanup["procedures"] if k.startswith("remove_"))
    limpa_count = sum(1 for k in cleanup["procedures"] if k.startswith("limpa_"))
    print(f"  -> {cleanup_count} cleanup procedures ({remove_count} remove_*, {limpa_count} limpa_*)")

    # 4. Module Test Map
    print("[4/4] Building module-test-map.json ...")
    test_map = build_module_test_map(source_dir)
    test_path = os.path.join(output_dir, "module-test-map.json")
    with open(test_path, "w", encoding="utf-8") as f:
        json.dump(test_map, f, indent=2, ensure_ascii=False)
    test_pkg_count = len(test_map["tests"])
    total_test_procs = sum(t["test_count"] for t in test_map["tests"].values())
    with_setup = sum(1 for t in test_map["tests"].values() if t["has_setup"])
    all_tables = set()
    for t in test_map["tests"].values():
        all_tables.update(t["tables_referenced"])
    print(f"  -> {test_pkg_count} test packages, {total_test_procs} test procedures")
    print(f"     {with_setup} with ut_setup, {len(all_tables)} distinct tables referenced")

    # Summary
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Source files:         {len(pck_files)}")
    print(f"  SAFX tables indexed:  {safx_count}")
    print(f"  Utility packages:     {len(util_index['packages'])}")
    print(f"  Utility procedures:   {total_util_procs} ({suite_count} suites)")
    print(f"  Cleanup procedures:   {cleanup_count}")
    print(f"  Test packages:        {test_pkg_count}")
    print(f"  Test procedures:      {total_test_procs}")
    print(f"  Tables referenced:    {len(all_tables)}")
    print()
    print("Output files:")
    for p in [safx_path, util_path, cleanup_path, test_path]:
        size = os.path.getsize(p)
        print(f"  {p}  ({size:,} bytes)")
    print()
    print("Done.")


if __name__ == "__main__":
    main()
