---
name: taxone-compound
description: Documenta solucao implementada (bug/feature/refactoring/optimization) como doc reutilizavel seguindo metodologia Compound Engineering. Transforma cada unidade de trabalho em conhecimento que facilita trabalhos futuros.
argument-hint: Opcional (deixar vazio para documentar trabalho atual)
allowed-tools: ["Agent", "Read", "Grep", "Glob", "Bash", "Write"]
---

Documentar solucao implementada recentemente no TAX ONE como doc reutilizavel seguindo metodologia Compound Engineering.

## Instrucoes

1. Ativar a skill `taxone-compound` que implementa o workflow de documentacao
2. **IMPORTANTE:** Este comando deve ser executado dentro do repositorio do TAX ONE
3. O skill analisa TODO o historico da conversa desde o inicio da investigacao/implementacao
4. Gera arquivo markdown em `knowledge/solutions/{wi_id}.md` com YAML frontmatter searchable

## Restricoes Criticas

- Este comando TEM ACESSO a Agent, Read, Grep, Glob, Bash e Write
- Agent tool e usado para lancar subagentes (taxone-architect, taxone-developer, taxone-tester)
- Glob e usado para buscar arquivos na base de conhecimento

## Comportamento

### Pre-check: Extrair WI ID, Vertical e Dados de PR

1. Validar que estamos em um repositorio git do TAX ONE
2. Criar estrutura `knowledge/solutions/` se nao existir
3. Extrair WI ID (6-7 digitos) do historico da conversa
4. Buscar WI no `knowledge/ado-fixes/_metadata.json` → extrair vertical, tags, pr_numbers
5. Buscar PR no `knowledge/ado-fixes/_pr_cache.json` → extrair files, additions, deletions
6. Verificar se WI ja existe em `knowledge/solutions/INDEX.md` (mode: create vs update)

### Fase 1: Pesquisa Paralela (5 subagentes simultaneos)

1. **Context Analyzer** (taxone-architect) - Extrai YAML frontmatter + dados de PR
2. **Solution Extractor** (taxone-developer) - Extrai Approach + Analysis + Solution
3. **Related Docs Finder** (taxone-architect) - Busca em 4 fontes: Compound Docs, WIs Similares, Zendesk Patterns, Webhelp
4. **Prevention Strategist** (taxone-tester) - Gera Patterns (com dados quantitativos) + Testes + Checklist + Takeaways
5. **Category Classifier** (taxone-architect) - Determina categoria, filename `{wi_id}.md`, e mode (create/update)

### Fase 2: Documentation Writer (sequencial)

1. Aguardar TODOS os 5 subagentes
2. Validar YAML frontmatter (incluindo pr_files, pr_stats)
3. Montar markdown completo com secoes expandidas:
   - PR Stats (files changed, additions, deletions)
   - Solucoes Relacionadas com 4 subsecoes (Compound Docs, WIs Similares, Zendesk Patterns, Webhelp)
4. Escrever arquivo em `knowledge/solutions/{wi_id}.md`
5. Atualizar `knowledge/solutions/INDEX.md` automaticamente

### Resultado Final

Apresentar ao usuario o arquivo criado/atualizado e recomendar adicionar ao PR.

## Quando Usar

**Compound se aplica a QUALQUER trabalho de desenvolvimento:**
- Bug fix (> 15min investigacao)
- Feature implementation (decisoes tecnicas, patterns descobertos)
- Refactoring (melhorias alcancadas)
- Optimization (performance gains em Oracle)

**Filosofia:**
- 4-step loop: Plan -> Work -> Review -> **Compound** -> Repeat
- Captura tentativas falhas (aprendizados criticos)
- Cria conhecimento reutilizavel que acelera trabalhos futuros

## Argumento

Este comando NAO requer argumentos. Analisa automaticamente o historico da conversa atual.
