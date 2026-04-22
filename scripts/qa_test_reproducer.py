#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
QA Test Reproducer - Reproduz testes de import SAFX em ambientes remotos (QA/AC/RC).

Pega artefatos de tests/{WI_ID}/ gerados localmente e os executa no ambiente alvo,
adaptando schema owner e conexao automaticamente.

Uso:
  python scripts/qa_test_reproducer.py --wi-id 1066543 --env QA
  python scripts/qa_test_reproducer.py --wi-id 1066543 --env QA --phase setup
  python scripts/qa_test_reproducer.py --wi-id 1066543 --env QA --phase all
  python scripts/qa_test_reproducer.py --wi-id 1066543 --env QA --phase cleanup
  python scripts/qa_test_reproducer.py --wi-id 1066543 --env LOCAL --no-cleanup
  python scripts/qa_test_reproducer.py --wi-id 1066543 --env LOCAL --no-suite

Fases:
  setup     - Testar conectividade e pre-condicoes
  data_load - Inserir dados de teste (SAFX INSERTs)
  import    - Executar SAF_IMPORTA_TAB
  validate  - Verificar dados importados
  suite     - Rodar SuiteAutomation via suite_detached.py
  cleanup   - Rollback dados de teste
  all       - Todas as fases em sequencia

Flags:
  --no-cleanup  Pula fase cleanup (dados permanecem na base, script de rollback gerado)
  --no-suite    Pula fase SuiteAutomation
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUITE_DIR = PROJECT_ROOT / "suite-automation"
CONFIG_DIR = SUITE_DIR / "config"
ENVIRONMENTS_FILE = CONFIG_DIR / "environments.json"
ENV_FILE = CONFIG_DIR / ".env"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


def get_qa_repo_path():
    """Retorna path do repo taxone_automacao_qa (para --from-qa-repo)."""
    from env_loader import get_repo_path
    return Path(get_repo_path("QA_REPO"))


def load_env_config(env_name):
    """Carrega config do ambiente de environments.json + .env."""
    with open(ENVIRONMENTS_FILE, encoding="utf-8") as f:
        envs = json.load(f)

    if env_name not in envs:
        print(f"ERRO: Ambiente '{env_name}' nao encontrado. Disponiveis: {list(envs.keys())}")
        sys.exit(1)

    params = dict(envs[env_name])

    # Carregar .env
    env_values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                env_values[key.strip()] = val.strip()

    # Para QA, usar credenciais _QA
    if env_name == "QA":
        params["BD_USER"] = env_values.get("BD_USER_QA", "")
        params["BD_PASSWD"] = env_values.get("BD_PASSWD_QA", "")
    else:
        params["BD_USER"] = env_values.get("BD_USER", params.get("BD_USER", ""))
        params["BD_PASSWD"] = env_values.get("BD_PASSWD", params.get("BD_PASSWD", ""))

    return params


class SqlPlusConnection:
    """Wrapper que executa SQL via sqlplus (fallback para quando oracledb thick mode falha)."""

    def __init__(self, config):
        self.config = config
        self.connect_string = (
            f"{config['BD_USER']}/{config['BD_PASSWD']}"
            f"@{config['BD_SERVER']}:{config['BD_PORT']}/{config['BD_DATABASE']}"
        )

    def execute(self, sql, params=None):
        """Executa SQL via sqlplus e retorna resultado."""
        import subprocess

        # Substituir bind vars :nome por valores
        resolved = sql
        if params:
            for key, val in params.items():
                resolved = resolved.replace(f":{key}", f"'{val}'" if isinstance(val, str) else str(val))

        input_sql = f"SET PAGESIZE 0\nSET FEEDBACK OFF\nSET HEADING ON\nSET LINESIZE 1000\n{resolved};\nEXIT;\n"

        result = subprocess.run(
            ["sqlplus", "-S", self.connect_string],
            input=input_sql,
            capture_output=True,
            text=True,
            timeout=120,
            encoding="latin-1",
            errors="replace",
        )
        return result.stdout, result.stderr, result.returncode

    def execute_script(self, sql_text):
        """Executa script SQL completo via sqlplus."""
        import subprocess

        # Garantir EXIT no final
        if "EXIT" not in sql_text.upper():
            sql_text += "\nEXIT;\n"

        result = subprocess.run(
            ["sqlplus", "-S", self.connect_string],
            input=sql_text,
            capture_output=True,
            text=True,
            timeout=300,
            encoding="latin-1",
            errors="replace",
        )
        return result.stdout, result.stderr, result.returncode

    def close(self):
        pass  # sqlplus nao mantém conexao

    def commit(self):
        pass  # COMMIT no proprio SQL

    def rollback(self):
        pass


def get_connection(config):
    """Cria conexao Oracle — tenta oracledb thin, thick, fallback sqlplus."""
    dsn = f"{config['BD_SERVER']}:{config['BD_PORT']}/{config['BD_DATABASE']}"

    try:
        import oracledb
        # Tentar thin mode
        try:
            conn = oracledb.connect(
                user=config["BD_USER"],
                password=config["BD_PASSWD"],
                dsn=dsn
            )
            return conn
        except Exception as e:
            if "DPY-3001" not in str(e):
                return None, str(e)
            # NNE requer thick mode — tentar
            try:
                oracledb.init_oracle_client()
                conn = oracledb.connect(
                    user=config["BD_USER"],
                    password=config["BD_PASSWD"],
                    dsn=dsn
                )
                return conn
            except Exception:
                pass  # fallback para sqlplus
    except ImportError:
        pass  # oracledb nao instalado, usar sqlplus

    # Fallback: sqlplus
    import subprocess
    try:
        connect_str = f"{config['BD_USER']}/{config['BD_PASSWD']}@{dsn}"
        result = subprocess.run(
            ["sqlplus", "-S", connect_str],
            input="SELECT 1 FROM DUAL;\nEXIT;\n",
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and "1" in result.stdout:
            return SqlPlusConnection(config)
        return None, f"sqlplus falhou: {result.stdout} {result.stderr}"
    except Exception as e:
        return None, f"Sem oracledb thick mode e sqlplus indisponivel: {e}"


def adapt_sql(sql_text, local_owner, target_owner):
    """Adapta schema owner nos scripts SQL."""
    if local_owner == target_owner:
        return sql_text
    # Substituir schema prefix (ex: V2R010AC. -> USER_TAXONE_OCLOUD.)
    adapted = sql_text.replace(f"{local_owner}.", f"{target_owner}.")
    return adapted


def parse_inserts(sql_text):
    """Extrai statements INSERT do SQL (ignora comentarios e SELECTs)."""
    statements = []
    current = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        if stripped.upper().startswith("SELECT"):
            continue
        if stripped.upper() == "COMMIT;":
            continue
        current.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(current).strip().rstrip(";")
            if stmt.upper().startswith("INSERT"):
                statements.append(stmt)
            current = []
    return statements


def parse_deletes(sql_text):
    """Extrai statements DELETE do SQL."""
    statements = []
    current = []
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        if stripped.upper().startswith("SELECT"):
            continue
        if stripped.upper() == "COMMIT;":
            continue
        current.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(current).strip().rstrip(";")
            if stmt.upper().startswith("DELETE"):
                statements.append(stmt)
            current = []
    return statements


def parse_selects(sql_text):
    """Extrai statements SELECT do SQL (para validacao)."""
    statements = []
    current = []
    in_select = False
    for line in sql_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            if in_select and current:
                # Pode ser fim do SELECT (comentario apos query)
                pass
            continue
        if stripped.upper().startswith("SELECT"):
            in_select = True
            current = [line]
        elif in_select:
            current.append(line)
        if in_select and stripped.endswith(";"):
            stmt = "\n".join(current).strip().rstrip(";")
            statements.append(stmt)
            current = []
            in_select = False
    # Se ultimo SELECT nao terminou com ;
    if in_select and current:
        stmt = "\n".join(current).strip().rstrip(";")
        if stmt:
            statements.append(stmt)
    return statements


class PhaseResult:
    """Resultado de uma fase de teste."""
    def __init__(self, phase_name, phase_desc):
        self.name = phase_name
        self.desc = phase_desc
        self.status = "PENDING"
        self.details = []
        self.start_time = None
        self.end_time = None
        self.error = None

    def start(self):
        self.status = "RUNNING"
        self.start_time = time.time()

    def pass_(self, detail=None):
        self.status = "PASS"
        self.end_time = time.time()
        if detail:
            self.details.append(detail)

    def fail(self, detail=None, error=None):
        self.status = "FAIL"
        self.end_time = time.time()
        self.error = error
        if detail:
            self.details.append(detail)

    def skip(self, reason):
        self.status = "SKIP"
        self.end_time = time.time()
        self.details.append(f"SKIP: {reason}")

    @property
    def duration_s(self):
        if self.start_time and self.end_time:
            return round(self.end_time - self.start_time, 2)
        return 0

    def to_dict(self):
        return {
            "phase": self.name,
            "description": self.desc,
            "status": self.status,
            "duration_s": self.duration_s,
            "details": self.details,
            "error": self.error,
        }


def is_sqlplus_conn(conn):
    """Verifica se a conexao e SqlPlusConnection."""
    return isinstance(conn, SqlPlusConnection)


def sqlplus_query(conn, sql, params=None):
    """Executa SELECT via SqlPlusConnection e retorna rows parsed."""
    stdout, stderr, rc = conn.execute(sql, params)
    lines = [l.strip() for l in stdout.strip().splitlines() if l.strip()]
    # Remover separadores (------)
    lines = [l for l in lines if not l.startswith("---")]
    return lines, rc


def phase_setup(config, test_dir, manifest):
    """Fase 1: Testar conectividade e pre-condicoes."""
    result = PhaseResult("SETUP", "Conectividade + pre-condicoes")
    result.start()

    dsn = f"{config['BD_SERVER']}:{config['BD_PORT']}/{config['BD_DATABASE']}"
    result.details.append(f"Conectando a {config['BD_USER']}@{dsn}")

    conn_result = get_connection(config)
    if isinstance(conn_result, tuple):
        result.fail(f"Falha de conexao: {conn_result[1]}", error=conn_result[1])
        return result, None

    conn = conn_result
    mode = "sqlplus" if is_sqlplus_conn(conn) else "oracledb"
    result.details.append(f"Conexao OK (modo: {mode})")

    # Verificar se tabelas SAFX existem
    tables = manifest.get("tables", [])
    wi_id = manifest.get("wi_id")
    lote = f"LOTE_TESTE_{wi_id}"

    if is_sqlplus_conn(conn):
        for table in tables:
            lines, rc = sqlplus_query(conn, f"SELECT COUNT(*) AS CNT FROM {table} WHERE ROWNUM = 1")
            if rc != 0 or "ORA-" in " ".join(lines):
                result.fail(f"Tabela {table} inacessivel")
                return result, conn
            result.details.append(f"Tabela {table}: acessivel")

        for table in tables:
            lines, rc = sqlplus_query(conn, f"SELECT COUNT(*) AS CNT FROM {table} WHERE NUM_LOTE = '{lote}'")
            if rc == 0 and lines:
                count_str = lines[-1].strip() if lines else "0"
                try:
                    count = int(count_str)
                except ValueError:
                    count = 0
                if count > 0:
                    result.details.append(f"AVISO: {table} ja tem {count} rows com NUM_LOTE={lote}")
                else:
                    result.details.append(f"{table}: limpo (0 rows de teste)")
    else:
        cur = conn.cursor()
        for table in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table} WHERE ROWNUM = 1")
                result.details.append(f"Tabela {table}: acessivel")
            except Exception as e:
                result.fail(f"Tabela {table} inacessivel: {e}")
                return result, conn

        for table in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table} WHERE NUM_LOTE = :lote", {"lote": lote})
                count = cur.fetchone()[0]
                if count > 0:
                    result.details.append(f"AVISO: {table} ja tem {count} rows com NUM_LOTE={lote}")
                else:
                    result.details.append(f"{table}: limpo (0 rows de teste)")
            except Exception:
                pass
        cur.close()

    result.pass_(f"Schema {config['BD_OWNER']} acessivel, {len(tables)} tabela(s) verificada(s)")
    return result, conn


def phase_data_load(conn, config, test_dir, manifest):
    """Fase 2: Inserir dados de teste."""
    result = PhaseResult("DATA_LOAD", "Inserir dados de teste (SAFX INSERTs)")
    result.start()

    import_file = test_dir / "01_safx_import.sql"
    if not import_file.exists():
        result.fail("Arquivo 01_safx_import.sql nao encontrado")
        return result

    sql_text = import_file.read_text(encoding="utf-8")

    # Adaptar schema
    local_owner = "V2R010AC"
    target_owner = config["BD_OWNER"]
    sql_text = adapt_sql(sql_text, local_owner, target_owner)

    # Extrair INSERTs
    inserts = parse_inserts(sql_text)
    if not inserts:
        result.fail("Nenhum INSERT encontrado em 01_safx_import.sql")
        return result

    result.details.append(f"INSERTs encontrados: {len(inserts)}")

    if is_sqlplus_conn(conn):
        # Executar via sqlplus: montar script com todos os INSERTs + COMMIT
        script = "\n".join(stmt + ";" for stmt in inserts) + "\nCOMMIT;\n"
        stdout, stderr, rc = conn.execute_script(script + "\nEXIT;\n")
        if rc != 0 or "ORA-" in stdout:
            result.fail(f"INSERTs falharam: {stdout.strip()}", error=stdout[:300])
            return result
        result.details.append(f"Executado via sqlplus: {len(inserts)} INSERT(s)")
        result.pass_(f"Total: {len(inserts)} INSERT(s), COMMIT OK")
    else:
        cur = conn.cursor()
        rows_inserted = 0
        for i, stmt in enumerate(inserts, 1):
            try:
                cur.execute(stmt)
                rows_inserted += cur.rowcount
                result.details.append(f"INSERT #{i}: {cur.rowcount} row(s)")
            except Exception as e:
                result.fail(f"INSERT #{i} falhou: {e}", error=str(e))
                try:
                    conn.rollback()
                except Exception:
                    pass
                cur.close()
                return result
        conn.commit()
        cur.close()
        result.pass_(f"Total: {rows_inserted} rows inseridas, COMMIT OK")

    return result


def phase_import(conn, config, test_dir, manifest):
    """Fase 3: Executar SAF_IMPORTA_TAB."""
    result = PhaseResult("IMPORT", "Executar SAF_IMPORTA_TAB")
    result.start()

    wi_id = manifest.get("wi_id")
    tables = manifest.get("tables", [])
    empresa = manifest.get("cod_empresa", "076")
    estab = manifest.get("cod_estab", "01")

    # Grupo+numero para SAFX tables do manifest
    grupo_map_file = PROJECT_ROOT / "knowledge" / "suite-automation" / "grupo-arquivo-map.json"
    grupo_map = {}
    if grupo_map_file.exists():
        with open(grupo_map_file, encoding="utf-8") as f:
            grupo_map = json.load(f)

    # Resolver grupo+numero para cada tabela
    table_grupos = []
    for table in tables:
        found = False
        for key, val in grupo_map.items():
            safx_name = val.get("safx_table", val.get("safx", "")).upper()
            if safx_name == table.upper():
                parts = key.split("|")
                table_grupos.append((table, int(parts[0]), int(parts[1])))
                found = True
                break
        if not found:
            # Fallback: consultar CAT_PRIOR_IMP diretamente
            try:
                if is_sqlplus_conn(conn):
                    lines, _ = sqlplus_query(conn,
                        f"SELECT GRUPO_ARQUIVO, NUMERO_ARQUIVO FROM CAT_PRIOR_IMP WHERE UPPER(NOM_TAB_WORK) = '{table.upper()}'")
                    for l in lines:
                        parts = l.split()
                        if len(parts) >= 2:
                            table_grupos.append((table, int(parts[0]), int(parts[1])))
                            break
                else:
                    cur2 = conn.cursor()
                    cur2.execute("SELECT GRUPO_ARQUIVO, NUMERO_ARQUIVO FROM CAT_PRIOR_IMP WHERE UPPER(NOM_TAB_WORK) = :tab",
                                {"tab": table.upper()})
                    row = cur2.fetchone()
                    if row:
                        table_grupos.append((table, row[0], row[1]))
                    cur2.close()
            except Exception:
                pass

    if not table_grupos:
        result.fail(f"Nenhum mapeamento grupo/numero encontrado para tabelas: {tables}")
        return result

    result.details.append(f"Mapeamento: {[(t, g, n) for t, g, n in table_grupos]}")

    if is_sqlplus_conn(conn):
        # Montar PL/SQL block via sqlplus
        plsql_lines = ["SET SERVEROUTPUT ON SIZE 1000000", "DECLARE"]
        plsql_lines.append("  v_num_job NUMBER;")
        plsql_lines.append("  v_proc_ini NUMBER;")
        plsql_lines.append("  v_proc_fim NUMBER;")
        plsql_lines.append("  v_mens_err NUMBER;")
        plsql_lines.append("BEGIN")
        plsql_lines.append("  SAF_PEGA_IDENT('JOB_IMPORTACAO','NUM_JOB',v_num_job);")
        plsql_lines.append(f"  INSERT INTO JOB_IMPORTACAO (NUM_JOB, TIPO_JOB, STATUS_JOB, DATA_ABERTURA, DATA_ENCERRAMENTO, IND_ATO_COTEPE)")
        plsql_lines.append(f"  VALUES (v_num_job, 'C', 'P', SYSDATE, SYSDATE, 'N');")

        for table, grupo, numero in table_grupos:
            plsql_lines.append(f"  INSERT INTO DET_JOB_IMPORT (NUM_JOB,GRUPO_ARQUIVO,NUMERO_ARQUIVO,COD_EMPRESA,COD_ESTAB,DATA_INI,DATA_FIM,PERC_ERRO,IND_ABORTA_JOB,STATUS,IND_DROP_TAB,DAT_INI_EXEC,DAT_FIM_EXEC,IND_PERIODO,IND_SOBREPOR_REG,IND_LOG_X2013,IND_VALID_X2013)")
            plsql_lines.append(f"  VALUES (v_num_job,{grupo},{numero},'{empresa}','{estab}',TO_DATE('01032026','DDMMYYYY'),TO_DATE('31032026','DDMMYYYY'),100,'N','P','N',SYSDATE,SYSDATE,'N','S','N','N');")

        plsql_lines.append("  COMMIT;")
        plsql_lines.append(f"  SAF_IMPORTA_TAB(v_num_job, 'ut_{wi_id}', v_proc_ini, v_proc_fim, v_mens_err);")
        plsql_lines.append("  COMMIT;")
        plsql_lines.append("  DBMS_OUTPUT.PUT_LINE('JOB=' || v_num_job);")
        plsql_lines.append("  DBMS_OUTPUT.PUT_LINE('PROC_INI=' || v_proc_ini);")
        plsql_lines.append("  DBMS_OUTPUT.PUT_LINE('PROC_FIM=' || v_proc_fim);")
        plsql_lines.append("  DBMS_OUTPUT.PUT_LINE('MENS_ERR=' || v_mens_err);")
        plsql_lines.append("END;")
        plsql_lines.append("/")

        script = "\n".join(plsql_lines)
        stdout, stderr, rc = conn.execute_script(script + "\nEXIT;\n")

        result.details.append(f"PL/SQL executado (rc={rc})")

        # Parsear output
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("JOB=") or line.startswith("PROC_") or line.startswith("MENS_"):
                result.details.append(line)

        if "ORA-" in stdout or "PLS-" in stdout:
            # Extrair erro
            err_lines = [l for l in stdout.splitlines() if "ORA-" in l or "PLS-" in l]
            result.fail(f"Import falhou: {'; '.join(err_lines[:3])}", error=stdout[:500])
        elif "MENS_ERR=0" in stdout or "MENS_ERR=" not in stdout:
            # Parse JOB and PROC_INI from output
            parsed_job = None
            proc_ini = None
            for line in stdout.splitlines():
                ls = line.strip()
                if ls.startswith("JOB="):
                    parsed_job = ls.split("=")[1]
                if ls.startswith("PROC_INI="):
                    proc_ini = ls.split("=")[1]
            if parsed_job:
                det_lines, _ = sqlplus_query(conn,
                    f"SELECT GRUPO_ARQUIVO, NUMERO_ARQUIVO, QTDE_REGS_SAFX, QTDE_REGS_DUPL, STATUS FROM DET_JOB_IMPORT WHERE NUM_JOB = {parsed_job} ORDER BY GRUPO_ARQUIVO, NUMERO_ARQUIVO")
                for ll in det_lines[1:]:
                    result.details.append(f"DET_JOB: {ll}")
            if proc_ini:
                log_lines, _ = sqlplus_query(conn,
                    f"SELECT NUM_PROCESSO, STATUS, DESCRICAO FROM LOG_PROCESSO WHERE NUM_PROCESSO >= {proc_ini} ORDER BY NUM_PROCESSO")
                for ll in log_lines[1:]:
                    result.details.append(f"LOG: {ll}")
            result.pass_("Import executado com sucesso")
        else:
            result.fail("Import com erros (ver MENS_ERR)")
    else:
        # oracledb mode
        cur = conn.cursor()
        try:
            v_num_job = cur.var(int)
            cur.callproc("SAF_PEGA_IDENT", ["JOB_IMPORTACAO", "NUM_JOB", v_num_job])
            num_job = v_num_job.getvalue()

            cur.execute("""
                INSERT INTO JOB_IMPORTACAO (NUM_JOB, TIPO_JOB, STATUS_JOB, DATA_ABERTURA, DATA_ENCERRAMENTO, IND_ATO_COTEPE)
                VALUES (:num_job, 'C', 'P', SYSDATE, SYSDATE, 'N')
            """, {"num_job": num_job})

            for table, grupo, numero in table_grupos:
                cur.execute("""
                    INSERT INTO DET_JOB_IMPORT (NUM_JOB,GRUPO_ARQUIVO,NUMERO_ARQUIVO,COD_EMPRESA,COD_ESTAB,DATA_INI,DATA_FIM,PERC_ERRO,IND_ABORTA_JOB,STATUS,IND_DROP_TAB,DAT_INI_EXEC,DAT_FIM_EXEC,IND_PERIODO,IND_SOBREPOR_REG,IND_LOG_X2013,IND_VALID_X2013)
                    VALUES (:num_job,:grupo,:numero,:empresa,:estab,TO_DATE('01032026','DDMMYYYY'),TO_DATE('31032026','DDMMYYYY'),100,'N','P','N',SYSDATE,SYSDATE,'N','S','N','N')
                """, {"num_job": num_job, "grupo": grupo, "numero": numero, "empresa": empresa, "estab": estab})

            conn.commit()

            v_proc_ini = cur.var(int)
            v_proc_fim = cur.var(int)
            v_mens_err = cur.var(int)

            cur.callproc("SAF_IMPORTA_TAB", [num_job, f"ut_{wi_id}", v_proc_ini, v_proc_fim, v_mens_err])
            conn.commit()

            proc_ini = v_proc_ini.getvalue()
            proc_fim = v_proc_fim.getvalue()
            mens_err = v_mens_err.getvalue()

            result.details.append(f"JOB={num_job}, PROC_INI={proc_ini}, PROC_FIM={proc_fim}, MENS_ERR={mens_err}")

            if proc_ini:
                # Query DET_JOB_IMPORT for QTDE_REGS_SAFX/STATUS
                cur.execute("""
                    SELECT GRUPO_ARQUIVO, NUMERO_ARQUIVO, QTDE_REGS_SAFX, QTDE_REGS_DUPL, STATUS
                      FROM DET_JOB_IMPORT WHERE NUM_JOB = :num_job ORDER BY GRUPO_ARQUIVO, NUMERO_ARQUIVO
                """, {"num_job": num_job})
                for row in cur.fetchall():
                    grupo_a, numero_a, regs_safx, regs_dupl, det_status = row
                    result.details.append(f"DET_JOB: grupo={grupo_a}, numero={numero_a}, regs_safx={regs_safx}, dupl={regs_dupl}, status={det_status}")
                # Query LOG_PROCESSO for STATUS/DESCRICAO
                cur.execute("""
                    SELECT NUM_PROCESSO, STATUS, DESCRICAO
                      FROM LOG_PROCESSO WHERE NUM_PROCESSO BETWEEN :ini AND :fim ORDER BY NUM_PROCESSO
                """, {"ini": proc_ini, "fim": proc_fim or proc_ini})
                for row in cur.fetchall():
                    proc_id, log_status, descricao = row
                    result.details.append(f"LOG_PROCESSO #{proc_id}: status={log_status}, descricao={descricao}")

            if mens_err and mens_err > 0:
                result.fail(f"Import com erros: MENS_ERR={mens_err}")
            else:
                result.pass_(f"Import OK: JOB={num_job}, processos {proc_ini}-{proc_fim}")
        except Exception as e:
            result.fail(f"Erro durante import: {e}", error=str(e))
            try:
                conn.rollback()
            except Exception:
                pass
        cur.close()

    return result


def phase_validate(conn, config, test_dir, manifest):
    """Fase 4: Verificar dados importados."""
    result = PhaseResult("VALIDATE", "Verificar dados importados")
    result.start()

    val_file = test_dir / "02_import_validation.sql"
    if not val_file.exists():
        result.skip("Arquivo 02_import_validation.sql nao encontrado")
        return result

    sql_text = val_file.read_text(encoding="utf-8")
    sql_text = adapt_sql(sql_text, "V2R010AC", config["BD_OWNER"])

    selects = parse_selects(sql_text)
    if not selects:
        result.skip("Nenhum SELECT encontrado em 02_import_validation.sql")
        return result

    all_pass = True

    if is_sqlplus_conn(conn):
        for i, stmt in enumerate(selects, 1):
            # Executar via sqlplus com header
            script = f"SET LINESIZE 1000\nSET PAGESIZE 100\n{stmt};\n"
            stdout, stderr, rc = conn.execute_script(script + "\nEXIT;\n")
            lines = [l for l in stdout.splitlines() if l.strip()]
            result.details.append(f"Query #{i}: {len(lines)} linhas de output")
            for line in lines:
                line = line.strip()
                if "FAIL" in line.upper():
                    all_pass = False
                    result.details.append(f"  FAIL: {line}")
                elif "PASS" in line.upper():
                    result.details.append(f"  PASS: {line}")
                elif line and not line.startswith("-"):
                    result.details.append(f"  {line}")
    else:
        cur = conn.cursor()
        for i, stmt in enumerate(selects, 1):
            try:
                cur.execute(stmt)
                columns = [d[0] for d in cur.description]
                rows = cur.fetchall()
                result.details.append(f"Query #{i}: {len(rows)} row(s), colunas: {', '.join(columns)}")
                for row in rows:
                    row_dict = dict(zip(columns, row))
                    for col, val in row_dict.items():
                        if "CHECK" in col.upper():
                            val_str = str(val) if val else "NULL"
                            if "FAIL" in val_str.upper():
                                all_pass = False
                                result.details.append(f"  FAIL: {col}={val_str} (row {row_dict.get('NUM_DOCFIS', '?')})")
                            else:
                                result.details.append(f"  PASS: {col}={val_str} (row {row_dict.get('NUM_DOCFIS', '?')})")
            except Exception as e:
                result.details.append(f"Query #{i} erro: {e}")
                all_pass = False
        cur.close()

    if all_pass:
        result.pass_("Todas as validacoes passaram")
    else:
        result.fail("Falhas encontradas na validacao")
    return result


def phase_suite(config, test_dir, manifest, env_name, suite_timeout=900):
    """Fase 5: Rodar SuiteAutomation via suite_detached.py."""
    result = PhaseResult("SUITE", "SuiteAutomation regressao")
    result.start()

    import subprocess as sp

    # Determinar XML da suite a partir do component-test-map
    comp_map_file = PROJECT_ROOT / "knowledge" / "suite-automation" / "component-test-map.json"
    if not comp_map_file.exists():
        result.skip("component-test-map.json nao encontrado")
        return result

    with open(comp_map_file, encoding="utf-8") as f:
        comp_map = json.load(f)

    # Detectar suite relevante para a WI
    wi_id = manifest.get("wi_id", "")
    xml_file = None

    # Estrategia 1: manifest pode indicar a suite
    if manifest.get("suite_xml"):
        xml_file = manifest["suite_xml"]

    # Estrategia 2: buscar no component-test-map por packages/keywords
    if not xml_file:
        mappings = comp_map.get("mappings", [])
        # Procurar por keywords no manifest ou tabelas
        tables = [t.upper() for t in manifest.get("tables", [])]
        for m in mappings:
            pkgs = [p.upper() for p in m.get("packages", [])]
            kws = [k.upper() for k in m.get("keywords", [])]
            fps = [fp.lower() for fp in m.get("file_paths", [])]
            # Match por tabela (ex: SAFX3007 -> SAF_IB_X3007 -> basico/il/ibatch -> JOB_SERVIDOR_BAT)
            for t in tables:
                base_name = t.replace("SAFX", "")  # "3007"
                for kw in kws:
                    if base_name in kw:
                        xml_file = m.get("xml_file")
                        break
                if xml_file:
                    break
                for fp in fps:
                    if "batch" in fp or "ibatch" in fp or "il" in fp:
                        xml_file = m.get("xml_file")
                        break
                if xml_file:
                    break
            if xml_file:
                break

    # Estrategia 3: default JOB_SERVIDOR_BAT para importacao batch
    if not xml_file:
        for m in comp_map.get("mappings", []):
            if m.get("id") == "JOB_SERVIDOR_BAT":
                xml_file = m.get("xml_file")
                break

    if not xml_file:
        result.skip("Nenhuma suite mapeada para as tabelas do manifest")
        return result

    result.details.append(f"Suite XML: {xml_file}")

    # SuiteAutomation requer setup do ambiente
    props_file = CONFIG_DIR / f"suiteteste_{env_name}.properties"
    if not props_file.exists():
        setup_result = sp.run(
            [sys.executable, str(SUITE_DIR / "setup.py"), "--env", env_name],
            capture_output=True, text=True, timeout=30
        )
        if not props_file.exists():
            result.skip(f"Properties {env_name} nao gerado: {setup_result.stderr}")
            return result

    # Executar via suite_detached.py --wait
    detached_script = PROJECT_ROOT / "scripts" / "suite_detached.py"
    wi_title = manifest.get("wi_title", f"WI {wi_id}")

    cmd = [
        sys.executable, str(detached_script),
        "--wi-id", str(wi_id),
        "--title", str(wi_title),
        "--xml", xml_file,
        "--env", env_name,
        "--wait",
        "--timeout", str(suite_timeout),
    ]
    result.details.append(f"Comando: suite_detached.py --xml {xml_file} --env {env_name} --wait")

    try:
        proc = sp.run(cmd, capture_output=True, text=True, timeout=suite_timeout + 60)
        output = proc.stdout + proc.stderr

        # Parsear resultado
        if proc.returncode == 0:
            # Contar PASS/FAIL no output
            pass_count = output.lower().count("pass")
            fail_count = output.lower().count("fail")
            result.details.append(f"SuiteAutomation concluido (exit code 0)")
            if fail_count > 0:
                result.fail(f"Suite concluida com falhas: {fail_count} FAIL(s)")
            else:
                result.pass_(f"Suite concluida com sucesso")
        else:
            result.details.append(f"Exit code: {proc.returncode}")
            # Capturar ultimas linhas do output
            last_lines = output.strip().splitlines()[-10:]
            for line in last_lines:
                result.details.append(f"  {line}")
            result.fail(f"Suite falhou (exit code {proc.returncode})")

        # Salvar output completo
        suite_output_file = test_dir / f"suite_output_{wi_id}.txt"
        if suite_output_file.exists():
            suite_text = suite_output_file.read_text(encoding="latin-1", errors="replace")
            result.details.append(f"Output completo: {suite_output_file.name} ({len(suite_text)} bytes)")

    except sp.TimeoutExpired:
        result.fail(f"Suite timeout apos {suite_timeout}s")
    except Exception as e:
        result.fail(f"Erro ao executar suite: {e}")

    return result


def phase_cleanup(conn, config, test_dir, manifest):
    """Fase 6: Rollback dados de teste."""
    result = PhaseResult("CLEANUP", "Rollback dados de teste")
    result.start()

    rollback_file = test_dir / "05_rollback_test_data.sql"
    if not rollback_file.exists():
        result.skip("Arquivo 05_rollback_test_data.sql nao encontrado")
        return result

    sql_text = rollback_file.read_text(encoding="utf-8")
    sql_text = adapt_sql(sql_text, "V2R010AC", config["BD_OWNER"])

    deletes = parse_deletes(sql_text)
    if not deletes:
        result.skip("Nenhum DELETE encontrado em 05_rollback_test_data.sql")
        return result

    if is_sqlplus_conn(conn):
        script = "\n".join(stmt + ";" for stmt in deletes) + "\nCOMMIT;\n"
        stdout, stderr, rc = conn.execute_script(script + "\nEXIT;\n")
        if "ORA-" in stdout:
            result.fail(f"Cleanup falhou: {stdout[:300]}", error=stdout[:500])
        else:
            result.details.append(f"Executado via sqlplus: {len(deletes)} DELETE(s)")
            result.pass_(f"Cleanup OK: {len(deletes)} DELETE(s), COMMIT")
    else:
        cur = conn.cursor()
        total_deleted = 0
        for i, stmt in enumerate(deletes, 1):
            try:
                cur.execute(stmt)
                total_deleted += cur.rowcount
                result.details.append(f"DELETE #{i}: {cur.rowcount} row(s) removidas")
            except Exception as e:
                result.fail(f"DELETE #{i} falhou: {e}", error=str(e))
                try:
                    conn.rollback()
                except Exception:
                    pass
                cur.close()
                return result
        conn.commit()
        cur.close()
        result.pass_(f"Total: {total_deleted} rows removidas, COMMIT OK")

    return result


def print_results(results, config, wi_id, env_name):
    """Imprime tabela de resultados por fase."""
    print()
    print("=" * 70)
    print(f"  QA TEST REPRODUCER - WI #{wi_id}")
    print(f"  Ambiente: {env_name} ({config['BD_SERVER']}:{config['BD_PORT']}/{config['BD_DATABASE']})")
    print(f"  Schema: {config['BD_OWNER']}")
    print(f"  Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print()

    for r in results:
        icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]", "PENDING": "[----]"}.get(r.status, "[????]")
        print(f"  {icon} Fase {r.name}: {r.desc} ({r.duration_s}s)")
        for detail in r.details:
            print(f"         {detail}")
        if r.error:
            print(f"         ERRO: {r.error}")
        print()

    # Resumo
    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    skipped = sum(1 for r in results if r.status == "SKIP")

    print("-" * 70)
    print(f"  RESUMO: {passed} PASS | {failed} FAIL | {skipped} SKIP (total: {total})")
    if failed == 0:
        print("  >>> TODOS OS TESTES PASSARAM <<<")
    else:
        print("  >>> FALHAS ENCONTRADAS <<<")
    print("=" * 70)


def save_report(results, config, wi_id, env_name, test_dir):
    """Salva relatorio em Markdown."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = test_dir / f"qa_test_report_{env_name}_{timestamp}.md"

    lines = []
    lines.append(f"# QA Test Report - WI #{wi_id}")
    lines.append(f"")
    lines.append(f"- **Ambiente**: {env_name} ({config['BD_SERVER']}:{config['BD_PORT']}/{config['BD_DATABASE']})")
    lines.append(f"- **Schema**: {config['BD_OWNER']}")
    lines.append(f"- **Data**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"")
    lines.append(f"## Resultados por Fase")
    lines.append(f"")
    lines.append(f"| Fase | Descricao | Status | Duracao |")
    lines.append(f"|------|-----------|--------|---------|")

    for r in results:
        lines.append(f"| {r.name} | {r.desc} | **{r.status}** | {r.duration_s}s |")

    lines.append(f"")
    lines.append(f"## Detalhes")
    lines.append(f"")

    for r in results:
        lines.append(f"### {r.name}: {r.desc} ({r.status})")
        lines.append(f"")
        for detail in r.details:
            lines.append(f"- {detail}")
        if r.error:
            lines.append(f"- **ERRO**: {r.error}")
        lines.append(f"")

    report_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nRelatorio salvo em: {report_file}")
    return report_file


def save_html_report(results, config, wi_id, env_name, test_dir, label):
    """Salva relatorio em HTML standalone com tabelas coloridas."""
    html_file = test_dir / f"evidencia_{label}_{wi_id}.html"

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    skipped = sum(1 for r in results if r.status == "SKIP")

    label_class = "antes" if label.upper() == "ANTES" else "depois"
    summary_class = "pass" if failed == 0 else "fail"
    verdict = "TODOS OS TESTES PASSARAM" if failed == 0 else "FALHAS ENCONTRADAS"

    # Build phase rows
    phase_rows = []
    for r in results:
        status_class = r.status.lower()
        phase_rows.append(
            f'    <tr class="{status_class}">'
            f"<td>{r.name}</td><td>{r.desc}</td>"
            f"<td><b>{r.status}</b></td><td>{r.duration_s}s</td></tr>"
        )

    # Build detail sections
    detail_sections = []
    for r in results:
        status_class = r.status.lower()
        detail_lines = []
        detail_lines.append(f'  <div class="phase-detail">')
        detail_lines.append(f'    <h3 class="{status_class}-text">{r.name}: {r.desc} ({r.status})</h3>')
        if r.details:
            detail_lines.append("    <ul class='details'>")
            for d in r.details:
                detail_lines.append(f"      <li>{_html_escape(d)}</li>")
            detail_lines.append("    </ul>")
        if r.error:
            detail_lines.append(f'    <p class="error">ERRO: {_html_escape(r.error)}</p>')
        detail_lines.append("  </div>")
        detail_sections.append("\n".join(detail_lines))

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <title>Evidencia {label.upper()} - WI #{wi_id}</title>
  <style>
    body {{ font-family: 'Segoe UI', Tahoma, Arial, sans-serif; margin: 20px; background: #fafafa; color: #333; }}
    h1 {{ color: #004578; border-bottom: 3px solid #004578; padding-bottom: 8px; }}
    h2 {{ color: #004578; margin-top: 25px; }}
    h3 {{ margin: 10px 0 5px; }}
    .label-antes {{ color: #d13438; font-weight: bold; }}
    .label-depois {{ color: #107c10; font-weight: bold; }}
    .metadata {{ background: #f0f0f0; padding: 10px 15px; border-radius: 4px; margin: 10px 0 20px; }}
    .metadata b {{ color: #004578; }}
    table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
    th {{ background: #004578; color: #fff; padding: 10px 14px; text-align: left; font-size: 0.95em; }}
    td {{ padding: 8px 14px; border: 1px solid #ccc; }}
    .pass {{ background: #dff6dd; }}
    .fail {{ background: #fde7e9; }}
    .skip {{ background: #fff4ce; }}
    .pass-text {{ color: #107c10; }}
    .fail-text {{ color: #d13438; }}
    .skip-text {{ color: #7a6400; }}
    .phase-detail {{ margin: 10px 0; padding: 8px 12px; border-left: 3px solid #ccc; background: #fff; }}
    .details {{ font-size: 0.9em; color: #444; line-height: 1.6; }}
    .details li {{ margin-bottom: 2px; }}
    .error {{ color: #d13438; font-weight: bold; font-size: 0.9em; }}
    .summary {{ font-size: 1.15em; padding: 15px 20px; margin: 20px 0; border-radius: 4px; }}
    .summary-pass {{ background: #dff6dd; border-left: 5px solid #107c10; }}
    .summary-fail {{ background: #fde7e9; border-left: 5px solid #d13438; }}
    .footer {{ margin-top: 30px; padding-top: 10px; border-top: 1px solid #ddd; font-size: 0.85em; color: #888; }}
  </style>
</head>
<body>
  <h1>Evidencia <span class="label-{label_class}">{label.upper()}</span> &mdash; WI #{wi_id}</h1>

  <div class="metadata">
    <b>Ambiente:</b> {env_name} ({config['BD_SERVER']}:{config['BD_PORT']}/{config['BD_DATABASE']}) &nbsp;|&nbsp;
    <b>Schema:</b> {config['BD_OWNER']} &nbsp;|&nbsp;
    <b>Data:</b> {timestamp}
  </div>

  <h2>Resultado por Fase</h2>
  <table>
    <tr><th>Fase</th><th>Descricao</th><th>Status</th><th>Duracao</th></tr>
{chr(10).join(phase_rows)}
  </table>

  <h2>Detalhes por Fase</h2>
{chr(10).join(detail_sections)}

  <div class="summary summary-{summary_class}">
    <b>RESUMO:</b> {passed} PASS &nbsp;|&nbsp; {failed} FAIL &nbsp;|&nbsp; {skipped} SKIP (total: {total})
    &mdash; <b>{verdict}</b>
  </div>

  <div class="footer">
    Gerado por <b>qa_test_reproducer.py</b> em {timestamp} &nbsp;|&nbsp; WI #{wi_id} &nbsp;|&nbsp; Label: {label.upper()}
  </div>
</body>
</html>"""

    html_file.write_text(html, encoding="utf-8")
    print(f"Evidencia HTML salva em: {html_file}")
    return html_file


def _html_escape(text):
    """Escape basico para HTML."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _remap_qa_cenario(cenario_dir):
    """Cria symlinks/copia dos subdirs do cenario QA para o formato flat esperado.

    No repo QA, cenarios/{WI_ID}/ tem subdiretorios:
      sql/01_safx_import.sql, sql/02_import_validation.sql, etc.
      safx_files/SAFX07.txt

    O reproducer espera:
      01_safx_import.sql (na raiz do test_dir)
      safx_files/SAFX07.txt (ja esta no lugar certo)

    Copia os .sql de sql/ para a raiz se nao existirem la.
    """
    sql_dir = cenario_dir / "sql"
    if sql_dir.exists():
        for sql_file in sql_dir.glob("*.sql"):
            target = cenario_dir / sql_file.name
            if not target.exists():
                import shutil
                shutil.copy2(sql_file, target)


def main():
    parser = argparse.ArgumentParser(
        description="Reproduz testes de import SAFX em ambientes remotos"
    )
    parser.add_argument("--wi-id", required=True, help="ID do work item")
    parser.add_argument("--env", required=True, choices=["LOCAL", "AC", "RC", "QA"],
                        help="Ambiente alvo")
    parser.add_argument("--phase", default="all",
                        choices=["setup", "data_load", "import", "validate", "suite", "cleanup", "all"],
                        help="Fase a executar (default: all)")
    parser.add_argument("--from-qa-repo", action="store_true",
                        help="Buscar artefatos de cenarios/{WI_ID}/ no repo taxone_automacao_qa")
    parser.add_argument("--label", default=None, choices=["ANTES", "DEPOIS"],
                        help="Gerar evidencia HTML com label ANTES ou DEPOIS")
    parser.add_argument("--no-cleanup", action="store_true",
                        help="Pula fase cleanup (dados permanecem na base, script de rollback apenas gerado)")
    parser.add_argument("--no-suite", action="store_true",
                        help="Pula fase SuiteAutomation")
    parser.add_argument("--suite-timeout", type=int, default=900,
                        help="Timeout em segundos para SuiteAutomation (default: 900)")

    args = parser.parse_args()

    # Carregar config do ambiente
    config = load_env_config(args.env)

    if not config.get("BD_USER"):
        print(f"ERRO: BD_USER nao configurado para ambiente {args.env}")
        print(f"Configure BD_USER_{args.env} e BD_PASSWD_{args.env} em {ENV_FILE}")
        sys.exit(1)

    # Diretorio de testes — local ou repo QA
    if args.from_qa_repo:
        try:
            qa_repo = get_qa_repo_path()
        except EnvironmentError as e:
            print(f"ERRO: {e}")
            sys.exit(1)
        test_dir = qa_repo / "cenarios" / str(args.wi_id)
        if not test_dir.exists():
            print(f"ERRO: Cenario nao encontrado no repo QA: {test_dir}")
            print(f"  Publique o cenario primeiro: python scripts/qa_test_publisher.py --wi-id {args.wi_id}")
            sys.exit(1)
        # Mapear subdiretorios do cenario QA para o formato esperado
        # cenarios/{WI_ID}/sql/01_safx_import.sql -> como se fosse tests/{WI_ID}/01_safx_import.sql
        _remap_qa_cenario(test_dir)
        print(f"  Artefatos do repo QA: {test_dir}")
    else:
        test_dir = PROJECT_ROOT / "tests" / str(args.wi_id)
    if not test_dir.exists():
        print(f"ERRO: Diretorio de testes nao encontrado: {test_dir}")
        sys.exit(1)

    # Carregar manifest
    manifest_file = test_dir / "test_data_manifest.json"
    manifest = {}
    if manifest_file.exists():
        with open(manifest_file, encoding="utf-8") as f:
            manifest = json.load(f)
    else:
        # Manifest minimo
        manifest = {"wi_id": int(args.wi_id), "tables": ["SAFX3007"], "cod_empresa": "076", "cod_estab": "01"}

    results = []
    conn = None

    phases_to_run = []
    if args.phase == "all":
        phases_to_run = ["setup", "data_load", "import", "validate", "suite", "cleanup"]
        if args.no_suite:
            phases_to_run.remove("suite")
        if args.no_cleanup:
            phases_to_run.remove("cleanup")
    else:
        phases_to_run = [args.phase]

    try:
        for phase in phases_to_run:
            if phase == "setup":
                r, conn = phase_setup(config, test_dir, manifest)
                results.append(r)
                if r.status == "FAIL":
                    print(f"\nFase SETUP falhou, abortando.")
                    break

            elif phase == "data_load":
                if conn is None:
                    r, conn = phase_setup(config, test_dir, manifest)
                    results.append(r)
                    if r.status == "FAIL":
                        break
                r = phase_data_load(conn, config, test_dir, manifest)
                results.append(r)
                if r.status == "FAIL" and args.phase == "all":
                    print(f"\nFase DATA_LOAD falhou, abortando.")
                    break

            elif phase == "import":
                if conn is None:
                    r, conn = phase_setup(config, test_dir, manifest)
                    results.append(r)
                    if r.status == "FAIL":
                        break
                r = phase_import(conn, config, test_dir, manifest)
                results.append(r)

            elif phase == "validate":
                if conn is None:
                    r, conn = phase_setup(config, test_dir, manifest)
                    results.append(r)
                    if r.status == "FAIL":
                        break
                r = phase_validate(conn, config, test_dir, manifest)
                results.append(r)

            elif phase == "suite":
                r = phase_suite(config, test_dir, manifest, args.env, args.suite_timeout)
                results.append(r)

            elif phase == "cleanup":
                if conn is None:
                    r, conn = phase_setup(config, test_dir, manifest)
                    results.append(r)
                    if r.status == "FAIL":
                        break
                r = phase_cleanup(conn, config, test_dir, manifest)
                results.append(r)

        # Se --no-cleanup, informar que dados permanecem e onde esta o script de rollback
        if args.no_cleanup:
            rollback_file = test_dir / "05_rollback_test_data.sql"
            r = PhaseResult("CLEANUP", "Rollback dados de teste")
            r.start()
            if rollback_file.exists():
                r.details.append(f"--no-cleanup: dados de teste PERMANECEM na base")
                r.details.append(f"Script de rollback disponivel em: {rollback_file}")
                r.skip("Cleanup pulado (--no-cleanup). Dados permanecem na base.")
            else:
                r.details.append(f"--no-cleanup: dados de teste PERMANECEM na base")
                r.skip("Cleanup pulado (--no-cleanup). Sem script de rollback encontrado.")
            results.append(r)

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    # Imprimir resultados
    print_results(results, config, args.wi_id, args.env)

    # Salvar relatorio MD
    save_report(results, config, args.wi_id, args.env, test_dir)

    # Salvar evidencia HTML (se --label informado)
    if args.label:
        save_html_report(results, config, args.wi_id, args.env, test_dir, args.label)

    # Exit code
    has_failures = any(r.status == "FAIL" for r in results)
    sys.exit(1 if has_failures else 0)


if __name__ == "__main__":
    main()
