# CLAUDE.md — tr_conversion_not

## Projeto

Agentes Claude para extração de regras de negócio do TAX ONE legado (PowerBuilder + PL/SQL + Oracle) visando reescrita em Java 21 / Spring Boot.

## Repositórios

| Alias | Caminho / GitHub | Conteúdo |
|-------|-----------------|----------|
| **taxone_dw** (legado) | `C:/Repositorios/taxone_dw` | Fonte primário PB + PL/SQL (~48.500 arquivos) |
| **convertedFonts** | `tr/taxsami_convertedFonts` (branch master) | 10.236 DWs convertidos para Java (Mobilize jWebMAP) |
| **t1dw** (target) | `tr/taxone_modules_t1dw` (branch master) | Nova app Java nativa (Spring Boot 3.5, Java 21) |
| **conversiontool** | `tr/taxsami_conversiontool` (branch master) | Engine Mobilize PB→Java |
| **artifacts** | `tr/taxsami_artifacts` (branch master) | Runtime Java (PB2JavaHelpers) |
| **conteudo** | `tr/taxone_dw_conteudo` (branch master) | Documentação, help online, layouts SAFX |
| **frontend** | `tr/taxsami_taxone_frontend` (branch master) | Frontend Angular 11 (monorepo) |
| **conversion (PB)** | `C:/Repositorios/taxsami_taxone-conversion` (branch rc) | Fontes .pblwebmap (redundantes — usar .srw do taxone_dw) |

## Regras fundamentais

1. **Este repo é read-only sobre o código legado** — nunca modificar fontes em taxone_dw a partir daqui
2. **Fonte primária é sempre o .srw/.srd do taxone_dw** — arquivos .pblwebmap são cópias congeladas, não mapas de transformação
3. **Extrair regras do PB original** — usar Java convertido (convertedFonts) apenas como cross-reference, pois é tradução mecânica 5-10x mais verbosa
4. **Output dos agentes deve ser Markdown estruturado** com tags parseáveis, consumível por outros agentes downstream
5. **Regras de negócio usam classificação padrão:** RV (Validação), RC (Cálculo), RF (Filtro), RT (Transformação), RW (Workflow)
6. **Status de migração por regra:** `[NEW]`, `[CONVERTED]`, `[NATIVE]`, `[JAVA_GAP]`

## Variáveis de ambiente

Definidas em `.env` (gitignored). Copiar de `.env.example` e ajustar. Copiado do `taxone-support-dev/.env`.

| Variável | Obrigatório | Uso |
|----------|-------------|-----|
| `TAXONE_DW_REPO` | Sim | Caminho do repo legado (default: `C:/Repositorios/taxone_dw`) |
| `QA_REPO` | Sim | Repo SuiteAutomation (default: `C:/Repositorios/taxone_automacao_qa`) |
| `TAXONE_FRONTEND_REPO` | Não | Repo Angular 11 |
| `PLAYWRIGHT_REPO` | Não | Repo testes E2E Playwright |
| `ADO_PAT` | Sim* | Personal Access Token do Azure DevOps (*obrigatório para triagem/planning/auto-fix) |
| `ZENDESK_SUBDOMAIN` | Não* | Subdomínio Zendesk (*obrigatório para N3 triage/PM) |
| `ZENDESK_EMAIL` | Não* | Email do agente Zendesk |
| `ZENDESK_API_TOKEN` | Não* | API token do Zendesk |

## Knowledge base

Diretório `.claude/knowledge/` (371MB, ~20k arquivos, gitignored). Copiado de `taxone-support-dev/knowledge/`.

| Diretório | Conteúdo | Tamanho |
|-----------|----------|---------|
| `architecture/` | overview, patterns, tech-stack | 36KB |
| `conventions/` | code-standards, naming | 40KB |
| `features/` | Docs por feature (dinâmico) | 36KB |
| `schema/` | COLUMN_GLOSSARY, PLSQL_MAP, TABLE_HOTSPOTS, MODULE_MAP, RELATIONSHIPS, CORE_SAFX | 8.3MB |
| `triage/` | MODULE_KNOWLEDGE_MAP, decision-matrix | 24KB |
| `team/` | TEAM_ROSTER, TEAM_SPECIALIZATIONS | 57KB |
| `business-rules/` | Regras de negócio extraídas por módulo | 271MB |
| `ado-fixes/` | WIs documentados com metadata e PR cache | 54MB |
| `webhelp/` | Artigos de help online | 22MB |
| `zendesk-patterns/` | Patterns de tickets por vertical | 6.2MB |
| `solutions/` | Compound docs de soluções | 196KB |
| `suite-automation/` | Mapas de teste (component, playwright, reference-data) | 4.6MB |
| `utplsql/` | Templates e mapas de teste PL/SQL | 3MB |

Os agentes acessam via `${CLAUDE_PLUGIN_ROOT}/knowledge/`. Para manter atualizado, re-copiar de `taxone-support-dev/knowledge/`.

## Estrutura do projeto

```
tr_conversion_not/
├── CLAUDE.md              # Instruções globais para o Claude Code
├── .env.example           # Template de variáveis de ambiente
├── .env                   # Variáveis de ambiente (gitignored)
├── .gitignore
├── README.md
├── scripts/               # 38 scripts Python/Shell usados pelos agentes
│   ├── faq_triage.py          # FAQ matching para triagem de WIs
│   ├── zendesk_client.py      # Busca tickets Zendesk (SSL corp + encoding)
│   ├── git_history_analyzer.py # Histórico git de packages/procedures
│   ├── suite_runner.py        # Runner SuiteAutomation (.jar → Oracle)
│   ├── playwright_runner.py   # Runner E2E Playwright (Chromium)
│   ├── explorer_runner.py     # Testes exploratórios visuais via browser
│   ├── targeted_e2e_runner.py # E2E seleção automática por WI
│   ├── env_loader.py          # Carrega .env sem dependência externa
│   ├── ado_discussion_templates.py # Templates de comments ADO
│   ├── qa_test_reproducer.py  # Reprodutor de testes QA
│   ├── qa_test_publisher.py   # Publica evidências de teste
│   ├── qa_task_creator.py     # Cria tasks QA no ADO
│   └── ...                    # +25 scripts auxiliares
└── .claude/
    ├── knowledge/         # Base de conhecimento (371MB, gitignored)
    ├── agents/            # 31 agentes especializados
    │   ├── taxone-rule-extractor.md   # Principal — extração de regras de negócio
    │   ├── taxone-developer.md        # Dispatcher implementação (PL/SQL, PB, Java)
    │   ├── taxone-reviewer.md         # Dispatcher code review (PL/SQL, PB, Java)
    │   ├── taxone-architect.md        # Análise arquitetural (read-only)
    │   ├── taxone-plsql.md            # Implementação PL/SQL
    │   ├── taxone-pb.md               # Implementação PowerBuilder
    │   ├── taxone-java.md             # Implementação Java
    │   ├── taxone-angular.md          # Implementação Angular
    │   ├── taxone-*-reviewer.md       # Code review (plsql, pb, java)
    │   ├── taxone-dw-*.md             # Pipeline de teste DW (8 agentes)
    │   ├── taxone-pm.md               # Triagem de Work Items
    │   ├── taxone-sm.md               # Sprint Planning
    │   ├── taxone-n3-triage.md        # Enrichment N3
    │   └── taxone-techlead-*.md       # Gates de qualidade (dev, qa)
    ├── commands/          # 9 slash commands (pipelines orquestrados)
    │   ├── taxone-feature-dev.md      # Pipeline completo 13 fases
    │   ├── taxone-auto-fix.md         # ADO query → PR automático
    │   ├── taxone-bug-fix.md          # Fix leve e autônomo
    │   ├── taxone-compound.md         # Documentação Compound Engineering
    │   └── ...
    └── settings.local.json
```

## Convenções de agentes

- **model: inherit** é o padrão — usar modelo específico apenas quando justificado (ex: `sonnet` para agentes de alto volume/baixa complexidade)
- Agentes read-only: tools `["Read", "Grep", "Glob"]` ou `["Read", "Bash", "Grep", "Glob"]`
- Agentes de implementação: tools `["Read", "Write", "Edit", "Bash", "Grep", "Glob"]`
- Todo agente que usa knowledge deve incluir a regra de visibilidade: `> [Knowledge] Carregando: {arquivo}`
- Processo padrão: Carregar knowledge → Entender problema → Implementar/Analisar → Reportar

## Dispatcher agents

Os commands usam `taxone-developer` e `taxone-reviewer` como dispatchers genéricos que detectam a tecnologia e assumem o papel do agente especializado:

| Dispatcher | Detecta | Assume papel de |
|------------|---------|-----------------|
| `taxone-developer` | `.pck`/`.sql` → PL/SQL | `taxone-plsql` |
| | `.srd`/`.srw`/`.sru` → PowerBuilder | `taxone-pb` |
| | `.java` → Java | `taxone-java` |
| `taxone-reviewer` | `.pck`/`.sql` → PL/SQL | `taxone-plsql-reviewer` |
| | `.srd`/`.srw`/`.sru` → PowerBuilder | `taxone-pb-reviewer` |
| | `.java` → Java | `taxone-java-reviewer` |

Os dispatchers **não delegam** via Agent tool — assumem diretamente o papel do agente especializado para evitar cadeia desnecessária de sub-agentes.

## Stack target (Java)

| Tecnologia | Versão |
|-----------|--------|
| Java | 21 |
| Spring Boot | 3.5.x |
| Spring Data JPA / Hibernate | - |
| Lombok | - |
| t1shared | 5.1.0 |
