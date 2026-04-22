#!/usr/bin/env python3
"""
schema_extractor.py - Extract full Oracle data model to Markdown files.

Connects to TAX ONE schema via oracledb and queries the data dictionary
(USER_TABLES, USER_TAB_COLUMNS, USER_CONSTRAINTS, USER_INDEXES, etc.)
to produce organized .md files grouped by table prefix (module).

Output: knowledge/schema/*.md
"""

import os
import sys
import time
from collections import defaultdict

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
MIN_PREFIX_COUNT = 10  # prefixes with <= this many tables go to MISC.md

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_prefix(table_name: str) -> str:
    """Extract module prefix from table name (first segment before '_')."""
    # Special multi-segment prefixes
    for sp in ("SAFX", "DWT"):
        if table_name.startswith(sp):
            return sp
    # X01..X11 → "X"
    if len(table_name) >= 3 and table_name[0] == "X" and table_name[1:3].isdigit():
        return "X"
    # Default: first segment
    idx = table_name.find("_")
    if idx > 0:
        return table_name[:idx]
    return table_name


def fmt_type(data_type, data_length, data_precision, data_scale, char_length):
    """Format Oracle column type for display."""
    t = data_type or ""
    if t in ("VARCHAR2", "CHAR", "NVARCHAR2", "NCHAR", "RAW"):
        return f"{t}({char_length or data_length})"
    if t == "NUMBER":
        if data_precision is not None:
            if data_scale and data_scale > 0:
                return f"NUMBER({data_precision},{data_scale})"
            return f"NUMBER({data_precision})"
        return "NUMBER"
    if t.startswith("TIMESTAMP"):
        return t
    return t


def escape_md(text: str) -> str:
    """Escape pipe characters for markdown tables."""
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", "")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_all(conn):
    """Load all metadata from Oracle data dictionary in batched queries."""
    cur = conn.cursor()
    cur.arraysize = 5000

    print("  Loading tables + comments...")
    cur.execute("""
        SELECT t.TABLE_NAME, NVL(c.COMMENTS, '')
        FROM USER_TABLES t
        LEFT JOIN USER_TAB_COMMENTS c ON c.TABLE_NAME = t.TABLE_NAME
        ORDER BY t.TABLE_NAME
    """)
    tables = {}  # table_name -> comment
    for row in cur:
        tables[row[0]] = row[1] or ""
    print(f"    → {len(tables)} tables")

    print("  Loading columns + comments...")
    cur.execute("""
        SELECT tc.TABLE_NAME, tc.COLUMN_NAME, tc.DATA_TYPE,
               tc.DATA_LENGTH, tc.DATA_PRECISION, tc.DATA_SCALE,
               tc.CHAR_LENGTH, tc.NULLABLE, tc.DATA_DEFAULT,
               tc.COLUMN_ID, NVL(cc.COMMENTS, '')
        FROM USER_TAB_COLUMNS tc
        LEFT JOIN USER_COL_COMMENTS cc
            ON cc.TABLE_NAME = tc.TABLE_NAME AND cc.COLUMN_NAME = tc.COLUMN_NAME
        WHERE tc.TABLE_NAME IN (SELECT TABLE_NAME FROM USER_TABLES)
        ORDER BY tc.TABLE_NAME, tc.COLUMN_ID
    """)
    columns = defaultdict(list)  # table -> [(col_name, type_str, nullable, default, comment)]
    for row in cur:
        tname, cname = row[0], row[1]
        type_str = fmt_type(row[2], row[3], row[4], row[5], row[6])
        nullable = "Y" if row[7] == "Y" else "N"
        default_val = (row[8] or "").strip() if row[8] else ""
        if len(default_val) > 50:
            default_val = default_val[:47] + "..."
        comment = row[10] or ""
        columns[tname].append((cname, type_str, nullable, default_val, comment))
    total_cols = sum(len(v) for v in columns.values())
    print(f"    → {total_cols} columns")

    print("  Loading primary keys...")
    cur.execute("""
        SELECT cc.TABLE_NAME, cc.COLUMN_NAME, cc.POSITION
        FROM USER_CONSTRAINTS c
        JOIN USER_CONS_COLUMNS cc ON cc.CONSTRAINT_NAME = c.CONSTRAINT_NAME
        WHERE c.CONSTRAINT_TYPE = 'P'
        ORDER BY cc.TABLE_NAME, cc.POSITION
    """)
    pks = defaultdict(list)  # table -> [col_name ordered by position]
    for row in cur:
        pks[row[0]].append(row[1])
    print(f"    → {len(pks)} tables with PKs")

    print("  Loading foreign keys...")
    cur.execute("""
        SELECT c.TABLE_NAME, c.CONSTRAINT_NAME, c.R_CONSTRAINT_NAME,
               r.TABLE_NAME AS R_TABLE_NAME
        FROM USER_CONSTRAINTS c
        JOIN USER_CONSTRAINTS r ON r.CONSTRAINT_NAME = c.R_CONSTRAINT_NAME
        WHERE c.CONSTRAINT_TYPE = 'R'
        ORDER BY c.TABLE_NAME, c.CONSTRAINT_NAME
    """)
    fk_meta = {}  # constraint_name -> (table, r_table, r_constraint_name)
    for row in cur:
        fk_meta[row[1]] = (row[0], row[3], row[2])

    cur.execute("""
        SELECT cc.CONSTRAINT_NAME, cc.COLUMN_NAME, cc.POSITION
        FROM USER_CONS_COLUMNS cc
        WHERE cc.CONSTRAINT_NAME IN (
            SELECT CONSTRAINT_NAME FROM USER_CONSTRAINTS WHERE CONSTRAINT_TYPE = 'R'
        )
        ORDER BY cc.CONSTRAINT_NAME, cc.POSITION
    """)
    fk_cols_map = defaultdict(list)
    for row in cur:
        fk_cols_map[row[0]].append(row[1])

    # Also get referenced PK columns for each R_CONSTRAINT_NAME
    r_constraint_names = set(v[2] for v in fk_meta.values())
    ref_pk_cols = defaultdict(list)
    if r_constraint_names:
        # batch in groups of 900 to avoid IN-clause limits
        r_list = list(r_constraint_names)
        for i in range(0, len(r_list), 900):
            batch = r_list[i:i+900]
            placeholders = ",".join(f":p{j}" for j in range(len(batch)))
            bind = {f"p{j}": v for j, v in enumerate(batch)}
            cur.execute(f"""
                SELECT CONSTRAINT_NAME, COLUMN_NAME, POSITION
                FROM USER_CONS_COLUMNS
                WHERE CONSTRAINT_NAME IN ({placeholders})
                ORDER BY CONSTRAINT_NAME, POSITION
            """, bind)
            for row in cur:
                ref_pk_cols[row[0]].append(row[1])

    # Build FK structure: table -> [(fk_cols, ref_table, ref_cols)]
    fks = defaultdict(list)
    for cname, (tbl, r_tbl, r_cname) in fk_meta.items():
        fk_columns = fk_cols_map.get(cname, [])
        ref_columns = ref_pk_cols.get(r_cname, [])
        fks[tbl].append((fk_columns, r_tbl, ref_columns))
    print(f"    → {sum(len(v) for v in fks.values())} foreign keys")

    print("  Loading indexes...")
    cur.execute("""
        SELECT i.TABLE_NAME, i.INDEX_NAME, i.UNIQUENESS
        FROM USER_INDEXES i
        WHERE i.TABLE_NAME IN (SELECT TABLE_NAME FROM USER_TABLES)
          AND i.INDEX_TYPE != 'LOB'
        ORDER BY i.TABLE_NAME, i.INDEX_NAME
    """)
    idx_meta = {}  # index_name -> (table, uniqueness)
    for row in cur:
        idx_meta[row[1]] = (row[0], row[2])

    # Exclude PK indexes (they share constraint name)
    pk_constraint_names = set()
    cur.execute("""
        SELECT CONSTRAINT_NAME FROM USER_CONSTRAINTS WHERE CONSTRAINT_TYPE = 'P'
    """)
    for row in cur:
        pk_constraint_names.add(row[0])

    cur.execute("""
        SELECT ic.INDEX_NAME, ic.COLUMN_NAME, ic.COLUMN_POSITION
        FROM USER_IND_COLUMNS ic
        WHERE ic.TABLE_NAME IN (SELECT TABLE_NAME FROM USER_TABLES)
        ORDER BY ic.INDEX_NAME, ic.COLUMN_POSITION
    """)
    idx_cols_map = defaultdict(list)
    for row in cur:
        idx_cols_map[row[0]].append(row[1])

    # Build index structure: table -> [(idx_name, uniqueness, [cols])]
    indexes = defaultdict(list)
    for idx_name, (tbl, uniq) in idx_meta.items():
        if idx_name in pk_constraint_names:
            continue  # skip PK indexes
        cols = idx_cols_map.get(idx_name, [])
        indexes[tbl].append((idx_name, uniq, cols))
    print(f"    → {sum(len(v) for v in indexes.values())} indexes (excl PKs)")

    cur.close()
    return tables, columns, pks, fks, indexes


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

def write_table_md(f, tname, comment, cols, pk_cols, fk_list, idx_list):
    """Write markdown for a single table."""
    f.write(f"## {tname}\n")
    if comment:
        f.write(f"**Comment**: {escape_md(comment)}\n\n")
    else:
        f.write("\n")

    # Columns table
    f.write("| # | Coluna | Tipo | Null | Default | Comment |\n")
    f.write("|---|--------|------|------|---------|--------|\n")
    for i, (cname, ctype, nullable, default, col_comment) in enumerate(cols, 1):
        f.write(f"| {i} | {cname} | {ctype} | {nullable} | {escape_md(default)} | {escape_md(col_comment)} |\n")
    f.write("\n")

    # PK
    if pk_cols:
        f.write(f"**PK**: {', '.join(pk_cols)}\n\n")

    # FKs
    if fk_list:
        f.write("**FKs**:\n")
        for fk_cols, ref_tbl, ref_cols in fk_list:
            fk_str = ", ".join(fk_cols)
            ref_str = ", ".join(ref_cols)
            f.write(f"- {fk_str} → {ref_tbl}({ref_str})\n")
        f.write("\n")

    # Indexes
    if idx_list:
        f.write("**Indexes**:\n")
        for idx_name, uniq, idx_cols in idx_list:
            u_tag = " (UNIQUE)" if uniq == "UNIQUE" else ""
            f.write(f"- {idx_name}{u_tag}: ({', '.join(idx_cols)})\n")
        f.write("\n")

    f.write("---\n\n")


def generate_module_file(filepath, prefix, label, table_names, tables, columns, pks, fks, indexes):
    """Generate a module .md file for a group of tables."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"# Módulo: {prefix} ({label}) - {len(table_names)} tabelas\n\n")
        for tname in sorted(table_names):
            write_table_md(
                f, tname,
                tables.get(tname, ""),
                columns.get(tname, []),
                pks.get(tname, []),
                fks.get(tname, []),
                indexes.get(tname, []),
            )
    return len(table_names)


# Module labels for known prefixes
MODULE_LABELS = {
    "SAFX": "Interface/Staging",
    "X": "Transacionais X01-X11",
    "DWT": "Dimensões/Lookup",
    "TMP": "Temporárias",
    "EST": "Estadual",
    "ICT": "ICMS",
    "PRT": "PRT",
    "REINF": "EFD-Reinf",
    "PFI": "PFI",
    "EPC": "EFD PIS/COFINS",
    "EFD": "EFD",
    "ACT": "Auditoria/Controle",
    "CTB": "Contábil",
    "GIA": "GIA",
    "DIEF": "DIEF",
    "SEF": "SEF",
    "DMS": "DMS",
    "NFE": "NF-e",
    "NFS": "NFS-e",
    "CTE": "CT-e",
    "GIS": "GISS",
    "DIRF": "DIRF",
    "PERDCOMP": "PER/DCOMP",
    "ECF": "ECF",
    "DCTF": "DCTF",
    "CIAP": "CIAP",
    "LFP": "Livro Fiscal",
    "MFS": "MFS Core",
    "DES": "DES",
    "ISS": "ISS",
    "OBR": "Obrigações",
    "CAD": "Cadastros",
    "RFB": "Receita Federal",
    "DME": "DME",
}


def generate_relationships_file(filepath, fks, tables):
    """Generate cross-module FK relationships in Mermaid."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("# Relacionamentos Cross-Module (FKs)\n\n")
        f.write("## Diagrama Mermaid (top 100 cross-module FKs)\n\n")
        f.write("```mermaid\ngraph LR\n")

        cross_module = []
        for tbl, fk_list in fks.items():
            src_prefix = get_prefix(tbl)
            for fk_cols, ref_tbl, ref_cols in fk_list:
                dst_prefix = get_prefix(ref_tbl)
                if src_prefix != dst_prefix:
                    cross_module.append((tbl, ref_tbl, fk_cols, ref_cols))

        # Group by module pair and count
        pair_count = defaultdict(int)
        for src, dst, _, _ in cross_module:
            pair_count[(get_prefix(src), get_prefix(dst))] += 1

        # Top 100 module pairs
        top_pairs = sorted(pair_count.items(), key=lambda x: -x[1])[:100]
        for (src_p, dst_p), cnt in top_pairs:
            f.write(f"    {src_p} -->|{cnt} FKs| {dst_p}\n")

        f.write("```\n\n")

        f.write(f"**Total cross-module FKs**: {len(cross_module)}\n\n")

        # Detailed list
        f.write("## Lista Detalhada\n\n")
        f.write("| Tabela Origem | Colunas FK | Tabela Destino | Colunas Ref |\n")
        f.write("|---------------|-----------|----------------|-------------|\n")
        for src, dst, fk_cols, ref_cols in sorted(cross_module):
            f.write(f"| {src} | {', '.join(fk_cols)} | {dst} | {', '.join(ref_cols)} |\n")
        f.write(f"\n**Total**: {len(cross_module)} relacionamentos cross-module\n")


def generate_index_file(filepath, module_files, stats):
    """Generate INDEX.md with links to all module files."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("# TAX ONE - Modelo de Dados\n\n")
        f.write(f"**Gerado em**: {time.strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(f"| Métrica | Valor |\n")
        f.write(f"|---------|-------|\n")
        f.write(f"| Tabelas | {stats['tables']:,} |\n")
        f.write(f"| Colunas | {stats['columns']:,} |\n")
        f.write(f"| Foreign Keys | {stats['fks']:,} |\n")
        f.write(f"| Indexes | {stats['indexes']:,} |\n")
        f.write(f"| Módulos | {len(module_files)} |\n\n")

        f.write("## Módulos\n\n")
        f.write("| Módulo | Arquivo | Tabelas | Descrição |\n")
        f.write("|--------|---------|---------|----------|\n")
        for fname, prefix, label, count in sorted(module_files, key=lambda x: -x[3]):
            f.write(f"| {prefix} | [{fname}]({fname}) | {count} | {label} |\n")
        f.write(f"\n**Cross-module**: [RELATIONSHIPS.md](RELATIONSHIPS.md)\n")


def generate_stats_file(filepath, tables, columns, pks, fks, indexes, prefix_groups):
    """Generate _STATS.md with detailed statistics."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("# Estatísticas do Schema TAX ONE\n\n")
        f.write(f"**Gerado em**: {time.strftime('%Y-%m-%d %H:%M')}\n\n")

        total_cols = sum(len(v) for v in columns.values())
        total_fks = sum(len(v) for v in fks.values())
        total_idx = sum(len(v) for v in indexes.values())

        f.write("## Resumo Geral\n\n")
        f.write(f"- **Tabelas**: {len(tables):,}\n")
        f.write(f"- **Colunas**: {total_cols:,}\n")
        f.write(f"- **Média colunas/tabela**: {total_cols/max(len(tables),1):.1f}\n")
        f.write(f"- **PKs**: {len(pks):,} tabelas com PK\n")
        f.write(f"- **FKs**: {total_fks:,}\n")
        f.write(f"- **Indexes**: {total_idx:,} (excl. PKs)\n\n")

        f.write("## Top 20 tabelas por número de colunas\n\n")
        f.write("| Tabela | Colunas |\n")
        f.write("|--------|---------|\n")
        top_tables = sorted(columns.items(), key=lambda x: -len(x[1]))[:20]
        for tname, cols in top_tables:
            f.write(f"| {tname} | {len(cols)} |\n")

        f.write("\n## Distribuição por módulo\n\n")
        f.write("| Prefixo | Tabelas | Colunas |\n")
        f.write("|---------|---------|--------|\n")
        for prefix in sorted(prefix_groups.keys(), key=lambda p: -len(prefix_groups[p])):
            tcount = len(prefix_groups[prefix])
            ccount = sum(len(columns.get(t, [])) for t in prefix_groups[prefix])
            f.write(f"| {prefix} | {tcount} | {ccount} |\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Connecting to Oracle (thin mode)...")
    db_user = os.environ.get("ORACLE_USER", "V2R010AC")
    db_pass = os.environ.get("ORACLE_PASSWORD", "V2R010AC")
    db_dsn = os.environ.get("ORACLE_DSN", "localhost:1521/MFSPDB")
    conn = oracledb.connect(user=db_user, password=db_pass, dsn=db_dsn)
    print("Connected.\n")

    print("Loading metadata from data dictionary...")
    t0 = time.time()
    tables, columns, pks, fks, indexes = load_all(conn)
    conn.close()
    elapsed = time.time() - t0
    print(f"\nMetadata loaded in {elapsed:.1f}s\n")

    # Group tables by prefix
    prefix_groups = defaultdict(list)
    for tname in tables:
        prefix_groups[get_prefix(tname)].append(tname)

    # Separate large vs small prefix groups
    large_prefixes = {p: tlist for p, tlist in prefix_groups.items() if len(tlist) > MIN_PREFIX_COUNT}
    misc_tables = []
    misc_prefixes = []
    for p, tlist in prefix_groups.items():
        if len(tlist) <= MIN_PREFIX_COUNT:
            misc_tables.extend(tlist)
            misc_prefixes.append(p)

    print(f"Modules: {len(large_prefixes)} large + 1 MISC ({len(misc_prefixes)} small prefixes, {len(misc_tables)} tables)")
    print("Generating markdown files...\n")

    module_files = []  # (filename, prefix, label, count)

    # Generate per-module files
    for prefix, tlist in sorted(large_prefixes.items(), key=lambda x: -len(x[1])):
        label = MODULE_LABELS.get(prefix, prefix)
        if prefix == "X":
            fname = "CORE_X.md"
        elif prefix == "SAFX":
            fname = "CORE_SAFX.md"
        elif prefix == "DWT":
            fname = "DWT.md"
        elif prefix == "TMP":
            fname = "TMP.md"
        else:
            fname = f"{prefix}.md"

        fpath = os.path.join(OUTPUT_DIR, fname)
        count = generate_module_file(fpath, prefix, label, tlist, tables, columns, pks, fks, indexes)
        module_files.append((fname, prefix, label, count))
        print(f"  {fname}: {count} tables")

    # Generate MISC.md
    if misc_tables:
        fpath = os.path.join(OUTPUT_DIR, "MISC.md")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(f"# Módulo: MISC (Prefixos pequenos) - {len(misc_tables)} tabelas\n\n")
            f.write(f"Prefixos agrupados ({len(misc_prefixes)}): {', '.join(sorted(misc_prefixes))}\n\n")
            for tname in sorted(misc_tables):
                write_table_md(
                    f, tname,
                    tables.get(tname, ""),
                    columns.get(tname, []),
                    pks.get(tname, []),
                    fks.get(tname, []),
                    indexes.get(tname, []),
                )
        module_files.append(("MISC.md", "MISC", "Prefixos pequenos", len(misc_tables)))
        print(f"  MISC.md: {len(misc_tables)} tables ({len(misc_prefixes)} prefixes)")

    # Generate RELATIONSHIPS.md
    print("\n  Generating RELATIONSHIPS.md...")
    generate_relationships_file(os.path.join(OUTPUT_DIR, "RELATIONSHIPS.md"), fks, tables)

    # Generate _STATS.md
    generate_stats_file(
        os.path.join(OUTPUT_DIR, "_STATS.md"),
        tables, columns, pks, fks, indexes, prefix_groups,
    )

    # Generate INDEX.md
    total_fks = sum(len(v) for v in fks.values())
    total_idx = sum(len(v) for v in indexes.values())
    total_cols = sum(len(v) for v in columns.values())
    stats = {
        "tables": len(tables),
        "columns": total_cols,
        "fks": total_fks,
        "indexes": total_idx,
    }
    generate_index_file(os.path.join(OUTPUT_DIR, "INDEX.md"), module_files, stats)

    print(f"\nDone! Generated {len(module_files) + 3} files in {OUTPUT_DIR}")
    print(f"  Tables: {len(tables):,} | Columns: {total_cols:,} | FKs: {total_fks:,} | Indexes: {total_idx:,}")


if __name__ == "__main__":
    main()
