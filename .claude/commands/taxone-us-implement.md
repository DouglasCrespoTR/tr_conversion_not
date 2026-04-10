---
name: taxone-us-implement
description: Pipeline de implementacao de Features e User Stories do Azure DevOps. Analisa escopo, formaliza regras de negocio, projeta solucao, implementa codigo, valida e cria PR. Separado do fluxo de bugs.
argument-hint: ID da Work Item ou Query ID do Azure DevOps
allowed-tools: ["Read", "Agent", "Bash", "AskUserQuestion"]
---

Iniciar o pipeline de implementacao de Features e User Stories do Azure DevOps.

## Instrucoes

1. Ativar a skill `taxone-us-implement` que contem o pipeline completo
2. Seguir RIGOROSAMENTE as regras R1-R6 definidas na skill
3. O projeto TAX ONE e o diretorio de trabalho atual (current working directory)
4. A base de conhecimento esta em `${CLAUDE_PLUGIN_ROOT}/knowledge/`
5. O guia da API do ADO esta em `${CLAUDE_PLUGIN_ROOT}/references/ado-api-guide.md`

## Restricoes Criticas

- Este comando TEM ACESSO a Read, Agent, Bash e AskUserQuestion
- Bash e usado APENAS para: curl (ADO API), git, gh (GitHub CLI)
- Toda implementacao de codigo DEVE ser feita via Agent com subagent_type="taxone-developer"
- Todo code review DEVE ser feito via Agent com subagent_type="taxone-reviewer"
- Analise DBA DEVE ser feita via Agent com subagent_type="taxone-dba"
- NUNCA usar agentes externos ao plugin
- Os UNICOS agentes permitidos sao: taxone-pm, taxone-architect, taxone-developer, taxone-angular, taxone-dba, taxone-reviewer, taxone-tester, taxone-sm
- O PAT token para Azure DevOps DEVE estar na variavel de ambiente ADO_PAT

## Diferenca do Bug Fix

- **Bug fix** (`/taxone-auto-fix`): "O que esta quebrado?" → corrige codigo existente
- **Feature/US** (`/taxone-us-implement`): "Qual o escopo e quais as regras?" → cria codigo novo

O PM faz analise de escopo (nao triage de bug), regras de negocio sao formalizadas antes da implementacao, e o developer cria codigo novo seguindo o design spec.

## Comportamento

1. **PRIMEIRO:** Validar que ADO_PAT esta configurado e que o WI ID ou Query ID foi informado
2. **SEGUNDO:** Buscar Work Item do ADO via REST API
3. **TERCEIRO:** PM analisa escopo e formaliza regras de negocio
4. **QUARTO:** Architect projeta solucao e design spec
5. **QUINTO:** Developer implementa seguindo o design
6. **SEXTO:** Reviewer valida, testes automatizados, PR no GitHub e update no ADO

## Argumento

O argumento pode ser:
- `/taxone-us-implement 1067285` - Implementar User Story especifica
- `/taxone-us-implement 9bb6e572-...` - Buscar USs de uma query ADO
- `/taxone-us-implement` - Sem argumento: perguntar ao usuario
