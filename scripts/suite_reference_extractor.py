#!/usr/bin/env python3
"""
suite_reference_extractor.py
Extracts reference data from SuiteAutomation test XMLs and SAFX carga files.

Generates:
  1. reference-data.json       — Valid empresa+estab base data (COD_NATUREZA_OP, COD_FIS_JUR, etc.)
  2. grupo-arquivo-map.json    — GRUPO_ARQUIVO+NUMERO_ARQUIVO → SAFX table mapping
  3. cleanup-patterns.json     — DELETE patterns per table (from XML tests)
  4. safx-field-layouts.json   — Column order for SAFX tables (for carga file parsing)

Usage:
  python scripts/suite_reference_extractor.py [--db]

  --db  Complement with database queries (RELAC_TAB_GRUPO, CAT_PRIOR_IMP, X2006)
        Requires: oracledb, connection to V2R010AC@localhost:1521/MFSPDB
"""

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

# --- Config ---
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SUITE_TESTE_DIR = PROJECT_ROOT / "suite-automation" / "teste"
OUTPUT_DIR = PROJECT_ROOT / "knowledge" / "suite-automation"

# QA repo path from .env or default
ENV_FILE = PROJECT_ROOT / ".env"
QA_REPO = None
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("QA_REPO="):
            QA_REPO = Path(line.split("=", 1)[1].strip())
            break
if not QA_REPO:
    QA_REPO = Path(r"C:\@@Dev\Git\taxone_automacao_qa")

CARGAS_DIR = QA_REPO / "arquivos" / "esperado" / "UT_JOB_SERVIDOR" / "Cargas"

# SAFX07 field positions (0-indexed, tab-delimited)
SAFX07_FIELDS = {
    0: "COD_EMPRESA",
    1: "COD_ESTAB",
    2: "MOVTO_E_S",
    3: "NORM_DEV",
    4: "COD_DOCTO",
    5: "IDENT_FIS_JUR",
    6: "COD_FIS_JUR",
    7: "NUM_DOCFIS",
    8: "SERIE_DOCFIS",
    9: "SUB_SERIE_DOCFIS",
    10: "DATA_EMISSAO",
    11: "COD_CLASS_DOC_FIS",
    12: "COD_MODELO",
    13: "COD_CFO",
    14: "COD_NATUREZA_OP",
}


def parse_xmls():
    """Parse all SuiteAutomation XMLs for cargasafx, inserts, deletes."""
    cargasafx_refs = []        # [(empresa, safx_table, estab, source_xml)]
    det_job_inserts = []       # [(grupo, numero, empresa, estab, source_xml)]
    delete_patterns = defaultdict(list)  # table → [where_clause]
    libparams = defaultdict(set)         # key → {values}

    if not SUITE_TESTE_DIR.exists():
        print(f"WARNING: {SUITE_TESTE_DIR} not found, skipping XML parsing")
        return cargasafx_refs, det_job_inserts, delete_patterns, libparams

    for xml_path in SUITE_TESTE_DIR.rglob("*.xml"):
        try:
            tree = ET.parse(xml_path, parser=ET.XMLParser(encoding="iso-8859-1"))
        except ET.ParseError:
            # Try with latin-1 recovery
            try:
                content = xml_path.read_bytes().decode("iso-8859-1", errors="replace")
                tree = ET.ElementTree(ET.fromstring(content))
            except Exception:
                print(f"  SKIP (parse error): {xml_path.name}")
                continue

        rel_path = xml_path.relative_to(SUITE_TESTE_DIR)

        # Extract cargasafx
        for elem in tree.iter("cargasafx"):
            text = (elem.text or "").strip()
            if ";" in text:
                empresa, path = text.split(";", 1)
                empresa = empresa.strip()
                # Extract SAFX table from path (e.g., \\Cargas\\safx07\\safx07_00001.txt)
                path_lower = path.lower().replace("\\\\", "/").replace("\\", "/")
                m = re.search(r"/cargas/(safx\w+)/", path_lower)
                if m:
                    safx_table = m.group(1).upper()
                    # Extract estab from file content later; for now note the reference
                    cargasafx_refs.append((empresa, safx_table, path, str(rel_path)))

        # Extract inserts into det_job_import
        for elem in tree.iter("insert"):
            text = (elem.text or "").strip().lower()
            if "det_job_import" in text:
                # Extract VALUES(...)
                m = re.search(r"values\s*\((.*?)\)", text, re.DOTALL | re.IGNORECASE)
                if m:
                    vals = [v.strip().strip("'\"") for v in m.group(1).split(",")]
                    # Expected: (num_job, grupo, numero, empresa, estab, ...)
                    if len(vals) >= 5:
                        grupo = vals[1].strip()
                        numero = vals[2].strip()
                        empresa = vals[3].strip()
                        estab = vals[4].strip()
                        # Skip template vars
                        if not grupo.startswith("#"):
                            det_job_inserts.append((grupo, numero, empresa, estab, str(rel_path)))

        # Extract deletes
        for elem in tree.iter("delete"):
            text = (elem.text or "").strip()
            if text:
                # Extract table name
                m = re.match(r"delete\s+from\s+(\w+)", text, re.IGNORECASE)
                if m:
                    table = m.group(1).upper()
                    # Extract WHERE clause
                    where_m = re.search(r"where\s+(.+)", text, re.IGNORECASE)
                    where_clause = where_m.group(1).strip() if where_m else ""
                    delete_patterns[table].append({
                        "where": where_clause,
                        "source": str(rel_path),
                    })

        # Extract libparams
        for elem in tree.iter("libparam"):
            text = (elem.text or "").strip()
            if ";" in text:
                key, val = text.split(";", 1)
                libparams[key.strip().upper()].add(val.strip())

    return cargasafx_refs, det_job_inserts, delete_patterns, libparams


def parse_cargas():
    """Parse SAFX carga .txt files to extract reference data per empresa+estab."""
    reference_data = {}  # "empresa|estab" → {...}

    if not CARGAS_DIR.exists():
        print(f"WARNING: {CARGAS_DIR} not found, skipping carga parsing")
        return reference_data

    # Track which SAFX tables have cargas per empresa+estab
    estab_tables = defaultdict(set)  # "empresa|estab" → {SAFX07, SAFX08, ...}

    # Parse SAFX07 cargas for reference fields
    safx07_dir = CARGAS_DIR / "safx07"
    if safx07_dir.exists():
        for txt_file in sorted(safx07_dir.glob("*.txt")):
            try:
                lines = txt_file.read_text(encoding="latin-1", errors="replace").strip().splitlines()
            except Exception:
                continue

            for line in lines:
                fields = line.split("\t")
                if len(fields) < 15:
                    continue

                empresa = fields[0].strip()
                estab = fields[1].strip()
                if not empresa or not estab:
                    continue

                key = f"{empresa}|{estab}"
                estab_tables[key].add("SAFX07")

                if key not in reference_data:
                    reference_data[key] = {
                        "empresa": empresa,
                        "estab": estab,
                        "source_files": [],
                        "cod_natureza_op": [],
                        "cod_class_doc_fis": [],
                        "cod_docto": [],
                        "ident_fis_jur": [],
                        "cod_fis_jur": [],
                        "serie_docfis": [],
                        "cod_modelo": [],
                        "movto_e_s": [],
                    }

                ref = reference_data[key]
                if txt_file.name not in ref["source_files"]:
                    ref["source_files"].append(txt_file.name)

                # Extract non-empty reference values
                def add_val(lst, val):
                    val = val.strip().replace("@", "").strip()
                    if val and val not in lst:
                        lst.append(val)

                add_val(ref["cod_natureza_op"], fields[14])
                add_val(ref["cod_class_doc_fis"], fields[11])
                add_val(ref["cod_docto"], fields[4])
                add_val(ref["ident_fis_jur"], fields[5])
                add_val(ref["cod_fis_jur"], fields[6])
                add_val(ref["serie_docfis"], fields[8])
                add_val(ref["cod_modelo"], fields[12])
                add_val(ref["movto_e_s"], fields[2])

    # Scan all other SAFX dirs to map tables per estab
    for safx_dir in sorted(CARGAS_DIR.iterdir()):
        if not safx_dir.is_dir():
            continue
        safx_name = safx_dir.name.upper()
        if not safx_name.startswith("SAFX"):
            continue

        for txt_file in safx_dir.glob("*.txt"):
            try:
                first_line = txt_file.read_text(encoding="latin-1", errors="replace").split("\n")[0]
            except Exception:
                continue

            fields = first_line.split("\t")
            if len(fields) >= 2:
                empresa = fields[0].strip()
                estab = fields[1].strip()
                if empresa and estab:
                    estab_tables[f"{empresa}|{estab}"].add(safx_name)

    # Add available tables to reference data
    for key, tables in estab_tables.items():
        if key in reference_data:
            reference_data[key]["valid_safx_tables"] = sorted(tables)
        else:
            empresa, estab = key.split("|")
            reference_data[key] = {
                "empresa": empresa,
                "estab": estab,
                "source_files": [],
                "valid_safx_tables": sorted(tables),
                "cod_natureza_op": [],
                "cod_class_doc_fis": [],
                "cod_docto": [],
                "ident_fis_jur": [],
                "cod_fis_jur": [],
                "serie_docfis": [],
                "cod_modelo": [],
                "movto_e_s": [],
            }

    return reference_data


def build_grupo_arquivo_map(det_job_inserts):
    """Build GRUPO_ARQUIVO+NUMERO_ARQUIVO → SAFX table mapping."""
    # From XML det_job_import inserts
    grupo_map = {}
    for grupo, numero, empresa, estab, source in det_job_inserts:
        key = f"{grupo}|{numero}"
        if key not in grupo_map:
            grupo_map[key] = {
                "grupo_arquivo": int(grupo) if grupo.isdigit() else grupo,
                "numero_arquivo": int(numero) if numero.isdigit() else numero,
                "sources": [],
            }
        grupo_map[key]["sources"].append(source)

    return grupo_map


def build_cleanup_patterns(delete_patterns):
    """Build cleanup patterns per table."""
    result = {}
    for table, patterns in sorted(delete_patterns.items()):
        # Deduplicate and keep unique WHERE patterns
        unique_wheres = []
        seen = set()
        for p in patterns:
            w = p["where"]
            if w not in seen:
                seen.add(w)
                unique_wheres.append(p)

        result[table] = {
            "delete_count": len(patterns),
            "unique_patterns": len(unique_wheres),
            "examples": unique_wheres[:5],  # Keep top 5 examples
        }

    return result


def complement_with_db(grupo_map):
    """Complement GRUPO_ARQUIVO map with database CAT_PRIOR_IMP data."""
    try:
        import oracledb
        oracledb.init_oracle_client()
    except Exception:
        try:
            import oracledb
        except ImportError:
            print("  oracledb not available, skipping DB complement")
            return

    try:
        db_user = os.environ.get("BD_USER", "")
        db_pass = os.environ.get("BD_PASSWD", "")
        db_dsn = os.environ.get("BD_DSN", "localhost:1521/MFSPDB")
        if not db_user or not db_pass:
            print("  BD_USER/BD_PASSWD not set, skipping DB complement")
            return
        conn = oracledb.connect(
            user=db_user, password=db_pass,
            dsn=db_dsn
        )
        cur = conn.cursor()

        # CAT_PRIOR_IMP: GRUPO_ARQUIVO, NUMERO_ARQUIVO, NOM_TAB_WORK
        cur.execute("""
            SELECT GRUPO_ARQUIVO, NUMERO_ARQUIVO, NOM_TAB_WORK
              FROM CAT_PRIOR_IMP
             WHERE NOM_TAB_WORK LIKE 'SAFX%'
             ORDER BY GRUPO_ARQUIVO, NUMERO_ARQUIVO
        """)

        for grupo, numero, nom_tab in cur:
            key = f"{grupo}|{numero}"
            safx_name = nom_tab.strip() if nom_tab else ""
            if key not in grupo_map:
                grupo_map[key] = {
                    "grupo_arquivo": grupo,
                    "numero_arquivo": numero,
                    "safx_table": safx_name,
                    "sources": ["CAT_PRIOR_IMP"],
                }
            else:
                grupo_map[key]["safx_table"] = safx_name

        # RELAC_TAB_GRUPO for grupo_natureza per empresa+estab
        # (stored separately, returned for enrichment)
        cur.execute("""
            SELECT COD_EMPRESA, COD_ESTAB, GRUPO_ESTAB,
                   MAX(VALID_INICIAL) AS max_valid
              FROM RELAC_TAB_GRUPO
             WHERE COD_TABELA = 2006
             GROUP BY COD_EMPRESA, COD_ESTAB, GRUPO_ESTAB
             HAVING MAX(VALID_INICIAL) = (
                SELECT MAX(VALID_INICIAL)
                  FROM RELAC_TAB_GRUPO r2
                 WHERE r2.COD_EMPRESA = RELAC_TAB_GRUPO.COD_EMPRESA
                   AND r2.COD_ESTAB = RELAC_TAB_GRUPO.COD_ESTAB
                   AND r2.COD_TABELA = 2006
             )
             ORDER BY COD_EMPRESA, COD_ESTAB
        """)

        grupo_natureza_map = {}
        for empresa, estab, grupo_estab, _ in cur:
            key = f"{empresa.strip()}|{estab.strip()}"
            grupo_natureza_map[key] = grupo_estab.strip() if grupo_estab else None

        # X2006_NATUREZA_OP: valid COD_NATUREZA_OP per GRUPO_NATUREZA_OP
        natop_by_grupo = defaultdict(list)
        cur.execute("""
            SELECT GRUPO_NATUREZA_OP, COD_NATUREZA_OP, ENT_SAI
              FROM X2006_NATUREZA_OP
             ORDER BY GRUPO_NATUREZA_OP, COD_NATUREZA_OP
        """)
        for grupo, natop, ent_sai in cur:
            g = grupo.strip() if grupo else ""
            n = natop.strip() if natop else ""
            e = ent_sai.strip() if ent_sai else ""
            natop_by_grupo[g].append({"cod": n, "ent_sai": e})

        conn.close()
        return grupo_natureza_map, natop_by_grupo

    except Exception as e:
        print(f"  DB error: {e}")
        return None


def build_safx_field_layouts():
    """Document SAFX table field layouts (column positions for carga files)."""
    layouts = {
        "SAFX07": {
            "description": "Documento Fiscal (header)",
            "fields": {
                "0": "COD_EMPRESA",
                "1": "COD_ESTAB",
                "2": "MOVTO_E_S",
                "3": "NORM_DEV",
                "4": "COD_DOCTO",
                "5": "IDENT_FIS_JUR",
                "6": "COD_FIS_JUR",
                "7": "NUM_DOCFIS",
                "8": "SERIE_DOCFIS",
                "9": "SUB_SERIE_DOCFIS",
                "10": "DATA_EMISSAO",
                "11": "COD_CLASS_DOC_FIS",
                "12": "COD_MODELO",
                "13": "COD_CFO",
                "14": "COD_NATUREZA_OP",
            },
            "key_fields_for_reference": [0, 1, 4, 5, 6, 8, 11, 12, 14],
            "total_columns": 283,
        }
    }
    return layouts


def main():
    use_db = "--db" in sys.argv

    print("=== SuiteAutomation Reference Extractor ===")
    print(f"Suite XMLs: {SUITE_TESTE_DIR}")
    print(f"QA Cargas:  {CARGAS_DIR}")
    print(f"Output:     {OUTPUT_DIR}")
    print(f"DB mode:    {'ON' if use_db else 'OFF'}")
    print()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Parse XMLs
    print("[1/4] Parsing SuiteAutomation XMLs...")
    cargasafx_refs, det_job_inserts, delete_patterns, libparams = parse_xmls()
    print(f"  Found: {len(cargasafx_refs)} cargasafx refs, {len(det_job_inserts)} det_job inserts, "
          f"{len(delete_patterns)} tables with delete patterns")

    # Step 2: Parse cargas
    print("[2/4] Parsing SAFX carga files...")
    reference_data = parse_cargas()
    print(f"  Found: {len(reference_data)} empresa+estab combinations")

    # Step 3: Build maps
    print("[3/4] Building maps...")
    grupo_map = build_grupo_arquivo_map(det_job_inserts)
    cleanup = build_cleanup_patterns(delete_patterns)
    layouts = build_safx_field_layouts()

    # Optional: complement with DB
    grupo_natureza_map = None
    if use_db:
        print("  Querying database...")
        result = complement_with_db(grupo_map)
        if result:
            grupo_natureza_map, natop_by_grupo = result
            # Enrich reference data with grupo_natureza + valid COD_NATUREZA_OP
            for key, grupo_nat in grupo_natureza_map.items():
                if key in reference_data:
                    reference_data[key]["grupo_natureza_resolved"] = grupo_nat
                    # Add valid COD_NATUREZA_OP from X2006 for this grupo
                    if grupo_nat in natop_by_grupo:
                        natops = natop_by_grupo[grupo_nat]
                        # Filter: prefer ENT_SAI='A' (ambos), then '9' (entrada), then '1' (saida)
                        ambos = sorted(set(n["cod"] for n in natops if n["ent_sai"] == "A"))
                        reference_data[key]["valid_cod_natureza_op"] = ambos[:10] if ambos else sorted(set(n["cod"] for n in natops))[:10]
                        reference_data[key]["valid_cod_natureza_op_count"] = len(natops)
            print(f"  DB: {len(grupo_map)} grupo_arquivo entries, "
                  f"{len(grupo_natureza_map)} grupo_natureza entries, "
                  f"{sum(len(v) for v in natop_by_grupo.values())} natop entries")

    # Add cargasafx refs to reference data
    for empresa, safx_table, path, source in cargasafx_refs:
        # Match empresa to existing reference entries
        for key, ref in reference_data.items():
            if ref["empresa"] == empresa:
                if "carga_refs" not in ref:
                    ref["carga_refs"] = []
                ref["carga_refs"].append({"table": safx_table, "source_xml": source})

    # Step 4: Write outputs
    print("[4/4] Writing output files...")

    # 1. reference-data.json
    out_ref = OUTPUT_DIR / "reference-data.json"
    with open(out_ref, "w", encoding="utf-8") as f:
        json.dump(reference_data, f, indent=2, ensure_ascii=False)
    print(f"  {out_ref.name}: {len(reference_data)} entries")

    # 2. grupo-arquivo-map.json
    out_grupo = OUTPUT_DIR / "grupo-arquivo-map.json"
    with open(out_grupo, "w", encoding="utf-8") as f:
        json.dump(grupo_map, f, indent=2, ensure_ascii=False)
    print(f"  {out_grupo.name}: {len(grupo_map)} entries")

    # 3. cleanup-patterns.json
    out_cleanup = OUTPUT_DIR / "cleanup-patterns.json"
    with open(out_cleanup, "w", encoding="utf-8") as f:
        json.dump(cleanup, f, indent=2, ensure_ascii=False)
    print(f"  {out_cleanup.name}: {len(cleanup)} tables")

    # 4. safx-field-layouts.json
    out_layouts = OUTPUT_DIR / "safx-field-layouts.json"
    with open(out_layouts, "w", encoding="utf-8") as f:
        json.dump(layouts, f, indent=2, ensure_ascii=False)
    print(f"  {out_layouts.name}: {len(layouts)} layouts")

    # Summary
    print()
    print("=== Summary ===")
    # Top empresa+estab by number of valid tables
    top = sorted(reference_data.items(), key=lambda x: len(x[1].get("valid_safx_tables", [])), reverse=True)[:5]
    for key, ref in top:
        tables = ref.get("valid_safx_tables", [])
        natops = ref.get("cod_natureza_op", [])
        print(f"  {key}: {len(tables)} SAFX tables, natop={natops[:3]}")

    print(f"\nDone! Output in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
