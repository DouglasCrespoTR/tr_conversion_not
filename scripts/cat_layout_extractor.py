#!/usr/bin/env python3
"""
cat_layout_extractor.py - Extract CAT_LAYOUT_IMP layouts to JSON cache.

Connects to TAX ONE schema via oracledb (thin mode) and queries CAT_LAYOUT_IMP
to produce a JSON file with all SAFX layout definitions.

Output: knowledge/schema/CAT_LAYOUT_IMP_CACHE.json

Usage:
    python scripts/cat_layout_extractor.py
    python scripts/cat_layout_extractor.py --conn "user/pass@host:port/service"
    python scripts/cat_layout_extractor.py --table 07   # Extract only SAFX07 layout
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone

import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    import oracledb
except ImportError:
    print("ERROR: oracledb not installed. Run: pip install oracledb")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CONN_STR = os.environ.get("ORACLE_CONN_STR", "V2R010AC/V2R010AC@localhost:1521/MFSPDB")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge", "schema")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "CAT_LAYOUT_IMP_CACHE.json")

# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------
LAYOUT_QUERY = """
SELECT NOM_TAB_WORK,
       NUM_CAMPO,
       NOME_CAMPO,
       NVL(IND_OBRIG, 'N') AS IND_OBRIG,
       NVL(TIPO, 'A') AS TIPO,
       TAMANHO,
       DSC_COMENTARIO,
       NOME_CAMPO_DEST
FROM CAT_LAYOUT_IMP
{where_clause}
ORDER BY NOM_TAB_WORK, NUM_CAMPO
"""

LAYOUT_QUERY_SINGLE = "WHERE NOM_TAB_WORK = :tab_work"


def extract_layouts(conn, table_filter=None):
    """Extract all layouts from CAT_LAYOUT_IMP."""
    cursor = conn.cursor()

    if table_filter:
        where_clause = LAYOUT_QUERY_SINGLE
        query = LAYOUT_QUERY.format(where_clause=where_clause)
        cursor.execute(query, {"tab_work": table_filter})
    else:
        query = LAYOUT_QUERY.format(where_clause="")
        cursor.execute(query)

    layouts = {}
    for row in cursor:
        tab_work = row[0].strip() if row[0] else ""
        if not tab_work:
            continue

        if tab_work not in layouts:
            layouts[tab_work] = []

        field = {
            "num_campo": row[1],
            "nome_campo": row[2].strip() if row[2] else "",
            "ind_obrig": row[3].strip() if row[3] else "N",
            "tipo": row[4].strip() if row[4] else "A",
            "tamanho": row[5].strip() if row[5] else "",
            "dsc_comentario": row[6].strip() if row[6] else "",
            "nome_campo_dest": row[7].strip() if row[7] else "",
        }
        layouts[tab_work].append(field)

    cursor.close()
    return layouts


def build_cache(layouts, dsn):
    """Build the cache JSON structure."""
    return {
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "source_dsn": dsn,
        "total_tables": len(layouts),
        "total_fields": sum(len(fields) for fields in layouts.values()),
        "layouts": layouts,
    }


def print_summary(cache):
    """Print extraction summary."""
    print(f"\n=== CAT_LAYOUT_IMP Extraction Summary ===")
    print(f"  Tables: {cache['total_tables']}")
    print(f"  Fields: {cache['total_fields']}")
    print(f"  Source: {cache['source_dsn']}")
    print(f"  Time:   {cache['extracted_at']}")

    if cache["total_tables"] > 0:
        print(f"\n  Top 10 tables by field count:")
        sorted_tables = sorted(
            cache["layouts"].items(), key=lambda x: len(x[1]), reverse=True
        )
        for tab, fields in sorted_tables[:10]:
            mandatory = sum(1 for f in fields if f["ind_obrig"] == "S")
            print(f"    SAFX{tab}: {len(fields)} fields ({mandatory} mandatory)")


def main():
    parser = argparse.ArgumentParser(
        description="Extract CAT_LAYOUT_IMP layouts to JSON cache"
    )
    parser.add_argument(
        "--conn",
        default=CONN_STR,
        help=f"Oracle connection string (default: {CONN_STR})",
    )
    parser.add_argument(
        "--table",
        default=None,
        help="Extract only a specific SAFX table (e.g., '07' for SAFX07)",
    )
    parser.add_argument(
        "--output",
        default=OUTPUT_FILE,
        help=f"Output JSON file path (default: {OUTPUT_FILE})",
    )
    args = parser.parse_args()

    # Parse connection string
    if "/" in args.conn and "@" in args.conn:
        user_pass, dsn = args.conn.split("@", 1)
        user, password = user_pass.split("/", 1)
    else:
        print(f"ERROR: Invalid connection string format: {args.conn}")
        print("Expected: user/password@host:port/service")
        sys.exit(1)

    print(f"Connecting to {dsn} ...")
    try:
        conn = oracledb.connect(user=user, password=password, dsn=dsn)
    except Exception as e:
        print(f"ERROR: Failed to connect: {e}")
        sys.exit(1)

    print(f"Extracting CAT_LAYOUT_IMP layouts ...")
    try:
        layouts = extract_layouts(conn, table_filter=args.table)
    except Exception as e:
        print(f"ERROR: Failed to extract layouts: {e}")
        conn.close()
        sys.exit(1)

    conn.close()

    if not layouts:
        print("WARNING: No layouts found in CAT_LAYOUT_IMP")
        if args.table:
            print(f"  (filtered by NOM_TAB_WORK = '{args.table}')")
        sys.exit(1)

    # Build and save cache
    cache = build_cache(layouts, dsn)
    print_summary(cache)

    # If extracting single table and cache file exists, merge
    if args.table and os.path.exists(args.output):
        print(f"\nMerging with existing cache ...")
        with open(args.output, "r", encoding="utf-8") as f:
            existing = json.load(f)
        existing["layouts"].update(cache["layouts"])
        existing["extracted_at"] = cache["extracted_at"]
        existing["total_tables"] = len(existing["layouts"])
        existing["total_fields"] = sum(
            len(fields) for fields in existing["layouts"].values()
        )
        cache = existing

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    print(f"\nSaved to: {args.output}")
    print(f"Size: {os.path.getsize(args.output) / 1024:.1f} KB")


if __name__ == "__main__":
    main()
