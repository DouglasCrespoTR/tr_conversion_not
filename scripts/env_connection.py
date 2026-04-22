#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
env_connection.py - Centralized Oracle connection factory for multi-environment support.

Reads environment configs from suite-automation/config/environments.json and
credentials from suite-automation/config/.env to provide a single entry point
for Oracle connections across all TAX ONE environments (LOCAL, AC, QA, RC).

Usage as module:
    from env_connection import get_connection, get_dsn, get_owner, load_env_config

    conn = get_connection("LOCAL")
    dsn  = get_dsn("QA")
    owner = get_owner("RC")
    cfg  = load_env_config("AC")

Usage as CLI (connectivity test):
    python scripts/env_connection.py --env LOCAL
    python scripts/env_connection.py --env QA --query "SELECT 1 FROM DUAL"
"""

import argparse
import json
import os
import sys
from pathlib import Path

try:
    import oracledb
except ImportError:
    oracledb = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENVIRONMENTS_FILE = PROJECT_ROOT / "suite-automation" / "config" / "environments.json"
ENV_FILE = PROJECT_ROOT / "suite-automation" / "config" / ".env"
VALID_ENVS = ("LOCAL", "AC", "QA", "RC")

# Oracle thick mode — required for remote environments using Native Network
# Encryption (NNE).  Search common 64-bit Oracle Client / Instant Client
# locations on Windows.  Only the first successful init takes effect.
_THICK_MODE_CANDIDATES = [
    r"C:\temp\WINDOWS.X64_193000_db_home\bin",
    r"C:\oracle\instantclient_19_20",
    r"C:\oracle\instantclient",
]
_thick_mode_initialised = False

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_dot_env():
    """Parse suite-automation/config/.env (key=value, # comments)."""
    env_values = {}
    if not ENV_FILE.exists():
        return env_values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            env_values[key.strip()] = val.strip()
    return env_values


def _resolve_credentials(env_name, env_values):
    """Return (user, passwd) for the given environment.

    Priority:
      1. System environment variables (BD_USER_QA/BD_PASSWD_QA for QA,
         BD_USER/BD_PASSWD for others)
      2. .env file values (same key names)
      3. For LOCAL only: fallback to DB_USER / DB_PASS env vars
         (backward compat with older scripts)
    """
    if env_name == "QA":
        user = os.environ.get("BD_USER_QA") or env_values.get("BD_USER_QA", "")
        passwd = os.environ.get("BD_PASSWD_QA") or env_values.get("BD_PASSWD_QA", "")
    else:
        user = os.environ.get("BD_USER") or env_values.get("BD_USER", "")
        passwd = os.environ.get("BD_PASSWD") or env_values.get("BD_PASSWD", "")

    # LOCAL backward-compat fallback: DB_USER / DB_PASS env vars
    if env_name == "LOCAL":
        if not user:
            user = os.environ.get("DB_USER", "")
        if not passwd:
            passwd = os.environ.get("DB_PASS", "")

    return user, passwd


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_env_config(env: str) -> dict:
    """Return config dict for *env* with keys:
    BD_SERVER, BD_PORT, BD_DATABASE, BD_OWNER, BD_USER, BD_PASSWD,
    COD_PROJETO, WS_URL.

    Raises ValueError if *env* is not in VALID_ENVS or environments.json.
    """
    env = env.upper()
    if env not in VALID_ENVS:
        raise ValueError(f"Ambiente invalido: '{env}'. Validos: {VALID_ENVS}")

    with open(ENVIRONMENTS_FILE, encoding="utf-8") as f:
        envs = json.load(f)

    if env not in envs:
        raise ValueError(
            f"Ambiente '{env}' nao encontrado em {ENVIRONMENTS_FILE}. "
            f"Disponiveis: {list(envs.keys())}"
        )

    params = dict(envs[env])
    env_values = _load_dot_env()
    user, passwd = _resolve_credentials(env, env_values)
    params["BD_USER"] = user
    params["BD_PASSWD"] = passwd

    # LOCAL backward-compat: honour DB_DSN override
    if env == "LOCAL":
        db_dsn = os.environ.get("DB_DSN")
        if db_dsn and "/" in db_dsn:
            # Format expected: host:port/service
            host_port, _, service = db_dsn.partition("/")
            if ":" in host_port:
                host, port = host_port.rsplit(":", 1)
                params["BD_SERVER"] = host
                params["BD_PORT"] = port
            params["BD_DATABASE"] = service

    return params


def get_dsn(env: str = "LOCAL") -> str:
    """Return DSN string ``host:port/database`` for *env*."""
    cfg = load_env_config(env)
    return f"{cfg['BD_SERVER']}:{cfg['BD_PORT']}/{cfg['BD_DATABASE']}"


def get_owner(env: str = "LOCAL") -> str:
    """Return the schema owner (BD_OWNER) for *env*."""
    cfg = load_env_config(env)
    return cfg["BD_OWNER"]


def _ensure_thick_mode(env: str) -> None:
    """Initialise Oracle thick mode when targeting a remote environment.

    Remote Oracle databases (QA, AC, RC) typically enforce Native Network
    Encryption (NNE) which is only supported in thick mode.  LOCAL connections
    work fine in thin mode and do not need this.

    The function tries known 64-bit Oracle Client paths on Windows.  It is
    safe to call multiple times — only the first successful init takes effect.
    """
    global _thick_mode_initialised
    if _thick_mode_initialised or oracledb is None:
        return
    if env.upper() == "LOCAL":
        return  # thin mode is enough for localhost

    for lib_dir in _THICK_MODE_CANDIDATES:
        if os.path.isdir(lib_dir) and os.path.isfile(os.path.join(lib_dir, "oci.dll")):
            oracle_home = str(Path(lib_dir).parent)
            os.environ.setdefault("ORACLE_HOME", oracle_home)
            try:
                oracledb.init_oracle_client(lib_dir=lib_dir)
                _thick_mode_initialised = True
                return
            except Exception:
                # Already initialised (e.g. by caller) or incompatible lib
                _thick_mode_initialised = True
                return

    # No 64-bit client found — connection may still succeed if NNE is not
    # enforced, so we do not raise here.


def get_connection(env: str = "LOCAL"):
    """Return an ``oracledb.Connection`` for *env*.

    Automatically enables Oracle thick mode for remote environments (QA, AC,
    RC) that require Native Network Encryption (NNE).  LOCAL connections use
    thin mode by default.

    Raises RuntimeError if oracledb is not installed.
    """
    if oracledb is None:
        raise RuntimeError(
            "oracledb nao instalado. Instale com: pip install oracledb"
        )

    _ensure_thick_mode(env)

    cfg = load_env_config(env)
    dsn = f"{cfg['BD_SERVER']}:{cfg['BD_PORT']}/{cfg['BD_DATABASE']}"

    conn = oracledb.connect(
        user=cfg["BD_USER"],
        password=cfg["BD_PASSWD"],
        dsn=dsn,
    )
    return conn


# ---------------------------------------------------------------------------
# CLI — connectivity test
# ---------------------------------------------------------------------------


def _cli():
    parser = argparse.ArgumentParser(
        description="Testa conectividade Oracle para o ambiente especificado."
    )
    parser.add_argument(
        "--env",
        default="LOCAL",
        choices=[e.lower() for e in VALID_ENVS] + list(VALID_ENVS),
        help="Ambiente alvo (default: LOCAL)",
    )
    parser.add_argument(
        "--query",
        default="SELECT 'OK' FROM DUAL",
        help="Query de teste (default: SELECT 'OK' FROM DUAL)",
    )
    args = parser.parse_args()

    env = args.env.upper()
    print(f"=== env_connection — teste de conectividade ({env}) ===\n")

    try:
        cfg = load_env_config(env)
    except ValueError as exc:
        print(f"ERRO: {exc}")
        sys.exit(1)

    dsn = f"{cfg['BD_SERVER']}:{cfg['BD_PORT']}/{cfg['BD_DATABASE']}"
    print(f"  DSN   : {dsn}")
    print(f"  Owner : {cfg['BD_OWNER']}")
    print(f"  User  : {cfg['BD_USER'] or '(vazio)'}")
    print()

    if oracledb is None:
        print("ERRO: oracledb nao instalado. Instale com: pip install oracledb")
        sys.exit(1)

    try:
        conn = get_connection(env)
        cur = conn.cursor()
        cur.execute(args.query)
        rows = cur.fetchall()
        print(f"  Query : {args.query}")
        for row in rows:
            print(f"  Result: {row}")
        cur.close()
        conn.close()
        print("\n  Conectividade OK.")
    except Exception as exc:
        print(f"  ERRO ao conectar: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    _cli()
