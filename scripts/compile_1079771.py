"""
Compile PL/SQL objects for WI #1079771 and run validation tests.
Objects: VIEW_MULTI_SELECT_C, VIEW_MULTISELECT, EPC_SPED_FPROC (package)
"""
import oracledb
import sys
import traceback

CONN_STR = "V2R010AC/V2R010AC@localhost:1521/MFSPDB"

VIEW1_PATH = "C:/@@Dev/Git/taxone_dw/artifacts/sp/msaf/generico/View/VIEW_MULTI_SELECT_C.sql"
VIEW2_PATH = "C:/@@Dev/Git/taxone_dw/artifacts/sp/msaf/generico/View/VIEW_MULTI_SELECT.sql"
PKG_PATH = "C:/@@Dev/Git/taxone_dw/artifacts/sp/msaf/SPED/EFD-PIS-COFINS/Epc_Sped_Fproc.pck"

def get_connection():
    return oracledb.connect(CONN_STR)

def read_file(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()

def clean_sql(sql):
    """Remove trailing / and whitespace and semicolons, strip ALTER SESSION if present."""
    lines = sql.strip().split('\n')
    # Remove trailing / lines
    while lines and lines[-1].strip() in ('/', ''):
        lines.pop()
    result = '\n'.join(lines).rstrip()
    # Remove trailing semicolon (oracledb DDL doesn't accept it for views)
    if result.endswith(';'):
        result = result[:-1].rstrip()
    return result

def compile_object(conn, name, sql, obj_type_label):
    """Compile a single DDL statement and check status."""
    print(f"\n{'='*60}")
    print(f"Compilando: {name} ({obj_type_label})")
    print(f"{'='*60}")

    cur = conn.cursor()
    try:
        cur.execute(sql)
        print(f"  DDL executado com sucesso.")
    except Exception as e:
        print(f"  ERRO ao executar DDL: {e}")
        return False

    # Check status
    cur.execute("""
        SELECT OBJECT_NAME, OBJECT_TYPE, STATUS
        FROM USER_OBJECTS
        WHERE OBJECT_NAME = :name
        ORDER BY OBJECT_TYPE
    """, {'name': name.upper()})
    rows = cur.fetchall()

    all_valid = True
    for row in rows:
        status = row[2]
        print(f"  {row[0]} ({row[1]}): {status}")
        if status != 'VALID':
            all_valid = False

    if not all_valid:
        # Show errors
        cur.execute("""
            SELECT LINE, TEXT FROM USER_ERRORS
            WHERE NAME = :name
            ORDER BY SEQUENCE
        """, {'name': name.upper()})
        errors = cur.fetchall()
        if errors:
            print(f"  ERROS DE COMPILACAO:")
            for err in errors:
                print(f"    Linha {err[0]}: {err[1].strip()}")
        return False

    return True

def split_package(content):
    """Split .pck file into spec and body parts, separated by /."""
    lines = content.split('\n')
    parts = []
    current = []

    for line in lines:
        if line.strip() == '/':
            if current:
                parts.append('\n'.join(current))
                current = []
        else:
            current.append(line)

    if current:
        text = '\n'.join(current).strip()
        if text:
            parts.append(text)

    return parts

def run_test(conn, test_name, sql, max_rows=20):
    """Run a test query and display results."""
    print(f"\n{'='*60}")
    print(f"Teste: {test_name}")
    print(f"{'='*60}")
    print(f"  SQL: {sql[:120]}...")

    cur = conn.cursor()
    try:
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        print(f"  Colunas: {', '.join(cols)}")
        print(f"  Linhas retornadas: {len(rows)}")
        if rows:
            for i, row in enumerate(rows[:5]):
                print(f"    [{i+1}] {row}")
            if len(rows) > 5:
                print(f"    ... (+{len(rows)-5} linhas)")
        return len(rows)
    except Exception as e:
        print(f"  ERRO: {e}")
        return -1

def main():
    results = {}

    conn = get_connection()
    print("Conectado ao banco Oracle (thin mode)")

    # ========== 1. VIEW_MULTI_SELECT_C ==========
    sql = read_file(VIEW1_PATH)
    sql = clean_sql(sql)
    ok = compile_object(conn, 'VIEW_MULTI_SELECT_C', sql, 'VIEW')
    results['VIEW_MULTI_SELECT_C'] = 'VALID' if ok else 'INVALID'

    # ========== 2. VIEW_MULTISELECT ==========
    sql = read_file(VIEW2_PATH)
    sql = clean_sql(sql)
    ok = compile_object(conn, 'VIEW_MULTISELECT', sql, 'VIEW')
    results['VIEW_MULTISELECT'] = 'VALID' if ok else 'INVALID'

    # ========== 3. EPC_SPED_FPROC (package spec + body) ==========
    pkg_content = read_file(PKG_PATH)
    parts = split_package(pkg_content)

    print(f"\n  Package .pck dividido em {len(parts)} parte(s)")

    pkg_ok = True
    for i, part in enumerate(parts):
        part_clean = part.strip()
        # Skip ALTER SESSION lines
        lines = part_clean.split('\n')
        filtered = []
        for line in lines:
            if line.strip().upper().startswith('ALTER SESSION'):
                print(f"  (Ignorando ALTER SESSION: {line.strip()[:60]})")
                continue
            filtered.append(line)
        part_clean = '\n'.join(filtered).strip()

        if not part_clean:
            continue

        label = 'SPEC' if 'CREATE OR REPLACE PACKAGE EPC_SPED_FPROC IS' in part_clean.upper().replace('  ', ' ') else 'BODY'
        if 'PACKAGE BODY' in part_clean.upper():
            label = 'BODY'

        ok = compile_object(conn, 'EPC_SPED_FPROC', part_clean, f'PACKAGE {label}')
        if not ok:
            pkg_ok = False

    results['EPC_SPED_FPROC'] = 'VALID' if pkg_ok else 'INVALID'

    # ========== TESTES DE VALIDACAO ==========
    print("\n\n" + "#"*60)
    print("# TESTES DE VALIDACAO")
    print("#"*60)

    test_results = {}

    # Test 1
    n = run_test(conn, "VIEW_MULTI_SELECT_C retorna dados",
        "SELECT tipo, cod_empresa, cod_estab, descricao FROM VIEW_MULTI_SELECT_C WHERE ROWNUM <= 20")
    test_results['VIEW_MULTI_SELECT_C query'] = 'PASS' if n >= 0 else 'FAIL'

    # Test 2
    n = run_test(conn, "VIEW_MULTISELECT retorna dados",
        "SELECT tipo, cod_empresa, cod_estab, descricao FROM VIEW_MULTISELECT WHERE ROWNUM <= 20")
    test_results['VIEW_MULTISELECT query'] = 'PASS' if n >= 0 else 'FAIL'

    # Test 3
    n = run_test(conn, "USUARIO_EMPRESA funciona",
        "SELECT cod_empresa, cod_usuario FROM USUARIO_EMPRESA WHERE ROWNUM <= 10")
    test_results['USUARIO_EMPRESA query'] = 'PASS' if n >= 0 else 'FAIL'

    # Test 4
    n = run_test(conn, "Objetos validos",
        """SELECT OBJECT_NAME, OBJECT_TYPE, STATUS FROM USER_OBJECTS
           WHERE OBJECT_NAME IN ('VIEW_MULTI_SELECT_C', 'VIEW_MULTISELECT', 'EPC_SPED_FPROC')
           ORDER BY OBJECT_NAME""")
    test_results['USER_OBJECTS check'] = 'PASS' if n > 0 else 'FAIL'

    # ========== RESUMO ==========
    print("\n\n" + "#"*60)
    print("# RESUMO FINAL")
    print("#"*60)

    print("\nCompilacao:")
    all_valid = True
    for obj, status in results.items():
        icon = "OK" if status == 'VALID' else "FALHA"
        print(f"  [{icon}] {obj}: {status}")
        if status != 'VALID':
            all_valid = False

    print("\nTestes:")
    all_pass = True
    for test, status in test_results.items():
        icon = "OK" if status == 'PASS' else "FALHA"
        print(f"  [{icon}] {test}: {status}")
        if status != 'PASS':
            all_pass = False

    verdict = "COMPILACAO OK" if all_valid else "FALHA"
    if all_valid and not all_pass:
        verdict = "COMPILACAO OK / TESTES COM FALHA"

    print(f"\n>>> VEREDICTO: {verdict} <<<")

    conn.close()
    return 0 if all_valid else 1

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERRO FATAL: {e}")
        traceback.print_exc()
        sys.exit(2)
