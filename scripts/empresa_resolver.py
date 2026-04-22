#!/usr/bin/env python3
"""
Resolve o nome da empresa (tenant) a partir do texto de uma Work Item do ADO.

Carrega a lista de empresas de TENANT_LOOKUP.txt e busca matches
case-insensitive nos campos do WI (Title, Description, ReproSteps, Tags).

Uso standalone:
    python empresa_resolver.py --title "Erro no calculo ACHE" --description "..."
    python empresa_resolver.py --title "Erro ICMS" --tags "FEMSA; producao"

Uso como modulo:
    from empresa_resolver import resolve_empresa
    matches = resolve_empresa(title="...", description="...", tags="...")
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
TENANT_LOOKUP_PATH = (
    REPO_ROOT / "skills" / "query-executor-prod-uat" / "resources" / "TENANT_LOOKUP.txt"
)

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class Tenant:
    empresa: str
    schema: str
    prod: str
    uat: str


@dataclass
class EmpresaMatch:
    empresa: str
    score: int
    match_type: str  # "word_boundary" | "substring"
    found_in: str    # campo onde foi encontrado


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_tenants(lookup_path: pathlib.Path | None = None) -> list[Tenant]:
    """Carrega lista de tenants do TENANT_LOOKUP.txt."""
    path = lookup_path or TENANT_LOOKUP_PATH
    if not path.exists():
        raise FileNotFoundError(f"TENANT_LOOKUP.txt nao encontrado: {path}")

    tenants: list[Tenant] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) < 4:
                continue
            tenants.append(Tenant(
                empresa=parts[0].strip(),
                schema=parts[1].strip(),
                prod=parts[2].strip(),
                uat=parts[3].strip(),
            ))
    return tenants


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _search_in_text(
    text: str,
    empresa: str,
    field_name: str,
) -> EmpresaMatch | None:
    """Busca empresa no texto. Retorna match com score baseado no tipo."""
    if not text:
        return None

    text_upper = text.upper()
    empresa_upper = empresa.upper()

    # Ignorar empresas com nome muito curto (<=2 chars) para evitar falsos positivos
    # exceto se for match exato de palavra
    min_len = 3

    # Word boundary match (score alto)
    pattern = r'\b' + re.escape(empresa_upper) + r'\b'
    if re.search(pattern, text_upper):
        return EmpresaMatch(
            empresa=empresa,
            score=100,
            match_type="word_boundary",
            found_in=field_name,
        )

    # Substring match (score menor, so para nomes >= min_len)
    if len(empresa) >= min_len and empresa_upper in text_upper:
        return EmpresaMatch(
            empresa=empresa,
            score=50,
            match_type="substring",
            found_in=field_name,
        )

    return None


def resolve_empresa(
    title: str = "",
    description: str = "",
    repro_steps: str = "",
    tags: str = "",
    lookup_path: pathlib.Path | None = None,
) -> list[EmpresaMatch]:
    """
    Busca nomes de empresa nos campos do WI.

    Retorna lista de matches rankeados por score (desc).
    Word boundary matches (score=100) > substring matches (score=50).
    Se a mesma empresa aparece em multiplos campos, mantém o de maior score.
    """
    tenants = load_tenants(lookup_path)

    fields = [
        ("Title", title),
        ("Tags", tags),
        ("Description", description),
        ("ReproSteps", repro_steps),
    ]

    best_matches: dict[str, EmpresaMatch] = {}

    for tenant in tenants:
        for field_name, field_text in fields:
            match = _search_in_text(field_text, tenant.empresa, field_name)
            if match:
                existing = best_matches.get(tenant.empresa)
                if not existing or match.score > existing.score:
                    best_matches[tenant.empresa] = match
                # Se ja tem word_boundary, nao precisa procurar mais
                if match.match_type == "word_boundary":
                    break

    # Ordenar: score desc, depois empresa asc
    results = sorted(
        best_matches.values(),
        key=lambda m: (-m.score, m.empresa),
    )

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve empresa (tenant) a partir de texto de Work Item.",
    )
    parser.add_argument("--title", default="", help="Titulo do WI")
    parser.add_argument("--description", default="", help="Descricao do WI")
    parser.add_argument("--repro-steps", default="", help="ReproSteps do WI")
    parser.add_argument("--tags", default="", help="Tags do WI")
    parser.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Formato de saida (default: text)",
    )

    args = parser.parse_args()

    if not any([args.title, args.description, args.repro_steps, args.tags]):
        print("Erro: forneça ao menos um campo (--title, --description, --repro-steps, --tags).",
              file=sys.stderr)
        return 2

    try:
        matches = resolve_empresa(
            title=args.title,
            description=args.description,
            repro_steps=args.repro_steps,
            tags=args.tags,
        )
    except FileNotFoundError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        output = {
            "matches": [
                {
                    "empresa": m.empresa,
                    "score": m.score,
                    "match_type": m.match_type,
                    "found_in": m.found_in,
                }
                for m in matches
            ],
            "count": len(matches),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        if not matches:
            print("Nenhuma empresa encontrada nos campos do WI.")
            print("Por favor, informe o nome da empresa manualmente.")
            return 1

        print(f"Empresas encontradas: {len(matches)}\n")
        for i, m in enumerate(matches, 1):
            print(f"  {i}. {m.empresa} (score: {m.score}, tipo: {m.match_type}, campo: {m.found_in})")

        if len(matches) == 1:
            print(f"\nEmpresa resolvida: {matches[0].empresa}")
        else:
            print(f"\nMultiplas empresas encontradas. Recomendacao: {matches[0].empresa}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
