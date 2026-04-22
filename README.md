# tr_conversion_not

Agentes Claude Code para extração de regras de negócio do **TAX ONE** (Mastersaf DW) legado, viabilizando a reescrita em Java.

## Objetivo

O TAX ONE é um sistema de compliance fiscal brasileiro construído em **PowerBuilder + PL/SQL + Oracle** (~48.500 arquivos, 4.6GB). Este repositório contém **31 agentes Claude Code** e **9 slash commands** que automatizam a análise e extração de conhecimento do código legado para guiar a migração para **Java 21 / Spring Boot 3.5**.

## Setup

```bash
# 1. Clonar o repositório
git clone <repo-url> && cd tr_conversion_not

# 2. Configurar variáveis de ambiente
cp .env.example .env
# Editar .env com caminhos locais e ADO_PAT

# 3. Copiar base de conhecimento (371MB, gitignored)
cp -r /caminho/para/taxone-support-dev/knowledge .claude/knowledge/
```

### Pré-requisitos

| Requisito | Descrição |
|-----------|-----------|
| Claude Code CLI | `claude` disponível no PATH |
| Repo legado | `C:/Repositorios/taxone_dw` clonado localmente |
| GitHub CLI | `gh` autenticado (para acessar repos TR) |
| ADO PAT | Token do Azure DevOps (para triagem/planning) |

### Variáveis de ambiente (.env)

| Variável | Obrigatório | Descrição |
|----------|-------------|-----------|
| `TAXONE_DW_REPO` | Sim | Caminho do repo legado PB + PL/SQL |
| `QA_REPO` | Sim | Repo SuiteAutomation |
| `TAXONE_FRONTEND_REPO` | Não | Repo Angular 11 |
| `PLAYWRIGHT_REPO` | Não | Repo testes E2E Playwright |
| `ADO_PAT` | Sim* | Token Azure DevOps (*para triagem/planning/auto-fix) |
| `ZENDESK_SUBDOMAIN` | Não* | Subdomínio Zendesk (*para N3 triage/PM) |
| `ZENDESK_EMAIL` | Não* | Email do agente Zendesk |
| `ZENDESK_API_TOKEN` | Não* | API token do Zendesk |

## Estrutura

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
│   └── ...                    # +32 scripts auxiliares
└── .claude/
    ├── knowledge/         # Base de conhecimento (371MB, 20k arquivos, gitignored)
    │   ├── architecture/      # Visão geral, padrões, tech-stack
    │   ├── conventions/       # Padrões de código, nomenclatura
    │   ├── schema/            # Glossário de colunas, mapa PL/SQL, hotspots (8.3MB)
    │   ├── business-rules/    # Regras de negócio extraídas por módulo (271MB)
    │   ├── ado-fixes/         # WIs documentados com metadata (54MB)
    │   ├── webhelp/           # Artigos de help online (22MB)
    │   ├── zendesk-patterns/  # Patterns de tickets por vertical (6.2MB)
    │   ├── rule-extractor/    # Knowledge específico do extrator de regras (37KB)
    │   └── ...                # features, solutions, team, triage, suite-automation, utplsql
    ├── agents/            # 31 agentes especializados
    └── commands/          # 9 slash commands (pipelines)
```

## Agentes (31)

### Extração e Análise (core)

| Agente | Função |
|--------|--------|
| `taxone-rule-extractor` | **Principal** — extrai regras de negócio de módulos PB + PL/SQL com inheritance crawling e gap analysis |
| `taxone-architect` | Análise arquitetural, impacto de mudanças, integração entre módulos |

### Dispatchers

| Agente | Função |
|--------|--------|
| `taxone-developer` | Detecta tecnologia (PL/SQL, PB, Java) e assume o papel do implementador correto |
| `taxone-reviewer` | Detecta tecnologia e aplica o checklist de code review correto |

### Implementação

| Agente | Tecnologia | Tools |
|--------|------------|-------|
| `taxone-plsql` | PL/SQL (packages, procedures, functions, triggers, views) | Read/Write/Edit/Bash/Grep/Glob |
| `taxone-pb` | PowerBuilder (DataWindows, user objects, event scripts) | Read/Write/Edit/Bash/Grep/Glob |
| `taxone-java` | Java (web services, servlets, Jasper Reports, batch) | Read/Write/Edit/Bash/Grep/Glob |
| `taxone-angular` | Angular 11 (monorepo frontend) | Read/Write/Edit/Bash/Grep/Glob |

### Code Review

| Agente | Foco |
|--------|------|
| `taxone-plsql-reviewer` | SQL injection, cursores, exception handling, performance Oracle |
| `taxone-pb-reviewer` | DataWindow SQL, encoding ISO-8859-1, event scripts, null handling |
| `taxone-java-reviewer` | JDBC security, resource management, Spring patterns |

### Gestão e Triagem

| Agente | Função |
|--------|--------|
| `taxone-pm` | Triagem de Work Items — identifica not-a-bugs, feature requests, duplicatas |
| `taxone-n3-triage` | Enrichment N3 — normaliza WIs e gera N3 Brief padronizado |
| `taxone-sm` | Sprint Planning — estimativa de story points, alocação dev/tester |
| `taxone-techlead-dev` | Gate de qualidade técnica, revisão cross-tecnologia |
| `taxone-techlead-qa` | Gate de qualidade QA, cobertura de testes |

### Teste

| Agente | Função |
|--------|--------|
| `taxone-tester` | Scripts de teste SQL e roteiros manuais |
| `taxone-test-data-engineer` | Gera massa de dados SAFX para cenários de teste |
| `taxone-suite-runner` | Executa regressão SuiteAutomation |
| `taxone-e2e-tester` | Testes E2E Playwright |
| `taxone-explorer` | Testes exploratórios visuais via browser |

### Pipeline de Teste DW (8 agentes)

| Agente | Fase |
|--------|------|
| `taxone-dw-test-orchestrator` | Orquestra os 7 agentes abaixo |
| `taxone-dw-ddl-runner` | Executa DDLs no Oracle local |
| `taxone-dw-sp-compiler` | Compila packages PL/SQL |
| `taxone-dw-sql-validator` | Valida saúde do banco |
| `taxone-dw-pb-compiler` | Compila PBLs via OrcaScript |
| `taxone-dw-pb-analyzer` | Análise estática de fontes PB |
| `taxone-dw-pb-dw-tester` | Testa SQL dos DataWindows |
| `taxone-dw-pb-desktop-tester` | Teste funcional desktop via pywinauto |
| `taxone-dw-evidence-generator` | Gera documento .docx de evidências |

### Infraestrutura

| Agente | Função |
|--------|--------|
| `taxone-dba` | Performance Oracle, explain plans, índices, particionamento |

## Commands (9)

| Command | Função | Integração |
|---------|--------|------------|
| `/taxone-feature-dev` | Pipeline completo de desenvolvimento (13 fases) | Local |
| `/taxone-auto-fix` | Busca bugs no ADO → corrige → cria PR | ADO + GitHub |
| `/taxone-bug-fix` | Fix leve e autônomo (sem ADO) | Local |
| `/taxone-us-implement` | Implementação de User Stories do ADO | ADO + GitHub |
| `/taxone-us-auto-implement` | Pipeline automatizado US → PR | ADO + GitHub |
| `/taxone-sprint-plan` | Sprint Planning com WIs do ADO | ADO |
| `/taxone-compound` | Documenta solução como Compound Engineering | Local |
| `/taxone-doc-gen` | Gera documentação técnica/funcional | Local |
| `/query-executor-prod-uat` | Executa queries em Production/UAT | ADO |

## Agente Principal: taxone-rule-extractor

Analisa módulos do TAX ONE e produz documentos Markdown estruturados com:

- **Regras de Validação (RV)** — condições obrigatórias
- **Regras de Cálculo (RC)** — fórmulas fiscais
- **Regras de Filtro (RF)** — critérios de seleção
- **Regras de Transformação (RT)** — mapeamentos de dados
- **Regras de Workflow (RW)** — orquestração de processos

Cada regra é classificada com status de migração: `[NEW]`, `[CONVERTED]`, `[NATIVE]`, `[JAVA_GAP]`.

Inclui **inheritance crawling** (segue cadeia de herança PB até a raiz) e **gap analysis** para reescrita web moderna.

### Uso

```bash
# Extrair regras de um módulo
claude --agent taxone-rule-extractor "Extrair regras do módulo ESTADUAL/Safousp"

# Pipeline completo de desenvolvimento
claude /taxone-feature-dev "Implementar novo cálculo de DIFAL"

# Buscar e corrigir bugs do ADO
claude /taxone-auto-fix 9bb6e572-8580-4eb3-86e6-a56bc5303d69
```

## Repositórios Relacionados

| Repositório | Branch | Conteúdo |
|-------------|--------|----------|
| `tr/taxone_dw` | RC/DEV | Código fonte legado PB + PL/SQL (~48.500 arquivos) |
| `tr/taxsami_convertedFonts` | master | 10.236 DataWindows convertidos para Java (Mobilize jWebMAP) |
| `tr/taxone_modules_t1dw` | master | Nova aplicação Java nativa (Spring Boot 3.5, Java 21) |
| `tr/taxsami_conversiontool` | master | Engine de conversão PB→Java (Mobilize/Artinsoft) |
| `tr/taxsami_artifacts` | master | Runtime Java auxiliar (PB2JavaHelpers) |
| `tr/taxone_dw_conteudo` | master | Documentação, help online e layouts SAFX |
| `tr/taxsami_taxone_frontend` | master | Frontend Angular 11 (monorepo) |

## Gaps Conhecidos

| # | Gap | Status |
|---|-----|--------|
| 6 | Falta agente de conversão Java (consome output do rule-extractor → gera código T1DW) | Pendente |
| ~~7~~ | ~~Falta diretório `scripts/`~~ | Resolvido — 38 scripts copiados + zendesk_client.py criado |
| ~~8~~ | ~~Sem testes/validação dos agentes~~ | Resolvido — `scripts/validate_agents.py` |
| ~~8~~ | ~~Sem testes/validação dos agentes~~ | Resolvido — `scripts/validate_agents.py` |
| ~~9~~ | ~~`taxone-pb` usa `model: sonnet` hardcoded~~ | Resolvido — todos os 14 agentes corrigidos para `inherit` |
