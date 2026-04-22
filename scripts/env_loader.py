#!/usr/bin/env python3
"""
env_loader - Carrega variaveis de ambiente do .env na raiz do projeto.

Prioridade:
  1. Variavel de ambiente do sistema (os.environ)
  2. Arquivo .env na raiz do projeto
  3. Erro claro se nao encontrar
"""

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
_env_cache = None


def _load_dotenv() -> dict:
    """Parse simples do .env (sem dependencia externa)."""
    global _env_cache
    if _env_cache is not None:
        return _env_cache

    _env_cache = {}
    if not _ENV_FILE.exists():
        return _env_cache

    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Remove aspas opcionais
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        _env_cache[key] = value

    return _env_cache


def get_repo_path(var_name: str) -> str:
    """Retorna o path de um repositorio, com erro claro se nao configurado.

    Prioridade: env var do sistema > .env da raiz > erro.
    """
    # 1. Env var do sistema
    value = os.environ.get(var_name)
    if value:
        return value

    # 2. .env da raiz
    dotenv = _load_dotenv()
    value = dotenv.get(var_name)
    if value:
        return value

    # 3. Erro claro
    raise EnvironmentError(
        f"Variavel '{var_name}' nao configurada.\n"
        f"Configure no arquivo .env na raiz do projeto (copie de .env.example):\n"
        f"  {_ENV_FILE.parent / '.env.example'}"
    )
